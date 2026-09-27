"""Tier 0 of the BOQ read: what the page's own geometry says of each row
the model read.

A Design Sheet is a scan; the PDF has no text coordinates. What the page
has is the table's vertical rules (the column boundaries --
design_sheet_extractor._find_layout), the horizontal rules of a ruled
layout, and Tesseract's word boxes down each column strip (_read_column).
Laid against a row the model read, they corroborate or contradict it:

  part_number_exact              the part number is printed in the catalog
                                 column at the row
  quantity_inside_expected_column the quantity is printed in the quantity
                                 column at the row -- or inline, "( n )",
                                 at the start of the description, where a
                                 panel's components carry theirs
  same_row_alignment             the row was found on the page near where
                                 the model's box put it (the boxes drift)
  description_alignment          the description column reads alike there
  single_quantity_candidate      one quantity at the row, not two
  neighbor_conflict              a quantity sits a row away and none at the
                                 row: the model may have taken a neighbour's
  part_number_disagreement       the catalog column reads another part there
  quantity_disagreement          the quantity column reads another number

Each component carries a weight; the row's confidence is the sum, in
[0, 1]; the levels "high" / "medium" / "low" come from the settings
(BOQ_CONFIDENCE_HIGH, BOQ_CONFIDENCE_LOW), so the thresholds are set by
benchmark, not here. A number inside a description ("42 in.", "SD-T42") is
never a quantity: quantities come from the quantity column and the inline
brackets only. Nothing here reads a row on its own: the model reads, the
geometry witnesses.
"""

from __future__ import annotations

import dataclasses
import difflib
import re

import numpy as np
from PIL import Image

from app.core.config import get_settings
from app.extraction import identity, values
from app.services import design_sheet_extractor as ocr

# The model's row boxes drift down a page; how far from its box a row is
# looked for (EP-30784: 70 px between two bands' boxes of the same row).
ROW_DRIFT_PX = 160
# OCR lines within this of the row's position belong to it (body text is
# ~40 px tall at the extractor's 300 dpi).
ROW_TOLERANCE_PX = 22
DESCRIPTION_LIKE = 0.6

WEIGHTS = {
    "part_number_exact": 0.35,
    # The catalog column reads the part the way a scan misreads it (I for
    # 1, S for 5, O for 0), or cut off at the column's edge, or wrapped
    # onto a second line: the same part, less surely.
    "part_number_near": 0.2,
    "quantity_inside_expected_column": 0.35,
    "same_row_alignment": 0.15,
    "description_alignment": 0.10,
    "single_quantity_candidate": 0.05,
    "neighbor_conflict": -0.25,
    "part_number_disagreement": -0.40,
    "quantity_disagreement": -0.30,
}


@dataclasses.dataclass
class ColumnRead:
    x0: int
    x1: int
    # (y centre, text, Tesseract confidence) per OCR line, in page order.
    lines: list[tuple[float, str, float]]


@dataclasses.dataclass
class PageGeometry:
    width: int
    height: int
    available: bool
    reason: str | None = None
    rules: list[int] | None = None
    quantity: ColumnRead | None = None
    catalog: ColumnRead | None = None
    description: ColumnRead | None = None
    row_rules: list[int] = dataclasses.field(default_factory=list)

    def columns(self) -> dict:
        if not self.available:
            return {}
        return {"quantity": [self.quantity.x0, self.quantity.x1], "catalog": [self.catalog.x0, self.catalog.x1],
                "description": [self.description.x0, self.description.x1]}


