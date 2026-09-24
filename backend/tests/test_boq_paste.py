"""A BOQ copied out of a spreadsheet, read into lines."""

import pytest

from app.services import boq_paste
from tests.conftest import login, make_user

TAB = chr(9)
NL = chr(10)


def sheet(*rows: tuple[str, ...]) -> str:
    return NL.join(TAB.join(cell for cell in row) for row in rows)


def test_a_boq_pasted_out_of_excel_comes_back_as_lines_under_their_headings():
    """The shape of every BOQ: a heading, the parts quoted under it, then
    the next heading. A heading is a row with words and nothing quoted
    against it, and it belongs on the lines that follow rather than being
    a line of its own."""
    pasted = boq_paste.read(sheet(
        ("SN", "Description", "Model / Part No.", "Manufacturer", "Qty", "Unit"),
        ("", "MAIN FIRE ALARM CONTROL PANEL & ACCESSORIES", "", "", "", ""),
        ("1", "Central Processor Unit", "4-CPU", "EDWARDS", "1", "Nos"),
        ("2", "Loop Card", "3-SDDC2", "EDWARDS", "4", "Nos"),
        ("", "NOTIFICATION DEVICES", "", "", "", ""),
        ("3", "Smoke Detector", "SIGA-PS", "EDWARDS", "48", "Nos"),
    ))
    assert pasted.header_row and pasted.heading_rows == 2
    assert pasted.headings == ["MAIN FIRE ALARM CONTROL PANEL & ACCESSORIES", "NOTIFICATION DEVICES"]
    # The running number down the left is not a field.
    assert "description" in pasted.columns and pasted.columns["description"] == 1
    assert [(line.group_heading, line.catalog_no, line.quantity) for line in pasted.lines] == [
        ("MAIN FIRE ALARM CONTROL PANEL & ACCESSORIES", "4-CPU", "1"),
        ("MAIN FIRE ALARM CONTROL PANEL & ACCESSORIES", "3-SDDC2", "4"),
        ("NOTIFICATION DEVICES", "SIGA-PS", "48"),
    ]
    assert all(not line.problems for line in pasted.lines)


def test_a_quantity_is_read_the_way_the_extractor_reads_one():
    """The same parser the Design Sheets go through, so a pasted quantity
    and an extracted one mean the same thing -- and one that is not a
    quantity is kept as it was written and flagged, not stored as a number
    it is not."""
    pasted = boq_paste.read(sheet(
        ("Description", "Part No", "Qty"),
        ("Loop Card", "3-SDDC2", "1,250"),
        ("Detector", "SIGA-PS", "48 Nos"),
        ("Power Supply", "4-PPS/M", "2 x 10"),
        ("Cable", "FRC-2C", ""),
    ))
    by_part = {line.catalog_no: line for line in pasted.lines}
    assert by_part["3-SDDC2"].quantity == "1250"
    # The unit comes off the quantity where the sheet wrote it there.
    assert (by_part["SIGA-PS"].quantity, by_part["SIGA-PS"].unit) == ("48", "Nos")
    assert by_part["4-PPS/M"].quantity == "2 x 10"
    assert "not a quantity" in by_part["4-PPS/M"].problems[0]
    assert by_part["FRC-2C"].problems == ["No quantity"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,250.00", "1250.00"), ("AED 1,250", "1250"), ("1 250", "1250"), ("12.50", "12.50"),
     ("-", None), ("included", None), ("", None), (None, None), ("-5", None)],
)
def test_a_rate_is_read_as_a_spreadsheet_writes_it(raw, expected):
    """A BOQ writes a rate with its currency and its thousands separator,
    and writes "-" or "included" where a line carries none."""
    found = boq_paste.price(raw)
    assert (str(found) if found is not None else None) == expected


