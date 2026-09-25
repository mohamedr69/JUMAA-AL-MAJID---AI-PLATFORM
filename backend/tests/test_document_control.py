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
    # The drawings pad the number and the schedules do not. Unpadded, B01
    # and B1 were different floors and never matched each other.
    assert normalize_floor("B01") == normalize_floor("BASEMENT-1")
    assert normalize_floor("L01") == normalize_floor("1ST FLOOR")
    assert normalize_floor("P01") == normalize_floor("PODIUM-1")
    # And a padded number is not folded into a shorter one.
    assert normalize_floor("B10") != normalize_floor("B1")
    assert normalize_floor("L10") == normalize_floor("10TH FLOOR")


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
    keeping only the higher-ranked page lost the other half.

    The floor is one row whichever page each half came off."""
    form = """Shop Drawing Submittal Form
SDW Reference No. BBY006-GME-SDW-FP-FA-POD-BGF-010002
SDW Rev.: 00
Consultant status: Approved as Noted
"""
    pdf(tmp_path / "R1" / "submission.pdf", [form, TITLE_BLOCK])

    rows, _ = scan_document_control(tmp_path, use_ocr=False)

    # One row for the floor, as the log shows it.
    row, = [r for r in rows if r.category == "drawings"]
    assert row.name == "GROUND FLOOR PLAN"   # from the sheet
    assert row.floor == "GROUND FLOOR"       # from the sheet
    # The form prints "SDW Rev.: 00" while the folder says R1, and the
    # drawing is what the revision is read off: the stamped page is the R0
    # it says it is, the sheet beside it the R1 the folder says. Before
    # 2026-09-24 the folder won and both were R1, which made a consultant's
    # verdict on R0 read as a verdict on R1. See the note in
    # document_control.parse_page.
    assert row.revision == "R1" and row.status == "UR"
    assert [(r.revision, r.status) for r in row.superseded] == [("R0", "ANN")]


def test_a_material_approval_request_and_a_shop_drawing_are_controlled_documents():
    """A contractor numbers a material submittal -MAR- (material approval
    request) and a shop drawing -SD- as readily as -MAS- and -SDW-
    (EP-29495): both belong in the log, a method statement quoting its own
    number does not."""
    form = "\n".join(["MATERIAL SUBMITTAL", "Submittal No. ICC-DLRC-SIG2-MAR-MEP-0060", "Rev. 02",
                         "Material Submittal for Central Battery System"])
    sheet = "\n".join(["SHOP DRAWING SUBMITTAL", "Submittal No. ICC-DLRC-SIG2-SD-MEP-0081", "Rev. 00",
                          "EMERGENCY LIGHTING LAYOUT GROUND FLOOR"])
    statement = "\n".join(["METHOD STATEMENT", "Submittal No. ICC-DLRC-SIG2-MAR-MEP-0027", "Rev. 00",
                              "Material Submittal for the installation of the fire alarm system"])

    submittal = parse_page(form, "mar.pdf", NOW, 1)
    assert [(r.reference, r.revision, r.category) for r in submittal] == [("ICC-DLRC-SIG2-MAR-MEP-0060", "R2", "submittals")]
    drawing = parse_page(sheet, "sd.pdf", NOW, 1)
    assert [(r.reference, r.category) for r in drawing] == [("ICC-DLRC-SIG2-SD-MEP-0081", "drawings")]
    assert parse_page(statement, "ms.pdf", NOW, 1) == []


def test_a_floor_is_one_drawing_at_the_revision_that_stands():
    """Every revision was a row of its own, so a floor whose R0 came back
    for revision was listed twice -- once rejected, once under review --
    and the log read as two drawings for one floor. What came before is
    kept on the row that replaced it."""
    from dataclasses import replace

    from app.services.document_control import ControlledDocument, combine

    def drawing(reference, revision, status, floor):
        return ControlledDocument(
            system_code="FAS", name=f"{floor} FLOOR PLAN", path=f"{revision}/{reference}.pdf",
            modified=NOW, reference=reference, revision=revision, status=status,
            floor=floor, category="drawings")

    rows = combine([
        drawing("BBY006-GME-SDW-FP-FA-POD-BGF-010002", "R0", "RR", "GROUND FLOOR"),
        drawing("BBY006-GME-SDW-FP-FA-POD-BGF-010002", "R1", "UR", "GROUND FLOOR"),
        drawing("BBY006-GME-SDW-FP-FA-POD-P01-010003", "R0", "ANN", "PODIUM-1"),
        # Filed again with nothing said about the first: one submission.
        drawing("BBY006-GME-SDW-FP-FA-POD-P02-010004", "R0", "UR", "PODIUM-2"),
        drawing("BBY006-GME-SDW-FP-FA-POD-P02-010004", "R1", "UR", "PODIUM-2"),
    ])
    drawings = [row for row in rows if row.category == "drawings"]
    # One row per floor, at the revision that stands.
    assert [(r.reference[-6:], r.revision, r.status) for r in drawings] == [
        ("010002", "R1", "UR"), ("010003", "R0", "ANN"), ("010004", "R1", "UR")]
    # The R0 the consultant answered is kept: it is what the R1 answers.
    assert [(r.revision, r.status) for r in drawings[0].superseded] == [("R0", "RR")]
    assert drawings[1].superseded == ()
    # An R1 exists, so R0 was submitted and answered (the platform owner's
    # rule, 2026-09-25): the R0 whose answer was not read stays in the
    # history, once, as answered with its reply not found -- never dropped
    # for the log to call "not submitted".
    assert [(r.revision, r.status, r.path) for r in drawings[2].superseded] == [
        ("R0", "SUPERSEDED", "R0/BBY006-GME-SDW-FP-FA-POD-P02-010004.pdf")]


def test_a_drawings_revision_is_the_one_printed_on_it_not_the_folder():
    """A resubmission is filed with the comments it answers: a floor's R1
    folder holds the R1 drawing and the stamped R0 sheet beside it.
    Reading the revision off the folder made that sheet an R1, and its
    'revise and resubmit' the verdict on a revision the consultant had
    not seen. Settled by the platform owner on 2026-09-24.

    The folder still says where the drawing prints nothing."""
    printed = (
        "DRAWING NO: BBY006-GME-SDW-FP-FA-POD-BGF-010002" + chr(10)
        + "DRAWING TITLE: GROUND FLOOR PLAN - FIRE ALARM LAYOUT" + chr(10)
        + "SDW Date: 19 August 2026 SDW Rev.: 00" + chr(10)
        + "ENGINEERING CONSULTANT COMMENTS AND APPROVAL STATUS"
    )
    filed_under_r1 = "C:/p/04- Drawings/R1/05. Ground Floor/sheet.pdf"
    assert [row.revision for row in parse_page(printed, filed_under_r1, NOW, 1)] == ["R0"]

    # The drawing beside it prints no revision, so the folder still says.
    silent = ("DRAWING NO: BBY006-GME-SDW-FP-FA-POD-BGF-010002" + chr(10)
              + "DRAWING TITLE: GROUND FLOOR PLAN - FIRE ALARM LAYOUT")
    beside = "C:/p/04- Drawings/R1/05. Ground Floor/dwg.pdf"
    assert [row.revision for row in parse_page(silent, beside, NOW, 1)] == ["R1"]


def test_a_floor_re_issued_under_another_number_is_still_one_drawing():
    """A sheet re-issued for the next revision writes its own title block.
    Where the first named the floor (...-ZZZ-L22-010009) the second can
    carry the placeholder (...-ZZZ-ZZZ-010009): the same drawing of L22
    under two numbers, which the log showed as two drawings of L22, one
    answered and one under review.

    A floor has one shop drawing per system -- but only within a system,
    and only where the floor is known."""
    from app.services.document_control import ControlledDocument, combine

    def drawing(reference, revision, status, floor, system="FAS"):
        return ControlledDocument(
            system_code=system, name=f"{floor} PLAN", path=f"{reference}.pdf", modified=NOW,
            reference=reference, revision=revision, status=status, floor=floor,
            category="drawings")

    rows = combine([
        drawing("BBY006-GME-SDW-FP-FA-ZZZ-L22-010009", "R0", "rejected", "L22"),
        drawing("BBY006-GME-SDW-FP-FA-ZZZ-ZZZ-010009", "R1", "UR", "L22"),
        # The same floor of another system is a drawing of its own.
        drawing("BBY006-GME-SDW-EL-LI-ZZZ-L22-010035", "R0", "ANN", "L22", system="ELS"),
        # Two different floors that happen to share a drawing number stay
        # two drawings: the number is not what identifies them.
        drawing("BBY006-GME-SDW-FP-FA-BSM-B04-010025", "R1", "UR", "BASEMENT-4"),
        drawing("BBY006-GME-SDW-FP-FA-ZZZ-L42-010025", "R0", "UR", "L42"),
    ])
    drawings = [r for r in rows if r.category == "drawings"]

    fas = [r for r in drawings if r.system_code == "FAS"]
    l22 = [r for r in fas if r.floor == "L22"]
    assert len(l22) == 1, "L22 is one drawing, not one per number it was filed under"
    assert l22[0].revision == "R1"
    assert [(s.revision, s.status) for s in l22[0].superseded] == [("R0", "rejected")]

    # The other system keeps its own L22.
    assert len([r for r in drawings if r.system_code == "ELS" and r.floor == "L22"]) == 1
    # And the shared number did not merge two floors.
    assert {r.floor for r in fas} == {"L22", "BASEMENT-4", "L42"}


def test_a_decision_marked_by_filling_a_box_is_read():
    """The approval block lists every option -- Approved (A), Approved as
    Noted (B), Re-Submit (C) -- and marks one by filling the box beside
    it. As text that is a list of choices and settles nothing, so the
    decision has to be read from what is drawn: an unchosen box is filled
    white, the chosen one with a colour."""
    import pymupdf

    from app.services.document_control import boxed_decision

    with pymupdf.open() as document:
        page = document.new_page()
        for y, label in ((100, "Approved (A)"), (130, "Approved as Noted (B)"), (160, "Re- Submit (C)")):
            page.insert_text((70, y + 9), label)
            page.draw_rect(pymupdf.Rect(50, y, 62, y + 12), color=(0, 0, 0),
                           fill=(1, 1, 1))          # the boxes not chosen
        # The one the consultant filled.
        page.draw_rect(pymupdf.Rect(50, 130, 62, 142), color=(0, 0, 0), fill=(0.11, 0.16, 0.75))
        reread = pymupdf.open(stream=document.tobytes(), filetype="pdf")

    status, evidence = boxed_decision(reread[0])
    assert status == "ANN"
    assert "Approved as Noted" in evidence

    # Two marks say no more than none.
    with pymupdf.open() as document:
        page = document.new_page()
        for y, label in ((100, "Approved (A)"), (130, "Re- Submit (C)")):
            page.insert_text((70, y + 9), label)
            page.draw_rect(pymupdf.Rect(50, y, 62, y + 12), color=(0, 0, 0), fill=(0.11, 0.16, 0.75))
        both = pymupdf.open(stream=document.tobytes(), filetype="pdf")
    assert boxed_decision(both[0]) is None


def _approval_sheet(path, *, chosen: str | None, image: bool = True):
    """A shop drawing's page: its title block, the approval block's three
    options with `chosen` filled, and (by default) a logo image."""
    import pymupdf

    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((300, 60), "Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout")
        for y, label in ((100, "Approved (A)"), (130, "Approved as Noted (B)"), (160, "Re- Submit (C)")):
            page.insert_text((70, y + 9), label)
            fill = (0.11, 0.16, 0.75) if label == chosen else (1, 1, 1)
            page.draw_rect(pymupdf.Rect(50, y, 62, y + 12), color=(0, 0, 0), fill=fill)
        # A coloured square beside something that is not an option: a symbol on the plan.
        page.insert_text((420, 309), "PLOT LIMIT")
        page.draw_rect(pymupdf.Rect(400, 300, 412, 312), color=(0, 0, 0), fill=(0.9, 0.1, 0.1))
        if image:
            logo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
            logo.clear_with(128)
            page.insert_image(pymupdf.Rect(500, 700, 540, 740), pixmap=logo)
        document.save(path)
    return path


def test_the_box_screens_never_change_the_answer(tmp_path):
    """The page and label screens only skip boxes whose label could name no
    option: with them or without, the same answer."""
    import pymupdf

    from app.services.document_control import _may_hold_an_option, boxed_decision

    for chosen, expected in (("Approved as Noted (B)", "ANN"), ("Re- Submit (C)", "rejected"), (None, None)):
        with pymupdf.open(_approval_sheet(tmp_path / f"{expected}.pdf", chosen=chosen)) as document:
            page = document[0]
            screened, unscreened = boxed_decision(page, page.get_text()), boxed_decision(page)
            assert screened == unscreened
            assert (screened[0] if screened else None) == expected
    assert _may_hold_an_option("Re-\nSubmit (C)") and _may_hold_an_option("APPROVED AS NOTED")
    assert not _may_hold_an_option("PLOT LIMIT\nF.L 0.15\nFIRE ALARM LAYOUT")


def test_a_consultants_stamp_overrides_the_ticked_box(tmp_path, monkeypatch):
    """EP-30784's emergency lighting sample (BBY006-GME-SAR-EL-LI-0001) has
    "Approved as Noted (B)" ticked on the form and the consultant's
    "(C) Revise & Resubmit" stamp pasted beside it: the stamp is the verdict
    that stands. So a page whose box already gave a decision is still OCRed
    for its stamp, and the stamp wins."""
    import os

    from app.services import document_control as dc

    ocred = []
    monkeypatch.setattr(dc, "_ocr_page", lambda page: ocred.append(page.number) or
                        "(C) Revise & Resubmit\nReviewed By : Eng. Muhana")
    path = _approval_sheet(tmp_path / "stamped.pdf", chosen="Approved as Noted (B)")
    stat = os.stat(path)
    dc._read_pdf.cache_clear()
    records, _notes = dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, True, None)
    assert ocred == [0]
    assert records[0].status == "rejected" and "Revise & Resubmit" in records[0].reply_text


def test_the_same_content_is_not_ocred_or_box_read_twice(tmp_path, monkeypatch):
    """A re-read after a change to the rules finds the OCR text and the box
    reading of unchanged content in the page cache."""
    import os

    from app.services import document_control as dc
    from app.services.document_sync import sha256_of

    ocred, boxed = [], []
    real_boxed = dc.boxed_decision
    monkeypatch.setattr(dc, "_ocr_page", lambda page: ocred.append(page.number) or "Consultant stamp: none")
    monkeypatch.setattr(dc, "boxed_decision", lambda page, text=None: boxed.append(page.number) or real_boxed(page, text))
    path = _approval_sheet(tmp_path / "sheet.pdf", chosen=None)
    stat = os.stat(path)
    sha = sha256_of(path)

    def read(content_hash):
        dc._read_pdf.cache_clear()
        return dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, True, content_hash)

    first = read(sha)
    assert (len(ocred), len(boxed)) == (1, 1)
    assert read(sha) == first
    assert (len(ocred), len(boxed)) == (1, 1), "the second reading came from the cache"
    read("0" * 64)          # other content: read afresh
    assert (len(ocred), len(boxed)) == (2, 2)
    read(None)              # no hash to key on: never cached
    assert (len(ocred), len(boxed)) == (3, 3)
