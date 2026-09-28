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
    _unread_band,
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


def _unruled_sheet(path: Path) -> Path:
    """EP-30088's ELS layout, drawn: five column rules (the Total Price column
    is off the scan), no row rules, and every other row wrapped -- a two-line
    catalog number, a two-line description -- with the quantity centred on
    the row, level with neither line."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    columns = [40, 100, 230, 480, 560]
    for x in columns:
        page.draw_line((x, 40), (x, 800), width=1)
    for x, label in zip(columns, ["Qty.", "Catalog No.", "Description", "Unit Price"]):
        page.insert_text((x + 6, 60), label, fontsize=9)
    rows = [
        ("168", ["SL2-42D3D-CGL-M"], ["Surface Mounted Emergency Light"]),
        ("73", ["SL2-65D3D-CGL-M"], ["Surface Mounted Emergency Light"]),
        ("86", ["RT2RHEO200CGL3HIP", "M"], ["RTECH MR HEO CGL 200 MNM 3H IP65"]),
        ("48", ["SL2-42D3D-CGL-M", "+SL23I"], ["Wall Mounted Exit, 20 metre viewing distance,", "IP42"]),
        ("56", ["SL2-42D3D-CGL-M", "+SL2PPLR+SL2RB"], ["Exit Directional, Corridor Recessed, 20", "metre viewing distance"]),
        ("1", ["CTR400CGL2KS-M"], ["Menvier Brand CGLine Web Compact Controller"]),
    ]
    y = 100.0
    for quantity, catalog, description in rows:
        height = 14 * max(len(catalog), len(description))
        page.insert_text((columns[0] + 12, y + height / 2 + 3), quantity, fontsize=9)
        for index, text in enumerate(catalog):
            page.insert_text((columns[1] + 6, y + 10 + 14 * index), text, fontsize=9)
        for index, text in enumerate(description):
            page.insert_text((columns[2] + 6, y + 10 + 14 * index), text, fontsize=9)
        y += height + 12
    doc.save(path)
    doc.close()
    return path


@requires_tesseract
def test_an_unruled_sheet_keeps_wrapped_rows_together(tmp_path):
    """No row rules to hold a wrapped row together: the quantity is level
    with neither line, so pairing by position orphaned it and the second
    catalog line became an item. Rows are then bounded by the quantities."""
    from app.services.design_sheet_extractor import extract_design_sheet

    result = extract_design_sheet(_unruled_sheet(tmp_path / "els.pdf"))
    # Rows whose part number the independent passes could not confirm are
    # review rows (M2 review 03), read whole all the same: the row
    # assembly this test is about is judged over lines and review rows.
    held = [ExtractedBoqLine(catalog_no=i.detail["catalog_no"], description=i.detail["description"], quantity=i.detail["quantity"],
                             group_heading=None, confidence=0.0, page=i.page, y_px=i.region[1] if i.region else None)
            for i in result.issues if i.detail.get("reason_code") == "PART_NUMBER_CONFLICT"]
    lines = sorted(result.lines + held, key=lambda l: (l.page, l.y_px or 0))

    assert [line.quantity for line in lines] == ["168", "73", "86", "48", "56", "1"]
    by_quantity = {line.quantity: line for line in lines}
    # Part numbers by prefix: at this size OCR does not tell I from 1.
    assert by_quantity["48"].catalog_no.replace(" ", "").startswith("SL2-42D3D-CGL-M+SL23")
    assert by_quantity["48"].description.startswith("Wall Mounted Exit")
    assert by_quantity["48"].description.endswith("IP42")
    assert by_quantity["56"].catalog_no.replace(" ", "") == "SL2-42D3D-CGL-M+SL2PPLR+SL2RB"
    assert by_quantity["56"].description.endswith("metre viewing distance")
    assert by_quantity["86"].catalog_no.replace(" ", "") == "RT2RHEO200CGL3HIPM"
    # Two adjacent single-line rows stay two rows.
    assert by_quantity["168"].catalog_no == "SL2-42D3D-CGL-M" and by_quantity["73"].catalog_no == "SL2-65D3D-CGL-M"


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


# --- M2 review 01, R4: a band between two tables is accounted for ---


def test_an_inked_band_between_two_tables_is_a_skipped_region_not_a_silent_gap(monkeypatch):
    """EP-30784 FAS page 2: a scanner streak breaks the column rules across one
    item row, the table extent splits around it and the row went unread and
    unreported. The row reader is unchanged; the band is now a skipped region
    of the page's coverage."""
    import numpy as np
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    dark = np.zeros((400, 600), dtype=bool)
    dark[110:150, 210:390] = True        # text in the description column, in the band between the tables
    rules = [0, 100, 200, 400, 500, 600]
    layout = dse._Layout(quantity=(0, 1), catalog=(1, 2), description=(2, 3))
    assert _unread_band(dark, 100, 160, 200, 400) is True
    assert _unread_band(dark, 160, 400, 200, 400) is False        # blank
    assert _unread_band(dark, 100, 110, 200, 400) is False        # thinner than a row

    monkeypatch.setattr(dse, "_table_extents", lambda d, x: [(0, 100), (160, 400)])
    monkeypatch.setattr(dse, "_section_title", lambda *a, **k: None)
    calls = []

    def read_table(image, d, r, lay, page, top, bottom, section, heading=None):
        calls.append((top, bottom, heading))
        if (top, bottom) == (100, 160):
            return [], heading                     # the band gave no row
        return [], "Field Devices" if top == 0 else heading
    monkeypatch.setattr(dse, "_read_table", read_table)
    _lines, _section, heading, regions = dse._read_page(Image.new("L", (600, 400), 255), dark, rules, layout, 2, None, None)
    kinds = [(r.kind, r.status, r.top, r.bottom) for r in regions]
    assert ("band", "skipped", 100, 160) in kinds
    assert [k for k in kinds if k[0] == "table"] == [("table", "processed", 0, 100), ("table", "processed", 160, 400)]
    assert calls == [(0, 100, None), (100, 160, "Field Devices"), (160, 400, "Field Devices")], "the heading in force carries across the band and the split"
    assert heading == "Field Devices"

    # a band that gives a row is a processed region
    monkeypatch.setattr(dse, "_read_table", lambda image, d, r, lay, page, top, bottom, section, heading=None:
                        ([dse.ExtractedBoqLine(catalog_no="SIGA-OSHD-FC", description="Multisensor", quantity="525", group_heading=heading,
                                               confidence=90.0, page=page, y_px=(top + bottom) / 2)] if (top, bottom) == (100, 160) else [], heading))
    lines, _section, _heading, regions = dse._read_page(Image.new("L", (600, 400), 255), dark, rules, layout, 2, None, "Field Devices")
    assert [(r.kind, r.status, r.rows_accepted) for r in regions if r.kind == "band"] == [("band", "processed", 1)]
    assert [(l.catalog_no, l.quantity, l.group_heading) for l in lines] == [("SIGA-OSHD-FC", "525", "Field Devices")]

    # a blank band between two tables is not reported
    blank = np.zeros((400, 600), dtype=bool)
    monkeypatch.setattr(dse, "_read_table", lambda *a, **k: ([], None))
    _lines, _section, _heading, regions = dse._read_page(Image.new("L", (600, 400), 255), blank, rules, layout, 2, None)
    assert all(r.kind == "table" for r in regions)