def test_a_paste_with_no_header_is_read_from_the_shape_of_its_lines():
    """An engineer copies the rows without the header as often as with it.
    The widest column is the description, the one holding numbers is the
    quantity, and the short coded one is the part number."""
    pasted = boq_paste.read(sheet(
        ("1", "Central processor unit for the fire alarm panel", "4-CPU", "2"),
        ("2", "Addressable loop card with isolators fitted", "3-SDDC2", "4"),
        ("3", "Photoelectric smoke detector, ceiling mounted", "SIGA-PS", "48"),
    ))
    assert not pasted.header_row
    assert [line.catalog_no for line in pasted.lines] == ["4-CPU", "3-SDDC2", "SIGA-PS"]
    assert [line.quantity for line in pasted.lines] == ["2", "4", "48"]
    assert all("processor" in line.description or "loop" in line.description
               or "smoke" in line.description for line in pasted.lines)


def test_a_description_carrying_a_comma_is_one_cell():
    """Pasted as CSV rather than from a spreadsheet, a description with a
    comma in it is still one column -- split on the comma it would push
    the part number and the quantity across a column each."""
    pasted = boq_paste.read(NL.join([
        "Description,Part No,Qty",
        '"Smoke detector, photoelectric",SIGA-PS,48',
    ]))
    assert len(pasted.lines) == 1
    assert pasted.lines[0].description == "Smoke detector, photoelectric"
    assert (pasted.lines[0].catalog_no, pasted.lines[0].quantity) == ("SIGA-PS", "48")


def test_blank_rows_and_a_stray_note_are_left_out():
    """A spreadsheet has spacer rows and a note down the margin. Neither is
    a line and neither is a heading over the lines that follow."""
    pasted = boq_paste.read(sheet(
        ("Description", "Part No", "Qty"),
        ("MAIN PANEL", "", ""),
        ("Central Processor Unit", "4-CPU", "1"),
        ("", "", ""),
        ("", "", "checked by RM"),
    ))
    assert [line.catalog_no for line in pasted.lines] == ["4-CPU"]
    assert pasted.lines[0].group_heading == "MAIN PANEL"
    assert pasted.skipped_rows == 1
    assert boq_paste.read("").lines == []
    assert boq_paste.read("   ").lines == []


def test_the_pasted_lines_come_back_for_review_and_are_not_saved(client, db_session, tmp_path):
    """Nothing is written by the paste: the lines go to the page, where the
    engineer looks them over and saves them with the rest of the BOQ. A
    paste read wrongly should cost a glance, not a revision."""
    from app.core.config import get_settings
    from app.models import RoleEnum

    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)
    created = client.post("/projects", json={
        "ep_number": "40500", "project_name": "Pasted BOQ", "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": False}],
    }).json()

    body = {"text": sheet(
        ("Description", "Part No", "Qty", "Unit", "Unit Rate"),
        ("MAIN FIRE ALARM PANEL", "", "", "", ""),
        ("Central Processor Unit", "4-CPU", "1", "Nos", "AED 1,250.00"),
        ("Loop Card", "3-SDDC2", "4", "Nos", "-"),
    ), "system_code": "FAS"}
    read = client.post(f"/projects/{created['id']}/boq/paste", json=body)
    assert read.status_code == 200, read.text
    out = read.json()
    assert [line["catalog_no"] for line in out["lines"]] == ["4-CPU", "3-SDDC2"]
    assert all(line["system_code"] == "FAS" for line in out["lines"])
    assert all(line["group_heading"] == "MAIN FIRE ALARM PANEL" for line in out["lines"])
    assert out["lines"][0]["unit_price"] == "1250.00"
    assert out["lines"][1]["unit_price"] is None
    assert out["heading_rows"] == 1

    # Read, not written: the BOQ is still empty until the page saves.
    assert client.get(f"/projects/{created['id']}/boq").json() == []

    # And a reader cannot paste one.
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert client.post(f"/projects/{created['id']}/boq/paste", json=body).status_code == 403
