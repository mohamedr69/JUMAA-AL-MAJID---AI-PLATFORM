"""The reference grammar and the submission cover, repaired (2026-09-28):
a date is not a reference; a reference keeps the sheets it lists past a
slash, so submissions, sheets and replies of one project stay apart; a
submission cover's number is the numbered reference of its heading's
kind, not the first token on the page; a reply naming the sheets of the
submission before it is folded into that submission; a framed option
(an annotation) is a decision; the first pages' evidence is read and
kept. Fixtures are synthetic layouts built from the confirmed cases'
shapes -- no project file, no private path."""

from __future__ import annotations

from datetime import datetime, timezone

import pymupdf
import pytest

from app.services import content_evidence, document_control as dc

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)

# A contractor's shop drawing submission cover as the text layer gives it:
# labels and values in separate runs, the date beside the number, the
# sheets listed below, the recommendation row, the legend.
COVER = """No:
Rev:
01
S. No.
Remarks
1
Consultant
Recommendation
A - Approved
B - Approved With
Comments
C - Revise & Re-Submit
D- Rejected
UR- Under Review
CONSULTANT COMMENTS:
Submitted By:
Main Contractor:
WE ARE SUBMITTING HEREWITH THE DESIGN DETAILS & DRAWINGS LISTED BELOW FOR REVIEW & APPROVAL
DRAWING & DESIGN REF
DESIGN / DRAWING DESCRIPTION
CLIENT
THE CLIENT
6-Mar-2026
CONSULTANT
THE CONSULTANT
Date:
SHOP DRAWING SUBMITTAL
PROJECT
A Residential Building
ABC-XYZ-SPM-SD-MEP-FA-0054
Client
Consultant
Main Contractor
SOFT COPY
ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M
TYPICAL 2ND TO 14TH FLOOR PLAN FIRE ALARM LAYOUT
F2 - SDS
"""
REPLY_PAGE = """Consultant comments
Reply
ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105
1 PQ & MAS to be obtained approval. Noted
"""


# --- TEST: dates are not references; real MAR identifiers are ------------------------------------


def test_a_date_is_not_a_reference_and_a_numbered_mar_is():
    assert dc.is_date_shaped("6-Mar-2026") and dc.is_date_shaped("31-DEC-25") and dc.is_date_shaped("7-mar-2026")
    assert not dc.is_date_shaped("123-MAR-001") and not dc.is_date_shaped("BBY006-GME-MAR-EL-FA-0001")
    assert not dc.is_date_shaped("12-MAR-2025-001")
    text = "Submitted on 6-Mar-2026 under 123-MAR-001 and later 7-Mar-2026"
    found = dc.reference_candidates(text)
    assert [c.reference for c in found] == ["123-MAR-001"] and found[0].category == "submittals"
    assert dc.first_reference("Dated 3-Mar-2026 only") is None
    # The order on the page does not decide: the date first, the reference after.
    assert dc.first_reference("3-Mar-2026\nBBY006-GME-MAS-EL-FA-0001").reference == "BBY006-GME-MAS-EL-FA-0001"


def test_a_material_submittal_page_dated_in_march_keeps_its_own_reference():
    text = "Material Submittal for Fire Alarm System\nMAS Reference No: BBY006-GME-MAS-EL-FA-0001\nDate: 6-Mar-2026\nMAS Rev 00\n"
    [record] = dc.parse_page(text, "03- MS/form.pdf", NOW, 1)
    assert record.reference == "BBY006-GME-MAS-EL-FA-0001" and record.category == "submittals" and record.system_code == "FAS"


# --- TEST: sheets past a slash are kept; identities do not collapse -------------------------------


def test_a_reference_keeps_the_sheets_it_lists_past_a_slash():
    grouped = dc.reference_candidates("Ref ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105 attached")
    assert grouped[0].reference == "ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105" and grouped[0].kind == "sheets"
    ranged = dc.reference_candidates("ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M\nTYPICAL FLOOR")
    assert ranged[0].reference == "ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M"
    single = dc.reference_candidates("ABC-XYZ-SPM-SD-MEP/EM-104 B\n3RD FLOOR")
    assert single[0].reference == "ABC-XYZ-SPM-SD-MEP/EM-104 B"
    sheet = dc.reference_candidates("Drawing ABC-XYZ-SPM-SD-MEP/FA-104-M title")
    assert sheet[0].reference == "ABC-XYZ-SPM-SD-MEP/FA-104-M"
    # Words after the sheets are not part of it.
    trailing = dc.reference_candidates("ABC-XYZ-SPM-SD-MEP/FA-110 FIRST FLOOR")
    assert trailing[0].reference == "ABC-XYZ-SPM-SD-MEP/FA-110"
    assert dc._sheet_numbers("ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105") == {"100", "101", "102", "104", "105"}
    assert dc._sheet_numbers("ABC-XYZ-SPM-SD-MEP-FA-0054") == set()
    # Three different submissions and a reply naming a group are four references, not one.
    texts = ["ABC-XYZ-SPM-SD-MEP/FA-100,101", "ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M", "ABC-XYZ-SPM-SD-MEP/EM-104 B",
             "ABC-XYZ-SPM-SD-MEP-FA-0054"]
    assert len({dc.first_reference(t).reference for t in texts}) == 4