def test_a_longer_independent_reading_sends_a_cut_digit_to_review(monkeypatch):
    """EP-30784 FAS: TP606 printed 491, the column read 49 at 86 % and one
    independent pass read 491: neither value is taken, the row is reviewed."""
    from app.services import design_sheet_extractor as dse

    line = dse.ExtractedBoqLine(catalog_no="TP606", description="Back Box", quantity="49", group_heading=None, confidence=87.0, page=2,
                                raw_quantity="49", quantity_confidence=86.0, quantity_parse={"kind": "equipment_count", "raw": "49", "value": 49, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "binarised, single line, digits", "text": "491", "value": "491", "status": "ok"},
        {"pass": "binarised, single character", "text": "49]", "value": "49", "status": "ok"},
        {"pass": "greyscale, single line, digits or words", "text": "49", "value": "49", "status": "ok"}])
    dse._check_cut_digit(None, line)
    assert line.quantity is None and line.quantity_parse["status"] == "ambiguous" and "491" in line.quantity_parse["rule"]
    assert dse._dropped_row_issue(line, 1) is not None, "a row for review, not a line"
    # the same passes agreeing on the strip's value, or reading a shorter one, leave a confident value alone (G1ARN: 121 / "12")
    line = dse.ExtractedBoqLine(catalog_no="G1ARN", description="Horn", quantity="121", group_heading=None, confidence=90.0, page=2,
                                raw_quantity="121", quantity_confidence=88.0, quantity_parse={"kind": "equipment_count", "raw": "121", "value": 121, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "121", "value": "121", "status": "ok"}, {"pass": "b", "text": "12]", "value": "12", "status": "ok"}, {"pass": "c", "text": "121", "value": "121", "status": "ok"}])
    dse._check_cut_digit(None, line)
    assert line.quantity == "121"
    # and a pass reading something else entirely at 60-90 % leaves the strip's value alone (the cell pass is the worse reader: 48 read "86")
    line = dse.ExtractedBoqLine(catalog_no="X", description="Wrapped row", quantity="48", group_heading=None, confidence=80.0, page=1,
                                raw_quantity="48", quantity_confidence=75.0, quantity_parse={"kind": "equipment_count", "raw": "48", "value": 48, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "86", "value": "86", "status": "ok"}, {"pass": "b", "text": "86", "value": "86", "status": "ok"}, {"pass": "c", "text": "", "value": None, "status": "empty"}])
    dse._check_cut_digit(None, line)
    assert line.quantity == "48" and line.alternates is not None
    # the low-confidence path keeps its full confirmation logic, cut digit included
    line = dse.ExtractedBoqLine(catalog_no="TP606", description="Back Box", quantity="49", group_heading=None, confidence=50.0, page=2,
                                raw_quantity="49", quantity_confidence=45.0, quantity_parse={"kind": "equipment_count", "raw": "49", "value": 49, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "491", "value": "491", "status": "ok"}, {"pass": "b", "text": "49", "value": "49", "status": "ok"}, {"pass": "c", "text": "49", "value": "49", "status": "ok"}])
    dse._confirm_quantity(None, line)
    assert line.quantity is None and "491" in line.quantity_parse["rule"]


