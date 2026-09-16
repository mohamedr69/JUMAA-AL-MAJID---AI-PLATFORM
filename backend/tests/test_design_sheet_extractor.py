import os
from pathlib import Path

import pytest

from app.services.design_sheet_extractor import (
    INLINE_QUANTITY_RE,
    DesignSheetExtractionError,
    ExtractedBoqLine,
    _clean_quantity,
    _dropped_row_issue,
    _is_heading,
    extract_boq_lines,
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

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")
requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)


# --- pure unit tests: no OCR involved ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2430", "2430"),
        ("| 836", "836"),  # the column rule gets read as a pipe
        ("836 |", "836"),
        ("Lot", "Lot"),  # a real quantity on these sheets, not a number
        ("i", "1"),  # single-letter reads are misread digits
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_clean_quantity(raw, expected):
    assert _clean_quantity(raw) == expected


@pytest.mark.parametrize(
    ("text", "quantity"),
    [
        ("( 2 ) Intelligent Audio Amplifier", "2"),
        ("(1. ) Primary Power Supply, 230 V", "1"),  # OCR renders the brackets loosely
        ("(_ 1 )4-LCD w/ cable", "1"),
        ("( 18 ) Blank Filler Plate", "18"),
    ],
)
def test_inline_quantity_is_read_off_sub_components(text, quantity):
    match = INLINE_QUANTITY_RE.match(text)
    assert match is not None
    assert match.group(1) == quantity


def test_inline_quantity_ignores_a_bracket_mid_description():
    assert INLINE_QUANTITY_RE.match("Ceiling speaker, ABS fire dome (diameter 17,5 cm)") is None


def test_headings_are_short_unpunctuated_lines_without_a_quantity():
    assert _is_heading("Field Devices", None, None)
    assert _is_heading("EST4 Repeater Panel", None, None)
    # a line carrying its own quantity or part number is an item, not a heading
    assert not _is_heading("Remote Annunciator", "1", None)
    assert not _is_heading("Central Processor Module", None, "4-CPU")
    # continuation text of a long description must not be mistaken for one
    assert not _is_heading("one BPS. Components are:", None, None)
    assert not _is_heading(
        "Remote audio closet cabinet wallbox, red. Supports 2 x", None, None
    )


def test_missing_file_raises():
    with pytest.raises(DesignSheetExtractionError):
        extract_boq_lines(Path("no-such-design-sheet.pdf"))


# --- opt-in live tests: quantities verified against the real sheets ---


@requires_tesseract
@requires_live_archive
def test_els_design_sheet_quantities():
    """Spreadsheet-export layout: Catalog | Description | Qty, quantity on the
    right. Every quantity on the sheet, in order."""
    path = (
        Path(LIVE_ROOT)
        / "Samana Developers"
        / "EP-29495 IVY Garden 2"
        / "Commercial Document"
        / "EP-29495 ELS DESIGN SHEET.pdf"
    )
    lines = extract_boq_lines(path)

    assert [line.quantity for line in lines] == [
        "1", "8", "Lot", "505", "511", "204", "204", "238", "8", "1", "60", "60", "60", "176",
    ]
    # "Lot" is a quantity these sheets really use, so it must survive the rule
    # that a line without a quantity is not a BOQ line.
    assert all(line.quantity for line in lines)
    # Catalog numbers and descriptions are checked by prefix, not in full.
    # These sheets mix 0/O and 1/l/I in part numbers and at the printed size
    # the pairs are near-identical -- the sheet's "SL2-M65F0FCGS-J" carries a
    # digit zero (confirmed at 600 DPI) and OCR returns a letter O. No rule
    # separates them without corrupting part numbers that really do contain an
    # O, so the extractor returns what it sees and this asserts the part that
    # is unambiguous. Quantities carry no such ambiguity and are checked
    # exactly, digit for digit, above.
    assert lines[3].catalog_no.startswith("SL2-M65F")
    assert lines[3].description.startswith("SL20,Mains,")
    assert lines[3].description.endswith("Slave,CG-S JSB")