# --- TEST: the submission cover ------------------------------------------------------------------------


def test_a_shop_drawing_submission_cover_is_read_as_the_submission():
    cover = dc.submission_cover(COVER)
    assert cover is not None and cover.category == "drawings"
    assert cover.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054" and cover.revision == "R1"
    assert cover.listed == ("ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M",)
    assert cover.description == "TYPICAL 2ND TO 14TH FLOOR PLAN FIRE ALARM LAYOUT"
    [record] = dc.parse_page(COVER, "3. SHOP DRAWING/FIRE ALARM/ABC-XYZ-SPM-SD-MEP-FA-0054-01-COMMENTED-C.pdf", NOW, 1)
    assert record.category == "drawings" and record.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054" and record.revision == "R1"
    assert record.system_code == "FAS" and record.name == "TYPICAL 2ND TO 14TH FLOOR PLAN FIRE ALARM LAYOUT"
    assert record.listed == ("ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M",) and record.status == "UR", "an option list settles nothing"
    assert "MAR" not in record.reference and not dc.is_date_shaped(record.reference)
    # The revision is the cover's, the date is not a reference, and the title names the floor.
    assert record.floor is not None and "FLOOR" in record.floor.upper()


def test_a_cover_mentioning_mar_approval_in_its_comments_is_still_a_shop_drawing():
    text = COVER + "\nRevise and Resubmit with MAR Approval\n"
    [record] = dc.parse_page(text, "x/ABC-XYZ-SPM-SD-MEP-FA-0054-01.pdf", NOW, 1)
    assert record.category == "drawings" and record.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054"
    assert record.status == "UR", "an instruction to resubmit with a MAR is not a marked decision"


def test_a_material_submittal_cover_is_read_as_a_submittal_and_a_sample_cover_as_a_sample():
    text = ("MATERIAL SUBMITTAL\nWE ARE SUBMITTING HEREWITH\nNo: ABC-XYZ-SPM-MAR-MEP-0109\nRev: 00\nDate: 3-Mar-2026\n"
            "Consultant Recommendation\nA - Approved\nEmergency lighting luminaires\n")
    [record] = dc.parse_page(text, "06. MS/EML/cover.pdf", NOW, 1)
    assert record.category == "submittals" and record.reference == "ABC-XYZ-SPM-MAR-MEP-0109" and record.revision == "R0"
    sample = ("SAMPLE SUBMITTAL\nSubmitted By: us\nNo: ABC-XYZ-SPM-SAR-MEP-0003\nRev: 01\nFire alarm sounder sample\n")
    [record] = dc.parse_page(sample, "x/sample.pdf", NOW, 1)
    assert record.category == "samples" and record.reference == "ABC-XYZ-SPM-SAR-MEP-0003"


def test_a_page_that_is_not_a_cover_keeps_the_existing_rules():
    # A drawing sheet's title block, as before.
    sheet = "Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout\nshop drawing"
    [record] = dc.parse_page(sheet, "05- Drawings/L01.pdf", NOW, 1)
    assert record.reference == "BBY006-GME-SDW-EL-FA-0001" and record.revision == "R1" and record.category == "drawings"
    # A reply sheet quoting a grouped reference keeps the group.
    [reply] = dc.parse_page("Reply to consultant comments\nRef No : ABC-XYZ-SPM-SD-MEP/FA-100,101 - R.00\nComment: x\nReply: y",
                            "x/reply.pdf", NOW, 1)
    assert reply.category == "reply" and reply.reference == "ABC-XYZ-SPM-SD-MEP/FA-100,101"


# --- TEST: a reply behind the cover is folded into the submission ---------------------------------------


def _pdf(path, pages: list[str], *, annotate: tuple | None = None):
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_text((30, 30), text, fontsize=7)
        if annotate is not None:
            page_index, phrase = annotate
            page = document[page_index]
            hits = page.search_for(phrase)
            assert hits, phrase
            rect = hits[0]
            page.add_rect_annot(pymupdf.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3))
        document.save(path)
    return path