def analyse(image: Image.Image) -> PageGeometry:
    """The page's column rules and the OCR of each column strip. A page
    with no recognised layout gives a geometry that is not available: the
    rows on it are read by the model alone, and say so."""
    grey = image.convert("L")
    dark = np.array(grey) < 128
    found = ocr._find_layout(dark)
    if found is None:
        return PageGeometry(grey.width, grey.height, False, "no recognised column layout")
    rules, layout = found
    spans = ocr._table_extents(dark, rules[layout.description[0]])
    top, bottom = min(s[0] for s in spans), max(s[1] for s in spans)

    def column(bounds: tuple[int, int]) -> ColumnRead:
        x0, x1 = rules[bounds[0]], rules[bounds[1]]
        return ColumnRead(x0, x1, ocr._read_column(grey, x0, x1, top, bottom))

    row_rules = ocr._row_rules(dark, top, bottom, rules[layout.quantity[0]], rules[layout.quantity[1]])
    return PageGeometry(grey.width, grey.height, True, rules=rules, quantity=column(layout.quantity),
                        catalog=column(layout.catalog), description=column(layout.description), row_rules=row_rules)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _part(text: str | None) -> str:
    cleaned = identity.clean_catalog(text)[0] if text else None
    return identity.part_key(cleaned) if cleaned else ""


_FOLD = str.maketrans({"I": "1", "L": "1", "|": "1", "S": "5", "O": "0", "Z": "2", "B": "8", "G": "6"})


def _fold(part: str) -> str:
    """A part key with the characters a scan confuses folded together."""
    return part.translate(_FOLD)


def part_near(part: str, catalog_lines: list[tuple[float, str, float]]) -> bool:
    """Whether the catalog column reads `part` near enough: the same key
    once scan confusions are folded, or the column's text cut off at its
    edge (a prefix of the part, at least six characters), or the part
    spread over the column's lines at the row (a wrapped code)."""
    if not part:
        return False
    texts = [_part(text) for _y, text, _c in catalog_lines]
    joined = "".join(texts)
    for text in texts + ([joined] if len(texts) > 1 else []):
        if not text:
            continue
        if _fold(text) == _fold(part):
            return True
        if len(text) >= 6 and (_fold(part).startswith(_fold(text)) or _fold(text).startswith(_fold(part))):
            return True
    return False


def quantity_key(text: str | None) -> str | None:
    """The quantity a cell's text means, comparable: "2,396" and "2396" are
    one; text that is no quantity is None."""
    parsed = values.parse_quantity(text or "")
    return str(parsed.value).lower() if parsed.ok else None


def _near(lines: list[tuple[float, str, float]], y: float, within: float) -> list[tuple[float, str, float]]:
    return [line for line in lines if abs(line[0] - y) <= within]


def locate(row: dict, geometry: PageGeometry) -> tuple[float | None, str]:
    """Where on the page the row the model read really is: the catalog
    column's OCR line reading its part number nearest the model's box, or
    the description column's line most like its description. (None, why)
    when neither is found within ROW_DRIFT_PX."""
    x0, y0, x1, y1 = row["box"]
    centre = (y0 + y1) / 2
    part = _part(row.get("catalog_no"))
    if part and geometry.catalog is not None:
        hits = [line for line in _near(geometry.catalog.lines, centre, ROW_DRIFT_PX) if _part(line[1]) == part]
        if hits:
            return min(hits, key=lambda line: abs(line[0] - centre))[0], "part number in the catalog column"
    wanted = _norm(row.get("description"))
    if wanted and geometry.description is not None:
        best: tuple[float, float] | None = None
        for line_y, text, _conf in _near(geometry.description.lines, centre, ROW_DRIFT_PX):
            ratio = difflib.SequenceMatcher(None, wanted[:60], _norm(text)[:60]).ratio()
            if ratio >= DESCRIPTION_LIKE and (best is None or ratio > best[0]):
                best = (ratio, line_y)
        if best is not None:
            return best[1], "description in the description column"
    return None, "not found near the model's box"


