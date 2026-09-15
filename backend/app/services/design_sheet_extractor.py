"""Deterministic BOQ line-item extraction from a Design Sheet -- OCR plus
column-rule detection, no AI model involved.

Design Sheets are quotations for one system, and unlike the DRF there is no
single template: the archive holds a scanned EST4 quotation (Qty / Catalog
No. / Description / Unit Price / Total Price) and a spreadsheet export with a
different column set and no prices. What they do share is that the item table
is fenced by full-height vertical rules, and the *number* of those rules
identifies the layout -- which is what LAYOUTS keys off.

Each column is OCR'd as its own strip. Handing Tesseract the whole table
instead lets it run its own reading order across the columns, which
interleaves the quantity into the description; handing it one cell at a time
is worse still, because it reads a lone digit far better with the rest of the
column around it ("327" became "37" cell by cell, and was right as a strip).

How rows are recovered depends on the sheet. Some rule every row, and those
rules are authoritative -- a description wrapping onto a second line puts its
quantity level with neither line, so grouping by spacing drops the quantity
and emits the remainder as an item of its own. Where there are no row rules a
row is simply a line of text, and the strips are paired by vertical position.

Nothing here writes to the database: lines are returned for an engineer to
review and save, the same treatment DRF fields get.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
import pytesseract
from PIL import Image

from app.core.config import get_settings
from app.extraction import identity, values
from app.extraction.issues import Coverage, Issue, IssueCode, Outcome, PageCoverage, RegionCoverage, outcome_for

settings = get_settings()

# Part of every cache key: a change to how a sheet is read is a change to
# what a cached result means.
PARSER_VERSION = "2026-09-15.2"
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

RENDER_DPI = 300
COLUMN_LINE_DARK_FRACTION = 0.5

# Words landing within this many pixels of each other vertically belong to the
# same line of text. Body text on these sheets is ~40px tall at RENDER_DPI.
LINE_GROUP_TOLERANCE_PX = 14
# How far a quantity or catalog cell may sit from its description line before
# it is treated as belonging to a different row.
COLUMN_PAIRING_TOLERANCE_PX = 16
CELL_INSET_PX = 4  # keeps the column rule out of the OCR input
# A printed rule survives scanning with at most a few pixels dropped; a bigger
# break means the run has left the rule and is picking up unrelated ink.
MAX_RULE_GAP_PX = 5
# These pages are scanned slightly askew, so a rule wanders sideways over the
# height of the page. Following it down a 3px-wide column loses it partway and
# reports the table starting halfway down; this is how far to let it drift.
RULE_DRIFT_PX = 8
# A row of a column strip this dark across its width is a cell border, not
# text -- even a full line of text leaves gaps between its letters.
RULE_ROW_DARK_FRACTION = 0.8
# How far either side of a rule's solid core to keep erasing, and how dark a
# neighbouring row must still be to count as part of the same rule rather than
# as the text below it.
RULE_EDGE_GROWTH_PX = 4
RULE_EDGE_DARK_FRACTION = 0.25



@dataclass(frozen=True)
class _Layout:
    """Which column strip holds what, keyed by the count of vertical rules.

    Indices are into the detected rule positions, so (0, 1) means the strip
    between the first and second rule.
    """

    quantity: tuple[int, int]
    catalog: tuple[int, int]
    description: tuple[int, int]


LAYOUTS: dict[int, _Layout] = {
    # Qty | Catalog No. | Description | Unit Price | Total Price
    6: _Layout(quantity=(0, 1), catalog=(1, 2), description=(2, 3)),
    # Catalog | Description | Qty  (spreadsheet export; quantity on the right)
    4: _Layout(quantity=(2, 3), catalog=(0, 1), description=(1, 2)),
}

# A sub-component listed under a group heading carries its own quantity inline,
# as "( 2 )" ahead of the text. OCR renders the brackets loosely -- "(1. )",
# "(_ 1 )" -- so the digits are matched rather than the punctuation.
INLINE_QUANTITY_RE = re.compile(r"^\(\s*[^)\d]*(\d+)[^)\d]*\)\s*")

# "Page 3 of 4" -- and the ways a scan misreads it ("Bage 4 of 2", "Paqe 1 0f 2").
PAGE_FOOTER_RE = re.compile(r"^[pb8][ae]g[ea]\s*\d+\s*[o0][fr]\s*\d+$", re.IGNORECASE)

COLUMN_HEADINGS = {"qty", "qty.", "catalog no", "catalog no.", "description", "unit price",
                   "total price", "brand", "ms", "dwg"}

# A short line with no quantity and no catalog number is a group heading (a
# panel whose parts follow), not an item. Continuation text of a long
# description is the thing this must not swallow, and that runs longer and
# tends to end mid-sentence.
MAX_HEADING_LENGTH = 45

# What a real line-item table looks like, used to reject a table that merely
# shares a column count. Both real sheets clear these comfortably.
MIN_PLAUSIBLE_LINES = 5
MIN_NUMERIC_QUANTITY_SHARE = 0.6

# Some layouts rule every row, which settles where each item begins and ends
# even when its description wraps onto a second line. Below this many rules
# the sheet is treated as unruled and rows are recovered from text spacing.
MIN_ROW_RULES = 4
MIN_ROW_HEIGHT_PX = 25

# A run of the column rule shorter than this is a line of text crossing the
# rule's x, not a table: the address block above the table, on a slightly
# askew scan. Body text is ~40px tall at RENDER_DPI; a table is at least a
# heading row and one item.
MIN_TABLE_HEIGHT_PX = 60

# A multi-building design sheet (EP-30208: nine buildings over four pages)
# is a run of separate ruled boxes, one per building, each under a banner
# naming it and each closed by a totals row. Read as one table per page, the
# longest box alone came back and 47 of the 97 rows were lost. The banner
# text between boxes is kept as the section every row under it belongs to.
_SECTION_NOISE_RE = re.compile(
    r"total\s+price|grand\s+total|sub\s*total|in\s+aed\b|^page\s+\d+", re.IGNORECASE
)
# The address block above the first table is "Label : value" lines; a
# section banner never carries a colon.
_SECTION_LABEL_RE = re.compile(r":")
# The totals row's border reads as a stray short lower-case word ahead of
# the banner ("ee DHAID - B4 BUILDING"), and the banner's own rule as
# garbage after it ("DHAID - B1 BUILDING = =<...>"): the one is dropped,
# the line is cut at the other.
_SECTION_LEADING_NOISE_RE = re.compile(r"^(?:[a-z]{1,2}\s+)+(?=\S)")
_SECTION_TAIL_RE = re.compile(r"[^A-Za-z0-9 &/()'.,+-].*$")
MAX_SECTION_LENGTH = 45
# How far above a table its banner can sit: the totals row of the table
# before, then the banner. Further up is the address block, whose last line
# is not a section however it reads.
SECTION_BAND_PX = 150


def _is_banner(heading: str) -> bool:
    """A heading written in capitals ("DHAID - B1 BUILDING") is the sheet's
    banner for a section, where its sub-headings ("a. System Managers") are
    not. On page 1 the first building's banner sits inside the ruled box,
    so it cannot be read off the band above the table like the others."""
    letters = [ch for ch in heading if ch.isalpha()]
    return len(letters) >= 3 and sum(ch.isupper() for ch in letters) >= 0.8 * len(letters)
ROW_EDGE_INSET_PX = 6  # keeps a row's own bounding rules out of its cells
# A table ending this close to the foot of the page runs on to the next.
PAGE_EDGE_MARGIN_PX = 240

# The only non-numeric quantities these sheets use. Anything else alphabetic
# in the quantity column is a misread -- "7" comes back as "ae" on one scan --
# and is dropped rather than passed on, so the engineer sees an empty cell to
# fill instead of a plausible-looking wrong number.
WORD_QUANTITIES = values.WORD_QUANTITIES


@dataclass
class ExtractedBoqLine:
    """One row as read. `raw_quantity`, `y_px` and `quantity_span` locate
    the quantity cell on the rendered page (at RENDER_DPI) so a row whose
    quantity could not be parsed can be shown -- and, if AI assistance is
    on, read again -- as that cell's image. `quantity_parse` is what the
    typed parser made of the raw text and by which rule."""

    catalog_no: str | None
    description: str
    quantity: str | None
    group_heading: str | None
    confidence: float  # mean OCR word confidence across the row, 0-100
    page: int
    raw_quantity: str | None = None
    y_px: float | None = None
    quantity_span: tuple[int, int] | None = None
    quantity_parse: dict | None = None
    row_bounds: tuple[int, int] | None = None   # (top, bottom) of the row at RENDER_DPI, when ruled
    table_span: tuple[int, int] | None = None   # (first rule, last rule) of the table the row is in
    # The banner (building) and sub-heading the row sits under, apart: the
    # group heading joins them for display, identity keeps them separate.
    section: str | None = None
    heading: str | None = None
    building: dict | None = None
    # The catalog cell as OCR read it, before edge junk was removed.
    catalog_raw: str | None = None
    # What independent OCR passes read off the quantity cell when the column
    # read could not settle it.
    alternates: list[dict] | None = None
    # Tesseract's confidence in the quantity strip's own reading, 0-100.
    quantity_confidence: float | None = None
    # Set when a neighbouring row's quantity turned out to be merged into
    # this one's strip reading: its value is checked even if confident.
    verify_quantity: bool = False
    # {"source", "cleaned", "canonical", "reason"} (app.extraction.identity)
    catalog_match: dict | None = None

    def region(self) -> tuple[int, int, int, int] | None:
        """The row's box on the rendered page: from the first column rule to
        the last, top to bottom of the row -- what provenance records and
        what a reviewer is shown."""
        span = self.table_span or self.quantity_span
        if span is None or self.y_px is None:
            return None
        top, bottom = self.row_bounds or (int(self.y_px - _CELL_HALF_HEIGHT_PX), int(self.y_px + _CELL_HALF_HEIGHT_PX))
        return (int(span[0]), int(top), int(span[1]), int(bottom))


class DesignSheetExtractionError(RuntimeError):
    pass


def _detect_line_positions(dark_fraction: np.ndarray, min_frac: float) -> list[int]:
    positions = [i for i, frac in enumerate(dark_fraction) if frac > min_frac]
    lines: list[int] = []
    prev = -10
    for pos in positions:
        if pos - prev > 3:
            lines.append(pos)
        prev = pos
    return lines


def _render_page(page: pymupdf.Page, dpi: int = RENDER_DPI) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi)
    mode = "RGB" if pix.n >= 3 else "L"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("L")


# Where on the page to look for the rules fencing the item table. A rule only
# registers if it runs most of the probed band, so the band has to sit inside
# the table: the quotation's table fills the page, while the spreadsheet
# export's occupies only the top third and is missed by a mid-page probe.
# Tried in order; the first band yielding a layout in LAYOUTS wins.
PROBE_BANDS = ((0.25, 0.90), (0.10, 0.35), (0.05, 0.95))


def _find_layout(dark: np.ndarray) -> tuple[list[int], _Layout] | None:
    """Vertical rules fencing the item table, plus the layout their count
    identifies. Returns None when no probe band yields a known layout."""
    height = dark.shape[0]
    for top_fraction, bottom_fraction in PROBE_BANDS:
        band = dark[int(height * top_fraction) : int(height * bottom_fraction), :]
        rules = _detect_line_positions(band.mean(axis=0), COLUMN_LINE_DARK_FRACTION)
        layout = LAYOUTS.get(len(rules))
        if layout is not None:
            return rules, layout
    return None


def _rule_runs(dark: np.ndarray, x: int) -> list[tuple[int, int]]:
    """Every continuous run of dark pixels down the column at x, in page
    order. The rule is what runs unbroken; text crossing the column leaves
    short runs, which the caller filters by height."""
    column = dark[:, max(0, x - RULE_DRIFT_PX) : x + RULE_DRIFT_PX + 1].any(axis=1)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for y, is_dark in enumerate(column):
        if is_dark:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap > MAX_RULE_GAP_PX:
                runs.append((start, y - gap))
                start = None
                gap = 0
    if start is not None:
        runs.append((start, len(column)))
    return runs


def _table_extents(dark: np.ndarray, x: int) -> list[tuple[int, int]]:
    """Vertical spans of every item table on the page, top to bottom.

    A sheet with one table gives one span. A multi-building sheet gives one
    per building: its boxes are closed by a full-width totals row and the
    next building's banner, across which the column rules do not run.
    """
    spans = [(top, bottom) for top, bottom in _rule_runs(dark, x) if bottom - top >= MIN_TABLE_HEIGHT_PX]
    return spans or [(0, dark.shape[0])]


def _table_extent(dark: np.ndarray, x: int) -> tuple[int, int]:
    """The longest table on the page -- kept for callers that want one."""
    return max(_table_extents(dark, x), key=lambda span: span[1] - span[0])


def _section_title(image: Image.Image, left: int, right: int, top: int, bottom: int) -> str | None:
    """What the band between two tables names: the building the next table
    is for ("DHAID - B2 BUILDING"), or nothing.

    The band also carries the totals row that closes the table above and,
    at the top of a page, the column headings; those are not sections.
    """
    if bottom - top < MIN_ROW_HEIGHT_PX or right - left <= 2 * CELL_INSET_PX:
        return None
    text = pytesseract.image_to_string(image.crop((left, top, right, bottom)), config="--psm 6")
    candidates = []
    for raw in text.splitlines():
        # Border garbage after the banner can carry any character, a colon
        # included, so it is cut off before the line is judged.
        line = _SECTION_TAIL_RE.sub("", _SECTION_LEADING_NOISE_RE.sub("", raw.strip(" |_-—;."))).strip(" -")
        if not line or _SECTION_NOISE_RE.search(line) or _SECTION_LABEL_RE.search(line):
            continue
        if _is_column_heading(line):
            continue
        if len(line) > MAX_SECTION_LENGTH or sum(ch.isalpha() for ch in line) < 3:
            continue
        # The address block reads in mixed case; a banner is set in capitals.
        if not _is_banner(line):
            continue
        candidates.append(line)
    return candidates[-1] if candidates else None


def _erase_horizontal_rules(crop: Image.Image) -> Image.Image:
    """Whiten the cell borders running across a column strip.

    On the row-ruled layouts a cell's own borders sit inside the strip, and
    Tesseract reads them as part of the glyph -- a lone, perfectly legible "1"
    between two rules comes back as "ee". Removing them first is what makes
    single-digit quantities readable.
    """
    pixels = np.array(crop)
    if pixels.size == 0:
        return crop

    dark_fraction = (pixels < 128).mean(axis=1)
    is_rule = dark_fraction > RULE_ROW_DARK_FRACTION
    if not is_rule.any():
        return crop

    # Grow the erasure into the rule's anti-aliased edges. On a scan the page
    # sits slightly askew, so a rule fades over a few rows either side of its
    # solid core; whitening only the core leaves those fringes behind, and
    # Tesseract reads the leftover fragments as glyph parts -- a clean "759"
    # came back as "=". Removing the whole rule is better than removing most
    # of it: with the rule fully intact Tesseract handles it as a border, and
    # with it fully gone there is nothing to misread. Half-erased is the one
    # state that breaks.
    erase = is_rule.copy()
    for _ in range(RULE_EDGE_GROWTH_PX):
        neighbours = np.zeros_like(erase)
        neighbours[1:] |= erase[:-1]
        neighbours[:-1] |= erase[1:]
        erase |= neighbours & (dark_fraction > RULE_EDGE_DARK_FRACTION)

    pixels[erase] = 255
    return Image.fromarray(pixels)


def _read_column(image: Image.Image, x0: int, x1: int, y0: int, y1: int) -> list[tuple[float, str, float]]:
    """OCR one column strip into (y_centre, text, confidence) per line."""
    if x1 - x0 <= 2 * CELL_INSET_PX or y1 <= y0:
        return []
    crop = _erase_horizontal_rules(image.crop((x0 + CELL_INSET_PX, y0, x1 - CELL_INSET_PX, y1)))
    data = pytesseract.image_to_data(
        crop, config="--psm 6", output_type=pytesseract.Output.DICT
    )

    words: list[tuple[float, int, str, float]] = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        centre = y0 + data["top"][i] + data["height"][i] / 2
        words.append((centre, data["left"][i], text, conf))

    grouped: list[list] = []
    for centre, left, text, conf in sorted(words, key=lambda w: w[0]):
        if grouped and abs(centre - grouped[-1][0]) <= LINE_GROUP_TOLERANCE_PX:
            grouped[-1][1].append((left, text))
            grouped[-1][2].append(conf)
        else:
            grouped.append([centre, [(left, text)], [conf]])

    return [
        (
            centre,
            " ".join(text for _, text in sorted(parts, key=lambda p: p[0])),
            sum(confs) / len(confs),
        )
        for centre, parts, confs in grouped
    ]


def _row_rules(dark: np.ndarray, top: int, bottom: int, left: int, right: int) -> list[int]:
    """Horizontal rules separating item rows, where the layout has them."""
    band = dark[top:bottom, left:right]
    if band.size == 0:
        return []
    found = _detect_line_positions(band.mean(axis=1), RULE_ROW_DARK_FRACTION)
    # A doubled rule under the column headings reads as two lines a few pixels
    # apart; keep the first of each such pair.
    merged: list[int] = []
    for position in found:
        absolute = top + position
        if not merged or absolute - merged[-1] > MIN_ROW_HEIGHT_PX:
            merged.append(absolute)
    return merged


def _read_cell(image: Image.Image, x0: int, x1: int, y0: int, y1: int) -> tuple[str, float]:
    """One cell's whole contents as a single string, wrapped lines joined."""
    parts = _read_column(image, x0, x1, y0, y1)
    if not parts:
        return "", 0.0
    text = " ".join(part for _, part, _ in parts).strip()
    confidence = sum(conf for _, _, conf in parts) / len(parts)
    return text, confidence


