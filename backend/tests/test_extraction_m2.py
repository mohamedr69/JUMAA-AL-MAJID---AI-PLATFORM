"""M2 -- Extraction Reliability: regressions for the defects the Golden
originals demonstrated (docs/milestones/M2). Each test names the original
it stands for; none reads a project file."""
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from app.services import document_control as dc
from app.services import document_sync, transmittals

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_text((30, 30), text, fontsize=7)
        document.save(path)
    return path


# --- REF: a reference wrapped at a hyphen (document 687, a scanned MAR form) --------------------------


def test_a_reference_broken_across_a_line_at_its_hyphen_is_read_whole():
    text = "Material Submittal\nR1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ-\nZZZ-1004\nRev.02\nMaterial Submittal for Fire Alarm"
    found = dc.reference_candidates(text)
    assert [c.reference for c in found] == ["R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ-ZZZ-1004"]
    assert text[found[0].start:found[0].end].replace("-\n", "-") == found[0].reference
    # A bare line break is the end of the reference: the number on the next
    # line (a page number, a date) is never glued on.
    apart = dc.reference_candidates("R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ\n1004\n")
    assert [c.reference for c in apart] == ["R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ"]
    # A hyphen at the line end followed by a number is a cover's field read by
    # OCR, not a continuation: "...-SD-MEP-" / "0042" stays the base (EP-30088 FA-0042).
    numeric = dc.reference_candidates("Drawing ICC-DLRC-SPM-SD-MEP-\n0042\nRev 01")
    assert [c.reference for c in numeric] == ["ICC-DLRC-SPM-SD-MEP"]


# --- EXT: a cover of a discipline the platform does not track (documents 440-463) ---------------------

FF_COVER = (
    "SHOP DRAWING SUBMITTAL\nPROJECT: Samana Park Meadows\nNo: ICC-DLRC-SPM-SD-MEP-FF-0047\nRev: 02\nDate: 3-Mar-2026\n"
    "WE ARE SUBMITTING HEREWITH THE DESIGN DETAILS & DRAWINGS LISTED BELOW FOR REVIEW & APPROVAL\n"
    "DRAWING & DESIGN REF\nICC-DLRC-SPM-SD-MEP/FF-100\nBASEMENT-01 FLOOR PLAN FIREFIGHTING LAYOUT\nSOFT COPY\n"
    "Submitted By:\nReceived By:\nCONSULTANT COMMENTS:\nRevise and resubmit refer to marked comments on drawing\n"
    "Consultant Recommendation\nA - Approved\nB - Approved With Comments\nC - Revise & Re-Submit\nD- Rejected\n"
)


def test_a_cover_of_an_untracked_discipline_is_still_read_with_its_discipline_kept_raw():
    records = dc.parse_page(FF_COVER, "3. SHOP DRAWING/FIRE FIGHTING/ICC-DLRC-SPM-SD-MEP-FF-0047-02-COMMENTED-C.pdf", NOW, 1)
    assert len(records) == 1, "the submission is a record whatever discipline it is of"
    record = records[0]
    assert record.category == "drawings" and record.reference == "ICC-DLRC-SPM-SD-MEP-FF-0047"
    assert record.system_code is None, "fire fighting is not a system the platform tracks: nothing is mapped"
    assert record.raw_system == "FIREFIGHTING"
    assert record.revision == "R2" and record.revision_source == "cover"
    assert record.listed == ("ICC-DLRC-SPM-SD-MEP/FF-100",)
    assert record.status == "UR", "an option list is not a decision"
    # The reference infix stands in where the title names no discipline.
    assert dc.raw_system_of("ICC-DLRC-SPM-SD-MEP-FF-0047", "BASEMENT-01 FLOOR PLAN LAYOUT") == "FF"
    # The sheet behind the cover is not a submission: as before, a drawing
    # sheet of no tracked system is no record (one row per sheet number
    # would otherwise reach the register).
    sheet = "DRAWING NO:\nICC-DLRC-SPM-SD-MEP/FF-110\nDRAWING TITLE:\nBASEMENT-01 FIREFIGHTING LAYOUT\nSCALE 1 : 75\n"
    assert dc.parse_page(sheet, "x/FF-0047.pdf", NOW, 2) == []
    # A framed B on such a cover is "approved as noted", not approved.
    assert dc._option_in("B - Approved With Comments") == ("ANN", "B - Approved With Comments")


