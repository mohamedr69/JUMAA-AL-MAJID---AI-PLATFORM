"""Walk a DXF drawing and collect every symbol instance in model space.

The result is a list of symbol *groups*: instances that look the same
(same normalised geometry, same letters inside the block, same letters laid
over it) regardless of the block name they were inserted under. Each group
carries its signature, a preview, the block names that produced it and
every occurrence (position, layer, rotation, attributes) for the schedule.

Container blocks (a whole floor plan exported as one block, or an
architectural underlay with hundreds of nested doors and windows) are not
symbols: the walker descends into them and treats their nested inserts as
if they were placed directly on the plan.

Symbols drawn without a block -- exploded, or drawn in lines -- are read
from the loose geometry in model space by what they look like (`loose`),
and listed under the block name `loose.LOOSE_NAME`.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf.math import Matrix44, Vec3

from . import geometry as G
from . import loose as LOOSE
from . import sheets as SH

MAX_SPACE_DEPTH = 3  # containers inside containers


@dataclass
class Occurrence:
    handle: str
    block_name: str
    layer: str
    x: float
    y: float
    rotation: float
    scale: float
    space: str
    label_over: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    sheet: str = ""
    on_arch: bool | None = None  # None: the drawing has no architecture to judge by
    # Where the symbol is seen: the centre of its outline on the plan. Not
    # always its insertion point (x, y): a block can be drawn far from its
    # own base point -- on EP-30880 the sounder strobe's drawing sits 1 km
    # from it and the call point's 38 km -- so the symbol shows on the floor
    # plan while its insertion point is off every sheet. Which sheet shows
    # a device, and whether it sits on the architecture, go by this.
    cx: float | None = None
    cy: float | None = None

    @property
    def seen_at(self) -> tuple[float, float]:
        return (self.x, self.y) if self.cx is None or self.cy is None else (self.cx, self.cy)

    def to_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "on_arch": self.on_arch,
            "handle": self.handle,
            "block_name": self.block_name,
            "layer": self.layer,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "cx": round(self.seen_at[0], 2),
            "cy": round(self.seen_at[1], 2),
            "rotation": round(self.rotation, 1),
            "scale": round(self.scale, 3),
            "space": self.space,
            "label_over": self.label_over,
            "attrs": self.attrs,
        }


@dataclass
class SymbolGroup:
    signature: str
    label: str
    inner_label: str
    raster_hex: str
    svg: str
    entity_counts: dict[str, int]
    block_names: dict[str, int] = field(default_factory=dict)
    layers: dict[str, int] = field(default_factory=dict)
    occurrences: list[Occurrence] = field(default_factory=list)
    size: float = 0.0  # drawing units, longest side of the first instance

    @property
    def count(self) -> int:
        return len(self.occurrences)

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "label": self.label,
            "inner_label": self.inner_label,
            "raster_hex": self.raster_hex,
            "svg": self.svg,
            "entity_counts": self.entity_counts,
            "block_names": self.block_names,
            "layers": self.layers,
            "count": self.count,
            "size": round(self.size, 1),
            "occurrences": [o.to_dict() for o in self.occurrences],
        }


@dataclass
class ExtractionResult:
    groups: list[SymbolGroup]
    units: str
    dxf_version: str
    skipped_empty_blocks: list[str]
    containers: list[str]
    layouts: list[str]
    seconds: float
    sheets: list = field(default_factory=list)
    architecture: dict = field(default_factory=dict)
    loose_symbols: int = 0   # symbols read from geometry drawn without a block

    def to_dict(self) -> dict:
        return {
            "loose_symbols": self.loose_symbols,
            "sheets": [s.to_dict() for s in self.sheets],
            "architecture": self.architecture,
            "units": self.units,
            "dxf_version": self.dxf_version,
            "skipped_empty_blocks": self.skipped_empty_blocks,
            "containers": self.containers,
            "layouts": self.layouts,
            "seconds": round(self.seconds, 2),
            "groups": [g.to_dict() for g in self.groups],
        }


_UNITS = {0: "unitless", 1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}


class _Space:
    """Entities of one drawing space (model space or a container's content)
    with the loose text in it, for label-over lookup."""

    def __init__(self, name: str, entities, keep_loose: bool = False):
        self.name = name
        self.inserts = []
        # geometry that is no block: read for symbols drawn without one (model space only)
        self.loose: list = []
        # (x, y, height, text, text box as (x0, y0, x1, y1))
        self.texts: list[tuple[float, float, float, str, tuple[float, float, float, float]]] = []
        for e in entities:
            t = e.dxftype()
            if t == "INSERT":
                self.inserts.append(e)
            elif t in ("TEXT", "MTEXT"):
                item = G._text_item(e)
                if item and G.is_label_like(item.text):
                    self.texts.append((item.x, item.y, item.height, item.text, _text_box(e, item)))
            elif keep_loose and t in G.GEOMETRY_TYPES:
                self.loose.append(e)


class _LooseInsert:
    """A symbol drawn without a block, in the form the walker reads an INSERT in."""

    class _Dxf:
        def __init__(self, sym: "LOOSE.LooseSymbol"):
            self.name = LOOSE.LOOSE_NAME
            self.handle = sym.handle
            self.layer = sym.layer
            self.insert = Vec3(*sym.centre, 0)
            self.rotation = 0.0

    def __init__(self, sym: "LOOSE.LooseSymbol"):
        self.dxf = self._Dxf(sym)
        self.attribs: list = []


def _text_box(e, item: G.TextItem) -> tuple[float, float, float, float]:
    """The text's extent on the plan. MTEXT is anchored by its attachment
    point, so the insertion point alone can sit a full text-width away from
    the letters; measure the box instead, estimating it if ezdxf cannot."""
    try:
        b = ezbbox.extents([e])
        if b.has_data:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:
        pass
    w = item.height * 0.8 * max(1, len(item.text))
    return (item.x, item.y, item.x + w, item.y + item.height)


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Distance between two boxes; 0 when they overlap."""
    dx = max(a[0] - b[2], 0.0, b[0] - a[2])
    dy = max(a[1] - b[3], 0.0, b[1] - a[3])
    return math.hypot(dx, dy)


def assign_labels(symbol_boxes: list[tuple[float, float, float, float]], texts) -> list[list[tuple[float, float, float, str]]]:
    """Give every short label on the plan to the ONE symbol it belongs to.

    A label is a candidate for a symbol when its text is no taller than the
    symbol and the gap between the text box and the symbol's outline is
    within reach: half the symbol's size, or twice the text height, whichever
    is larger. Designers put qualifiers such as "wp" beside a symbol, not on
    it: on FA-102 the weatherproof call points' "wp" sits 78-309 mm from a
    300 mm symbol. Of all candidates the nearest wins (then the one whose
    centre is closest), so "EL" beside a speaker goes to its emergency light
    and one label never counts for two symbols."""
    out: list[list[tuple[float, float, float, str]]] = [[] for _ in symbol_boxes]
    if not symbol_boxes:
        return out
    sizes = [max(b[2] - b[0], b[3] - b[1], 1e-9) for b in symbol_boxes]
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in symbol_boxes]
    for x, y, th, s, tb in texts:
        tc = ((tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2)
        best, best_key = None, None
        for i, sb in enumerate(symbol_boxes):
            size = sizes[i]
            if th > size:
                continue
            reach = max(0.5 * size, 2.0 * th)
            # cheap reject before the exact gap
            if tb[0] > sb[2] + reach or tb[2] < sb[0] - reach or tb[1] > sb[3] + reach or tb[3] < sb[1] - reach:
                continue
            g = _gap(sb, tb)
            if g > reach:
                continue
            key = (g, math.hypot(tc[0] - centres[i][0], tc[1] - centres[i][1]))
            if best_key is None or key < best_key:
                best, best_key = i, key
        if best is not None:
            out[best].append((x, y, th, s))
    return out


_XREF_NAME = re.compile(r"\$0\$|^XR[-_ ]|XREF|_RVT\b|\bRVT\b", re.I)
_LEGEND = re.compile(r"LEGEND|DESCRIPTION|SCHEDULE|TITLE\s*BLOCK", re.I)
ARCH_MARGIN = 0.02  # of the architecture's size, so devices on an outside wall still count


class _Pieces:
    """How many drawn pieces (lines, arcs, splines...) a block reference
    flattens to, counted from the block definitions without flattening
    anything. It is what a stage of the read costs, and so how far it has
    got: a Revit model exported as one block is 400,000 pieces, a detector
    ten."""

    def __init__(self, doc):
        self.doc = doc
        self.blocks: dict[str, int] = {}

    def block(self, name: str) -> int:
        if name in self.blocks:
            return self.blocks[name]
        self.blocks[name] = 0  # a block that holds itself counts once
        blk = self.doc.blocks.get(name)
        n = 0
        if blk is not None:
            for e in blk:
                n += self.insert(e) if e.dxftype() == "INSERT" else 1
        self.blocks[name] = n
        return n

    def insert(self, ins) -> int:
        try:
            count = max(int(ins.mcount), 1)
            return self.block(ins.dxf.name) * count + len(ins.attribs)
        except Exception:
            return 1


def _architecture(doc, inserts, pieces: _Pieces | None = None,
                  progress=None) -> tuple[list[tuple[float, float, float, float]], list[str]]:
    """Outlines of the architecture placed in model space: the Revit model
    exported as one block, floor-plan blocks and bound xrefs. A block that
    carries a legend or a schedule is not architecture: symbols drawn in a
    legend are not devices on the plan.

    The outline is ezdxf's `extents(fast=True)`, taken box by box so that
    `progress(fraction)` hears how far it is; the merged boxes are the same
    outline as the one call."""
    pieces = pieces or _Pieces(doc)
    report = progress or (lambda fraction: None)
    total = sum(pieces.insert(ins) for ins in inserts) or 1
    done = 0
    boxes, names = [], []
    for ins in inserts:
        blk = doc.blocks.get(ins.dxf.name)
        size = pieces.insert(ins)
        if blk is None:
            done += size
            continue
        try:
            if any(_LEGEND.search(G.plain_text(e)) for e in blk.query("TEXT MTEXT")):
                done += size
                continue
            b = ezbbox.BoundingBox()
            for n, box in enumerate(ezbbox.multi_recursive([ins], fast=True)):
                b.extend(box)
                if n % 2000 == 0:
                    report(min((done + n) / total, 1.0))
        except Exception:
            done += size
            continue
        done += size
        report(min(done / total, 1.0))
        if not b.has_data:
            continue
        w, h = b.extmax.x - b.extmin.x, b.extmax.y - b.extmin.y
        m = ARCH_MARGIN * max(w, h)
        boxes.append((b.extmin.x - m, b.extmin.y - m, b.extmax.x + m, b.extmax.y + m))
        names.append(ins.dxf.name)
    return boxes, names


def extract(path: str, progress=None, plan=None) -> ExtractionResult:
    """`progress(stage, fraction)`, when given, hears how far the read is:
    "read" (the DXF opened), "walk" (the symbols, 0..1), "finish" (the
    sheets and the architecture's outline, 0..1). `plan(pieces)` hears what
    the walk and the architecture cost, in drawn pieces, before each runs --
    the architecture's first as a guess (the big blocks in model space),
    then as counted once the walk has found it."""
    report = progress or (lambda stage, fraction: None)
    tell_plan = plan or (lambda pieces: None)
    t0 = time.time()
    report("read", 0.0)
    doc = ezdxf.readfile(path)
    report("read", 1.0)
    pieces = _Pieces(doc)
    units = _UNITS.get(int(doc.header.get("$INSUNITS", 0) or 0), "unitless")
    shapes: dict[str, G.BlockShape] = {}
    groups: dict[str, SymbolGroup] = {}
    skipped: set[str] = set()
    containers: set[str] = set()

    def shape_of(name: str) -> G.BlockShape | None:
        if name in shapes:
            return shapes[name]
        block = doc.blocks.get(name)
        if block is None:
            shapes[name] = None  # type: ignore[assignment]
            return None
        shp = G.block_shape(block)
        shapes[name] = shp
        return shp

    def costs(inserts) -> list[int]:
        """What each insert costs the walk: a block is drawn once (its shape
        is kept), a container is walked every time."""
        seen: set[str] = set()
        out = []
        for ins in inserts:
            name = ins.dxf.name
            out.append(1 if name in seen else max(pieces.insert(ins), 1))
            seen.add(name)
        return out

    def walk(space: _Space, depth: int, base: float = 0.0, span: float = 1.0) -> None:
        # `base` and `span`: this space's share of the walk, which a container
        # walked inside it divides again by what its inserts cost
        cost = costs(space.inserts) if depth <= 1 else None
        scale = span / (sum(cost) or 1) if cost else 0.0
        spent = 0
        # pass 1: the symbols placed in this space, with their outlines
        placed = []
        for n, ins in enumerate(space.inserts):
            share = cost[n] * scale if cost else 0.0
            if cost and n % 25 == 0:
                report("walk", base + spent * scale)
            if cost:
                spent += cost[n]
            name = ins.dxf.name
            shp = shape_of(name)
            if shp is None:
                skipped.add(name)
                continue
            if shp.is_container:
                containers.add(name)
                if depth == 0:
                    arch_inserts.append(ins)
                if depth < MAX_SPACE_DEPTH:
                    try:
                        inner = _Space(name, ins.virtual_entities())
                    except Exception:
                        continue
                    walk(inner, depth + 1, base + spent * scale - share, share)
                continue
            if shp.is_empty:
                skipped.add(name)
                continue
            try:
                m = ins.matrix44()
            except Exception:
                continue
            if depth == 0 and _XREF_NAME.search(name) and shp.entity_total >= 50:
                arch_inserts.append(ins)  # a bound architectural xref too small to be a container
                continue
            placed.append((ins, shp, m, G.transformed_bbox(shp, m)))

        # pass 1b: symbols drawn without a block, sized against the drawing's own symbols
        if space.loose:
            size = LOOSE.symbol_size([max(bb[2] - bb[0], bb[3] - bb[1]) for *_, bb in placed])
            if size:
                for sym in LOOSE.find(space.loose, size):
                    placed.append((_LooseInsert(sym), sym.shape, Matrix44(), sym.box))
                    loose_found.append(sym)

        # pass 2: every label on the plan goes to the one symbol it belongs to
        labels = assign_labels([p[3] for p in placed], space.texts)

        for (ins, shp, m, bb), over in zip(placed, labels):
            name = ins.dxf.name
            label_over = G.join_labels(s for _, _, _, s in over)
            attrs: dict[str, str] = {}
            try:
                for a in ins.attribs:
                    attrs[str(a.dxf.tag)] = G.plain_text(a)
            except Exception:
                pass
            attr_label = G.join_labels(v for v in attrs.values() if G.is_label_like(v))
            label = G.join_labels([shp.inner_label, label_over, attr_label])
            key = (name, label)
            group = _group_for(shp, label, key, over, m)
            occ = Occurrence(
                handle=str(ins.dxf.handle),
                block_name=name,
                layer=str(ins.dxf.layer),
                x=float(ins.dxf.insert.x),
                y=float(ins.dxf.insert.y),
                rotation=float(ins.dxf.rotation),
                scale=G.uniform_scale(m),
                space=space.name,
                label_over=label_over,
                attrs=attrs,
                cx=(bb[0] + bb[2]) / 2,
                cy=(bb[1] + bb[3]) / 2,
            )
            group.occurrences.append(occ)
            group.block_names[name] = group.block_names.get(name, 0) + 1
            group.layers[occ.layer] = group.layers.get(occ.layer, 0) + 1

    arch_inserts: list = []
    loose_found: list = []
    fingerprints: dict[str, tuple[str, str, list, list]] = {}

    def _group_for(shp: G.BlockShape, label: str, key, over, m: Matrix44) -> SymbolGroup:
        name = shp.name
        if name not in fingerprints:
            pls, texts = G.normalised(shp)
            grid = G.rasterise(pls)
            fingerprints[name] = (G.grid_to_hex(grid), shp.inner_label, pls, texts)
        raster_hex, inner_label, pls, texts = fingerprints[name]
        sig = G.signature(raster_hex, label, shp.entity_counts)
        group = groups.get(sig)
        if group is None:
            # overlay text of the first instance, drawn in the preview where it sits
            extra = []
            for x, y, th, s in over:
                pt = G.to_block_frame(m, x, y)
                if pt is None:
                    continue
                extra.append(G.TextItem(s, pt[0], pt[1], th / G.uniform_scale(m)))
            _, all_texts = G.normalised(shp, extra)
            filled = list(shp.filled)
            svg = G.svg_preview(pls, filled, all_texts)
            group = SymbolGroup(
                signature=sig,
                label=label,
                inner_label=inner_label,
                raster_hex=raster_hex,
                svg=svg,
                entity_counts=dict(shp.entity_counts),
                size=max(shp.width, shp.height) * G.uniform_scale(m),
            )
            groups[sig] = group
        return group

    msp = doc.modelspace()
    model = _Space("Model", iter(msp), keep_loose=True)
    big = [pieces.insert(ins) for ins in model.inserts]
    tell_plan({"walk": sum(costs(model.inserts)), "finish": sum(n for n in big if n >= 1000)})
    walk(model, 0)
    report("walk", 1.0)
    tell_plan({"finish": sum(pieces.insert(ins) for ins in arch_inserts)})
    report("finish", 0.0)

    # which sheet (floor) shows each device
    try:
        sheet_list = SH.read_sheets(doc)
    except Exception:
        sheet_list = []
    for group in groups.values():
        for o in group.occurrences:
            o.sheet = SH.sheet_for(sheet_list, *o.seen_at) if sheet_list else SH.OUTSIDE

    # which devices are placed on the architecture
    arch_boxes, arch_names = _architecture(doc, arch_inserts, pieces, lambda f: report("finish", f))
    for group in groups.values():
        for o in group.occurrences:
            x, y = o.seen_at
            o.on_arch = (any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in arch_boxes)) if arch_boxes else None
    architecture = {"found": bool(arch_boxes), "blocks": len(arch_boxes), "names": arch_names[:20]}
    report("finish", 1.0)

    ordered = sorted(groups.values(), key=lambda g: (-g.count, g.label))
    return ExtractionResult(
        groups=ordered,
        units=units,
        dxf_version=doc.dxfversion,
        skipped_empty_blocks=sorted(skipped),
        containers=sorted(containers),
        layouts=[l.name for l in doc.layouts],
        seconds=time.time() - t0,
        sheets=sheet_list,
        architecture=architecture,
        loose_symbols=len(loose_found),
    )