def corroborate(row: dict, geometry: PageGeometry) -> dict:
    """The evidence for one row the model read: {"score", "level",
    "components", "ocr", "located", "inline"} (see the module docstring).
    `row` is a page-reading row: {"quantity", "catalog_no", "description",
    "box"}."""
    settings = get_settings()
    if not geometry.available:
        return {"score": 0.0, "level": "unknown", "components": {}, "ocr": None, "located": False, "inline": None,
                "reason": geometry.reason or "no page geometry"}
    x0, y0, x1, y1 = row["box"]
    centre = (y0 + y1) / 2
    height = max(30.0, float(y1 - y0))
    part = _part(row.get("catalog_no"))
    qty = quantity_key(row.get("quantity"))
    anchor, how = locate(row, geometry)
    located = anchor is not None
    y = anchor if anchor is not None else centre
    reach = max(ROW_TOLERANCE_PX, height * 0.6)

    # What the columns read at the row.
    qty_lines = _near(geometry.quantity.lines, y, reach)
    column_quantities = [quantity_key(text) for _y, text, _c in qty_lines]
    column_quantities = [q for q in column_quantities if q is not None]
    catalog_lines = _near(geometry.catalog.lines, y, reach)
    catalog_parts = [p for p in (_part(text) for _y, text, _c in catalog_lines) if p]
    description_lines = _near(geometry.description.lines, y, reach)
    # The inline quantity at the row: a value, "" when the description line
    # is there without one (the quantity is in the column), None when no
    # description line was found at the row.
    inline = "" if description_lines else None
    for _y, text, _c in description_lines:
        match = ocr.INLINE_QUANTITY_RE.match(text.strip())
        if match:
            inline = quantity_key(match.group(1))
            break
    description_like = any(
        difflib.SequenceMatcher(None, _norm(row.get("description"))[:60], _norm(text)[:60]).ratio() >= DESCRIPTION_LIKE
        for _y, text, _c in description_lines
    ) if row.get("description") else False
    # A quantity one row away and none at the row: the model may have
    # taken a neighbour's.
    neighbours = [line for line in geometry.quantity.lines
                  if reach < abs(line[0] - y) <= height * 1.4 and quantity_key(line[1]) is not None]

    candidates = list(dict.fromkeys(column_quantities + ([inline] if inline else [])))
    exact = bool(part) and part in catalog_parts
    components = {
        "part_number_exact": exact,
        "part_number_near": bool(part) and not exact and part_near(part, catalog_lines),
        "no_part_number_confirmed": not part and not catalog_parts,
        "quantity_inside_expected_column": qty is not None and (qty in column_quantities or (bool(inline) and qty == inline)),
        "same_row_alignment": located,
        "description_alignment": description_like,
        "single_quantity_candidate": len(candidates) == 1,
        "neighbor_conflict": not candidates and bool(neighbours),
        "part_number_disagreement": bool(part) and bool(catalog_parts) and part not in catalog_parts
                                   and not part_near(part, catalog_lines),
        "quantity_disagreement": qty is not None and bool(candidates) and qty not in candidates,
        "ocr_only": False,
    }
    score = 0.0
    for name, weight in WEIGHTS.items():
        if components.get(name):
            score += weight
    if components["no_part_number_confirmed"]:
        score += WEIGHTS["part_number_exact"]
    score = max(0.0, min(1.0, round(score, 3)))
    level = "high" if score >= settings.boq_confidence_high else ("low" if score < settings.boq_confidence_low else "medium")
    return {
        "score": score, "level": level, "components": components, "located": located, "located_by": how,
        "inline": inline, "y": round(y),
        "ocr": {"quantity": [text for _y, text, _c in qty_lines], "catalog": [text for _y, text, _c in catalog_lines],
                "description": [text[:80] for _y, text, _c in description_lines]},
    }


def page_evidence(rows: list[dict], image: Image.Image) -> tuple[dict[int, dict], PageGeometry]:
    """The evidence for every item row of a page reading, keyed by the
    row's index in `rows`; and the geometry it came from."""
    geometry = analyse(image)
    return {index: corroborate(row, geometry) for index, row in enumerate(rows)
            if row.get("kind") == "item" and (row.get("description") or row.get("catalog_no"))}, geometry
