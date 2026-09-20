"""Symbols drawn without a block: exploded, or drawn in lines to begin with.

Bad drafting leaves devices on an IFC drawing that are no block at all --
a sounder exploded into a triangle and a rectangle, a smoke detector as a
loose circle with a loose "OS" -- and a reader of blocks alone never sees
them. On EP-30880 there were 2,389 such entities in model space, most of
them on the fire alarm layers.

So the loose geometry is read by what it looks like, the same way a block
is: the pieces that touch each other are one candidate symbol, sized
against the drawing's own symbol blocks; a candidate is fingerprinted by
the very same function as a block (`geometry.block_shape`), and goes
through the same library, review, sheet and architecture checks. Anything
longer than a symbol -- a cable, a wall, a leader -- is not part of one,
and the architect's xref layers are left out.

An exploded copy has its rotation baked into its lines, where a block
carries it on the insert; each candidate is therefore turned to one
canonical orientation (of the eight quarter-turns and mirrors, the one
whose raster packs smallest), so the same symbol drawn four ways is one
group.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import numpy as np
from ezdxf import bbox as ezbbox

from . import geometry as G

# The block name every symbol drawn without a block is listed under.
LOOSE_NAME = "(drawn with lines, not a block)"

MAX_PIECE = 2.0     # x symbol size: a longer piece is a cable, a wall or a leader
MIN_SYMBOL = 0.25   # x symbol size: smaller is a tick, an arrow head, a dot
MAX_SYMBOL = 2.5    # x symbol size: larger is a detail, not a device
TOUCH = 0.05        # x symbol size: pieces this close are drawn together
MAX_PIECES = 60
MAX_CANDIDATES = 60_000  # pieces considered at most, so a drawing of loose architecture stays quick
_XREF_LAYER = re.compile(r"\$0\$|\|")


@dataclass
class LooseSymbol:
    shape: G.BlockShape                        # in its canonical orientation, about its own centre
    box: tuple[float, float, float, float]     # where it sits on the plan
    handle: str
    layer: str
    pieces: int

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2


class _Pieces:
    """Loose entities in the form `geometry.block_shape` reads a block in."""

    def __init__(self, name: str, entities: list):
        self.name = name
        self._entities = entities

    def __iter__(self):
        return iter(self._entities)


def symbol_size(sizes: list[float]) -> float | None:
    """The drawing's typical symbol size: the median of its symbol blocks'."""
    sizes = sorted(s for s in sizes if s > 0)
    return sizes[len(sizes) // 2] if sizes else None


def find(entities, size: float) -> list[LooseSymbol]:
    """The candidate symbols among loose model-space entities."""
    pieces: list[tuple[object, tuple[float, float, float, float]]] = []
    longest = MAX_PIECE * size
    for e in entities:
        if e.dxftype() not in G.GEOMETRY_TYPES:
            continue
        layer = str(e.dxf.get("layer", "0"))
        if _XREF_LAYER.search(layer):
            continue
        try:
            b = ezbbox.extents([e], fast=True)
        except Exception:
            continue
        if not b.has_data:
            continue
        box = (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
        if max(box[2] - box[0], box[3] - box[1]) > longest:
            continue
        pieces.append((e, box))
        if len(pieces) >= MAX_CANDIDATES:
            break

    # Pieces whose boxes touch are drawn together: union-find over a grid.
    parent = list(range(len(pieces)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tol = TOUCH * size
    cell = max(size, 1e-9)
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (_, (x0, y0, x1, y1)) in enumerate(pieces):
        for gx in range(int(np.floor((x0 - tol) / cell)), int(np.floor((x1 + tol) / cell)) + 1):
            for gy in range(int(np.floor((y0 - tol) / cell)), int(np.floor((y1 + tol) / cell)) + 1):
                for j in buckets.setdefault((gx, gy), []):
                    a, b = pieces[j][1], pieces[i][1]
                    if a[0] - tol <= b[2] and b[0] - tol <= a[2] and a[1] - tol <= b[3] and b[1] - tol <= a[3]:
                        ri, rj = root(i), root(j)
                        if ri != rj:
                            parent[ri] = rj
                buckets[(gx, gy)].append(i)

    clusters: dict[int, list[int]] = {}
    for i in range(len(pieces)):
        clusters.setdefault(root(i), []).append(i)

    out: list[LooseSymbol] = []
    for members in clusters.values():
        if len(members) > MAX_PIECES:
            continue
        boxes = [pieces[i][1] for i in members]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        extent = max(box[2] - box[0], box[3] - box[1])
        if not MIN_SYMBOL * size <= extent <= MAX_SYMBOL * size:
            continue
        ents = [pieces[i][0] for i in members]
        first = min(ents, key=lambda e: str(e.dxf.handle))
        shape = G.block_shape(_Pieces(f"(loose {first.dxf.handle})", ents))
        if not shape.has_geometry:
            continue
        _canonical(shape)
        layer = Counter(str(e.dxf.get("layer", "0")) for e in ents).most_common(1)[0][0]
        out.append(LooseSymbol(shape=shape, box=box, handle=str(first.dxf.handle), layer=layer, pieces=len(ents)))
    return out


def _canonical(shape: G.BlockShape) -> None:
    """Turn the drawing about its centre to the one of its eight quarter-turns
    and mirrors whose raster packs smallest, in place."""
    cx, cy = (shape.xmin + shape.xmax) / 2, (shape.ymin + shape.ymax) / 2
    turns = [(r, m) for r in range(4) for m in (False, True)]

    def turned(x: float, y: float, r: int, m: bool) -> tuple[float, float]:
        u, v = x - cx, y - cy
        if m:
            u = -u
        for _ in range(r):
            u, v = -v, u
        return cx + u, cy + v

    best, best_key = (0, False), None
    for r, m in turns:
        pls = [[turned(x, y, r, m) for x, y in pl] for pl in shape.polylines]
        xs = [x for pl in pls for x, _ in pl]
        ys = [y for pl in pls for _, y in pl]
        s = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        key = G.grid_to_int(G.rasterise([[((x - mx) / s, (y - my) / s) for x, y in pl] for pl in pls]))
        if best_key is None or key < best_key:
            best, best_key = (r, m), key
    r, m = best
    shape.polylines = [[turned(x, y, r, m) for x, y in pl] for pl in shape.polylines]
    for t in shape.texts:
        t.x, t.y = turned(t.x, t.y, r, m)
    xs = [x for pl in shape.polylines for x, _ in pl]
    ys = [y for pl in shape.polylines for _, y in pl]
    shape.xmin, shape.xmax, shape.ymin, shape.ymax = min(xs), max(xs), min(ys), max(ys)