def test_a_reply_naming_the_submissions_sheets_is_folded_into_it_not_registered(tmp_path):
    import os

    path = _pdf(tmp_path / "ABC-XYZ-SPM-SD-MEP-FA-0054-01-COMMENTED-C.pdf", [COVER, REPLY_PAGE])
    stat = os.stat(path)
    records, _notes = dc._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, False, None)
    # The reply is folded into the submission (its decision, if any, settles it) and,
    # since M2, kept as a record of its own too: a page never vanishes from the reading.
    assert [r.category for r in records] == ["drawings", "reply"]
    assert records[0].reference == "ABC-XYZ-SPM-SD-MEP-FA-0054"
    assert records[1].reference == "ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105" and records[1].page == 2
    assert [r.reference for r in dc.combine(list(records))] == ["ABC-XYZ-SPM-SD-MEP-FA-0054"], "the register never lists a reply"
    submission, reply = dc.parse_page(COVER, "x.pdf", NOW, 1)[0], dc.parse_page(REPLY_PAGE, "x.pdf", NOW, 2)[0]
    assert dc._answers(submission, reply), "sheet 104 is in both"
    other = dc.replace(reply, reference="ABC-XYZ-SPM-SD-MEP/FA-200,201")
    assert not dc._answers(submission, other), "another series is another submission's reply"
    foreign = dc.replace(reply, reference="OTHER-CO-SD-MEP/FA-104")
    assert not dc._answers(submission, foreign)


def test_replies_settle_only_the_submission_they_name_when_combined():
    from datetime import timedelta

    cover_a = dc.ControlledDocument("FAS", "A", "a.pdf", NOW, "ABC-XYZ-SPM-SD-MEP-FA-0054", "R0", "UR", page=1,
                                    category="drawings", listed=("ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M",))
    cover_b = dc.ControlledDocument("FAS", "B", "b.pdf", NOW + timedelta(days=1), "ABC-XYZ-SPM-SD-MEP-FA-0055", "R0", "UR",
                                    page=1, category="drawings", listed=("ABC-XYZ-SPM-SD-MEP/FA-105",))
    reply = dc.ControlledDocument(None, "Reply", "r.pdf", NOW, "ABC-XYZ-SPM-SD-MEP/FA-105", "R0", "rejected",
                                  reply_text="(C) revise", source="reply", category="reply")
    combined = {r.reference: r for r in dc.combine([cover_a, cover_b, reply])}
    assert combined["ABC-XYZ-SPM-SD-MEP-FA-0055"].status == "rejected", "the reply names 0055's sheet"
    assert combined["ABC-XYZ-SPM-SD-MEP-FA-0054"].status == "UR", "0054 is another submission"
    assert len(combined) == 2, "two submissions stay two"


# --- TEST: a framed option is a decision --------------------------------------------------------------


def test_a_framed_option_is_read_as_the_decision_and_an_unmarked_list_is_not(tmp_path):
    options = "Consultant Recommendation\nA - Approved\nB - Approved With Comments\nC - Revise & Re-Submit\nD- Rejected\n"
    framed = _pdf(tmp_path / "framed.pdf", [COVER], annotate=(0, "C - Revise & Re-Submit"))
    with pymupdf.open(framed) as document:
        assert dc.annotated_decision(document[0], document[0].get_text()) == ("rejected", "C - Revise & Re-Submit")
        assert dc.boxed_decision(document[0], document[0].get_text())[0] == "rejected"
    plain = _pdf(tmp_path / "plain.pdf", [options])
    with pymupdf.open(plain) as document:
        assert dc.annotated_decision(document[0], document[0].get_text()) is None
        assert dc.boxed_decision(document[0], document[0].get_text()) is None
        assert dc.read_decision(document[0].get_text())[0] == "UR"
    # A frame over the whole block, or over two answers, says nothing.
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), options, fontsize=7)
        page.add_rect_annot(pymupdf.Rect(20, 20, 300, 120))
        assert dc.annotated_decision(page, page.get_text()) is None
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), options, fontsize=7)
        for phrase in ("A - Approved", "C - Revise & Re-Submit"):
            hit = page.search_for(phrase)[0]
            page.add_rect_annot(pymupdf.Rect(hit.x0 - 3, hit.y0 - 3, hit.x1 + 3, hit.y1 + 3))
        assert dc.annotated_decision(page, page.get_text()) is None
    # Through the reader: the framed C settles the submission on the cover.
    import os

    stat = os.stat(framed)
    records, _notes = dc._read_pdf(str(framed), stat.st_mtime_ns, stat.st_size, False, None)
    assert records[0].status == "rejected" and "Re-Submit" in (records[0].reply_text or "")