def test_a_record_without_a_system_is_not_a_register_row_of_any_system():
    record = dc.parse_page(FF_COVER, "x/FF-0047.pdf", NOW, 1)[0]
    fas = dc.ControlledDocument("FAS", "A", "a.pdf", NOW, "ICC-DLRC-SPM-SD-MEP-FA-0054", "R0", "UR", category="drawings")
    combined = dc.combine([record, fas])
    assert {r.reference for r in combined} == {record.reference, fas.reference}, "kept, apart"
    assert next(r for r in combined if r.reference == record.reference).system_code is None


# --- decision evidence: a frame drawn into the page (409: green outline; 440: orange highlight) -----

OPTIONS = ("Consultant Recommendation\nA - Approved\nB - Approved With Comments\nC - Revise & Re-Submit\nD- Rejected\n"
           "UR- Under Review\n")


def _framed_pdf(path: Path, *, stroke=None, fill=None, phrase="C - Revise & Re-Submit", pad=3.0) -> Path:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), OPTIONS, fontsize=8)
        rect = page.search_for(phrase)[0]
        frame = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
        if stroke is not None:
            page.draw_rect(frame, color=stroke, width=3)
        if fill is not None:
            page.draw_rect(frame, color=None, fill=fill)
        document.save(path)
    return path


def test_an_option_framed_by_a_rectangle_drawn_into_the_page_is_the_decision(tmp_path):
    with pymupdf.open(_framed_pdf(tmp_path / "green.pdf", stroke=(0.02, 0.54, 0.11))) as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) == ("rejected", "C - Revise & Re-Submit")
    with pymupdf.open(_framed_pdf(tmp_path / "orange.pdf", fill=(1.0, 0.38, 0.0))) as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) == ("rejected", "C - Revise & Re-Submit")
    with pymupdf.open(_framed_pdf(tmp_path / "approved.pdf", stroke=(0, 0, 1), phrase="A - Approved")) as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) == ("approved", "A - Approved")


def test_a_frame_round_the_whole_row_or_two_frames_settle_nothing(tmp_path):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), OPTIONS, fontsize=8)
        first, last = page.search_for("A - Approved")[0], page.search_for("D- Rejected")[0]
        page.draw_rect(pymupdf.Rect(first.x0 - 3, first.y0 - 3, last.x1 + 3, last.y1 + 3), color=(0, 0.5, 0), width=3)
        document.save(tmp_path / "row.pdf")
    with pymupdf.open(tmp_path / "row.pdf") as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) is None, "a frame round every option names none"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), OPTIONS, fontsize=8)
        for phrase in ("A - Approved", "C - Revise & Re-Submit"):
            r = page.search_for(phrase)[0]
            page.draw_rect(pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3), color=(0, 0.5, 0), width=3)
        document.save(tmp_path / "two.pdf")
    with pymupdf.open(tmp_path / "two.pdf") as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) is None, "two frames over different answers say no more than none"
    # A coloured comment box on a drawing that *mentions* an option is not the option
    # (EP-30784 QA/QC comments: "Follow the approved builders work drawings for riser location").
    with pymupdf.open() as document:
        page = document.new_page()
        comment = "Follow the approved builders work drawings for riser location"
        page.insert_text((30, 30), OPTIONS + comment + "\n", fontsize=8)
        r = page.search_for(comment)[0]
        page.draw_rect(pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3), color=None, fill=(1.0, 0.9, 0.2))
        document.save(tmp_path / "comment.pdf")
    with pymupdf.open(tmp_path / "comment.pdf") as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS + comment) is None
    # The form's own black table rules are not frames.
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), OPTIONS, fontsize=8)
        r = page.search_for("C - Revise & Re-Submit")[0]
        page.draw_rect(pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3), color=(0, 0, 0), width=0.5)
        document.save(tmp_path / "rule.pdf")
    with pymupdf.open(tmp_path / "rule.pdf") as pdf:
        assert dc.boxed_decision(pdf[0], OPTIONS) is None


# --- a scanned document transmittal filed as a PDF (documents 729 and 730) ------------------------