def test_a_part_number_the_independent_passes_do_not_confirm_is_a_row_for_review(monkeypatch):
    """EP-30784 FAS: the column read "4-CABI6D"; a pass on the cell read
    "4-CAB16D". Nothing is substituted; the row goes to the engineer with
    both readings and its quantity."""
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    calls = iter([["4-CABI6D", "4-CAB16D"], ["SIGA-CT2", "SIGA-CT2"]])
    def image_to_string(img, config=""):
        return next(image_to_string.batch)
    image_to_string.batch = iter([])

    def run(image, line, span):
        readings = next(calls)
        line.catalog_alternates = [{"pass": f"pass {i}", "text": r, "value": r} for i, r in enumerate(readings)]
        line.catalog_uncertain = any(r != line.catalog_no for r in readings)
    monkeypatch.setattr(dse, "_confirm_catalog", run)
    uncertain = dse.ExtractedBoqLine(catalog_no="4-CABI6D", description="Door Assembly", quantity="1", group_heading="Panel", confidence=71.0,
                                     page=1, y_px=100.0, raw_quantity="1", catalog_confidence=71.0, table_span=(0, 600))
    sure = dse.ExtractedBoqLine(catalog_no="SIGA-CT2", description="Dual Input Module", quantity="119", group_heading=None, confidence=95.0,
                                page=1, y_px=200.0, raw_quantity="119", catalog_confidence=95.0, table_span=(0, 600))
    dse._confirm_catalog(None, uncertain, (0, 100))
    assert uncertain.catalog_uncertain and [r["value"] for r in uncertain.catalog_alternates] == ["4-CABI6D", "4-CAB16D"]
    issue = dse._uncertain_part_issue(uncertain, 1)
    assert issue.code == dse.IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE and issue.target == "boq_line:1:1"
    assert issue.detail["reason_code"] == "PART_NUMBER_CONFLICT" and issue.detail["quantity"] == "1" and "4-CAB16D" in issue.detail["reason"]
    dse._confirm_catalog(None, sure, (0, 100))
    assert not sure.catalog_uncertain


