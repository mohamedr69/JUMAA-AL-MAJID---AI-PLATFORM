"""BOQ Extraction V2, phase 2: the page's geometry witnesses each row the
model read -- quantities come from the quantity column (or the inline
brackets), never from a number inside a description or a part number;
adjacent rows keep their own quantities; a row reported by two page bands
is one row; a group is the heading interval the row sits in, and unresolved
rather than guessed."""

from __future__ import annotations

import pytest

from app.ai import sheet_reader
from app.extraction import row_geometry
from app.extraction.issues import ReviewReason
from app.extraction.row_geometry import ColumnRead, PageGeometry
from app.models import ExtractionIssue, ExtractionRun

from .test_ai_sheet_reader import PAGE_ANSWER, SECOND, _close_up, _login, _project, _row, ai, recording  # noqa: F401


def _geometry(quantity, catalog, description, width=2480, height=3507) -> PageGeometry:
    return PageGeometry(width, height, True, rules=[300, 355, 520, 1900, 2200, 2450],
                        quantity=ColumnRead(300, 355, quantity), catalog=ColumnRead(355, 520, catalog),
                        description=ColumnRead(520, 1900, description))


# The Field Devices strip of EP-30784's FAS sheet, as the columns read it.
FIELD_DEVICES = _geometry(
    quantity=[(510, "2396", 95), (550, "281", 95), (590, "525", 93)],
    catalog=[(510, "SIGA-OSD-FCN", 90), (550, "SIGA-HRD-FCN", 90), (590, "SIGA-OSHD-FCN", 88)],
    description=[(510, "Intelligent Photoelectric Smoke Detector", 92), (550, "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", 91),
                 (590, "Intelligent 3D Multisensor Detector - Photoelectric, Heat", 90)],
)


