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


def title_block(path, number, title, layout, history, box_revision="00"):
    """A sheet drawn the way the office draws one: labels, then the values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        page = doc.new_page(width=900, height=900)
        page.insert_text((400, 300), "Rev  Date  Description")
        for index, (revision, date, note) in enumerate(history):
            page.insert_text((400, 320 + index * 20), f"{revision}  {date}  {note}")
        page.insert_text((400, 600), "Purpose of Issue:")
        page.insert_text((400, 620), "DRAWING TITLE")
        page.insert_text((410, 645), title)
        page.insert_text((410, 665), layout)
        for offset, label in enumerate(["SCALE", "DRAWN", "CHECKED", "DATE", "SIZE", "REV. NO."]):
            page.insert_text((400 + offset * 70, 700), label)
        issued = history[-1][1] if history else "06.08.2026"
        for offset, value in enumerate(["1:100", "IS", "RAMADAN", issued, "A0", box_revision]):
            page.insert_text((400 + offset * 70, 720), value)
        page.insert_text((400, 760), number)
        doc.save(path)


def test_shop_drawing_is_read_from_its_own_title_block(tmp_path):
    title_block(tmp_path / "R1" / "ground.pdf", "BBY006-GME-SDW-FP-FA-POD-BGF-010002",
                "GROUND FLOOR PLAN", "FIRE ALARM LAYOUT",
                [("00", "06.08.2026", "ISSUED FOR APPROVAL"), ("01", "10.09.2026", "REVISED AS PER NEW ARCH")])
    rows, _ = scan_document_control(tmp_path, use_ocr=False)
    row, = rows
    assert (row.category, row.system_code, row.reference) == ("drawings", "FAS", "BBY006-GME-SDW-FP-FA-POD-BGF-010002")
    assert (row.revision, row.status, str(row.issued)) == ("R1", "UR", "2026-09-10")
    assert row.floor == "GROUND FLOOR"
    # The revision box was left at 00 while the history says 01: reported, not followed.
    assert row.note and "01" in row.note


def test_each_floor_is_logged_once_at_the_revision_last_issued(tmp_path):
    for folder, revision, date in [("R0", "00", "06.08.2026"), ("R1", "01", "10.09.2026"), ("copies", "00", "06.08.2026")]:
        title_block(tmp_path / folder / "podium.pdf", "BBY006-GME-SDW-FP-FA-POD-P01-010003",
                    "PODIUM-1 FLOOR PLAN", "FIRE ALARM LAYOUT",
                    [("00", "06.08.2026", "ISSUED FOR APPROVAL")] + ([("01", date, "REVISED")] if revision == "01" else []),
                    box_revision=revision)
    rows, _ = scan_document_control(tmp_path, use_ocr=False)
    assert [(row.revision, row.floor) for row in rows] == [("R0", "PODIUM-1 FLOOR"), ("R1", "PODIUM-1 FLOOR")]
    assert {row.group_reference for row in rows} == {"BBY006-GME-SDW-FP-FA-POD-P01-010003"}


def test_another_trade_drawing_is_not_ours_to_log(tmp_path):
    title_block(tmp_path / "slab.pdf", "BBY006-GME-SDW-ME-BL-ZZZ-L03-010099",
                "LEVEL 03 FLOOR PLAN", "MEP SLAB OPENING LAYOUT", [("00", "06.02.2026", "ISSUED")])
    assert scan_document_control(tmp_path, use_ocr=False)[0] == []


def test_a_form_bound_into_another_submission_is_not_a_submission(tmp_path):
    sample = "Sample Approval Form\nSAF Reference No.: BBY006-GME-SAR-EL-FA-0001\nSAF Rev.: 00\nSample Approval Request for Fire Alarm & Voice Evacuation System"
    pdf(tmp_path / "sample.pdf", [sample, "SAMPLE BOARD PHOTO", FORM])
    rows, _ = scan_document_control(tmp_path, use_ocr=False)
    row, = rows
    assert (row.category, row.reference) == ("samples", "BBY006-GME-SAR-EL-FA-0001")


def test_reply_to_comments_is_not_a_submittal(tmp_path):
    pdf(tmp_path / "reply.pdf", ["Reply to MS Consultant Comments\nBBY006-GME-MAS-EL-FA-0002\nSN Consultant Comments Al Arabia SSD Reply"])
    assert scan_document_control(tmp_path, use_ocr=False)[0] == []