def test_the_catalog_check_reads_the_cell_twice_and_keeps_what_each_pass_read(monkeypatch):
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    answers = iter(["757-7A-T", "757-7A-T"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="WSTIA-T", description="Horn/Strobe", quantity="56", group_heading=None, confidence=72.0,
                                page=2, y_px=100.0, raw_quantity="56", catalog_confidence=72.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and [r["value"] for r in line.catalog_alternates] == ["757-7A-T", "757-7A-T"]
    assert line.catalog_no == "WSTIA-T", "nothing is substituted: the engineer decides"
    answers = iter(["", "SIGA-SB"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SIGA-SB", description="Base", quantity="2206", group_heading=None, confidence=94.0,
                                page=2, y_px=100.0, raw_quantity="2206", catalog_confidence=94.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "a pass that read nothing is no evidence; the other agreed"
    # a near miss from one pass is evidence (4-CABI6D / 4-CAB16D); a fragment or an unrelated string from one pass is not
    answers = iter(["4-CABI6D", "4-CAB16D"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="4-CABI6D", description="Door", quantity="1", group_heading=None, confidence=71.0,
                                page=1, y_px=100.0, raw_quantity="1", catalog_confidence=71.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain
    answers = iter(["SL2MNM65D3C-M", "SL2M"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SL2MNM65D3C-M", description="Exit", quantity="1", group_heading=None, confidence=70.0,
                                page=1, y_px=100.0, raw_quantity="1", catalog_confidence=70.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "one pass agreed, the other read a fragment"


def test_a_multiline_catalog_cell_is_read_as_a_block_and_a_near_miss_holds_it(monkeypatch):
    """EP-30784 EML: "SL2MNM65D3C-M" over "+SL23I" in one cell; the strip
    read "+SL231". Line mode returns a fragment of one line; block mode
    reads both lines, and a reading a glyph apart holds the row."""
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    configs = []
    answers = iter(["SL2MNM65D3C-M\n+SLZ31", "SL2MNM65D3C-M\n+SL231"])

    def ocr(img, config=""):
        configs.append(config)
        return next(answers)
    monkeypatch.setattr(dse.pytesseract, "image_to_string", ocr)
    line = dse.ExtractedBoqLine(catalog_no="SL2MNM65D3C-M +SL231", description="Wall Mounted Exit", quantity="1", group_heading=None, confidence=67.0,
                                page=1, y_px=1552.0, row_bounds=(1500, 1604), raw_quantity="1", catalog_confidence=67.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 1700), 255), line, (100, 300))
    assert all("--psm 6" in c for c in configs), "a two-line cell is read as a block"
    assert line.catalog_uncertain and line.catalog_check["multiline"] and "near miss" in line.catalog_check["reason"]
    assert [r["value"] for r in line.catalog_alternates] == ["SL2MNM65D3C-M +SLZ31", "SL2MNM65D3C-M +SL231"]
    assert line.catalog_cell == [100, 1500, 300, 1604] and line.catalog_no == "SL2MNM65D3C-M +SL231", "the literal strip value and the cell stay on the row"
    issue = dse._uncertain_part_issue(line, 1)
    assert issue.detail["catalog_check"]["multiline"] and issue.detail["catalog_cell"] == [100, 1500, 300, 1604] and issue.detail["quantity"] == "1"


def test_fragments_do_not_confirm_a_part_and_a_whole_matching_reading_does(monkeypatch):
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    def line_for(part):
        return dse.ExtractedBoqLine(catalog_no=part, description="x", quantity="2", group_heading=None, confidence=70.0, page=1, y_px=100.0,
                                    row_bounds=(80, 130), raw_quantity="2", catalog_confidence=70.0, table_span=(0, 600))
    # fragments only: unconfirmed, held
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(iter(["C-M"])))
    answers = iter(["C-M", "SS C-M"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SL2MNM65D3C-M +SL231"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and line.catalog_check == {"confirmed": False, "multiline": False, "reason": "no independent pass read the whole part number"}
    # nothing read at all: unconfirmed, held
    answers = iter(["", ""]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("4-CPU"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and not line.catalog_check["confirmed"]
    # conflicting whole readings: held
    answers = iter(["757-7A-T", "757-7A-T"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("WSTIA-T"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and "another part number" in line.catalog_check["reason"]
    # a clear matching whole reading: confirmed, even beside a clipped fragment
    answers = iter(["-L210DI", "SL210DI"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SL210DI"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain and line.catalog_check["confirmed"]
    # a cut identity read literally by every pass is confirmed as printed: nothing is completed
    answers = iter(["SIGA-OSHD-FC", "SIGA-OSHD-FC"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SIGA-OSHD-FC"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain and line.catalog_no == "SIGA-OSHD-FC"