def _model_row(qty, catalog, description, y, height=34):
    return {"kind": "item", "quantity": qty, "catalog_no": catalog, "description": description, "readable": True,
            "box": [300, y - height // 2, 2450, y + height // 2]}


# --- TEST 5 / TEST 9: exact part and quantity, adjacent rows by geometry ----------------------------


def test_a_row_printed_as_read_is_high_confidence_and_adjacent_rows_keep_their_own_quantities():
    smoke = row_geometry.corroborate(_model_row("2396", "SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector", 510), FIELD_DEVICES)
    heat = row_geometry.corroborate(_model_row("281", "SIGA-HRD-FCN", "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", 550), FIELD_DEVICES)
    for evidence in (smoke, heat):
        c = evidence["components"]
        assert c["part_number_exact"] and c["quantity_inside_expected_column"] and c["same_row_alignment"]
        assert c["single_quantity_candidate"] and not c["neighbor_conflict"] and not c["quantity_disagreement"]
        assert evidence["level"] == "high", evidence
    # The model's box drifted 60 px down onto the next row: the part number
    # locates the row, and the quantity is still its own.
    drifted = row_geometry.corroborate(_model_row("281", "SIGA-HRD-FCN", "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", 610), FIELD_DEVICES)
    assert drifted["located"] and drifted["y"] == 550 and drifted["components"]["quantity_inside_expected_column"]
    # The neighbour's quantity claimed for this row: the column says otherwise.
    swapped = row_geometry.corroborate(_model_row("2396", "SIGA-HRD-FCN", "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", 550), FIELD_DEVICES)
    assert swapped["components"]["quantity_disagreement"] and swapped["level"] == "low"


def test_the_same_quantity_under_another_part_number_is_not_corroborated():
    """The model reads SIGA-HRD-FCN = 2396 where the sheet has SIGA-OSD-FCN
    = 2396 and SIGA-HRD-FCN = 281: the part number locates the row the
    model named, and that row's quantity is not 2396."""
    wrong_part = row_geometry.corroborate(_model_row("2396", "SIGA-HRD-FCN", "Intelligent Photoelectric Smoke Detector", 510), FIELD_DEVICES)
    assert wrong_part["y"] == 550 and wrong_part["components"]["quantity_disagreement"]
    assert not wrong_part["components"]["description_alignment"] and wrong_part["level"] == "low"
    # A part number the columns never show at all near the row: a disagreement.
    unknown = row_geometry.corroborate(_model_row("2396", "SIGA-PS", "Intelligent Photoelectric Smoke Detector", 510), FIELD_DEVICES)
    assert unknown["components"]["part_number_disagreement"] and not unknown["components"]["part_number_exact"]


# --- TEST 7 / TEST 8: numbers in descriptions and part numbers are not quantities ------------------


SUPER_DUCT = _geometry(
    quantity=[(400, "4", 95), (470, "8", 95)],
    catalog=[(430, "SIGA-SD", 90), (460, "SD-T42", 90), (470, "3-SDDC2", 90)],
    description=[(400, "Super Duct Detector", 92), (430, "( 1 ) SuperDuct", 90),
                 (460, "( 1 ) Duct Detector Accessory, 42 in. Sampling Tube", 90), (470, "Dual loop SIGA Data Controller", 91)],
)


def test_a_number_inside_a_description_is_not_a_quantity():
    """'42 in.' is in the description column; the row's quantity is the
    inline '( 1 )'. A reading of 42 is contradicted, a reading of 1 is
    corroborated."""
    forty_two = row_geometry.corroborate(_model_row("42", "SD-T42", "Duct Detector Accessory, 42 in. Sampling Tube", 460), SUPER_DUCT)
    assert not forty_two["components"]["quantity_inside_expected_column"]
    assert forty_two["components"]["quantity_disagreement"] and forty_two["level"] == "low"
    one = row_geometry.corroborate(_model_row("1", "SD-T42", "Duct Detector Accessory, 42 in. Sampling Tube", 460), SUPER_DUCT)
    assert one["inline"] == "1" and one["components"]["quantity_inside_expected_column"] and one["level"] == "high"


def test_a_part_number_with_digits_is_not_a_quantity():
    """3-SDDC2's digits are in the catalog column; the quantity column reads 8."""
    right = row_geometry.corroborate(_model_row("8", "3-SDDC2", "Dual loop SIGA Data Controller", 470), SUPER_DUCT)
    assert right["components"]["quantity_inside_expected_column"] and right["components"]["part_number_exact"]
    assert right["level"] == "high"
    two = row_geometry.corroborate(_model_row("2", "3-SDDC2", "Dual loop SIGA Data Controller", 470), SUPER_DUCT)
    assert two["components"]["quantity_disagreement"] and not two["components"]["quantity_inside_expected_column"]
    # The parent row's 4 taken for SIGA-SD's quantity: SIGA-SD's own is the inline 1.
    parent_qty = row_geometry.corroborate(_model_row("4", "SIGA-SD", "SuperDuct", 430), SUPER_DUCT)
    assert parent_qty["components"]["quantity_disagreement"] and parent_qty["inline"] == "1"


def test_without_page_geometry_the_evidence_is_unknown_not_wrong():
    evidence = row_geometry.corroborate(_model_row("8", "3-SDDC2", "Dual loop SIGA Data Controller", 470),
                                        PageGeometry(2480, 3507, False, "no recognised column layout"))
    assert evidence["level"] == "unknown" and evidence["score"] == 0.0


# --- band duplicates ---------------------------------------------------------------------------------


def test_a_row_reported_by_two_adjacent_bands_is_one_row_and_a_repeat_in_one_band_is_two():
    width, height = 2480, 3507
    # The band-1 / band-2 boundary of a 3507 px page sent at 1600 wide is at 1215 page px.
    first = _model_row("1", "4-COMREL", "Common Relay Module", 1160)
    again = _model_row("1", "4-COMREL", "Common Relay Module", 1229)
    other = _model_row("8", "3-SDDC2", "Dual loop SIGA Data Controller", 1196)
    assert sheet_reader.band_of(1160, width, height) == 0 and sheet_reader.band_of(1229, width, height) == 1
    dropped = sheet_reader.band_duplicates([first, again, other], width, height)
    assert dropped == {id(again)}
    # SIGA-CT2 quoted under two headings 278 px apart, both in band 3: two rows.
    ct2_a = _model_row("1", "SIGA-CT2", "Dual Input Module", 2745)
    ct2_b = _model_row("1", "SIGA-CT2", "Dual Input Module", 3022)
    assert sheet_reader.band_duplicates([ct2_a, ct2_b], width, height) == set()


# --- TEST 10 / TEST 11: groups from heading intervals; unresolved rather than guessed --------------


def test_the_group_is_the_heading_interval_the_row_sits_in():
    page = {"rows": [
        {"kind": "heading", "description": "EST4 Main Fire Alarm Control Panel", "box": [300, 100, 2450, 130], "quantity": "", "catalog_no": "", "readable": True},
        {"kind": "heading", "description": "Field Devices", "box": [300, 420, 2450, 450], "quantity": "", "catalog_no": "", "readable": True},
        {"kind": "heading", "description": "Modules", "box": [300, 900, 2450, 930], "quantity": "", "catalog_no": "", "readable": True},
    ]}
    events = sheet_reader._group_events(page, "B1 BUILDING", None)
    assert sheet_reader._group_at(events, 50) == ("B1 BUILDING", None)
    assert sheet_reader._group_at(events, 300) == ("B1 BUILDING", "EST4 Main Fire Alarm Control Panel")
    assert sheet_reader._group_at(events, 600) == ("B1 BUILDING", "Field Devices")
    assert sheet_reader._group_at(events, 950) == ("B1 BUILDING", "Modules")


def test_a_row_after_a_panels_components_has_an_unresolved_group_not_the_last_heading():
    page = {"rows": [
        {"kind": "heading", "description": "Booster Power Supply", "box": [300, 100, 2450, 130], "quantity": "", "catalog_no": "", "readable": True},
    ]}
    parent = _model_row("8", "", "Remote power supply cabinet wallbox, red.", 160)
    bps = _model_row("1", "BPS10A/230", "10 Amp Booster Power Supply, 220V", 200)
    battery = _model_row("2", "12V10A", "10Ah Sealed Lead Acid Battery - 12 Vdc", 240)
    kit = _model_row("4", "6538-G5", "Call for Assistance Kit", 300)
    items = [parent, bps, battery, kit]
    events = sheet_reader._group_events(page, None, None)
    groups = sheet_reader.resolve_groups(page, items, events, {id(parent): False, id(bps): True, id(battery): True, id(kit): False})
    assert groups[id(bps)] == {"section": None, "heading": "Booster Power Supply", "resolved": True, "why": "the heading in force above the row"}
    assert groups[id(kit)]["resolved"] is False and groups[id(kit)]["heading"] == "Booster Power Supply"
    # A flat list under a heading: every row has its quantity in the column, all belong.
    flat = [_model_row("2396", "SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector", 160),
            _model_row("281", "SIGA-HRD-FCN", "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", 200)]
    assert all(g["resolved"] for g in sheet_reader.resolve_groups(page, flat, events, {id(r): False for r in flat}).values())
    # The geometry could not tell: the heading in force stands.
    assert all(g["resolved"] for g in sheet_reader.resolve_groups(page, items, events, {}).values())


def test_an_unresolved_group_sends_the_row_to_review_with_its_values(client, db_session, tmp_path, ai, recording, monkeypatch):
    """SIGA-CT1 follows a component row: the geometry says its quantity is
    in the column; its group is for the engineer, its values intact."""
    sheet = tmp_path / "EP-72001 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    page = {"rows": [
        _row("heading", "", "", "Booster Power Supply", 50),
        _row("item", "1", "4-CPU", "Central Processor Module", 100),
        _row("item", "120", "SIGA-PS", "Photoelectric smoke detector", 140),
        _row("item", "14", "SIGA-CT1", "Single input module", 180),
    ], "has_line_items": True}
    inline = {1: False, 2: True, 3: False}

    def fake_evidence(rows, image):
        return {index: {"score": 0.9, "level": "high", "components": {}, "inline": inline[index]} for index in inline}

    monkeypatch.setattr(sheet_reader, "_page_evidence", fake_evidence)
    recording.answers = [page, page]
    project = _project(db_session, sheet, ep="72001")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"]: i["group_heading"] for i in body["items"]} == {"4-CPU": "Booster Power Supply", "SIGA-PS": "Booster Power Supply"}
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    issue = db_session.query(ExtractionIssue).filter(ExtractionIssue.run_id == run.id).one()
    assert issue.detail["reason_code"] == ReviewReason.GROUP_UNRESOLVED.value
    assert issue.detail["catalog_no"] == "SIGA-CT1" and issue.detail["primary"]["quantity"] == "14"
    assert issue.detail["evidence"]["level"] == "high" and run.state == "completed"
    assert recording.calls == 2, "no close-up: the rows agree, only the group is open"


def test_the_evidence_travels_with_the_line(client, db_session, tmp_path, ai, recording, monkeypatch):
    sheet = tmp_path / "EP-72002 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    monkeypatch.setattr(sheet_reader, "_page_evidence",
                        lambda rows, image: {i: {"score": 0.95, "level": "high", "components": {"part_number_exact": True}, "inline": False}
                                             for i, r in enumerate(rows) if r["kind"] == "item"})
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="72002")
    _login(client)
    body = client.post(f"/projects/{project.id}/boq/ensure").json()
    line = next(i for i in body["items"] if i["catalog_no"] == "SIGA-PS")
    assert line["raw_values"]["evidence"]["level"] == "high" and line["raw_values"]["row_id"] == "p1r2"