# --- TEST: the first pages' evidence ------------------------------------------------------------------


def test_page_evidence_names_what_the_pages_hold_and_who_wrote_them():
    findings = content_evidence.scan_texts({
        0: ("Cover page\nEmergency Light Panel", "text"),
        1: ("EATON CGLine+ Web Compact Controller Datasheet - November 2023\nDimensions 288 x 203", "text"),
    })
    kinds = {f.kind for f in findings}
    assert "datasheet" in kinds and all(f.page == 2 for f in findings if f.kind == "datasheet")
    certificate = content_evidence.scan_texts({0: ("Certificate of Approval\nThis is to certify that the Management System of X\nISO 9001:2015", "text")})
    assert {f.kind for f in certificate} == {"certificate"}
    transmittal = content_evidence.scan_texts({0: ("DOCUMENT TRANSMITAL\nAASS Ref. : TR/204/26\nProject ID : EP-30058\nSubject: Sample Board / Central Battery System\nReceived by", "ocr")})
    kinds = {f.kind for f in transmittal}
    assert {"transmittal", "sample_submittal", "receipt", "project_code"} <= kinds
    assert next(f for f in transmittal if f.kind == "project_code").rule == "EP-30058"
    assert all(f.method == "ocr" for f in transmittal)
    comments = content_evidence.scan_texts({0: ("ADDITIONAL COMMENTS SHEET\nProject: X, Status-C\nProposed system integrator to respond", "text")})
    assert {"consultant_comments", "decision_status"} <= {f.kind for f in comments}
    assert "contractor_reply" not in {f.kind for f in comments}
    reply = content_evidence.scan_texts({0: ("Consultant Comments | Al Arabia SSD Reply\nReply to Consultant Comments on EML MS\nComply", "text")})
    assert "contractor_reply" in {f.kind for f in reply} and "author_contractor" in {f.kind for f in reply}
    schedule = content_evidence.scan_texts({0: ("SUMMARY OF CONNECTED LOAD/MAXIMUM DEMAND & KWH METERING\nLV PANEL-1", "text")})
    assert {f.kind for f in schedule} == {"load_schedule"}
    vendors = content_evidence.scan_texts({0: ("Vendor List - Project\nLIST OF MAKES:\nPROPOSED VENDOR", "text")})
    assert {f.kind for f in vendors} == {"vendor_list"}
    matrix = content_evidence.scan_texts({0: ("Project Responsibility Matrix - Fire & Life Safety System Subcontractor", "text")})
    assert {f.kind for f in matrix} == {"scope_matrix"}
    diagram = content_evidence.scan_texts({0: ("SINGLE LINE DIAGRAM\nLV PANEL-01\n2500A 4P ACB", "text")})
    assert {f.kind for f in diagram} == {"single_line_diagram"}
    spec = content_evidence.scan_texts({0: ("265200- EMERGENCY LIGHTING\nPART 1 GENERAL\n1.1 SECTION INCLUDES", "text")})
    assert {f.kind for f in spec} == {"specification"}
    assert content_evidence.scan_texts({0: ("", "text")}) == []


def test_page_evidence_is_bounded_to_the_first_pages_and_reuses_cached_ocr(tmp_path, monkeypatch):
    from app.services import page_cache

    path = _pdf(tmp_path / "long.pdf", ["Certificate of Approval\nThis is to certify that", "page two", "page three",
                                       "page four: Vendor List"])
    with pymupdf.open(path) as document:
        texts: dict = {}
        evidence = content_evidence.scan_pdf(document, texts, "sha-x")
    assert evidence["pages_read"] == 3 and evidence["page_count"] == 4
    kinds = {f["kind"] for f in evidence["findings"]}
    assert "certificate" in kinds and "vendor_list" not in kinds, "page four is past the bound"
    assert all(f["content_sha256"] == "sha-x" and f["version"] == content_evidence.EVIDENCE_VERSION for f in evidence["findings"])
    # A scanned first page: the OCR the reader cached is reused; nothing is rendered here.
    scanned = _pdf(tmp_path / "scan.pdf", [" "])
    monkeypatch.setattr(page_cache, "get_ocr", lambda sha, index, version=None: "DOCUMENT TRANSMITTAL\nReceived by" if index == 0 else None)
    monkeypatch.setattr(dc, "_ocr_page", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered")))
    with pymupdf.open(scanned) as document:
        evidence = content_evidence.scan_pdf(document, {}, "sha-scan")
    assert evidence["scanned_pages"] == [1] and {f["kind"] for f in evidence["findings"]} == {"transmittal", "receipt"}
    assert all(f["method"] == "ocr" for f in evidence["findings"])
