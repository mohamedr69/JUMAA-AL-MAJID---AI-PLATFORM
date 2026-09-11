"""Deterministic field extraction from a DRF (Design Request Form) PDF --
OCR + classic image-processing table detection, no AI model involved.

Why not just OCR the whole page: the DRF's Project Detail table sits next to
a second table (Scope of Work / Systems checkboxes) on the same rows.
Tesseract's page-layout analysis (--psm 6 on the full page) tries to
reconstruct one reading order across both tables and drops or scrambles
label text for several rows once the two tables compete. Isolating each row
band -- and, within it, the label column from the value column -- via
detected grid lines avoids that: each OCR call sees a simple, single strip
with nothing to get confused about. Validated against three real DRFs from
different projects/scan sessions (EP-24601, EP-29495, EP-30784): every field
row extracted correctly, full values (long project titles, emails) intact, no
leakage from the adjacent Systems table.

Both crop edges and both line thresholds are load-bearing, and getting any of
them slightly wrong degrades quietly rather than loudly -- a value keeps its
plausible shape while losing its first word, or a label matches the row below
it. The constants below record what each one is holding off.

This targets the DRF template seen in the archive (Document Reference
SSD-P-06 B/IQF.17). The grid-line detection adapts to the actual scan
(dividers are found per-document, not hardcoded pixel positions), so minor
scan/DPI variation is tolerated; a template redesign would need this
revisited.
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

settings = get_settings()
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

RENDER_DPI = 300

# Row and column dividers are structurally different signals and need
# different thresholds. Column dividers run the full height of the table and
# read as near-solid ink (0.9+ of the probed span). Horizontal row rules are
# printed much lighter on some scans -- on EP-24601 they peak at only
# 0.35-0.42, well under the 0.5 that the first two validated DRFs happened to
# clear, which collapsed every field row into one band. 0.3 sits below that
# and comfortably above the 0.05-0.13 baseline that body text alone produces.
# Keep the column threshold high: lowering it lets column-aligned text ink
# register as a divider, which pulls the value column's right edge inward and
# truncates values.
ROW_LINE_DARK_FRACTION = 0.3
COLUMN_LINE_DARK_FRACTION = 0.5

# Canonical field name -> the label text identifying its row, matched
# fuzzily (see _label_matches) against the OCR'd label with non-letters
# stripped -- OCR occasionally misreads one letter ("Consultant" ->
# "Consuttant" was observed on a real DRF), so exact substring matching is
# too brittle. project_reference is deliberately not here: it's the EP
# number the caller already has (the one just typed in), not something
# worth re-deriving from an OCR read that's less reliable than the input.
FIELD_LABEL_KEYWORDS: dict[str, str] = {
    "project_title": "projecttitle",
    "plot_number": "plotnumber",
    "location": "location",
    "client": "client",
    "consultant": "consultant",
    "contractor": "contractor",
    "contact_person": "contactperson",
    "contact_phone": "phonenumber",
    "contact_email": "mailid",
}

# Scope of Work is single-select, in the box beside the Project Detail table;
# its options share that table's row bands and are read from the same geometry.
SCOPE_OPTION_KEYWORDS: dict[str, str] = {
    "Full Package": "fullpackage",
    "Design, Supply, T&C": "designsupply",
    "Supply Only": "supplyonly",
}

# The Systems/Brand/MS/DWG table below the Project Detail block, in template
# order. It is laid out as two side-by-side blocks sharing one set of row
# rules, so the right block's rows sit on the first len(...) rows of the left.
#
# Rows are identified by position, not by reading them: the template is fixed
# (SSD-P-06 B/IQF.17) and the two-line row labels OCR too unreliably to key on
# ("Emergency Light Monitoring" came back as "eee | Monitoring" on a real
# scan). _read_systems sanity-checks the alignment rather than trusting it
# blindly.
SYSTEM_ROWS_LEFT: tuple[str, ...] = (
    "Fire Alarm",
    "Voice Evacuation",
    "Fire Telephone",
    "Smoke Management",
    "Aspiration Smoke Detection",
    "Central Battery System",
    "Emergency Light Monitoring",
    "PA/VA & BGM",
    "CCTV",
    "Access Control",
)

SYSTEM_ROWS_RIGHT: tuple[str, ...] = (
    "Structured Cabling",
    "Gate Barrier",
    "SMATV & IPTV",
    "WIFI Solution",
    "ICT Switches",
    "Nurse Call / Disable Toilet Alarm",
    "Others",
)

# Cell content is judged by how many dark pixels it holds rather than what
# fraction of the cell they cover: the same tick sits in cells of quite
# different sizes (the Scope column is half the width of the MS/DWG columns,
# and two-line rows are a third taller than one-line rows), so a fraction
# threshold that fits one cell shape misreads another. Counts are stable
# across all of them because a tick and a brand word are a fixed physical
# size. Calibrated at RENDER_DPI over EP-24601/29495/30784, where ticks
# measured 242-382 against 0-121 for blank cells, and brand text 1561-1801
# against 0-622 for empty ones.
MIN_TICK_DARK_PIXELS = 150
MIN_BRAND_DARK_PIXELS = 1000
TICK_CELL_INSET_PX = 6  # keeps cell borders out of the ink measurement

# Tallest plausible row in the Systems table; used to find where it ends. Its
# two-line rows reach 89px at RENDER_DPI, while the gap to the next block is
# 170+.
MAX_TABLE_ROW_HEIGHT = 130

MAX_LABEL_EDIT_DISTANCE = 2
CROP_INSET_PX = 3  # keeps grid-line pixels at crop edges out of the OCR input

# Clearance past the table's left border before the label crop starts. The
# border is a solid vertical line, and including it makes Tesseract read it as
# a character fused to the label's first letter -- enough to corrupt short
# labels beyond the edit-distance tolerance ("Location" -> "laenian",
# "Client" -> "ICent" on EP-24601). Wider than CROP_INSET_PX because the
# border is thicker than a cell rule.
BORDER_CLEARANCE_PX = 9

# Shortest run of uniform rows still recognised as the Project Detail table.
# The template has 10 (Project Reference through E-Mail ID) and the longest
# run always wins, so this is only the floor for "is this a table at all" --
# the next-longest uniform run on a real DRF (the Systems block) is 3.
MIN_FIELD_ROWS = 4


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def _label_matches(normalized_label: str, keyword: str) -> bool:
    if keyword in normalized_label:
        return True
    # A row whose label text is much longer than any real field label is
    # prose (e.g. the Attachments paragraph further down the page), not a
    # field row -- don't fuzzy-match against it. Without this guard, a long
    # enough garbled block will contain *some* window within edit distance
    # 2 of a short keyword by sheer chance.
    if len(normalized_label) > len(keyword) + 8:
        return False
    # Otherwise slide a window the size of the keyword across the label and
    # allow a small edit distance, so a single misread letter (or one stray
    # leading/trailing char from a border-line artifact) still matches.
    # Scale the tolerance to the keyword: 2 edits on a short keyword like
    # "elv" matches almost any three letters, and OCR noise in an empty cell
    # ("(aL eee wag ag 2m" on a real DRF) does exactly that.
    max_edits = min(MAX_LABEL_EDIT_DISTANCE, len(keyword) // 3)
    for start in range(0, max(1, len(normalized_label) - len(keyword) + 1)):
        window = normalized_label[start : start + len(keyword)]
        if _edit_distance(window, keyword) <= max_edits:
            return True
    return False


@dataclass
class ExtractedField:
    value: str
    confidence: float  # mean OCR word confidence for the value crop, 0-100
    raw_label: str


@dataclass
class ExtractedSystem:
    """A row of the DRF's Systems table that carries any mark at all -- a
    brand written in, or a Method Statement / Drawing tick."""

    name: str
    brand: str | None
    method_statement: bool
    drawing: bool


@dataclass
class DrfExtractionResult:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    scope_of_work: str | None = None
    systems: list[ExtractedSystem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _detect_line_positions(dark_fraction: np.ndarray, min_frac: float) -> list[int]:
    positions = [i for i, frac in enumerate(dark_fraction) if frac > min_frac]
    lines: list[int] = []
    prev = -10
    for pos in positions:
        if pos - prev > 3:
            lines.append(pos)
        prev = pos
    return lines


def _find_field_row_band(h_lines: list[int]) -> tuple[int, int] | None:
    """Locate the Project Detail table as the longest run of consecutive,
    near-equally-spaced row dividers.

    The field rows are the only place on the page with ~10 uniform-height rows
    in a row; every other block (Systems checkboxes, Attachments, signatures)
    has irregular heights. Finding them by shape rather than by index matters
    for the vertical-divider probe below: the label|value divider only spans
    this table, so probing a taller band dilutes it below any useful
    threshold while unrelated lines from the lower tables dominate instead.
    """
    if len(h_lines) < MIN_FIELD_ROWS + 1:
        return None

    best: tuple[int, int] | None = None
    start = 0
    for i in range(1, len(h_lines) - 1):
        gap = h_lines[i] - h_lines[i - 1]
        next_gap = h_lines[i + 1] - h_lines[i]
        if abs(next_gap - gap) <= max(4, gap * 0.1):
            continue
        if i - start >= MIN_FIELD_ROWS and (best is None or i - start > best[1] - best[0]):
            best = (start, i)
        start = i
    if len(h_lines) - 1 - start >= MIN_FIELD_ROWS and (
        best is None or len(h_lines) - 1 - start > best[1] - best[0]
    ):
        best = (start, len(h_lines) - 1)

    return (h_lines[best[0]], h_lines[best[1]]) if best else None


def _dark_pixels(dark: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> int:
    cell = dark[
        y0 + TICK_CELL_INSET_PX : y1 - TICK_CELL_INSET_PX,
        x0 + TICK_CELL_INSET_PX : x1 - TICK_CELL_INSET_PX,
    ]
    return int(cell.sum()) if cell.size else 0


def _read_scope_of_work(
    gray: Image.Image,
    dark: np.ndarray,
    h_lines: list[int],
    band: tuple[int, int],
    v_lines: list[int],
    result: DrfExtractionResult,
) -> None:
    columns = [x for x in v_lines if x > gray.width * 0.5]
    if len(columns) < 3:
        result.warnings.append("Could not locate the Scope of Work column")
        return
    option_left, tick_left, tick_right = columns[:3]

    top, bottom = band
    start_i, end_i = h_lines.index(top), h_lines.index(bottom)
    row_height = int(np.median([h_lines[i + 1] - h_lines[i] for i in range(start_i, end_i)]))

    # The rule between the "Scope of Work" banner and its first option is not
    # reliably printed, so that option shares a band with the banner and would
    # be measured through the banner's ink. Read it as the last row_height
    # pixels above the field rows instead.
    rows = [(top - row_height, top)]
    rows += [(h_lines[i], h_lines[i + 1]) for i in range(start_i, end_i)]

    for y0, y1 in rows:
        if _dark_pixels(dark, tick_left, tick_right, y0, y1) < MIN_TICK_DARK_PIXELS:
            continue

        label_crop = gray.crop(
            (option_left + CROP_INSET_PX, y0 + CROP_INSET_PX, tick_left, y1 - CROP_INSET_PX)
        )
        normalized = _normalize_label(pytesseract.image_to_string(label_crop, config="--psm 6"))
        scope = next(
            (n for n, kw in SCOPE_OPTION_KEYWORDS.items() if _label_matches(normalized, kw)), None
        )
        if scope is None:
            continue
        if result.scope_of_work is None:
            result.scope_of_work = scope
        elif result.scope_of_work != scope:
            result.warnings.append(
                f"More than one Scope of Work ticked ({result.scope_of_work}, {scope})"
            )

    if result.scope_of_work is None:
        result.warnings.append("Could not extract Scope of Work")


def _find_systems_table_rows(h_lines: list[int], field_band_bottom: int) -> list[tuple[int, int]]:
    """Row bands of the Systems table, which sits directly below the Project
    Detail block and ends where the gap to the next block exceeds a row."""
    below = [y for y in h_lines if y >= field_band_bottom]
    if len(below) < 3:
        return []

    kept = [below[0]]
    for y in below[1:]:
        if y - kept[-1] < 20:  # sliver band between doubled rules
            continue
        # The first gap spans the table's banner as well as its first row,
        # because the rule between them is not reliably printed.
        if len(kept) > 1 and y - kept[-1] > MAX_TABLE_ROW_HEIGHT:
            break
        kept.append(y)

    if len(kept) < 3:
        return []

    heights = [kept[i + 1] - kept[i] for i in range(1, len(kept) - 1)]
    first_row_height = int(np.median(heights))
    rows = [(kept[1] - first_row_height, kept[1])]
    rows += [(kept[i], kept[i + 1]) for i in range(1, len(kept) - 1)]
    return rows


def _read_systems(
    gray: Image.Image,
    dark: np.ndarray,
    rows: list[tuple[int, int]],
    v_lines: list[int],
    result: DrfExtractionResult,
) -> None:
    """Read the Systems/Brand/MS/DWG table.

    A system counts as selected for the project if its row carries any mark at
    all -- a brand written in, or an MS or DWG tick.
    """
    if len(v_lines) < 9 or not rows:
        result.warnings.append("Could not locate the Systems table")
        return

    blocks = (
        (SYSTEM_ROWS_LEFT, v_lines[0], v_lines[1], v_lines[2], v_lines[3], v_lines[4]),
        (SYSTEM_ROWS_RIGHT, v_lines[4], v_lines[5], v_lines[6], v_lines[7], v_lines[8]),
    )

    matched_names = 0
    checked_names = 0
    for names, name_left, brand_left, ms_left, dwg_left, dwg_right in blocks:
        for name, (y0, y1) in zip(names, rows):
            checked_names += 1
            name_crop = gray.crop(
                (name_left + CROP_INSET_PX, y0 + CROP_INSET_PX, brand_left, y1 - CROP_INSET_PX)
            )
            read_name = _normalize_label(
                pytesseract.image_to_string(name_crop, config="--psm 6")
            )
            if _label_matches(read_name, _normalize_label(name)):
                matched_names += 1

            method_statement = (
                _dark_pixels(dark, ms_left, dwg_left, y0, y1) >= MIN_TICK_DARK_PIXELS
            )
            drawing = _dark_pixels(dark, dwg_left, dwg_right, y0, y1) >= MIN_TICK_DARK_PIXELS

            brand = None
            if _dark_pixels(dark, brand_left, ms_left, y0, y1) >= MIN_BRAND_DARK_PIXELS:
                brand_crop = gray.crop(
                    (brand_left + CROP_INSET_PX, y0 + CROP_INSET_PX, ms_left, y1 - CROP_INSET_PX)
                )
                brand = (
                    pytesseract.image_to_string(brand_crop, config="--psm 6")
                    .strip()
                    .strip(" :;|_-—")
                ) or None

            if brand or method_statement or drawing:
                result.systems.append(
                    ExtractedSystem(
                        name=name,
                        brand=brand,
                        method_statement=method_statement,
                        drawing=drawing,
                    )
                )

    # Rows are keyed by position, so a template whose rows have moved would be
    # silently mislabelled. Reading most of the names back is the guard.
    if matched_names < checked_names // 2:
        result.warnings.append(
            "Systems table rows did not match the expected template order; "
            "system names may be wrong"
        )
    if not result.systems:
        result.warnings.append("Could not extract Systems")


def _render_page_grayscale(pdf_path: Path, dpi: int = RENDER_DPI) -> Image.Image:
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    mode = "RGB" if pix.n >= 3 else "L"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    return img.convert("L")


def extract_drf_fields(pdf_path: Path) -> DrfExtractionResult:
    """Render page 1 of the DRF PDF and extract fields from it."""
    return extract_fields_from_image(_render_page_grayscale(pdf_path))


def extract_fields_from_image(gray: Image.Image) -> DrfExtractionResult:
    """Core extraction logic, taking an already-rendered grayscale page
    image directly -- split out from extract_drf_fields so it can be
    exercised in tests against a synthetic image without needing a real
    scanned PDF."""
    result = DrfExtractionResult()

    arr = np.array(gray)
    h, w = arr.shape
    dark = arr < 128

    # Row boundaries: scan only the left ~80% so the page's outer content
    # (title block, right-margin text) doesn't drown out the table's own
    # grid lines.
    scan_width = int(w * 0.8)
    row_dark_fraction = dark[:, :scan_width].mean(axis=1)
    h_lines = _detect_line_positions(row_dark_fraction, ROW_LINE_DARK_FRACTION)

    if len(h_lines) < 5:
        result.warnings.append("Could not detect the Project Detail table (too few grid lines)")
        return result

    # Probe the field rows -- and only them -- for vertical dividers, to find
    # the label|value divider and the value column's right edge (the
    # boundary before the adjacent Scope of Work/Systems table) for *this*
    # document, rather than assuming a fixed position.
    field_row_band = _find_field_row_band(h_lines)
    if field_row_band is None:
        result.warnings.append("Could not detect the Project Detail table (no uniform row band)")
        return result

    probe_top, probe_bottom = field_row_band
    col_dark_fraction = dark[probe_top:probe_bottom, :].mean(axis=0)
    v_lines = _detect_line_positions(col_dark_fraction, COLUMN_LINE_DARK_FRACTION)

    dividers_in_label_zone = [x for x in v_lines if w * 0.15 < x < w * 0.5]
    label_value_x = dividers_in_label_zone[0] if dividers_in_label_zone else int(w * 0.258)

    # Start the label crop past the table's outer left border, if there is
    # one. Only a divider left of the label column qualifies -- taking the
    # first vertical line unconditionally would swallow the label column
    # whole on a table drawn without an outer border.
    left_borders = [x for x in v_lines if x < label_value_x - BORDER_CLEARANCE_PX * 2]
    label_left_x = max(left_borders) + BORDER_CLEARANCE_PX if left_borders else 0

    dividers_past_midpoint = [x for x in v_lines if x > w * 0.5]
    value_right_x = dividers_past_midpoint[0] if dividers_past_midpoint else int(w * 0.755)

    for i in range(len(h_lines) - 1):
        y0, y1 = h_lines[i], h_lines[i + 1]
        if y1 - y0 < 20:  # skip sliver bands (anti-aliasing artifacts)
            continue

        label_crop = gray.crop(
            (label_left_x, y0 + CROP_INSET_PX, label_value_x - CROP_INSET_PX, y1 - CROP_INSET_PX)
        )
        label_text = pytesseract.image_to_string(label_crop, config="--psm 6").strip()
        normalized = _normalize_label(label_text)

        field_name = next(
            (
                name
                for name, keyword in FIELD_LABEL_KEYWORDS.items()
                if _label_matches(normalized, keyword)
            ),
            None,
        )
        if field_name is None or field_name in result.fields:
            continue

        # No inset on the right: a detected divider x is the line's leading
        # edge, so an exclusive crop bound there already excludes the line,
        # and insetting further clips values that run to the cell edge.
        value_left = label_value_x + CROP_INSET_PX
        value_crop = gray.crop(
            (value_left, y0 + CROP_INSET_PX, value_right_x, y1 - CROP_INSET_PX)
        )
        value_data = pytesseract.image_to_data(
            value_crop, config="--psm 6", output_type=pytesseract.Output.DICT
        )
        word_confidences = [
            float(conf)
            for conf, text in zip(value_data["conf"], value_data["text"])
            if text.strip() and float(conf) >= 0
        ]
        value_text = " ".join(t.strip() for t in value_data["text"] if t.strip())
        value_text = value_text.strip(" :;|_-—")

        if not value_text:
            continue

        result.fields[field_name] = ExtractedField(
            value=value_text,
            confidence=sum(word_confidences) / len(word_confidences) if word_confidences else 0.0,
            raw_label=label_text,
        )

    for name in FIELD_LABEL_KEYWORDS:
        if name not in result.fields:
            result.warnings.append(f"Could not extract field: {name}")

    _read_scope_of_work(gray, dark, h_lines, field_row_band, v_lines, result)

    # The Systems table has its own column layout, so it needs its own probe
    # rather than reusing the Project Detail table's dividers.
    systems_rows = _find_systems_table_rows(h_lines, field_row_band[1])
    if systems_rows:
        systems_col_dark = dark[systems_rows[0][1] : systems_rows[-1][1], :].mean(axis=0)
        _read_systems(
            gray,
            dark,
            systems_rows,
            _detect_line_positions(systems_col_dark, COLUMN_LINE_DARK_FRACTION),
            result,
        )
    else:
        result.warnings.append("Could not locate the Systems table")

    return result