def _nearest(lines: list[tuple[float, str, float]], centre: float) -> tuple[str | None, float]:
    best: tuple[float, str, float] | None = None
    for line_centre, text, conf in lines:
        distance = abs(line_centre - centre)
        if distance <= COLUMN_PAIRING_TOLERANCE_PX and (best is None or distance < best[0]):
            best = (distance, text, conf)
    return (best[1], best[2]) if best else (None, 0.0)


def _parse_quantity(text: str | None) -> values.ParsedValue:
    return values.parse_quantity(text)


def _clean_quantity(text: str | None) -> str | None:
    """The quantity a cell's text means, or None when it means none for
    certain. Quantities read back with the column rule attached ("| 836")
    and "Lot" is a quantity these sheets really use; "1,250" is 1250, while
    "12.5", "-3" and "2 x 10" are no count at all (app.extraction.values)."""
    return _parse_quantity(text).text()


def _is_column_heading(text: str) -> bool:
    """Whether a cell holds a column label rather than content. OCR decorates
    these with the neighbouring rules ("| Description"), so the comparison
    strips punctuation before matching."""
    return text.strip(" |_-—:;.").lower() in COLUMN_HEADINGS


def _is_heading(description: str, quantity: str | None, catalog: str | None) -> bool:
    if quantity or catalog:
        return False
    if len(description) > MAX_HEADING_LENGTH:
        return False
    # A heading names something. A cell border the OCR turned into "|" or
    # "~" is not one, and taking it as one filed the next items under it.
    if sum(ch.isalpha() for ch in description) < 2:
        return False
    return not description.endswith((",", ":", ";", "-"))


