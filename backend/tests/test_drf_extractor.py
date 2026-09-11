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
    dependency on real scanned documents.

    Page width matches an A4 rendered at RENDER_DPI, which is what the
    production path always feeds the extractor. That proportion matters: row
    detection separates grid lines from body text by how much of the page
    width each row inks, so a narrower canvas with the same font would make
    text rows as dark as real grid lines and no threshold could tell them
    apart.
    """
    width, height = 2480, 950
    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 28)
        big_font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = big_font = ImageFont.load_default()

    row_ys = [0, 100, 200, 300, 400, 500]
    for y in row_ys:
        draw.rectangle([0, y, 2430, y + 3], fill=0)

    label_x, value_x, option_x, tick_x, tick_right = 620, 640, 1900, 2250, 2400
    for x in (label_x, option_x, tick_x, tick_right):
        draw.rectangle([x, 0, x + 3, 500], fill=0)

    rows = [
        ("Project Title", "ACME TOWER - G+20 RESIDENTIAL BUILDING", "Full Package", False),
        ("Client", "ACME DEVELOPERS", "Design, Supply, T&C", True),
        ("Consultant", "BEST CONSULTANTS LLC", "Supply Only", False),
        ("Contractor", "ACME CONTRACTING", "Fire Alarm", True),
        ("Contact Person", "JOHN SMITH", "Others", False),
    ]
    for row_index, (label, value, option, ticked) in enumerate(rows):
        row_top = row_ys[row_index]
        y = row_top + 30
        draw.text((10, y), label, fill=0, font=font)
        draw.text((value_x + 10, y), value, fill=0, font=font)
        draw.text((option_x + 10, y), option, fill=0, font=font)
        if ticked:
            draw.line(
                [
                    (tick_x + 40, row_top + 55),
                    (tick_x + 60, row_top + 75),
                    (tick_x + 105, row_top + 25),
                ],
                fill=0,
                width=10,
            )

    _draw_synthetic_systems_table(draw, big_font)
    return img


def _draw_synthetic_systems_table(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> None:
    """The Systems/Brand/MS/DWG table below the Project Detail block.

    The rule between its banner and its first row is deliberately left out,
    because that is how the real scans print: the first system shares a band
    with the banner and has to be recovered from the row height.
    """
    name_x, brand_x, ms_x, dwg_x, right_x = 100, 700, 1000, 1150, 1300
    for x in (name_x, brand_x, ms_x, dwg_x, right_x, 1800, 2050, 2180, 2310):
        draw.rectangle([x, 500, x + 3, 900], fill=0)
    for y in (660, 740, 820, 900):
        draw.rectangle([name_x, y, 2310, y + 3], fill=0)

    def tick(x_left: int, row_top: int) -> None:
        draw.line(
            [(x_left + 40, row_top + 45), (x_left + 58, row_top + 62), (x_left + 100, row_top + 18)],
            fill=0,
            width=10,
        )

    # (row top, brand, method statement, drawing) for Fire Alarm, Voice
    # Evacuation, Fire Telephone, Smoke Management -- the first four rows of
    # SYSTEM_ROWS_LEFT.
    rows = [
        (580, "EDWARDS", True, False),
        (660, None, False, False),
        (740, None, False, True),
        (820, None, False, False),
    ]
    for row_top, brand, ms, dwg in rows:
        if brand:
            draw.text((brand_x + 15, row_top + 18), brand, fill=0, font=font)
        if ms:
            tick(ms_x, row_top)
        if dwg:
            tick(dwg_x, row_top)


@requires_tesseract
def test_extract_fields_from_synthetic_image():
    img = _draw_synthetic_drf_table()
    result = extract_fields_from_image(img)

    assert result.fields["project_title"].value == "ACME TOWER - G+20 RESIDENTIAL BUILDING"
    assert result.fields["client"].value == "ACME DEVELOPERS"
    assert result.fields["consultant"].value == "BEST CONSULTANTS LLC"
    assert result.fields["contractor"].value == "ACME CONTRACTING"
    assert result.fields["contact_person"].value == "JOHN SMITH"
    assert result.scope_of_work == "Design, Supply, T&C"
    assert [(s.name, s.brand, s.method_statement, s.drawing) for s in result.systems] == [
        ("Fire Alarm", "EDWARDS", True, False),
        ("Fire Telephone", None, False, True),
    ]
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
    assert result.fields["contact_person"].value == "KHATIB MOHAMMED RIFATH"
    assert result.scope_of_work == "Full Package"
    # Emergency Light Monitoring is marked by brand alone on this DRF, and is
    # the one row whose two-line label OCRs too poorly to key on by name.
    assert [(s.name, s.brand, s.method_statement, s.drawing) for s in result.systems] == [
        ("Fire Alarm", "EDWARDS", True, True),
        ("Voice Evacuation", "EDWARDS", True, True),
        ("Fire Telephone", "EDWARDS", True, True),
        ("Emergency Light Monitoring", "MENVIER", True, True),
    ]


@requires_tesseract
@requires_live_archive
def test_extract_real_drf_ep_24601():
    # Lighter-printed row rules than EP-29495/EP-30784: they peak at a dark
    # fraction of 0.35-0.42, so a 0.5 row threshold merged every field row
    # into one band and the whole table read as a single mislabelled field.
    drf_path = (
        Path(LIVE_ROOT)
        / "Bilt Middle East"
        / "EP-24601 - Construction of 2B+G+6 Podium + Al Safouh, Dubai"
        / "Scan Document"
        / "EP-24601 DRF.pdf"
    )
    result = extract_drf_fields(drf_path)

    assert result.fields["project_title"].value == (
        "Construction of 2B+G+6 Podiums+Recreational Floor+Twin Towers(37F+R)"
    )
    assert result.fields["plot_number"].value == "Plot No: 382-0126"
    assert result.fields["location"].value == "Al Safouh, Dubai"
    assert result.fields["client"].value == "M/s. Arenco"
    assert result.fields["consultant"].value == "M/s. Arenco"
    assert result.fields["contractor"].value == "M/s. Bilt Middle East LLC"
    assert result.fields["contact_person"].value == "Mr. Rajesh"
    assert result.fields["contact_phone"].value == "055 929 3637"
    assert result.fields["contact_email"].value == "rajeshtr@bilt.ae"
    # Client and consultant really are the same company on this DRF.
    assert result.scope_of_work == "Design, Supply, T&C"
    # This DRF ticks Method Statement only -- no Drawing ticks anywhere.
    assert [(s.name, s.brand, s.method_statement, s.drawing) for s in result.systems] == [
        ("Fire Alarm", "Edwards", True, False),
        ("Voice Evacuation", "Edwards", True, False),
        ("Fire Telephone", "Edwards", True, False),
        ("Central Battery System", None, True, False),
    ]


@requires_tesseract
@requires_live_archive
def test_extract_real_drf_ep_29495():
    drf_path = (
        Path(LIVE_ROOT)
        / "Samana Developers"
        / "EP-29495 IVY Garden 2"
        / "Scan Document"
        / "EP-29495 DRF.pdf"
    )
    result = extract_drf_fields(drf_path)

    assert result.fields["project_title"].value == (
        "IVY GARDEN 2 - 1B+G+5P+34+R RESIDENTIAL BUILDING"
    )
    assert result.fields["plot_number"].value == "648-8523"
    assert result.fields["location"].value == "WADI AL SAFA 5, DLRC, Dubai"
    assert result.fields["client"].value == "SAMANA"
    assert result.fields["consultant"].value == "AL HILAL"
    assert result.fields["contractor"].value == "SAMANA DEVELOPERS"
    assert result.fields["contact_email"].value == "mahammad.bennapade@samanadevelopers.com"
    assert result.scope_of_work == "Design, Supply, T&C"
    assert [(s.name, s.brand, s.method_statement, s.drawing) for s in result.systems] == [
        ("Fire Alarm", "EDWARDS", True, True),
        ("Voice Evacuation", "EDWARDS", True, True),
        ("Fire Telephone", "EDWARDS", True, True),
        ("Central Battery System", None, True, True),
    ]