@requires_tesseract
@requires_live_archive
def test_eml_design_sheet_quantities():
    """Quotation layout, but row-ruled and scanned askew, with several
    descriptions wrapping onto a second line."""
    path = (
        Path(LIVE_ROOT)
        / "Granada Europe"
        / "EP-30784 - Binghatti Skyblade"
        / "01- EP-30784 Scan"
        / "EP-30784 EML Design.pdf"
    )
    lines = extract_boq_lines(path)

    # The sheet lists 12 items: 759, 458, 458, 327, 33, 169, 17, 119, 111, 8,
    # 7, 1. The column strip returns letters for the 7 on this scan ("ae");
    # three independent passes on that cell alone agree it reads 7, which is
    # what brings it back -- recorded as their agreement, not the strip's.
    assert len(lines) == 12
    assert [line.quantity for line in lines] == [
        "759", "458", "458", "327", "33", "169", "17", "119", "111", "8", "7", "1",
    ]
    seven = lines[10]
    assert seven.raw_quantity == "ae" and seven.quantity_parse["independent_agreement"] >= 2


@requires_tesseract
@requires_live_archive
def test_ep30208_multi_building_sheet_reads_every_row_or_flags_it():
    """EP-30208: nine buildings over four pages, 97 rows, 700 units by eye.
    Every row is either read with the printed quantity or sent to review --
    none is lost, none carries a wrong number (checked against the page
    images): stacked "2"/"1" pairs the column strip merged, and a "1" read
    as "4", are the rows for review."""
    path = (Path(LIVE_ROOT) / "AELIA TEK" / "EP-30208 Kalba Phase 2 & Al Dhaid Phase 1" / "Commercial Document"
            / "EP-30208 PA Design Sheet.pdf")
    from app.services.design_sheet_extractor import extract_design_sheet

    result = extract_design_sheet(path)
    review = [i for i in result.issues if (i.detail.get("description") or "").strip(" _") not in ("f. Speakers",)]
    assert len(result.lines) + len(review) == 97
    printed_for_review = {"PA Rack": 1, "Monitor Panel": 2, "Voice Evacuation Frame 4AB": 1}
    reviewed_units = sum(printed_for_review[(i.detail["description"]).strip(" _")] for i in review)
    assert sum(int(line.quantity) for line in result.lines) + reviewed_units == 700
    assert [b["display"] for b in result.buildings] == [
        "DHAID - B1 BUILDING", "DHAID - B2 BUILDING", "DHAID - B3 BUILDING", "DHAID - B4 BUILDING", "DHAID - B5 BUILDING",
        "KALBA - B2 BUILDING", "KALBA - B4 BUILDING", "KALBA - B5 BUILDING", "KALBA - B6 BUILDING",
    ]
    # "DH AID-BIBUILDING" and "DH AID - BS BUILDING" are aliases, not buildings of their own.
    assert any("DH AID-BIBUILDING" in b["aliases"] for b in result.buildings)


@requires_tesseract
@requires_live_archive
def test_eml_design_sheet_keeps_wrapped_rows_together():
    """A wrapped row puts its quantity level with neither line of text, so
    grouping by spacing alone loses it and emits the remainder as its own
    item. The row rules are what hold these together."""
    path = (
        Path(LIVE_ROOT)
        / "Granada Europe"
        / "EP-30784 - Binghatti Skyblade"
        / "01- EP-30784 Scan"
        / "EP-30784 EML Design.pdf"
    )
    lines = extract_boq_lines(path)

    wrapped = next(line for line in lines if line.quantity == "119")
    assert "SL2-42D3D-CGL-M" in wrapped.catalog_no
    assert "+SL2PPLR+SL2RB" in wrapped.catalog_no
    assert wrapped.description.startswith("Exit Directional, Corridor Recessed")
    assert wrapped.description.endswith("metre viewing distance")