def _group(section: str | None, heading: str | None) -> str | None:
    """The group a line belongs to: its heading on the sheet, under the
    section (building) the sheet placed it in. Two buildings quoting the same
    part under the same heading are two lines, not a duplicate -- which is
    what the section prefix tells the BOQ's duplicate check."""
    if section and heading:
        return f"{section} / {heading}"
    return section or heading


@dataclass
class DesignSheetExtraction:
    """A sheet as read: the lines, what the read covered, what it could not
    settle, and -- when it could not read the sheet at all -- why."""

    lines: list[ExtractedBoqLine] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    failure: str | None = None
    # One entry per building the sheet names, with every spelling OCR gave it.
    buildings: list[dict] = field(default_factory=list)

    @property
    def outcome(self) -> Outcome:
        return outcome_for(self.coverage, self.issues, lines=len(self.lines), failure=self.failure)


# Half the height of a quantity cell's crop, either side of the row centre.
_CELL_HALF_HEIGHT_PX = 22


def _dropped_row_issue(line: ExtractedBoqLine, ordinal: int) -> Issue | None:
    """A row the read kept everything of except its quantity. Page furniture
    -- totals rows, footers -- has no catalog number and no item-like
    description and is left out; an item is offered for review with the
    cell it came from."""
    description = (line.description or "").strip()
    if not line.catalog_no and (len(description) < 6 or _SECTION_NOISE_RE.search(description)):
        return None
    region = None
    if line.quantity_span is not None and line.y_px is not None:
        x0, x1 = line.quantity_span
        region = (int(x0), int(line.y_px - _CELL_HALF_HEIGHT_PX), int(x1), int(line.y_px + _CELL_HALF_HEIGHT_PX))
    return Issue(
        code=IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE,
        page=line.page,
        region=region,
        target=f"boq_line:{line.page}:{ordinal}",
        detail={
            "description": description,
            "catalog_no": line.catalog_no,
            "group_heading": line.group_heading,
            "raw_quantity": line.raw_quantity,
            # Why the quantity was refused: "decimal", "negative", "multiplier"...
            "quantity_parse": line.quantity_parse,
            # What the independent passes read, for the reviewer to weigh.
            "alternates": line.alternates,
            "building": line.building,
        },
    )