OCR_729 = """DOCUMENT TRANSMITAL

Al Arabia

For Safety & Security LLC

_To : | M/s. Samana Developers | Date |: | 13/08/2026
| Attn. | Mr. Siddharth | AASS Ref. | : | TR/204/26 ;
| Tel & Fax | | 0504354652 | Project ID | : | EP-30058
i Project . | Samana park Meadows (DLRC 4) - 2B +G+16+R Residential Building @ Wadi Al Safa 5,
| Subject _: | Sample Board / Central Battery System -EATON |
FIRE FIGHTING SYSTEM FIRE ALARM SYSTEM | SPECIAL SYSTEM |
SCOPE: OTHER LOW VOLTAGE CBS x
Dear Sir/ Madam,
With reference to the above subject, please find attached herewith sample Board detail:
ITEM Drawing No. Description Qty
1 | _ Sample Board / Central Battery System -EATON | 1 No.
RECEIVED BY: Er. Shibin SIGNATURE: DATE: 13-8-26
"""


def test_a_scanned_transmittal_is_read_off_its_ocr_and_a_receipt_is_not_an_approval():
    assert transmittals.looks_like_transmittal(OCR_729)
    assert not transmittals.looks_like_transmittal("Please see the transmittal attached. TR/1/26 is not a form.")
    # OCR that lost the banner (730 came back as "~ T") is still the form when its own labels are there.
    headless = "~ T\nTo : | M/s. X | : | 24/07/2026\nAttn. | AASS Ref. TR/187/26\nProject ID | : | EP-30058\nSubject : | Sample Board / Fire Alarm\nDear Sir/ Madam,"
    assert transmittals.looks_like_transmittal(headless)
    assert [(r.system_code, r.reference) for r in transmittals.from_ocr(headless, "x.pdf", NOW)] == [("FAS", "TR/187/26")]
    records = transmittals.from_ocr(OCR_729, "09. Scan Document/EP-30088 CBS Sam B ack 13.08.26.pdf", NOW, page=1)
    assert [(r.category, r.system_code, r.reference, r.status, r.source) for r in records] == \
        [("samples", "ELS", "TR/204/26", "UR", "transmittal")], "a central battery system is emergency lighting's (transmittals.SYSTEMS)"
    assert records[0].modified == datetime(2026, 8, 13, tzinfo=timezone.utc), "the transmittal's own date, not the file's"
    assert records[0].group_reference == "SAMPLE BOARD"
    # The same submission the Word original gives: numbered once between them.
    word = transmittals.read_transmittal(
        "To | M/s. Samana Developers | Date | 13/08/2026\nAASS Ref. | TR/204/26\nSubject | Sample Board / Central Battery System -EATON\n"
        "Dear Sir\nSample Board / Central Battery System -EATON | 1 No.", "12. Transmittal/EP-30058 CBS Sam B -EATON.doc", NOW)
    assert len(transmittals.number([*word, *records])) == 1


def test_a_scanned_transmittal_page_reaches_the_reader_through_ocr(tmp_path, monkeypatch):
    path = tmp_path / "EP-30088 CBS Sam B ack 13.08.26.pdf"
    with pymupdf.open() as document:
        document.new_page()      # a scan: no text layer at all
        document.save(path)
    monkeypatch.setattr(dc, "_ocr_text", lambda page, sha256, index, renders=None: OCR_729)
    with pymupdf.open(path) as pdf:
        records, notes = dc.read_open_pdf(pdf, str(path), NOW, True, "sha-729")
    assert [(r.category, r.reference, r.page) for r in records] == [("samples", "TR/204/26", 1)]
    assert notes == ()


# --- REV: the printed revision and the revision's source are kept beside the revision -----------------

SHEET = ("SHOP DRAWING\nDRAWING TITLE\nSCALE\nREV. NO.\nGENERAL NOTES\n"
         "BBY006-GME-SDW-FP-FA-POD-BGF-010002\nGROUND FLOOR PLAN\nFIRE ALARM LAYOUT\n06.08.2026\nA0\n00\nRAMADAN\nIS\n1:100\n"
         "ISSUED FOR APPROVAL\n06.08.2026\n00\nCONSULTANT COMMENTS STATUS\nA APPROVED\nB APPROVED AS NOTED\nC REVISION & RESUBMIT\n")


