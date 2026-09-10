import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services.drf_extractor import (
    _edit_distance,
    _label_matches,
    extract_drf_fields,
    extract_fields_from_image,
)


def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


requires_tesseract = pytest.mark.skipif(
    not _tesseract_available(), reason="tesseract is not installed/configured in this environment"
)


# --- pure unit tests: no OCR involved ---


def test_edit_distance_identical():
    assert _edit_distance("consultant", "consultant") == 0


def test_edit_distance_single_substitution():
    assert _edit_distance("consuttant", "consultant") == 1


def test_label_matches_exact_substring():
    assert _label_matches("iprojecttitlex", "projecttitle")


def test_label_matches_single_ocr_typo():
    # "Consultant" misread as "Consuttant" (l -> t) was observed on a real DRF.
    assert _label_matches("iconsuttant", "consultant")


def test_label_matches_rejects_unrelated_short_text():
    assert not _label_matches("thequickbrownfox", "client")


def test_label_matches_rejects_long_prose_even_if_a_window_would_fuzzy_match():
    # Regression: a long garbled block (e.g. an OCR'd paragraph) must not
    # match just because *some* substring happens to be within edit
    # distance of a short keyword -- this produced a real false positive
    # (an "Attachments" paragraph matched "location") before the length
    # guard was added.
    long_prose = "s attached with the form in order to avoid any delays in ee locatiom something"
    assert not _label_matches(long_prose, "location")


# --- synthetic-image test: exercises the real OCR + line-detection pipeline
# without depending on the live archive ---


def _draw_synthetic_drf_table() -> Image.Image:
    """A minimal 2-column table mimicking the DRF's Project Detail block:
    grid lines + label/value text, built from scratch so this test has no
    dependency on real scanned documents."""
    width, height = 1200, 600
    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()

    row_ys = [0, 100, 200, 300, 400, 500]
    for y in row_ys:
        draw.rectangle([0, y, 1150, y + 3], fill=0)

    label_x, value_x, value_right = 300, 320, 1130
    draw.rectangle([label_x, 0, label_x + 3, 500], fill=0)
    draw.rectangle([value_right, 0, value_right + 3, 500], fill=0)

    rows = [
        ("Project Title", "ACME TOWER - G+20 RESIDENTIAL BUILDING"),
        ("Client", "ACME DEVELOPERS"),
        ("Consultant", "BEST CONSULTANTS LLC"),
        ("Contractor", "ACME CONTRACTING"),
        ("Contact Person", "JOHN SMITH"),
    ]
    for row_index, (label, value) in enumerate(rows):
        y = row_ys[row_index] + 30
        draw.text((10, y), label, fill=0, font=font)
        draw.text((value_x + 10, y), value, fill=0, font=font)

    return img


@requires_tesseract
def test_extract_fields_from_synthetic_image():
    img = _draw_synthetic_drf_table()
    result = extract_fields_from_image(img)

    assert result.fields["project_title"].value == "ACME TOWER - G+20 RESIDENTIAL BUILDING"
    assert result.fields["client"].value == "ACME DEVELOPERS"
    assert result.fields["consultant"].value == "BEST CONSULTANTS LLC"
    assert result.fields["contractor"].value == "ACME CONTRACTING"
    assert result.fields["contact_person"].value == "JOHN SMITH"
    for extracted in result.fields.values():
        assert extracted.confidence > 50


@requires_tesseract
def test_extract_fields_from_blank_image_warns_instead_of_crashing():
    blank = Image.new("L", (800, 400), color=255)
    result = extract_fields_from_image(blank)
    assert result.fields == {}
    assert result.warnings


# --- opt-in live test against the real archive ---

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")

requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)


@requires_tesseract
@requires_live_archive
def test_extract_real_drf_ep_30784():
    drf_path = (
        Path(LIVE_ROOT)
        / "Granada Europe"
        / "EP-30784 - Binghatti Skyblade"
        / "01- EP-30784 Scan"
        / "EP-30784 DRF.pdf"
    )
    result = extract_drf_fields(drf_path)

    assert result.fields["project_title"].value == "BINGHATTI SKYBLADE (4B+G+3P+61 FLOORS+ROOF)"
    assert result.fields["plot_number"].value == "3450398"
    assert result.fields["location"].value == "BURJ KHALIFA DISTRICT"
    assert result.fields["client"].value == "BINGHATTI PROPERTIES"
    assert result.fields["consultant"].value == "SILVER STONE"
    assert result.fields["contractor"].value == "GRANADA EUROPE"
    assert result.fields["contact_email"].value == "khatib.rifath@binghatti.com"