# --- a second look at what the column read could not settle -----------------------------

# Each pass reads the cell alone, preprocessed differently, so agreement
# between them is independent evidence and not the same misread twice. Tried
# on EP-30208's unsettled cells: the column strip read nothing, a plain
# upscale read "Z", and the padded, binarised crop read the printed "2" in
# every mode.
_SECOND_PASSES = (
    ("binarised, single line, digits", "binarised", "--psm 7 -c tessedit_char_whitelist=0123456789"),
    ("binarised, single character", "binarised", "--psm 10"),
    ("greyscale, single line, digits or words", "greyscale", "--psm 7 -c tessedit_char_whitelist=0123456789LotSetNos"),
)
_PASS_PADDING_PX = 30
_PASS_SCALE = 3
_UNRULED_CELL_HALF_HEIGHT_PX = 24
# A pass's reading counts only if it is plainly a quantity: digits, grouped
# digits or a quantity word -- not a letter taken for a digit.
_PASS_RULES = {"digits", "comma thousands grouping", "quantity word"}


def _otsu_threshold(pixels: np.ndarray) -> int:
    histogram = np.bincount(pixels.ravel(), minlength=256).astype(float)
    total = pixels.size
    weighted_total = float(np.dot(np.arange(256), histogram))
    background_weight = background_sum = 0.0
    best, threshold = -1.0, 128
    for level in range(256):
        background_weight += histogram[level]
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += level * histogram[level]
        mean_background = background_sum / background_weight
        mean_foreground = (weighted_total - background_sum) / foreground_weight
        between = background_weight * foreground_weight * (mean_background - mean_foreground) ** 2
        if between > best:
            best, threshold = between, level
    return threshold


def _cell_image(image: Image.Image, line: ExtractedBoqLine) -> Image.Image | None:
    if line.quantity_span is None or line.y_px is None:
        return None
    x0, x1 = line.quantity_span
    if line.row_bounds:
        top, bottom = line.row_bounds[0] + ROW_EDGE_INSET_PX, line.row_bounds[1] - ROW_EDGE_INSET_PX
    else:
        # No rules to bound the row: tall enough for a digit (~35px), short of
        # the next row's digit (~45px away).
        top, bottom = int(line.y_px - _UNRULED_CELL_HALF_HEIGHT_PX), int(line.y_px + _UNRULED_CELL_HALF_HEIGHT_PX)
    box = (x0 + CELL_INSET_PX + 2, top, x1 - CELL_INSET_PX - 2, bottom)
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return None
    crop = _erase_horizontal_rules(image.crop(box))
    pixels = np.array(crop)
    # Vertical rule fragments at the cell's edges, as the horizontal ones.
    columns = (pixels < 128).mean(axis=0) > RULE_ROW_DARK_FRACTION
    pixels[:, columns] = 255
    return Image.fromarray(pixels)