def test_the_printed_revision_is_kept_as_printed_beside_the_revision_the_folder_gave():
    [record] = dc.parse_page(SHEET, "1.FAVE/R1/05. Ground Floor/BBY006-GME-SDW-FP-FA-POD-BGF-010002.pdf", NOW, 2)
    assert record.reference == "BBY006-GME-SDW-FP-FA-POD-BGF-010002"
    assert record.revision == "R1" and record.revision_source == "folder", "the settled rule stands: the folder where the sheet prints none"
    assert record.printed_revision == "00", "what the sheet prints is kept as a fact of its own"
    assert record.floor == "GROUND FLOOR" and record.status == "UR"
    [printed] = dc.parse_page("SHOP DRAWING\nDrawing Title: X\nBBY006-GME-SDW-FP-FA-POD-BGF-010002\nREV: 01\nGROUND FLOOR PLAN\n", "R2/x.pdf", NOW, 1)
    assert (printed.revision, printed.revision_source, printed.printed_revision) == ("R1", "printed", None)
    [plain] = dc.parse_page("SHOP DRAWING\nDrawing Title: X\nBBY006-GME-SDW-FP-FA-POD-BGF-010002\nGROUND FLOOR PLAN\n", "x/y.pdf", NOW, 1)
    assert (plain.revision, plain.revision_source) == ("R0", "default")


def test_the_new_fields_travel_with_the_stored_record(tmp_path):
    [record] = dc.parse_page(FF_COVER, "x/FF-0047.pdf", NOW, 1)
    stored = document_sync._record_dict(record, tmp_path)
    assert stored["raw_system"] == "FIREFIGHTING" and stored["revision_source"] == "cover" and stored["printed_revision"] is None
    # An older stored record (without the fields) still reads back.
    assert dc.ControlledDocument(**{k: v for k, v in stored.items() if k not in ("raw_system", "revision_source", "printed_revision", "modified")},
                                 modified=NOW).raw_system is None


# --- EXT: a reader defect is a failure, an unopenable file is a note ----------------------------------


def test_a_reader_defect_raises_instead_of_standing_as_an_empty_reading(tmp_path, monkeypatch):
    path = _pdf(tmp_path / "form.pdf", ["Shop Drawing Submittal Form\nSDW Reference No.: BBY006-GME-SDW-FP-FA-0001\nSDW Rev.: 00"])
    import os

    stat = os.stat(path)
    records, notes = dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, False, "sha-a")
    assert [r.reference for r in records] == ["BBY006-GME-SDW-FP-FA-0001"] and notes == ()

    def broken(*args, **kwargs):
        raise RuntimeError("the reader fell over")

    monkeypatch.setattr(dc, "parse_page", broken)
    with pytest.raises(RuntimeError, match="fell over"):
        dc._read_pdf(str(path), stat.st_mtime_ns + 1, stat.st_size, False, "sha-b")
    with pytest.raises(RuntimeError, match="fell over"):
        document_sync.extract(str(path), "form.pdf", "sha-b", False)


def test_an_unopenable_file_is_still_a_document_with_the_note_it_always_got(tmp_path):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(b"not a pdf at all")
    import os

    stat = os.stat(path)
    records, notes = dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, False, None)
    assert records == () and notes and notes[0].startswith(dc.UNREADABLE)
    role, records, notes, timing = document_sync.extract(str(path), "damaged.pdf", None, False)
    assert records == () and notes[0].startswith(dc.UNREADABLE) and timing["evidence"] is None


# --- a folded reply page stays a record (5 EP-30784 files lost theirs on the clone re-read) ---------

FOLD_COVER = ("SHOP DRAWING SUBMITTAL\nNo: ABC-XYZ-SPM-SD-MEP-FA-0054\nRev: 01\nsubmitting herewith\nDRAWING & DESIGN REF\n"
              "ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M\nTYPICAL 2ND TO 14TH FLOOR PLAN FIRE ALARM LAYOUT\nSubmitted By:\nReceived By:\n")
FOLD_REPLY = "Reply to Consultant Comments\nABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105\nRev: 0\n1 PQ & MAS to be obtained approval. Noted\n"


def test_a_folded_reply_page_is_still_a_record(tmp_path):
    import os

    path = _pdf(tmp_path / "ABC-XYZ-SPM-SD-MEP-FA-0054-01.pdf", [FOLD_COVER, FOLD_REPLY])
    stat = os.stat(path)
    records, _notes = dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, False, "sha-fold")
    assert [(r.category, r.page, r.status) for r in records] == [("drawings", 1, "UR"), ("reply", 2, "UR")]
    assert records[1].reference == "ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105", "the contractor's reply, with the sheets it names"
    assert records[0].status == "UR", "a contractor reply is never an approval"
    assert [r.category for r in dc.combine(list(records))] == ["drawings"], "the register lists the submission only"
