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
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
import pytesseract
from PIL import Image

from app.core.config import get_settings
from app.extraction import values
from app.extraction.issues import Coverage, Issue, IssueCode, Outcome, PageCoverage, RegionCoverage, outcome_for

settings = get_settings()

# Part of every cache key: a change to how a sheet is read is a change to
# what a cached result means.
PARSER_VERSION = "2026-09-15.1"
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

PAGE_FOOTER_RE = re.compile(r"^page\s+\d+\s+of\s+\d+$", re.IGNORECASE)

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

    def region(self) -> tuple[int, int, int, int] | None:
        """The row's box on the rendered page: from the first column rule to
        the last, top to bottom of the row -- what provenance records."""
        if self.quantity_span is None or self.y_px is None:
            return None
        top, bottom = self.row_bounds or (int(self.y_px - _CELL_HALF_HEIGHT_PX), int(self.y_px + _CELL_HALF_HEIGHT_PX))
        return (int(self.quantity_span[0]), int(top), int(self.quantity_span[1]), int(bottom))


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
        },
    )


def extract_design_sheet(pdf_path: Path) -> DesignSheetExtraction:
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
        image = _render_page(document[page_index])
        dark = np.array(image) < 128
        coverage = PageCoverage(page=page_number)
        result.coverage.pages.append(coverage)

        found = _find_layout(dark)
        if found is None:
            coverage.reason = "no recognised column layout"
            result.issues.append(Issue(IssueCode.UNPROCESSED_PAGE_OR_REGION, page=page_number,
                                       target=f"page:{page_number}", detail={"reason": coverage.reason}))
            continue
        rules, layout = found
        readable_pages += 1
        page_lines, section, regions = _read_page(image, dark, rules, layout, page_number, section)
        coverage.processed = True
        coverage.regions = regions
        read.extend(page_lines)

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
        catalog = catalog_text.strip(" |_-—~") or None
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
        catalog = catalog_text.strip(" |_-—~") if catalog_text else None
        if catalog and _is_column_heading(catalog):
            catalog = None

        inline = INLINE_QUANTITY_RE.match(text)
        if inline:
            parsed = _parse_quantity(inline.group(1))
            quantity = parsed.text()
            text = text[inline.end() :].strip()

        if not inline and parsed.status == values.EMPTY and _is_heading(text, quantity, catalog):
            take_heading(text)
            continue

        # A line with neither its own quantity nor a catalog number is the
        # rest of the previous item's description, not a new item -- unless
        # its quantity cell held text the parser refused, which makes it an
        # item whose quantity needs a person.
        if not inline and parsed.status == values.EMPTY and catalog is None:
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
                quantity_parse=parsed.to_dict(),
            )
        )

    return lines