# A quantity strip read below this confidence is not taken on its own word.
LOW_QUANTITY_CONFIDENCE = 60
# A strip read this confident, backed by any pass, stands against a pass that
# dropped or added a digit ("4" beside a pass reading "40").
CONFIDENT_QUANTITY = 85
# Below this, a strip read no pass can confirm goes to review rather than
# into the BOQ; between this and LOW_QUANTITY_CONFIDENCE it is kept, noted.
UNCONFIRMED_REVIEW_BELOW = 50


def _confirm_quantity(image: Image.Image, line: ExtractedBoqLine) -> None:
    """Check a quantity the column strip read against independent passes on
    its own cell. Passes that read nothing change nothing (a lone "1" is the
    hardest glyph to read in isolation). Passes that agree on a different
    value -- at least two, none backing the strip -- replace it, with both
    recorded: a strip read of a short column merges stacked digits ("2" over
    "1" read as "3"). A split verdict sends the row to review."""
    strip_value, strip_raw = line.quantity, line.raw_quantity
    readings = _independent_readings(image, line)
    if readings is None:
        return
    valid = [r["value"] for r in readings if r["value"] is not None]
    if not valid:
        if (line.quantity_confidence or 100) < UNCONFIRMED_REVIEW_BELOW and not line.verify_quantity:
            # Too uncertain to keep unconfirmed: EP-30208's "1" read "4" at 44%.
            line.alternates = readings
            line.quantity = None
            line.quantity_parse = {**(line.quantity_parse or {}), "status": values.AMBIGUOUS,
                                   "rule": f"the column read {strip_value!r} at {line.quantity_confidence:.0f}% confidence "
                                           "and no independent pass could read the cell"}
            return
        if line.verify_quantity:
            # Suspected of holding a neighbour's digit, and nothing independent
            # says otherwise: not a value to keep on the strip's word.
            line.alternates = readings
            line.quantity = None
            line.quantity_parse = {**(line.quantity_parse or {}), "status": values.AMBIGUOUS,
                                   "rule": f"the column read {strip_value!r} next to a row whose digit it may have "
                                           "absorbed, and no independent pass could read the cell"}
        return
    line.alternates = readings
    agreeing = valid.count(strip_value)
    others = {v: valid.count(v) for v in valid if v != strip_value}
    confidence = f" ({line.quantity_confidence:.0f}%)" if line.quantity_confidence is not None else ""
    if not others or (agreeing >= 1 and (line.quantity_confidence or 0) >= CONFIDENT_QUANTITY):
        line.quantity_parse = {**(line.quantity_parse or {}),
                               "rule": f"column read {strip_value!r}{confidence} confirmed by {agreeing} independent "
                                       f"pass{'es' if agreeing != 1 else ''}"}
        return
    best = max(others, key=others.get)
    if agreeing == 0 and len(others) == 1 and others[best] >= 2:
        line.quantity = best
        line.quantity_parse = {**values.parse_quantity(best).to_dict(), "raw": strip_raw,
                               "rule": f"the column read {strip_value!r}{confidence}; {others[best]} independent passes on "
                                       f"the cell read {best}",
                               "independent_agreement": others[best], "replaced": strip_value}
        return
    line.quantity = None
    line.quantity_parse = {**(line.quantity_parse or {}), "status": values.AMBIGUOUS,
                           "rule": f"the column read {strip_value!r}{confidence} but independent passes read "
                                   + ", ".join(sorted(set(valid)))}


def _independent_readings(image: Image.Image, line: ExtractedBoqLine) -> list[dict] | None:
    from PIL import ImageOps

    cell = _cell_image(image, line)
    if cell is None:
        return None
    padded = ImageOps.expand(cell, border=_PASS_PADDING_PX, fill=255)
    greyscale = padded.resize((padded.width * _PASS_SCALE, padded.height * _PASS_SCALE), Image.LANCZOS)
    array = np.array(greyscale)
    binarised = Image.fromarray(np.where(array < _otsu_threshold(array), 0, 255).astype(np.uint8))
    sources = {"greyscale": greyscale, "binarised": binarised}
    readings: list[dict] = []
    for name, source, config in _SECOND_PASSES:
        try:
            text = pytesseract.image_to_string(sources[source], config=config).strip()
        except Exception:  # noqa: BLE001 -- a pass that cannot run is no evidence either way
            continue
        parsed = values.parse_quantity(text)
        plain = parsed.ok and parsed.rule in _PASS_RULES
        readings.append({"pass": name, "text": text, "value": parsed.text() if plain else None,
                         "status": parsed.status if plain or not parsed.ok else values.AMBIGUOUS})
    return readings


def _read_quantity_again(image: Image.Image, line: ExtractedBoqLine) -> None:
    """Read an unsettled quantity cell again, three independent ways. When
    at least two passes read the same valid quantity and none reads a
    different one, that is the quantity -- with the rule saying so. Anything
    else leaves the row for review, with what each pass read."""
    readings = _independent_readings(image, line)
    if readings is None:
        return
    valid = [r["value"] for r in readings if r["value"] is not None]
    line.alternates = readings
    if len(valid) >= 2 and len(set(valid)) == 1:
        agreed = valid[0]
        line.quantity = agreed
        line.quantity_parse = {
            **values.parse_quantity(agreed).to_dict(),
            "raw": line.raw_quantity,
            "rule": f"the column read gave {line.raw_quantity!r}; {len(valid)} independent passes on the cell read {agreed}",
            "independent_agreement": len(valid),
        }


