"""The map the AI draws of a project's material submittals, and which files
it reads at all."""

from pathlib import Path

import pymupdf

from app.ai import submittal_reader
from tests.test_submittal_scanner import _form


def _reading(reference, revision, status="none", *, relative, from_consultant=True, modified="2026-09-01T10:00:00",
             approval=False, title="Material Submittal for Fire Alarm System"):
    return {
        "is_submittal": True, "reference": reference, "revision": revision, "title": title, "system": "Fire Alarm",
        "supplier": "AL ARABIA", "manufacturer": "EDWARDS", "submitted": "20 April 2026",
        "reply": {"present": status != "none", "from_consultant": from_consultant and status != "none", "status": status,
                  "code": "", "consultant": "Al Hilal" if status != "none" else "", "date": "", "evidence": ""},
        "relative": relative, "modified": modified, "in_approval_folder": approval,
    }


def test_a_revision_filed_twice_is_settled_by_the_copy_with_the_consultants_reply():
    readings = [
        _reading("X-MAS-0001", 0, relative="03- MS/form.pdf", modified="2026-09-05T10:00:00"),
        _reading("X-MAS-0001", 0, "approved_as_noted", relative="08- approval/form.pdf", approval=True),
    ]
    result = submittal_reader.build_map(readings)
    (system,) = result["systems"]
    (row,) = system["rows"]
    assert row["cells"]["R0"]["status"] == "ANN" and row["cells"]["R0"]["file"] == "08- approval/form.pdf"
    assert row["cells"]["R0"]["copies"] == 2 and result["actions"] == []
    assert system["system_code"] == "FAS"


def test_a_reply_not_verified_as_the_consultants_is_still_under_review():
    readings = [_reading("X-MAS-0001", 0, "approved", relative="03- MS/form.pdf", from_consultant=False)]
    (row,) = submittal_reader.build_map(readings)["systems"][0]["rows"]
    assert row["cells"]["R0"]["status"] == "UR" and row["cells"]["R0"]["unverified_reply"] is True


def test_a_returned_revision_with_no_later_one_is_an_action():
    readings = [_reading("X-MAS-0002", 0, "resubmit", relative="a.pdf")]
    result = submittal_reader.build_map(readings)
    assert result["actions"] == ["Material submittal required: X-MAS-0002 R0 was returned revise and resubmit; R1 is not filed"]
    assert result["systems"][0]["rows"][0]["action"]

    # R1 filed: it is under review, and there is nothing to ask for.
    readings.append(_reading("X-MAS-0002", 1, relative="b.pdf"))
    result = submittal_reader.build_map(readings)
    (row,) = result["systems"][0]["rows"]
    assert result["revisions"] == ["R0", "R1"]
    assert (row["cells"]["R0"]["status"], row["cells"]["R1"]["status"]) == ("RR", "UR")
    assert row["latest"] == "R1" and result["actions"] == []

    # A rejection with nothing after it, likewise.
    result = submittal_reader.build_map([_reading("X-MAS-0003", 2, "rejected", relative="c.pdf")])
    assert result["actions"] == ["Material submittal required: X-MAS-0003 R2 was returned rejected; R3 is not filed"]
    assert result["revisions"] == ["R0", "R1", "R2"]


def test_no_submittal_at_all_is_an_action():
    result = submittal_reader.build_map([])
    assert result["systems"] == [] and result["actions"] == ["Material submittal required: no material submittal is filed for this project"]
    # A document the model says is not a submittal counts for nothing.
    not_one = {**_reading("", None, relative="drawing.pdf"), "is_submittal": False}
    assert submittal_reader.build_map([not_one])["actions"] == submittal_reader.build_map([])["actions"]


def test_only_forms_and_scanned_documents_named_for_submittals_are_read(tmp_path):
    root = tmp_path / "EP-1"
    _form(root / "03- MS" / "01- FA" / "form.pdf")
    # A scanned form: no text layer, in a submittals folder.
    scanned = root / "08- approval" / "MS" / "scan.pdf"
    scanned.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page()
    doc.save(scanned)
    doc.close()
    # A scanned drawing: no text layer, not in a submittals folder.
    drawing = root / "05- Drawings" / "L01.pdf"
    drawing.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page()
    doc.save(drawing)
    doc.close()
    # A datasheet with text and no MAS reference.
    sheet = root / "03- MS" / "01- FA" / "datasheet.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "SIGA-PS Photoelectric Smoke Detector\nStandby 45 uA", fontsize=9)
    doc.save(sheet)
    doc.close()

    found, warnings = submittal_reader.candidates(root)
    assert [str(p.relative_to(root)) for p in found] == [str(Path("03- MS/01- FA/form.pdf")), str(Path("08- approval/MS/scan.pdf"))]
    assert warnings == []
