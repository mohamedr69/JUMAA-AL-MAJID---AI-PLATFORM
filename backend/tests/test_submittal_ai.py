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

    # A contractor's own form: no "MAS Reference No.", headed MATERIAL
    # SUBMITTAL with a "Submittal No." (EP-29495's central battery submittal).
    own = root / "approved" / "MS" / "ICC-DLRC-SIG2-MAR-MEP-0060-02 - CENTRAL BATTERY SYSTEM.pdf"
    own.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "Consultant\nM/s. Al Hilal Engineering Consultants\nMATERIAL SUBMITTAL\n"
                                         "Submittal No. ICC-DLRC-SIG2-MAR-MEP-0060\nRev. 02\nDate: 26-08-2026", fontsize=9)
    doc.save(own)
    doc.close()
    # The same contractor's transmittal for a shop drawing: not a form,
    # though its checklist mentions material submittals.
    drawing_form = root / "approved" / "SD" / "ICC-DLRC-SIG2-SD-MEP-0081-00 - EMERGENCY LIGHTING LAYOUT.pdf"
    drawing_form.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "Consultant\nM/s. Al Hilal Engineering Consultants\nSHOP DRAWING SUBMITTAL\n"
                                         "Submittal No. ICC-DLRC-SIG2-SD-MEP-0081\nRev. 00\nType: Material Submittal / Drawing",
                               fontsize=9)
    doc.save(drawing_form)
    doc.close()
    # JAM's own package cover, in a submittal folder: a form to read.
    cover = root / "06. MS" / "CBS" / "CBS MS R00.pdf"
    cover.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "Project: MATERIAL SUBMITTAL FOR CENTRAL BATTERY SYSTEM.\nCLIENT\nCONSULTANT\nRevision 0",
                               fontsize=9)
    doc.save(cover)
    doc.close()

    found, warnings = submittal_reader.candidates(root)
    assert [str(p.relative_to(root)) for p in found] == [
        str(Path("03- MS/01- FA/form.pdf")), str(Path("06. MS/CBS/CBS MS R00.pdf")), str(Path("08- approval/MS/scan.pdf")),
        str(Path("approved/MS/ICC-DLRC-SIG2-MAR-MEP-0060-02 - CENTRAL BATTERY SYSTEM.pdf")),
    ]
    assert warnings == []


def test_the_model_names_the_system_and_emergency_lighting_goes_by_every_name():
    # The model's conclusion first: a central battery submittal filed under
    # the fire alarm folder is ELS.
    reading = {"title": "Material Submittal for Central Battery System", "system": "CBS", "system_code": "ELS"}
    assert submittal_reader._system_code(reading, "MS/FA/Revised/form.pdf") == "ELS"
    assert submittal_reader._normalise({**reading, "reply": {}})["system_code"] == "ELS"
    assert submittal_reader._normalise({**reading, "system_code": "els", "reply": {}})["system_code"] == "ELS"
    assert submittal_reader._normalise({**reading, "system_code": "Lighting", "reply": {}})["system_code"] == ""
    # Without the model's code, the wording: every name emergency lighting goes by.
    for title in ("Self Contained Monitoring Emergency & Exit Light System", "Self-Monitored Emergency Light",
                  "Central Battery Unit", "CBS for Emergency Lighting", "Exit Light Fittings"):
        assert submittal_reader._system_code({"title": title, "system": ""}, "06. MS/Revised/form.pdf") == "ELS", title
    assert submittal_reader._system_code({"title": "Fire Detection & Alarm System", "system": ""}, "MS/form.pdf") == "FAS"
    # The folder, in its spellings.
    for folder in ("06. MS/EML", "MS/EL", "MS/Emergency Lighting", "MS/CBS"):
        assert submittal_reader._system_code({"title": "Material Submittal", "system": ""}, f"{folder}/form.pdf") == "ELS", folder
    # OTHER: not a system the platform tracks, unless the folder says otherwise.
    assert submittal_reader._system_code({"title": "Fire Pump", "system": "", "system_code": "OTHER"}, "MS/Pumps/form.pdf") is None
