"""Deterministic field extraction from a DRF (Design Request Form) PDF --
OCR + classic image-processing table detection, no AI model involved.

Why not just OCR the whole page: the DRF's Project Detail table sits next to
a second table (Scope of Work / Systems checkboxes) on the same rows.
Tesseract's page-layout analysis (--psm 6 on the full page) tries to
reconstruct one reading order across both tables and drops or scrambles
label text for several rows once the two tables compete. Isolating each row
band -- and, within it, the label column from the value column -- via
detected grid lines avoids that: each OCR call sees a simple, single strip
with nothing to get confused about. Validated against two real DRFs from
different projects/scan sessions (EP-29495, EP-30784): every field row
extracted correctly, full values (long project titles, emails) intact, no
leakage from the adjacent Systems table.

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
MIN_GRID_LINE_DARK_FRACTION = 0.5

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

MAX_LABEL_EDIT_DISTANCE = 2
CROP_INSET_PX = 3  # keeps grid-line pixels at crop edges out of the OCR input


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
    for start in range(0, max(1, len(normalized_label) - len(keyword) + 1)):
        window = normalized_label[start : start + len(keyword)]
        if _edit_distance(window, keyword) <= MAX_LABEL_EDIT_DISTANCE:
            return True
    return False


@dataclass
class ExtractedField:
    value: str
    confidence: float  # mean OCR word confidence for the value crop, 0-100
    raw_label: str


@dataclass
class DrfExtractionResult:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
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
    h_lines = _detect_line_positions(row_dark_fraction, MIN_GRID_LINE_DARK_FRACTION)

    if len(h_lines) < 5:
        result.warnings.append("Could not detect the Project Detail table (too few grid lines)")
        return result

    # Probe a band spanning the field rows for vertical dividers, to find
    # the label|value divider and the value column's right edge (the
    # boundary before the adjacent Scope of Work/Systems table) for *this*
    # document, rather than assuming a fixed position.
    probe_top = h_lines[min(4, len(h_lines) - 2)]
    probe_bottom = h_lines[-1]
    col_dark_fraction = dark[probe_top:probe_bottom, :].mean(axis=0)
    v_lines = _detect_line_positions(col_dark_fraction, MIN_GRID_LINE_DARK_FRACTION)

    dividers_in_label_zone = [x for x in v_lines if w * 0.15 < x < w * 0.5]
    label_value_x = dividers_in_label_zone[0] if dividers_in_label_zone else int(w * 0.258)

    dividers_past_midpoint = [x for x in v_lines if x > w * 0.5]
    value_right_x = dividers_past_midpoint[0] if dividers_past_midpoint else int(w * 0.755)

    for i in range(len(h_lines) - 1):
        y0, y1 = h_lines[i], h_lines[i + 1]
        if y1 - y0 < 20:  # skip sliver bands (anti-aliasing artifacts)
            continue

        label_crop = gray.crop((0, y0 + CROP_INSET_PX, label_value_x - CROP_INSET_PX, y1 - CROP_INSET_PX))
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

        # Left edge is intentionally given back a couple of the inset pixels:
        # scan alignment varies enough between documents that the divider
        # line's detected x can sit a few px into where the value's first
        # character actually starts, clipping it (observed on a real DRF --
        # "IVY GARDEN 2" lost its first six characters at a full 3px inset).
        # A little divider-line noise at the edge is a smaller loss than a
        # clipped first word, and low-confidence rows are surfaced for
        # review anyway.
        value_left = label_value_x + CROP_INSET_PX - 10
        value_crop = gray.crop((value_left, y0 + CROP_INSET_PX, value_right_x - CROP_INSET_PX, y1 - CROP_INSET_PX))
        value_data = pytesseract.image_to_data(
            value_crop, config="--psm 6", output_type=pytesseract.Output.DICT
        )
        word_confidences = [
            float(conf)
            for conf, text in zip(value_data["conf"], value_data["text"])
            if text.strip() and float(conf) >= 0
        ]
        value_text = " ".join(t.strip() for t in value_data["text"] if t.strip())
        value_text = value_text.strip(" :|_-—")

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

    return result
