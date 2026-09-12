from datetime import datetime, timezone
import pymupdf
from app.services.document_control import parse_page, read_decision, scan_document_control

NOW = datetime.now(timezone.utc)
FORM = """Material Submittal for Fire Alarm
MAS Reference No.: BBY006-GME-MAS-EL-FA-0001
MAS Rev.: 00
ENGINEERING CONSULTANT COMMENTS AND APPROVAL STATUS
"""

def pdf(path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        for text in pages:
            page = doc.new_page()
            page.insert_text((30, 30), text)
        doc.save(path)


def test_blank_options_are_not_replies():
    assert read_decision("Approved (A)\nApproved as Noted (B)\nRe-submit (C)") == ("UR", None)
    assert read_decision("(A) Approved (B) Approved as noted (C) Re-submit") == ("UR", None)
    assert read_decision("\u2610 Approved (A)\n\u2610 Re-submit (C)") == ("UR", None)
    assert read_decision("\u2612 Approved as Noted (B)")[0] == "ANN"
    assert read_decision("Consultant status: Rejected")[0] == "rejected"
    assert read_decision("Review status: Not approved")[0] == "rejected"
    assert read_decision("(B) Approved As Noted")[0] == "ANN"
    assert read_decision("Consultant decision: Approved")[0] == "approved"


def test_prepared_material_defaults_to_r0_ur():
    row, = parse_page(FORM, "prepared.pdf", NOW, 1)
    assert (row.reference, row.revision, row.status) == ("BBY006-GME-MAS-EL-FA-0001", "R0", "UR")
    assert row.system_code == "FAS"


def test_schedule_covers_all_listed_floors_and_systems():
    rows = parse_page("DWG NO:\nDRAWING TITLE:\nFIRE ALARM SUBMISSION\nEML SUBMISSION\nFA 101\nBASEMENT-4 FLOOR PLAN\n17/04/2026\nFA 102\nTYPICAL 2ND TO 4TH FLOOR PLAN\n24/04/2026", "schedule.pdf", NOW, 1)
    assert len(rows) == 4
    assert {row.system_code for row in rows} == {"FAS", "EML"}
    assert all(row.status == "UR" and row.revision == "R0" for row in rows)
    assert {row.floor for row in rows} == {"BASEMENT-4", "TYPICAL 2ND TO 4TH FLOOR"}


def test_unrelated_drawing_folder_file_excluded():
    assert not parse_page("LV Technical Drawing\nProvisionally Approved\nConnected load 1600 kW", "Drawings/approved.pdf", NOW, 1)


def test_replies_merge_by_reference_revision_and_refresh(tmp_path):
    pdf(tmp_path / "prepared.pdf", [FORM])
    pdf(tmp_path / "reply.pdf", [FORM, "Consultant comments\nReview status: Rejected"])
    pdf(tmp_path / "r1.pdf", [FORM.replace("Rev.: 00", "Rev.: 01") + "\nReview status: Approved as Noted"])
    rows, warnings = scan_document_control(tmp_path, use_ocr=False)
    assert not warnings
    assert [(r.revision, r.status) for r in rows] == [("R0", "rejected"), ("R1", "ANN")]
    assert rows[0].page == 2
    (tmp_path / "reply.pdf").unlink()
    rows, _ = scan_document_control(tmp_path, use_ocr=False)
    assert rows[0].status == "UR"


def test_drawing_title_block_and_floor():
    row, = parse_page("Drawing No: BBY006-GME-SDW-FA-B4-0001\nDrawing title: Fire Alarm Layout - B4\nRevision: 01\nConsultant status: Approved", "layout.pdf", NOW, 1)
    assert (row.category, row.floor, row.revision, row.status) == ("drawings", "B4", "R1", "approved")


def test_inline_ocr_stamp_and_conflicting_decisions():
    assert read_decision("11. Samples are required. (B) Approved As Noted")[0] == "ANN"
    assert read_decision("Consultant status: Approved\nConsultant status: Rejected")[0] == "UR"


def test_floor_normalization():
    from app.services.document_control import normalize_floor
    assert normalize_floor("BASEMENT-4") == normalize_floor("B4")
    assert normalize_floor("3RD FLOOR") == normalize_floor("L3")


def test_ocr_reply_updates_matching_revision(tmp_path, monkeypatch):
    import app.services.document_control as scanner
    pdf(tmp_path / "prepared.pdf", [FORM, "Consultant comments"])
    monkeypatch.setattr(scanner, "ocr_available", lambda: True)
    monkeypatch.setattr(scanner, "_ocr_page", lambda page: "Consultant status: Approved as Noted")
    rows, warnings = scanner.scan_document_control(tmp_path)
    assert not warnings
    assert len(rows) == 1
    assert rows[0].revision == "R0"
    assert rows[0].status == "ANN"