def _straighten(image: Image.Image) -> tuple[Image.Image, str] | None:
    """The page turned upright and deskewed, with what was done -- or None
    when it already is, or cannot be told."""
    turned, notes = image, []
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0")
        rotate = int(re.search(r"Rotate:\s*(\d+)", osd).group(1))
    except Exception:  # noqa: BLE001 -- too little text to tell the orientation
        rotate = 0
    if rotate:
        turned = image.rotate(-rotate, expand=True, fillcolor=255)
        notes.append(f"turned {rotate} degrees")
    small = turned.resize((max(1, turned.width // 4), max(1, turned.height // 4)))
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-3.0, 3.01, 0.25):
        dark = np.array(small.rotate(float(angle), fillcolor=255)) < 128
        profile = dark.mean(axis=0)
        score = float((profile ** 2).sum())
        if score > best_score:
            best_angle, best_score = float(angle), score
    if abs(best_angle) >= 0.25:
        turned = turned.rotate(best_angle, expand=False, fillcolor=255)
        notes.append(f"deskewed {best_angle:+.2f} degrees")
    if not notes:
        return None
    return turned, "read after the page was " + " and ".join(notes)


def extract_design_sheet(pdf_path: Path, on_page=None) -> DesignSheetExtraction:
    """Read a sheet and say what was and was not read.

    Never raises for what is in the document: an unreadable sheet comes
    back with `failure` set and an UNRECOGNIZED_TABLE_LAYOUT issue, a page
    with no recognisable table with an UNPROCESSED_PAGE_OR_REGION issue,
    and a row whose quantity could not be parsed as a
    QUANTITY_OR_UNIT_PARSE_FAILURE issue locating the cell -- rather than
    the row disappearing and the count being quietly short.
    """
    result = DesignSheetExtraction()
    if not pdf_path.exists():
        result.failure = f"Design Sheet not found: {pdf_path}"
        result.issues.append(Issue(IssueCode.UNSUPPORTED_DOCUMENT, detail={"reason": "missing"}))
        return result
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - pymupdf raises bare exceptions
        result.failure = f"Could not open the Design Sheet: {exc}"
        result.issues.append(Issue(IssueCode.UNSUPPORTED_DOCUMENT, detail={"reason": str(exc)}))
        return result

    read: list[ExtractedBoqLine] = []
    readable_pages = 0
    # The building a page's first table continues from the page before: a
    # box that runs over a page break has its banner on the earlier page.
    section: str | None = None

    for page_index in range(document.page_count):
        page_number = page_index + 1
        if on_page is not None:
            # Progress, and the point at which a job asked to stop does.
            on_page(page_number, document.page_count)
        image = _render_page(document[page_index])
        dark = np.array(image) < 128
        coverage = PageCoverage(page=page_number)
        result.coverage.pages.append(coverage)

        found = _find_layout(dark)
        if found is None:
            # A page scanned sideways or askew has no straight rules to find.
            # Straightened, it is tried once more before being reported.
            straightened = _straighten(image)
            if straightened is not None:
                image, how = straightened
                dark = np.array(image) < 128
                found = _find_layout(dark)
                if found is not None:
                    coverage.reason = how
        if found is None:
            coverage.reason = "no recognised column layout"
            result.issues.append(Issue(IssueCode.UNPROCESSED_PAGE_OR_REGION, page=page_number,
                                       target=f"page:{page_number}", detail={"reason": coverage.reason}))
            continue
        rules, layout = found
        readable_pages += 1
        page_lines, section, regions = _read_page(image, dark, rules, layout, page_number, section)
        for line in page_lines:
            if not line.quantity:
                _read_quantity_again(image, line)
            elif line.verify_quantity or (line.quantity_confidence is not None
                                          and line.quantity_confidence < LOW_QUANTITY_CONFIDENCE):
                # Confident strip reads are not second-guessed: a single-cell
                # pass drops or adds digits on multi-digit counts more often
                # than the strip misreads them (EP-30784 FAS: "121" read "12").
                _confirm_quantity(image, line)
        coverage.processed = True
        coverage.regions = regions
        read.extend(page_lines)

    # One building, one name: the banners' OCR variants across the pages
    # are settled together (app.extraction.identity).
    buildings = identity.canonical_buildings([line.section for line in read if line.section])
    seen: dict[str, dict] = {}
    for line in read:
        building = buildings.get(line.section) if line.section else None
        if building is None:
            continue
        line.building = building.as_dict()
        line.section = building.display
        line.group_heading = _group(building.display, line.heading)
        seen.setdefault(building.key, building.as_dict())
    result.buildings = list(seen.values())

    # One part, one spelling: codes on the sheet sharing their letters and
    # digits ("E232 301H" beside six "E-232 301H") are matched to the
    # spelling the sheet uses most. The cleaned code stays the line's value;
    # the canonical one is recorded beside it with the reason.
    spellings: dict[str, Counter] = {}
    for line in read:
        if line.catalog_no:
            spellings.setdefault(identity.part_key(line.catalog_no), Counter())[line.catalog_no] += 1
    for line in read:
        if not line.catalog_no and not line.catalog_raw:
            continue
        cleaned, removed = identity.clean_catalog(line.catalog_raw) if line.catalog_raw else (line.catalog_no, None)
        canonical, reason = None, removed or "as read"
        if line.catalog_no:
            votes = spellings.get(identity.part_key(line.catalog_no))
            if votes:
                best, count = votes.most_common(1)[0]
                canonical = best
                if best != line.catalog_no:
                    reason = f"{reason}; the sheet spells this part {best!r} on {count} other row{'s' if count != 1 else ''}"
        line.catalog_match = {"source": line.catalog_raw, "cleaned": line.catalog_no, "canonical": canonical, "reason": reason}

    # A BOQ line is something being quoted in some amount, so a row whose
    # quantity could not be read is not one and is not stored as one. It is
    # not lost either: it becomes an issue that locates its quantity cell.
    ordinal = 0
    for line in read:
        if line.quantity:
            result.lines.append(line)
            continue
        ordinal += 1
        issue = _dropped_row_issue(line, ordinal)
        if issue is not None:
            result.issues.append(issue)

    if readable_pages == 0 or not _looks_like_line_items(result.lines):
        result.failure = (
            "Could not find a line-item table in this Design Sheet -- its layout is not one "
            "this extractor recognises"
        )
        result.issues.append(Issue(IssueCode.UNRECOGNIZED_TABLE_LAYOUT, detail={"pages": document.page_count}))
        result.lines = []
    return result


def extract_boq_lines(pdf_path: Path) -> list[ExtractedBoqLine]:
    """The lines alone, raising where the sheet cannot be read -- the
    original contract, kept for callers and tests."""
    result = extract_design_sheet(pdf_path)
    if result.failure:
        raise DesignSheetExtractionError(result.failure)
    return result.lines


def _looks_like_line_items(lines: list[ExtractedBoqLine]) -> bool:
    """Guard against reading a table that merely has the right column count.

    A layout is picked by how many vertical rules fence it, which some other
    table on some other document will eventually match -- a DRF's Systems
    block does. What that block does not have is a column of quantities, so
    that is the thing worth checking before returning its contents as a BOQ.
    """
    if len(lines) < MIN_PLAUSIBLE_LINES:
        return False
    numeric = sum(1 for line in lines if line.quantity and line.quantity.isdigit())
    return numeric >= len(lines) * MIN_NUMERIC_QUANTITY_SHARE


def _read_ruled_rows(
    descriptions: list[tuple[float, str, float]],
    quantities: list[tuple[float, str, float]],
    catalogs: list[tuple[float, str, float]],
    row_rules: list[int],
    page_number: int,
    section: str | None = None,
) -> list[ExtractedBoqLine]:
    """Assemble rows from a table whose rows are ruled.

    The rules settle where an item starts and stops, which pairing text by
    vertical position cannot: a description wrapping onto a second line puts
    its quantity level with neither line, so proximity pairing drops the
    quantity and emits the wrapped remainder as an item of its own.

    Each column is still read as one strip rather than cell by cell. Tesseract
    reads a whole column far better than it reads a single cell handed to it
    in isolation -- on a scanned sheet, per-cell crops turned "327" into "37"
    and "17" into "47" that the strip read got right.
    """
    lines: list[ExtractedBoqLine] = []
    heading: str | None = None

    def take_heading(text: str) -> None:
        nonlocal heading, section
        if heading is not None and not lines and _is_banner(heading):
            section = heading
        heading = text

    for row_top, row_bottom in zip(row_rules, row_rules[1:]):
        if row_bottom - row_top < MIN_ROW_HEIGHT_PX:
            continue

        def cell(strip: list[tuple[float, str, float]]) -> tuple[str, float]:
            parts = [
                (text, conf) for centre, text, conf in strip if row_top < centre < row_bottom
            ]
            if not parts:
                return "", 0.0
            return (
                " ".join(text for text, _ in parts).strip(),
                sum(conf for _, conf in parts) / len(parts),
            )

        description, description_conf = cell(descriptions)
        if not description or PAGE_FOOTER_RE.match(description):
            continue
        if _is_column_heading(description):
            continue

        quantity_text, quantity_conf = cell(quantities)
        catalog_text, catalog_conf = cell(catalogs)
        parsed = _parse_quantity(quantity_text)
        quantity = parsed.text()
        catalog = identity.clean_catalog(catalog_text)[0]
        if catalog and _is_column_heading(catalog):
            catalog = None

        inline = INLINE_QUANTITY_RE.match(description)
        if inline:
            parsed = _parse_quantity(inline.group(1))
            quantity = parsed.text()
            description = description[inline.end() :].strip()
        elif _is_heading(description, quantity, catalog) and parsed.status == values.EMPTY:
            # A row whose quantity cell holds something the parser refused
            # ("12.5", "ae") is an item with a bad quantity, not a heading.
            take_heading(description)
            continue

        confs = [c for c in (description_conf, quantity_conf, catalog_conf) if c > 0]
        lines.append(
            ExtractedBoqLine(
                catalog_no=catalog,
                description=description,
                quantity=quantity,
                group_heading=_group(section, heading),
                confidence=sum(confs) / len(confs) if confs else 0.0,
                page=page_number,
                raw_quantity=quantity_text or None,
                y_px=(row_top + row_bottom) / 2,
                quantity_parse=parsed.to_dict(),
                row_bounds=(row_top, row_bottom),
                section=section,
                heading=heading,
                catalog_raw=catalog_text or None,
                quantity_confidence=quantity_conf if quantity_text else None,
            )
        )

    return lines


def _read_page(
    image: Image.Image,
    dark: np.ndarray,
    rules: list[int],
    layout: _Layout,
    page_number: int,
    section: str | None = None,
) -> tuple[list[ExtractedBoqLine], str | None]:
    """Every table on the page, in order, each under the section named
    above it. Returns the lines and the section in force at the foot of the
    page, which the next page's first table continues."""
    lines: list[ExtractedBoqLine] = []
    regions: list[RegionCoverage] = []
    previous_bottom = 0
    span = (rules[layout.quantity[0]], rules[layout.quantity[1]])
    for top, bottom in _table_extents(dark, rules[layout.description[0]]):
        named = _section_title(image, rules[0], rules[-1], max(previous_bottom, top - SECTION_BAND_PX), top)
        if named:
            section = named
        table_lines = _read_table(image, dark, rules, layout, page_number, top, bottom, section)
        for line in table_lines:
            line.quantity_span = span
            line.table_span = (rules[0], rules[-1])
        lines.extend(table_lines)
        accepted = sum(1 for line in table_lines if line.quantity)
        regions.append(RegionCoverage("table", top, bottom, "processed", rows_accepted=accepted,
                                      rows_dropped=len(table_lines) - accepted))
        previous_bottom = bottom
    return lines, section, regions


def _read_table(
    image: Image.Image,
    dark: np.ndarray,
    rules: list[int],
    layout: _Layout,
    page_number: int,
    top: int,
    bottom: int,
    section: str | None,
) -> list[ExtractedBoqLine]:
    def strip(bounds: tuple[int, int]) -> list[tuple[float, str, float]]:
        return _read_column(image, rules[bounds[0]], rules[bounds[1]], top, bottom)

    descriptions = strip(layout.description)
    quantities = strip(layout.quantity)
    catalogs = strip(layout.catalog)

    # Probed down a single column, not the full table width: a row rule
    # crosses every column, and over the whole width a slightly skewed scan
    # never darkens one pixel row enough to register.
    row_rules = _row_rules(
        dark, top, bottom, rules[layout.quantity[0]], rules[layout.quantity[1]]
    )
    if len(row_rules) >= MIN_ROW_RULES:
        return _read_ruled_rows(descriptions, quantities, catalogs, row_rules, page_number, section)

    # The rule bounding the table starts level with its column-header row, so
    # the last line of the address block above can bleed in. When the sheet
    # labels its columns, start below that label; layouts that don't label
    # them (the spreadsheet export) start at the top.
    start = 0
    for index, (_, text, _) in enumerate(descriptions):
        if _is_column_heading(text):
            start = index + 1
            break

    lines: list[ExtractedBoqLine] = []
    heading: str | None = None
    verify_following = False
    trailing_heading: tuple[float, str] | None = None
    heading_before_trailing: str | None = None

    def take_heading(text: str) -> None:
        nonlocal heading, section
        if heading is not None and not lines and _is_banner(heading):
            section = heading
        heading = text

    for centre, text, conf in descriptions[start:]:
        if not text or PAGE_FOOTER_RE.match(text) or _is_column_heading(text):
            continue

        quantity_text, quantity_conf = _nearest(quantities, centre)
        catalog_text, catalog_conf = _nearest(catalogs, centre)
        parsed = _parse_quantity(quantity_text)
        quantity = parsed.text()
        catalog = identity.clean_catalog(catalog_text)[0] if catalog_text else None
        if catalog and _is_column_heading(catalog):
            catalog = None

        inline = INLINE_QUANTITY_RE.match(text)
        if inline:
            parsed = _parse_quantity(inline.group(1))
            quantity = parsed.text()
            text = text[inline.end() :].strip()

        probe_parse: dict | None = None
        if not inline and parsed.status == values.EMPTY and catalog is None:
            # A short line with nothing beside it reads as a heading, and a
            # merged strip read leaves exactly that ("PA Rack" under
            # "Batteries", their "2" and "1" read as one "3"). Its cell is read
            # on its own first when a quantity reading sits close by.
            nearby = [y for y, _t, _c in quantities if abs(y - centre) <= 3 * COLUMN_PAIRING_TOLERANCE_PX]
            if (not lines or nearby) and sum(ch.isalpha() for ch in text) >= 3:
                probe = ExtractedBoqLine(catalog_no=None, description=text, quantity=None, group_heading=None,
                                         confidence=0.0, page=page_number, y_px=centre,
                                         quantity_span=(rules[layout.quantity[0]], rules[layout.quantity[1]]))
                _read_quantity_again(image, probe)
                if probe.quantity:
                    quantity, parsed, probe_parse = probe.quantity, values.parse_quantity(probe.quantity), probe.quantity_parse
                    # The strip reading this row lacked went to a neighbour:
                    # that neighbour's value is suspect now, however confident.
                    if lines and abs((lines[-1].y_px or 0) - centre) <= 3 * COLUMN_PAIRING_TOLERANCE_PX:
                        lines[-1].verify_quantity = True
                    verify_following = True

        if not inline and parsed.status == values.EMPTY and _is_heading(text, quantity, catalog):
            heading_before_trailing = heading
            take_heading(text)
            trailing_heading = (centre, text) if lines else None
            continue

        # A line with neither its own quantity nor a catalog number is the
        # rest of the previous item's description, not a new item -- unless
        # its quantity cell held text the parser refused, which makes it an
        # item whose quantity needs a person.
        if not inline and parsed.status == values.EMPTY and catalog is None and not quantity:
            if lines:
                lines[-1].description = f"{lines[-1].description} {text}".strip()
            # Nothing yet to continue: this is text from above the table that
            # the bounding rule reached -- the tail of the address block, or
            # the commercial block some sheets carry. An item always has a
            # quantity or a part number, so it cannot be one.
            continue

        confs = [c for c in (conf, quantity_conf, catalog_conf) if c > 0]
        lines.append(
            ExtractedBoqLine(
                catalog_no=catalog or None,
                description=text,
                quantity=quantity,
                group_heading=_group(section, heading),
                confidence=sum(confs) / len(confs) if confs else 0.0,
                page=page_number,
                raw_quantity=quantity_text or None,
                y_px=centre,
                quantity_parse=probe_parse or parsed.to_dict(),
                verify_quantity=verify_following and probe_parse is None,
                section=section,
                heading=heading,
                catalog_raw=catalog_text or None,
                # A probed quantity was confirmed by independent passes already.
                quantity_confidence=quantity_conf if quantity_text and probe_parse is None else None,
            )
        )
        # Only the row straight after a recovered one is suspect.
        verify_following = probe_parse is not None
        trailing_heading = None

    # A "heading" with nothing under it before the table ends heads nothing:
    # it is a row whose quantity could not be read ("PA Rack", its "1" merged
    # into the "2" above). Kept as a row for review rather than lost, and the
    # row above it -- which may hold its digit -- is checked.
    # A table the page edge cut off continues overleaf, where its heading's
    # rows are ("Local Material" at the foot of a page).
    cut_by_page = bottom >= image.height - PAGE_EDGE_MARGIN_PX
    if trailing_heading and not cut_by_page and not _is_banner(trailing_heading[1]) and lines:
        centre, text = trailing_heading
        lines[-1].verify_quantity = True
        lines.append(ExtractedBoqLine(
            catalog_no=None, description=text, quantity=None, group_heading=_group(section, heading_before_trailing),
            confidence=0.0, page=page_number, raw_quantity=None, y_px=centre, section=section,
            heading=heading_before_trailing,
            quantity_parse={"kind": "equipment_count", "raw": None, "value": None, "status": values.EMPTY,
                            "rule": "read as a heading, but nothing follows it in the table: a row with no readable quantity"},
        ))

    return lines