@requires_tesseract
@requires_live_archive
def test_fas_design_sheet_field_device_quantities():
    """Scanned quotation layout: Qty | Catalog No. | Description | Unit | Total.

    The Field Devices block is a flat table on page 2, and page 2 is where
    most of this sheet's line items live -- reading page 1 alone loses them.
    """
    path = (
        Path(LIVE_ROOT)
        / "Samana Developers"
        / "EP-29495 IVY Garden 2"
        / "Commercial Document"
        / "EP-29495 FAS Design.pdf"
    )
    lines = extract_boq_lines(path)

    assert any(line.page == 2 for line in lines), "page 2 was not read"

    field_devices = {
        line.catalog_no: line.quantity
        for line in lines
        if line.group_heading == "Field Devices" and line.catalog_no
    }
    assert field_devices == {
        "SIGA-OSD-FCN": "2430",
        "SIGA-HRD-FCN": "144",
        "SIGA-OSHD": "836",
        "SIGA-SB": "2145",
        "SIGA-IB": "250",
        "SIGA-LPS": "1015",
        "SIGA-LED": "2",
        "SIGA-278": "135",
        "STI-1230": "12",
        "STI-3002": "12",
        "G4SRN": "83",
        "EST-S186C": "571",
        "757-7A-SS70": "83",
        "757-7A-T": "15",
        "202-7A-TW": "26",
        "6833-4": "102",
        "TCS-6": "1",
        "6830-3": "3",
        "SIGA-CT2": "56",
        "SIGA-CR": "200",
        "SIGA-UM": "10",
        "SIGA-CC1": "4",
        "SIGA-CC2A": "44",
        "TP606": "239",
        "TP434": "83",
        "27193-11": "282",
        "27193-21": "58",
        "757A-WB": "98",
        "GRSW-10": "9",
    }


@requires_tesseract
@requires_live_archive
def test_fas_design_sheet_reads_grouped_sub_components():
    """Sub-components are indented under a panel heading with their quantity
    inline as "( n )" rather than in the Qty column."""
    path = (
        Path(LIVE_ROOT)
        / "Samana Developers"
        / "EP-29495 IVY Garden 2"
        / "Commercial Document"
        / "EP-29495 FAS Design.pdf"
    )
    lines = extract_boq_lines(path)

    main_panel = [
        line for line in lines if line.group_heading == "EST4 Main Fire Alarm Control Panel"
    ]
    quantities = {line.catalog_no: line.quantity for line in main_panel if line.catalog_no}
    assert quantities["4-CPU"] == "1"
    assert quantities["3-SDDC2"] == "5"
    assert quantities["4-FIL"] == "18"
    assert quantities["12V65A"] == "2"

    # The address block above the table must not be read as line items.
    assert not any("samanadevelopers.com" in line.description.lower() for line in lines)
    assert not any(line.description.strip().lower().startswith("quotation for") for line in lines)


@requires_tesseract
@requires_live_archive
def test_unreadable_layout_reports_rather_than_inventing_lines():
    """A DRF is not a Design Sheet; its column count matches no known layout."""
    path = (
        Path(LIVE_ROOT)
        / "Samana Developers"
        / "EP-29495 IVY Garden 2"
        / "Scan Document"
        / "EP-29495 DRF.pdf"
    )
    with pytest.raises(DesignSheetExtractionError):
        extract_boq_lines(path)


def _row(description: str, raw_quantity: str | None, catalog_no: str | None = None) -> ExtractedBoqLine:
    """A row the read kept without a usable quantity."""
    return ExtractedBoqLine(
        catalog_no=catalog_no, description=description, quantity=None, group_heading=None,
        confidence=90.0, page=1, raw_quantity=raw_quantity,
    )


def test_the_tables_own_header_row_is_not_sent_for_review():
    # EP-30784: the header was read as a row, and the engineer was asked to
    # settle a quantity of "Qty." -- the column label itself.
    assert _dropped_row_issue(_row("a Description", "Qty."), 0) is None
    assert _dropped_row_issue(_row("Description", None), 0) is None
    # However the OCR decorates it with the neighbouring rules.
    assert _dropped_row_issue(_row("a Description", "| QTY. |"), 0) is None


def test_a_real_row_missing_its_quantity_is_still_reviewed():
    issue = _dropped_row_issue(_row("Addressable Smoke Detector", "l0", catalog_no="SIGA-PS"), 0)
    assert issue is not None and issue.detail["catalog_no"] == "SIGA-PS"
    # A described item with no catalog number is an item too.
    assert _dropped_row_issue(_row("Surface Mounted Emergency Light", ""), 0) is not None
