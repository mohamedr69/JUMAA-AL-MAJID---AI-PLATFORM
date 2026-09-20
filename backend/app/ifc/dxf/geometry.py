"""Block geometry: flatten a block definition to primitives, normalise it,
fingerprint it (a name-independent signature) and draw an SVG preview.

Why this exists: block names on fire alarm drawings cannot be trusted. A
Revit export names the same smoke detector "SMOKE DETECTOR1 - ...-8784684"
and "...-8784680"; an AutoCAD drawing calls it "OS" or "SD" or "*U12". The
only thing that identifies a symbol is what it looks like: its geometry, the
letters drawn inside the block and the letters laid over it on the plan.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from ezdxf import path as ezpath
from ezdxf.math import Matrix44, Vec3

RASTER = 32              # fingerprint grid (RASTER x RASTER)
MAX_DEPTH = 4            # nested INSERT depth inside a block definition
CONTAINER_INSERTS = 10   # a block with this many nested inserts is a plan, not a symbol
CONTAINER_ENTITIES = 400 # ... or this many entities

GEOMETRY_TYPES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE",
    "SOLID", "TRACE", "3DFACE", "HATCH", "MPOLYGON",
}
TEXT_TYPES = {"TEXT", "MTEXT", "ATTDEF"}
SKIP_TYPES = {
    "WIPEOUT", "IMAGE", "POINT", "ATTRIB", "DIMENSION", "LEADER", "MLEADER",
    "XLINE", "RAY", "VIEWPORT", "ACAD_PROXY_ENTITY",
}


@dataclass
class TextItem:
    text: str
    x: float
    y: float
    height: float


@dataclass
class BlockShape:
    """A block definition reduced to polylines (block coordinates)."""
    name: str
    polylines: list[list[tuple[float, float]]] = field(default_factory=list)
    filled: list[bool] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    entity_counts: dict[str, int] = field(default_factory=dict)
    nested_inserts: int = 0
    entity_total: int = 0
    xmin: float = 0.0
    ymin: float = 0.0
    xmax: float = 0.0
    ymax: float = 0.0
    has_geometry: bool = False

    @property
    def is_container(self) -> bool:
        return self.nested_inserts >= CONTAINER_INSERTS or self.entity_total >= CONTAINER_ENTITIES

    @property
    def is_empty(self) -> bool:
        return not self.has_geometry and not self.texts

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    @property
    def inner_label(self) -> str:
        return join_labels(t.text for t in self.texts)


# ---------------------------------------------------------------- text helpers

_MTEXT_CODES = re.compile(r"\\[A-Za-z][^;]*;|\\[A-Za-z]|[{}]")
_DIGIT = re.compile(r"\d")


def plain_text(entity) -> str:
    t = entity.dxftype()
    s = ""
    try:
        if t == "MTEXT":
            s = entity.plain_text(split=False)
        elif t == "TEXT":
            s = entity.plain_text()
        else:
            s = entity.dxf.get("text", "") or ""
    except Exception:
        s = entity.dxf.get("text", "") or ""
    s = _MTEXT_CODES.sub("", str(s))
    return " ".join(s.split()).strip()


def normalise_label(s: str) -> str:
    return " ".join(s.upper().split())


def join_labels(texts: Iterable[str]) -> str:
    items = sorted({normalise_label(t) for t in texts if t and t.strip()})
    return " + ".join(items)


# Short notes that sit next to devices but never say what a device is:
# riser arrows ("from below", "to above") and stair directions.
NOT_DEVICE_LETTERS = {"F/B", "T/A", "FB", "TA", "UP", "DN", "DOWN"}


def is_label_like(s: str) -> bool:
    """Short device letters ("S", "MS", "EXIT", "WP") rather than address
    tags ("L1-D23"), room names, notes or riser arrows ("F/B"). Applied to
    text found outside the block: beside the symbol on the plan, or held in
    its attributes."""
    s = s.strip()
    if not (1 <= len(s) <= 8):
        return False
    # Device letters are one word: S, MS, EXIT, WP, W/P. Several words are a
    # note, typically a cable route beside the device ("TO VECP", "FROM FACP").
    if any(c.isspace() for c in s):
        return False
    if normalise_label(s) in NOT_DEVICE_LETTERS:
        return False
    if not any(c.isalpha() for c in s):
        return False
    return len(_DIGIT.findall(s)) <= 1


# ------------------------------------------------------------- flatten a block

def _text_item(e) -> TextItem | None:
    s = plain_text(e)
    if not s:
        return None
    try:
        p = e.dxf.insert
        if e.dxftype() == "MTEXT":
            h = float(e.dxf.get("char_height", 1.0) or 1.0)
        else:
            h = float(e.dxf.get("height", 1.0) or 1.0)
        return TextItem(s, float(p.x), float(p.y), h)
    except Exception:
        return None


def _paths_for(e) -> tuple[list, bool]:
    t = e.dxftype()
    try:
        if t in ("HATCH", "MPOLYGON"):
            return list(ezpath.from_hatch(e)), True
        if t in GEOMETRY_TYPES:
            return [ezpath.make_path(e)], t in ("SOLID", "TRACE", "3DFACE")
    except Exception:
        return [], False
    return [], False


def _walk(entities, shape: BlockShape, paths_out: list, depth: int) -> None:
    for e in entities:
        t = e.dxftype()
        shape.entity_total += 1
        if t in SKIP_TYPES:
            continue
        if t == "INSERT":
            shape.nested_inserts += 1
            if depth < MAX_DEPTH and not shape.is_container:
                try:
                    _walk(e.virtual_entities(), shape, paths_out, depth + 1)
                except Exception:
                    pass
            continue
        shape.entity_counts[t] = shape.entity_counts.get(t, 0) + 1
        if t in TEXT_TYPES:
            item = _text_item(e)
            if item:
                shape.texts.append(item)
            continue
        paths, filled = _paths_for(e)
        for p in paths:
            if len(p) == 0:
                continue
            paths_out.append((p, filled))


def block_shape(block) -> BlockShape:
    """Reduce a block layout (ezdxf BlockLayout) to a BlockShape."""
    shape = BlockShape(name=block.name)
    paths: list = []
    _walk(iter(block), shape, paths, 0)
    if shape.is_container:
        shape.polylines, shape.filled, shape.texts = [], [], []
        return shape
    if not paths and not shape.texts:
        return shape
    # Geometry decides the box: letters sit inside the symbol, and letting
    # their estimated extents widen the box would shrink the same circle
    # differently in a block with inner text and one without. Text only
    # defines the box when the block is nothing but text.
    pts: list[Vec3] = []
    for p, _ in paths:
        pts.extend(p.control_vertices())
    if not pts:
        for tx in shape.texts:
            pts.append(Vec3(tx.x, tx.y, 0))
            pts.append(Vec3(tx.x + tx.height * 0.8 * max(1, len(tx.text)), tx.y + tx.height, 0))
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    shape.xmin, shape.xmax = min(xs), max(xs)
    shape.ymin, shape.ymax = min(ys), max(ys)
    size = max(shape.width, shape.height, 1e-9)
    dist = size / 300.0
    for p, filled in paths:
        try:
            verts = [(float(v.x), float(v.y)) for v in p.flattening(dist)]
        except Exception:
            continue
        if len(verts) >= 2:
            shape.polylines.append(verts)
            shape.filled.append(filled)
    if shape.polylines:
        shape.has_geometry = True
    return shape


# ------------------------------------------------------------- normalise / raster

def normalised(shape: BlockShape, extra_texts: list[TextItem] | None = None):
    """Polylines scaled into the unit square centred on the origin, and the
    text items in the same frame as (x, y, height, text) tuples."""
    if shape.is_empty:
        return [], []
    cx = (shape.xmin + shape.xmax) / 2
    cy = (shape.ymin + shape.ymax) / 2
    s = max(shape.width, shape.height, 1e-9)
    pls = [[((x - cx) / s, (y - cy) / s) for x, y in pl] for pl in shape.polylines]
    texts = []
    for tx in list(shape.texts) + list(extra_texts or []):
        texts.append(((tx.x - cx) / s, (tx.y - cy) / s, tx.height / s, tx.text))
    return pls, texts


def rasterise(polylines: list[list[tuple[float, float]]], n: int = RASTER) -> np.ndarray:
    grid = np.zeros((n, n), dtype=bool)
    if not polylines:
        return grid
    scale = n - 1
    for pl in polylines:
        for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
            px0 = (x0 + 0.5) * scale
            py0 = (0.5 - y0) * scale
            px1 = (x1 + 0.5) * scale
            py1 = (0.5 - y1) * scale
            steps = int(max(abs(px1 - px0), abs(py1 - py0)) * 2) + 1
            for i in range(steps + 1):
                t = i / steps
                ix = min(max(int(round(px0 + (px1 - px0) * t)), 0), n - 1)
                iy = min(max(int(round(py0 + (py1 - py0) * t)), 0), n - 1)
                grid[iy, ix] = True
    return grid


def grid_to_int(grid: np.ndarray) -> int:
    bits = np.packbits(grid.astype(np.uint8).ravel())
    return int.from_bytes(bits.tobytes(), "big")


def grid_to_hex(grid: np.ndarray) -> str:
    return np.packbits(grid.astype(np.uint8).ravel()).tobytes().hex()


def hex_to_int(h: str) -> int:
    return int(h, 16) if h else 0


def symmetries(grid: np.ndarray) -> list[int]:
    """The 8 rotations/mirrors of a grid packed as ints, so a symbol drawn
    rotated or flipped in another library still finds its twin."""
    out = []
    g = grid
    for _ in range(4):
        out.append(grid_to_int(g))
        out.append(grid_to_int(np.fliplr(g)))
        g = np.rot90(g)
    return out


def symmetries_hex(grid_hex: str, n: int = RASTER) -> list[int]:
    raw = bytes.fromhex(grid_hex) if grid_hex else b""
    if not raw:
        return [0] * 8
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[: n * n]
    return symmetries(bits.reshape(n, n).astype(bool))


def iou(a: int, b: int) -> float:
    inter = (a & b).bit_count()
    union = (a | b).bit_count()
    if union == 0:
        return 1.0
    return inter / union


def dilate(grid: np.ndarray) -> np.ndarray:
    """One-pixel dilation, so a line one cell off still overlaps."""
    g = grid.copy()
    g[1:, :] |= grid[:-1, :]
    g[:-1, :] |= grid[1:, :]
    g[:, 1:] |= grid[:, :-1]
    g[:, :-1] |= grid[:, 1:]
    return g


def similarity(query_syms: list[int], query_dilated_syms: list[int], target: int, target_dilated: int) -> float:
    """Symmetric tolerant overlap: how much of each shape lies within one
    pixel of the other. 1.0 for the same shape, ~0 for unrelated ones."""
    best = 0.0
    tb = target.bit_count()
    for q, qd in zip(query_syms, query_dilated_syms):
        qb = q.bit_count()
        if qb == 0 or tb == 0:
            s = 1.0 if qb == tb else 0.0
        else:
            a = (q & target_dilated).bit_count() / qb
            b = (target & qd).bit_count() / tb
            s = min(a, b)
        if s > best:
            best = s
    return best


def grid_from_hex(h: str, n: int = RASTER) -> np.ndarray | None:
    if not h:
        return None
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(h), dtype=np.uint8))[: n * n]
    return bits.reshape(n, n).astype(bool)


def _shifted(g: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(g)
    n = g.shape[0]
    xs, xd = (slice(0, n - dx), slice(dx, n)) if dx >= 0 else (slice(-dx, n), slice(0, n + dx))
    ys, yd = (slice(0, n - dy), slice(dy, n)) if dy >= 0 else (slice(-dy, n), slice(0, n + dy))
    out[yd, xd] = g[ys, xs]
    return out


def aligned_containment(query: np.ndarray, target: np.ndarray, max_shift: int = 6) -> tuple[float, float]:
    """How much of `query` lies on `target`, at the best rotation, mirror and
    small shift, and how much of `target` that covers.

    For a symbol that lost part of its drawing in export (FA-104's call
    point is missing the bottom edge of its box), the plain comparison fails
    because the shape is scaled to its own, shorter, outline and so sits
    off-centre. Shifted back into place, every line it has lies on the
    verified symbol: containment ~1.0, coverage ~0.8."""
    q_total = int(query.sum())
    t_total = int(target.sum())
    if q_total == 0 or t_total == 0:
        return 0.0, 0.0
    t_dil = dilate(target)
    best = (0.0, 0.0)
    g = query
    for _ in range(4):
        for cand in (g, np.fliplr(g)):
            c_dil = dilate(cand)
            for dy in range(-max_shift, max_shift + 1):
                for dx in range(-max_shift, max_shift + 1):
                    s = _shifted(cand, dx, dy)
                    inside = int((s & t_dil).sum()) / q_total
                    if inside < best[0]:
                        continue
                    covered = int((target & _shifted(c_dil, dx, dy)).sum()) / t_total
                    if (inside, covered) > best:
                        best = (inside, covered)
        g = np.rot90(g)
    return best


def signature(grid_hex: str, label: str, entity_counts: dict[str, int]) -> str:
    """Name-independent identity of a symbol: geometry raster + letters +
    what it is made of."""
    counts = ",".join(f"{k}:{v}" for k, v in sorted(entity_counts.items()))
    h = hashlib.sha1(f"{grid_hex}|{normalise_label(label)}|{counts}".encode()).hexdigest()
    return h[:20]


# ---------------------------------------------------------------- SVG preview

def svg_preview(polylines, filled, texts, size: int = 96) -> str:
    """A small SVG of the normalised symbol drawn in currentColor."""
    pad = 8
    span = size - 2 * pad

    def X(x: float) -> float:
        return pad + (x + 0.5) * span

    def Y(y: float) -> float:
        return pad + (0.5 - y) * span

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">']
    for pl, fill in zip(polylines, filled):
        d = "M " + " L ".join(f"{X(x):.1f} {Y(y):.1f}" for x, y in pl)
        if fill:
            parts.append(f'<path d="{d} Z" fill="currentColor" fill-opacity="0.35" stroke="currentColor" stroke-width="1"/>')
        else:
            parts.append(f'<path d="{d}" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>')
    for x, y, h, text in texts:
        fs = max(7.0, min(h * span, span * 0.45))
        safe = text.replace("&", "&amp;").replace("<", "&lt;")
        parts.append(
            f'<text x="{X(x):.1f}" y="{Y(y) + fs * 0.35:.1f}" font-size="{fs:.1f}" '
            f'font-family="Arial, sans-serif" fill="currentColor">{safe}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------- transforms

def transformed_bbox(shape: BlockShape, m: Matrix44) -> tuple[float, float, float, float]:
    corners = [
        m.transform(Vec3(shape.xmin, shape.ymin, 0)),
        m.transform(Vec3(shape.xmax, shape.ymin, 0)),
        m.transform(Vec3(shape.xmax, shape.ymax, 0)),
        m.transform(Vec3(shape.xmin, shape.ymax, 0)),
    ]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def to_block_frame(m: Matrix44, x: float, y: float) -> tuple[float, float] | None:
    """Drawing coordinates to block coordinates; None for a degenerate insert."""
    inv = m.copy()
    try:
        r = inv.inverse()
        if r is not None:
            inv = r
    except ZeroDivisionError:
        return None
    v = inv.transform(Vec3(x, y, 0))
    return float(v.x), float(v.y)


def uniform_scale(m: Matrix44) -> float:
    v = m.transform_direction(Vec3(1, 0, 0))
    return float(math.hypot(v.x, v.y)) or 1.0
