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
    assert {row.system_code for row in rows} == {"FAS", "ELS"}
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
    # With the reply gone R0 has no decision on file -- but R1 exists, so R0
    # was superseded, not left with the consultant.
    assert rows[0].status == "SUPERSEDED" and rows[1].status == "ANN"


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


# --- a reply to the consultant's comments is evidence, not an entry ---------

# Shortened from EP-30784's "03- MS/02- EML/Reply to MS Consultant Comments.pdf".
# It quotes the submittal it answers, which is what used to register it as
# that submittal -- displacing the Materials Submittal Form of the same
# reference and listing the number twice.
REPLY = """SN
Consultant Comments
Al Arabia SSD Reply
REMARKS
1
All luminaires in wet areas must have a minimum ingress protection rating of IP65.
Comply
Reply to Consultant Comments on EML submittal
Ref No : BBY006-GME-MAS-EL-LI-0001 - R.00
"""


def test_reply_sheet_is_not_registered_as_the_submittal_it_answers():
    row, = parse_page(REPLY, "Reply to MS Consultant Comments.pdf", NOW, 1)
    assert row.category == "reply"
    assert row.source == "reply"
    # It still names what it answers, so a decision on it can be attached.
    assert row.reference == "BBY006-GME-MAS-EL-LI-0001"


def test_a_reply_never_displaces_the_submittal_form(tmp_path):
    pdf(tmp_path / "01 form.pdf", [FORM.replace("FA-0001", "LI-0001")])
    pdf(tmp_path / "02 reply.pdf", [REPLY])

    rows, _ = scan_document_control(tmp_path, use_ocr=False)

    submittals = [row for row in rows if row.category == "submittals"]
    assert [(r.reference, r.revision) for r in submittals] == [("BBY006-GME-MAS-EL-LI-0001", "R0")]
    # The form's own title, not the reply's.
    assert submittals[0].name.startswith("Fire Alarm")
    assert "Reply" not in submittals[0].name
    # The reply is not a register row of any kind.
    assert not [row for row in rows if row.category == "reply"]


def test_a_reply_carrying_a_decision_settles_the_submittal(tmp_path):
    pdf(tmp_path / "01 form.pdf", [FORM.replace("FA-0001", "LI-0001")])
    pdf(tmp_path / "02 reply.pdf", [REPLY + "\nConsultant status: Approved as Noted\n"])

    rows, _ = scan_document_control(tmp_path, use_ocr=False)

    submittal, = [row for row in rows if row.category == "submittals"]
    assert submittal.status == "ANN"


def test_a_reply_alone_is_not_an_approval(tmp_path):
    """The contractor answering comments says nothing about the outcome."""
    pdf(tmp_path / "01 form.pdf", [FORM.replace("FA-0001", "LI-0001")])
    pdf(tmp_path / "02 reply.pdf", [REPLY])

    rows, _ = scan_document_control(tmp_path, use_ocr=False)

    submittal, = [row for row in rows if row.category == "submittals"]
    assert submittal.status == "UR"


# --- drawings: the title block, read by position ---------------------------

# A CAD title block exports its labels and its values as separate runs, so the
# line after "DRAWING TITLE" is the next label. EP-30784's sheets look like
# this: reference, title, layout, date, size, revision.
TITLE_BLOCK = """GENERAL NOTES:
1)          ALL DIMENSIONS ARE IN MILLIMETERS.
DRAWING NO.
REV
1.)    ALL DIMENSIONS TO BE VERIFIED ON SITE
PROJECT CODE
DRAWING CODE
DRAWING TITLE
SCALE
PROJECT NAME
BBY006-GME-SDW-FP-FA-POD-BGF-010002
GROUND FLOOR PLAN
FIRE ALARM LAYOUT
06.08.2026
A0
00
"""


def test_drawing_title_and_floor_come_from_the_title_block():
    row, = parse_page(TITLE_BLOCK, r"C:\a\1.FAVE\R0\05. Ground Floor\d.pdf", NOW, 1)
    assert row.category == "drawings"
    # Was "SCALE" -- the label after the "DRAWING TITLE" label.
    assert row.name == "GROUND FLOOR PLAN"
    # Was None on every shop drawing in the archive.
    assert row.floor == "GROUND FLOOR"
    # The layout line names the system on the sheet itself.
    assert row.system_code == "FAS"


def test_a_numbered_note_is_not_a_revision():
    """"REV" is followed by the general notes, whose "1.)" read as R1."""
    row, = parse_page(TITLE_BLOCK, r"C:\a\1.FAVE\R0\05. Ground Floor\d.pdf", NOW, 1)
    assert row.revision == "R0"


def test_the_submission_folder_gives_the_drawing_revision():
    """Contractors leave the sheet's own revision at 00 across resubmissions
    -- on EP-30784 the R0 and R1 submissions of every FAVE drawing both say
    00 -- so the folder is what separates one submission from the next."""
    r0, = parse_page(TITLE_BLOCK, r"C:\a\1.FAVE\R0\05. Ground Floor\d.pdf", NOW, 1)
    r1, = parse_page(TITLE_BLOCK, r"C:\a\1.FAVE\R1\05. Ground Floor\d.pdf", NOW, 1)
    assert (r0.revision, r1.revision) == ("R0", "R1")
    # Same drawing, so the two are one row with two revisions, not two rows.
    assert r0.reference == r1.reference


def test_a_submission_read_off_two_pages_is_one_row(tmp_path):
    """A shop drawing is submitted as a form page plus the sheet. The form
    has the reference and the stamp, the sheet has the title and the floor;
    keeping only the higher-ranked page lost the other half."""
    form = """Shop Drawing Submittal Form
SDW Reference No. BBY006-GME-SDW-FP-FA-POD-BGF-010002
SDW Rev.: 00
Consultant status: Approved as Noted
"""
    pdf(tmp_path / "R1" / "submission.pdf", [form, TITLE_BLOCK])

    rows, _ = scan_document_control(tmp_path, use_ocr=False)

    row, = [r for r in rows if r.category == "drawings"]
    assert row.revision == "R1"
    assert row.status == "ANN"          # from the form page
    assert row.name == "GROUND FLOOR PLAN"   # from the sheet
    assert row.floor == "GROUND FLOOR"       # from the sheet
