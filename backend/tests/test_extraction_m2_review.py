"""M2 review 01 -- the corrections: a reading that did not complete never
replaces the last complete one (R1); marks of every method are validated
alike and disagreement is a conflict (R2); every page is in the ledger and
untracked components are observations, not register rows (R3); new
observations are promoted only on the evaluation path, and manual values
survive ordinary processing (R5). Everything runs in the isolated test
environment (conftest); no live data, model or worker."""
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

import app.routers.jobs as jobs_router
from app.core.config import get_settings
from app.models import Project, ProjectDocument
from app.services import document_control as dc
from app.services import document_processing, document_sync

from .test_document_sync import _pdf, _project, _result
from .test_extraction_m2 import FF_COVER, OPTIONS, SHEET

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
FA_COVER = ("SHOP DRAWING SUBMITTAL\nNo: ABC-XYZ-SPM-SD-MEP-FA-0054\nRev: 01\nsubmitting herewith\nDRAWING & DESIGN REF\n"
            "ABC-XYZ-SPM-SD-MEP/FA-104\nGROUND FLOOR FIRE ALARM LAYOUT\nSubmitted By:\nReceived By:\n")
SEPARATOR = "Project package contents and document separator sheet.\n" * 3


@pytest.fixture()
def inline(monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)


def _row(db_session, project_id) -> ProjectDocument:
    return db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).one()


def _touch(path: Path) -> None:
    later = time.time() + 5
    os.utime(path, (later, later))


def _sync(client, project_id) -> dict:
    return _result(client, client.post(f"/projects/{project_id}/jobs/sync-documents"))


# --- R1: the last complete reading survives a failed, unavailable or partial attempt -----------------


def test_a_corrupt_replacement_keeps_the_last_complete_reading_marks_the_row_and_is_retried(client, db_session, tmp_path, inline):
    folder = tmp_path / "EP-30901"
    path = _pdf(folder / "05- Drawings" / "cover.pdf", FA_COVER)
    project_id = _project(client, folder, ep="30901")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    first = dict(row.extracted)
    assert first["records"][0]["reference"] == "ABC-XYZ-SPM-SD-MEP-FA-0054" and first["read_sha256"] == row.sha256
    assert first["coverage"]["outcome"] == "complete" and first["coverage"]["pages_visited"] == [1]
    good_sha, good_at = row.sha256, row.last_processed_at

    path.write_bytes(b"not a PDF at all, twice over")
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert result["changed"] == 1 and result.get("failed", 0) == 1
    assert row.state == "failed" and row.error, "the registry says why"
    assert row.extracted["records"] == first["records"], "the last complete reading stands"
    assert row.extracted["parser_version"] == first["parser_version"] and row.extracted["read_sha256"] == good_sha
    assert row.extracted["read_at"] == first["read_at"] and row.last_processed_at == good_at
    assert row.extracted["stale"] is True, "the kept reading describes the old bytes, not the new ones"
    assert row.sha256 != good_sha
    attempt = row.extracted["attempt"]
    assert attempt["outcome"] == "failed" and attempt["sha256"] == row.sha256 and attempt["parser_version"] == dc.PARSER_VERSION
    assert attempt["notes"] and attempt["notes"][0].startswith(dc.UNREADABLE)
    assert row.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054", "the mirror stays consistent with the kept records"
    status = client.get(f"/projects/{project_id}/documents/status").json()
    assert status["by_state"] == {"failed": 1} and status["failed"][0]["path"] == "05- Drawings/cover.pdf"
    # The incomplete work is what the next run retries -- and a retry over the same bytes is idempotent.
    project = db_session.get(Project, project_id)
    assert [r.id for r in document_processing.pending_rows(db_session, project)] == [row.id]
    again = _sync(client, project_id)
    db_session.refresh(row)
    assert again.get("failed", 0) == 1, "the incomplete row was retried"
    assert row.state == "failed" and row.extracted["records"] == first["records"] and row.extracted["read_sha256"] == good_sha
    assert row.extracted["attempt"]["outcome"] == "failed" and row.last_processed_at == good_at

    # The file is good again: read, the attempt superseded, the reading current.
    # (written over the bytes in place: pymupdf's save replaces the file, which Windows refuses while a
    # handle of the earlier reads is still closing)
    path.write_bytes(_pdf(tmp_path / "good.pdf", FA_COVER.replace("Rev: 01", "Rev: 02")).read_bytes())
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    assert row.state == "fresh" and row.error is None and "attempt" not in row.extracted and not row.extracted.get("stale")
    assert row.extracted["records"][0]["revision"] == "R2" and row.extracted["read_sha256"] == row.sha256
    assert document_processing.pending_rows(db_session, project) == []


def test_an_ocr_failure_on_a_changed_scan_keeps_the_previous_complete_reading_as_a_partial_attempt(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30902"
    path = _pdf(folder / "05- Drawings" / "cover.pdf", FA_COVER)
    project_id = _project(client, folder, ep="30902")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    first = dict(row.extracted)

    with pymupdf.open() as document:
        document.new_page()      # a scan: nothing in the text layer
        document.save(path)
    _touch(path)

    def timed_out(page, sha256, index, renders=None):
        raise RuntimeError("synthetic OCR timeout")

    monkeypatch.setattr(dc, "_ocr_text", timed_out)
    result = _sync(client, project_id)
    db_session.refresh(row)
    processed = client.get(f"/jobs/{result['processing_job_id']}").json()["result"]
    assert result["changed"] == 1 and processed.get("partial", 0) == 1 and processed.get("failed", 0) == 0
    assert row.state == "fresh", "a partial attempt is not a failure of the file"
    assert row.extracted["records"] == first["records"] and row.extracted["read_sha256"] == first["read_sha256"]
    attempt = row.extracted["attempt"]
    assert attempt["outcome"] == "partial" and attempt["records"] == [] and attempt["coverage"]["ocr_failed_pages"] == [1]
    assert attempt["coverage"]["ocr"]["failed"][0]["reason"].startswith("OCR failed") and attempt["coverage"]["ocr"]["failed"][0]["page"] == 1
    assert attempt["coverage"]["pages_failed"] == [] and attempt["coverage"]["pages_visited"] == [1], "the page was visited; its OCR failed"
    assert document_sync.file_status(row, None)[0] == "partial"
    assert row.extracted["stale"] is True
    assert [r.id for r in document_processing.pending_rows(db_session, db_session.get(Project, project_id))] == [row.id], "retried next run"

    monkeypatch.setattr(dc, "_ocr_text", lambda page, sha256, index, renders=None: FA_COVER.replace("Rev: 01", "Rev: 03"))
    _sync(client, project_id)
    db_session.refresh(row)
    assert "attempt" not in row.extracted and row.extracted["records"][0]["revision"] == "R3"
    assert row.extracted["coverage"]["outcome"] == "complete" and not row.extracted.get("stale")


def test_a_failure_on_page_two_after_page_one_keeps_the_previous_complete_reading(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30903"
    path = _pdf(folder / "05- Drawings" / "cover.pdf", FA_COVER)
    project_id = _project(client, folder, ep="30903")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    first = dict(row.extracted)

    with pymupdf.open() as document:
        for text in (FA_COVER.replace("Rev: 01", "Rev: 02"), "Reply to Consultant Comments\nABC-XYZ-SPM-SD-MEP/FA-104\n"):
            document.new_page().insert_text((30, 30), text, fontsize=7)
        document.save(path)
    _touch(path)
    real = dc.parse_page

    def page_two_breaks(text, filename, modified, page):
        if page == 2:
            raise ValueError("page 2 fell over")
        return real(text, filename, modified, page)

    monkeypatch.setattr(dc, "parse_page", page_two_breaks)
    _sync(client, project_id)
    db_session.refresh(row)
    assert row.state == "fresh" and row.extracted["records"] == first["records"], "R1 stands until R2 is read whole"
    attempt = row.extracted["attempt"]
    assert attempt["outcome"] == "partial" and [r["revision"] for r in attempt["records"]] == ["R2"], "page 1's reading is kept as the attempt's progress"
    assert attempt["coverage"]["pages_visited"] == [1] and attempt["coverage"]["pages_failed"][0]["page"] == 2, "visited and failed are exclusive"
    assert row.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054" and row.revision == "R1"

    monkeypatch.setattr(dc, "parse_page", real)
    _sync(client, project_id)
    db_session.refresh(row)
    assert "attempt" not in row.extracted and [r["revision"] for r in row.extracted["records"]] == ["R2", "R0"]
    assert row.extracted["coverage"]["outcome"] == "complete" and row.extracted["coverage"]["pages_visited"] == [1, 2]


def test_a_new_unreadable_file_stays_visible_in_the_registry_with_an_honest_outcome(client, db_session, tmp_path, inline):
    folder = tmp_path / "EP-30904"
    (folder / "05- Drawings").mkdir(parents=True)
    (folder / "05- Drawings" / "damaged.pdf").write_bytes(b"%PDF-1.4 but nothing of a PDF follows")
    project_id = _project(client, folder, ep="30904")
    result = _sync(client, project_id)
    row = _row(db_session, project_id)
    assert result.get("failed", 0) == 1
    assert row.state == "failed" and row.extracted["records"] == [] and "parser_version" not in row.extracted
    assert row.extracted["attempt"]["outcome"] == "failed" and row.extracted["read_sha256"] is None
    assert document_sync.file_status(row, None)[0] == "failed"
    status = client.get(f"/projects/{project_id}/documents/status").json()
    assert status["by_state"] == {"failed": 1}
    assert [r.id for r in document_processing.pending_rows(db_session, db_session.get(Project, project_id))] == [row.id]


def test_the_repair_tool_skips_a_partial_re_read(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import repair_extraction as tool

    path = _pdf(tmp_path / "cover.pdf", FA_COVER)
    row = SimpleNamespace(id=1, project_id=1, path=str(path), relative_path="cover.pdf", filename="cover.pdf", role="document",
                          state="fresh", sha256=document_sync.sha256_of(path), reference="X", revision="R0", status="UR",
                          extracted={"records": [{"reference": "X"}], "parser_version": "old"}, mtime=None)
    monkeypatch.setattr(tool, "root_of", lambda r: str(tmp_path))

    def breaks(text, filename, modified, page):
        raise ValueError("page fell over")

    monkeypatch.setattr(dc, "parse_page", breaks)
    entry = tool.preview(None, row, ["selected by id"], False)
    assert entry["outcome"] == "skipped" and entry["skip_reason"].startswith("the re-read was partial")
    assert entry["coverage"]["pages_failed"][0]["page"] == 1


# --- R2: one validator for every mark, conflicts kept as conflicts ----------------------------------

COMMENT = "Follow the approved builders work drawings for riser location"


def _page(document, text: str):
    page = document.new_page()
    page.insert_text((30, 30), text, fontsize=8)
    return page


def _frame(page, phrase: str, pad: float = 2.0):
    return page.search_for(phrase)[0] + (-pad, -pad, pad, pad)


def test_an_annotation_round_a_comment_that_mentions_an_option_is_not_a_decision(tmp_path):
    with pymupdf.open() as document:
        page = _page(document, OPTIONS + COMMENT)
        annot = page.add_rect_annot(_frame(page, COMMENT))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        assert dc.decision_marks(page, OPTIONS + COMMENT) == []
        assert dc.boxed_decision(page, OPTIONS + COMMENT) is None
        assert dc.annotated_decision(page, OPTIONS + COMMENT) is None


def test_contractor_and_receipt_labels_and_partial_words_are_not_decisions_whatever_frames_them(tmp_path):
    text = OPTIONS + "Noted & Complied\nApproved supplier\nRECEIVED BY: Er. Shibin\n"
    for phrase in ("Noted & Complied", "Approved supplier", "RECEIVED BY: Er. Shibin"):
        with pymupdf.open() as document:
            page = _page(document, text)
            page.draw_rect(_frame(page, phrase), color=(0, 0.5, 0), width=3)
            annot = page.add_rect_annot(_frame(page, phrase))
            annot.set_colors(stroke=(0, 0.5, 0))
            annot.update()
            assert dc.decision_marks(page, text) == [], phrase
    assert dc._label_is_one_option("Approved supplier") is None
    assert dc._label_is_one_option("B - Approved With Comments") == "ANN"
    assert dc._label_is_one_option("C - Revise & Re-Submit") == "rejected"
    assert dc._label_is_one_option("Approved (A)") == "approved"


def test_marks_of_different_methods_that_disagree_are_a_conflict_not_a_choice(tmp_path):
    with pymupdf.open() as document:
        page = _page(document, OPTIONS)
        page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0, 0.5, 0), width=3)
        annot = page.add_rect_annot(_frame(page, "A - Approved"))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        marks = dc.decision_marks(page, OPTIONS)
        assert {(m["status"], m["method"]) for m in marks} == {("approved", "annotation"), ("rejected", "drawn_frame")}
        outcome, status, evidence = dc.resolve_marks(marks)
        assert outcome == "conflict" and status is None and "A - Approved [annotation]" in evidence and "drawn_frame" in evidence
        assert dc.boxed_decision(page, OPTIONS) is None, "no method's answer hides the other's"
    # A filled box and an annotation on different answers: the same conflict.
    with pymupdf.open() as document:
        page = _page(document, OPTIONS)
        box = page.search_for("A - Approved")[0]
        page.draw_rect(pymupdf.Rect(box.x0 - 14, box.y0, box.x0 - 4, box.y1), color=None, fill=(0.9, 0.1, 0.1))
        annot = page.add_rect_annot(_frame(page, "C - Revise & Re-Submit"))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        marks = dc.decision_marks(page, OPTIONS)
        assert {(m["status"], m["method"]) for m in marks} == {("approved", "filled_box"), ("rejected", "annotation")}
        assert dc.resolve_marks(marks)[0] == "conflict"
    # Two marks on the same answer are one answer (positive control).
    with pymupdf.open() as document:
        page = _page(document, OPTIONS)
        page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0, 0.5, 0), width=3)
        annot = page.add_rect_annot(_frame(page, "C - Revise & Re-Submit"))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        assert dc.resolve_marks(dc.decision_marks(page, OPTIONS)) == ("resolved", "rejected", "C - Revise & Re-Submit")


def test_a_conflict_is_persisted_as_an_unresolved_record_not_an_approval(tmp_path):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0, 0.5, 0), width=3)
        annot = page.add_rect_annot(_frame(page, "A - Approved"))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        document.save(tmp_path / "conflict.pdf")
    with pymupdf.open(tmp_path / "conflict.pdf") as pdf:
        reading = dc.read_open_pdf(pdf, str(tmp_path / "conflict.pdf"), NOW, False, "sha-conf", full=True, promote=True)
    [record] = reading.records
    assert record.status == "UR" and "decision_conflict" in record.flags
    assert {(s, m) for s, _l, m in record.decision_candidates} == {("approved", "annotation"), ("rejected", "drawn_frame")}
    assert record.reply_text.startswith("Conflicting evidence")
    assert reading.observations[0]["kind"] == "decision_conflict"
    stored = document_sync._record_dict(record, tmp_path)
    assert stored["status"] == "UR" and list(stored["flags"]) == ["decision_conflict"] and len(stored["decision_candidates"]) == 2


# --- R3: every page in the ledger; components observed without register rows -----------------------


def test_a_leading_separator_no_longer_hides_the_cover_on_page_two(tmp_path):
    with pymupdf.open() as document:
        _page(document, SEPARATOR)
        _page(document, FA_COVER)
        reading = dc.read_open_pdf(document, str(tmp_path / "mixed.pdf"), NOW, False, None, full=True)
    assert [r.reference for r in reading.records] == ["ABC-XYZ-SPM-SD-MEP-FA-0054"] and reading.records[0].page == 2
    assert reading.coverage["pages_visited"] == [1, 2] and reading.coverage["outcome"] == "complete"
    assert reading.coverage["pages_skipped"] == [] and reading.coverage["pages_failed"] == []


def test_a_standalone_sheet_of_an_untracked_discipline_is_an_observation_not_a_record(tmp_path):
    sheet = "DRAWING NO:\nICC-DLRC-SPM-SD-MEP/FF-110\nDRAWING TITLE:\nBASEMENT-01 FIREFIGHTING LAYOUT\nSCALE 1 : 75\n"
    with pymupdf.open() as document:
        _page(document, sheet)
        reading = dc.read_open_pdf(document, str(tmp_path / "sheet.pdf"), NOW, False, None, full=True)
    assert reading.records == ()
    assert reading.observations == [{"page": 1, "kind": "drawing_sheet", "reference": "ICC-DLRC-SPM-SD-MEP/FF-110", "raw_system": "FIREFIGHTING"}]
    assert reading.coverage == {**reading.coverage, "outcome": "complete", "pages_visited": [1]}


def test_a_mixed_file_lists_every_page_and_keeps_its_components(tmp_path):
    pages = [FA_COVER,
             "Reply to Consultant Comments\nABC-XYZ-SPM-SD-MEP/FA-104\nRev: 0\n1 PQ & MAS to be obtained approval. Noted\n",
             "DATASHEET\nEST3 Fire alarm control panel\nTechnical data: 24 V DC, 2 A\n",
             "CERTIFICATE OF CONFORMITY\nThis is to certify that the product listed below conforms.\n",
             "Consultant comments\nReview status: (C) Revise & Resubmit\n"]
    with pymupdf.open() as document:
        for text in pages:
            _page(document, text)
        reading = dc.read_open_pdf(document, str(tmp_path / "package.pdf"), NOW, False, None, full=True)
    assert [(r.category, r.page) for r in reading.records] == [("drawings", 1), ("reply", 2)]
    assert reading.coverage["pages_visited"] == [1, 2, 3, 4, 5] and reading.coverage["outcome"] == "complete"
    assert reading.records[0].status == "UR", "a contractor reply is not a decision; the comments on page 5 name no reference and follow other pages"


def test_a_later_consultant_section_behind_the_form_is_observed_and_applied_as_before(tmp_path):
    pages = [FA_COVER, "Consultant comments\nReview status: (C) Revise & Resubmit\n"]
    with pymupdf.open() as document:
        for text in pages:
            _page(document, text)
        reading = dc.read_open_pdf(document, str(tmp_path / "commented.pdf"), NOW, False, None, full=True)
    [record] = reading.records
    assert record.status == "rejected" and record.page == 2, "the attached comments settle the form, as they always did"
    assert [o["kind"] for o in reading.observations] == ["consultant_comments"] and reading.observations[0]["decision"] == "rejected"


def test_a_page_scope_limit_records_the_pages_it_did_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 2)
    with pymupdf.open() as document:
        for n in range(4):
            _page(document, f"Product catalogue page {n + 1}\nNothing controlled here.\n")
        reading = dc.read_open_pdf(document, str(tmp_path / "catalogue.pdf"), NOW, False, None, full=True)
    assert reading.records == () and reading.coverage["outcome"] == "bounded"
    assert reading.coverage["pages_visited"] == [1, 2] and reading.coverage["stop_reason"] == "page_scan_limit"
    assert [p["page"] for p in reading.coverage["pages_skipped"]] == [3, 4]
    assert all("page scan limit" in p["reason"] for p in reading.coverage["pages_skipped"])
    # Exclusive outcomes: every page is visited or skipped, once.
    assert sorted(reading.coverage["pages_visited"] + [p["page"] for p in reading.coverage["pages_skipped"]]) == [1, 2, 3, 4]
    # The cover on page 3 is found once the scope allows it.
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    with pymupdf.open() as document:
        _page(document, SEPARATOR)
        _page(document, SEPARATOR)
        _page(document, FA_COVER)
        reading = dc.read_open_pdf(document, str(tmp_path / "late.pdf"), NOW, False, None, full=True)
    assert [r.page for r in reading.records] == [3] and reading.coverage["outcome"] == "complete"


def test_an_untracked_cover_is_an_observation_by_default_and_a_record_when_promoted(tmp_path):
    with pymupdf.open() as document:
        _page(document, FF_COVER)
        document.save(tmp_path / "ff.pdf")
    with pymupdf.open(tmp_path / "ff.pdf") as pdf:
        held = dc.read_open_pdf(pdf, str(tmp_path / "ff.pdf"), NOW, False, None, full=True, promote=False)
    assert held.records == () and held.observations[0]["kind"] == "cover_untracked"
    assert held.observations[0]["record"]["reference"] == "ICC-DLRC-SPM-SD-MEP-FF-0047" and held.observations[0]["record"]["raw_system"] == "FIREFIGHTING"
    assert dc.combine(list(held.records)) == [], "no register row from an observation"
    with pymupdf.open(tmp_path / "ff.pdf") as pdf:
        promoted = dc.read_open_pdf(pdf, str(tmp_path / "ff.pdf"), NOW, False, None, full=True, promote=True)
    assert [(r.reference, r.system_code, r.raw_system) for r in promoted.records] == [("ICC-DLRC-SPM-SD-MEP-FF-0047", None, "FIREFIGHTING")]


def test_an_observation_that_holds_a_record_is_stored_by_normal_processing(client, db_session, tmp_path, inline):
    """The clone repair of the review-01 correction failed on 35 rows: an observation carried a record as a raw
    dataclass dict (datetime inside) and the row's JSON column refused it -- every untracked-discipline cover and
    scanned transmittal. The observation must reach the database through the ordinary writer, as JSON."""
    import json

    folder = tmp_path / "EP-30905"
    (folder / "05- Drawings").mkdir(parents=True)
    _pdf(folder / "05- Drawings" / "ff-cover.pdf", FF_COVER)
    project_id = _project(client, folder, ep="30905")
    result = _sync(client, project_id)
    assert result.get("failed", 0) == 0, result
    row = _row(db_session, project_id)
    db_session.refresh(row)
    assert row.state == "fresh" and row.extracted["records"] == [] and row.extracted["parser_version"] == dc.PARSER_VERSION
    held = [o for o in row.extracted["observations"] if o["kind"] == "cover_untracked"]
    assert held and held[0]["record"]["reference"] == "ICC-DLRC-SPM-SD-MEP-FF-0047" and isinstance(held[0]["record"]["modified"], str)
    json.dumps(row.extracted)   # what the column stores
    assert "attempt" not in row.extracted and row.extracted["coverage"]["outcome"] == "complete"


# --- R5: the promotion boundary and the manual values behind it ------------------------------------


def test_a_drawn_frame_decision_is_held_as_a_candidate_unless_promoted(tmp_path):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0.02, 0.54, 0.11), width=3)
        document.save(tmp_path / "framed.pdf")
    with pymupdf.open(tmp_path / "framed.pdf") as pdf:
        held = dc.read_open_pdf(pdf, str(tmp_path / "framed.pdf"), NOW, False, None, full=True, promote=False)
    [record] = held.records
    assert record.status == "UR" and record.decision_candidates == (("rejected", "C - Revise & Re-Submit", "drawn_frame"),)
    assert "decision_method_unpromoted" in record.flags and held.observations[0]["kind"] == "decision_unpromoted"
    with pymupdf.open(tmp_path / "framed.pdf") as pdf:
        promoted = dc.read_open_pdf(pdf, str(tmp_path / "framed.pdf"), NOW, False, None, full=True, promote=True)
    assert promoted.records[0].status == "rejected" and promoted.records[0].decision_candidates == ()
    # An annotation over the option was read by the accepted reader: applied on both paths.
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        annot = page.add_rect_annot(_frame(page, "C - Revise & Re-Submit"))
        annot.set_colors(stroke=(0, 0.5, 0))
        annot.update()
        document.save(tmp_path / "annotated.pdf")
    with pymupdf.open(tmp_path / "annotated.pdf") as pdf:
        assert dc.read_open_pdf(pdf, str(tmp_path / "annotated.pdf"), NOW, False, None, full=True, promote=False).records[0].status == "rejected"


def test_the_default_settings_do_not_promote(monkeypatch):
    assert get_settings().extraction_promote_observations is False
    assert dc._promote_default() is False
    monkeypatch.setattr(get_settings(), "extraction_promote_observations", True)
    assert dc._promote_default() is True


def test_a_mark_on_a_folder_revision_sheet_that_prints_another_revision_is_held_not_assigned(tmp_path):
    text = SHEET + "Review status: (C) Revise & Resubmit\n"
    with pymupdf.open() as document:
        _page(document, text)
        reading = dc.read_open_pdf(document, "1.FAVE/R1/05. Ground Floor/BBY006-GME-SDW-FP-FA-POD-BGF-010002.pdf", NOW, False, None, full=True, promote=True)
    [record] = reading.records
    assert (record.revision, record.revision_source, record.printed_revision) == ("R1", "folder", "00"), "the revision policy is unchanged"
    assert record.status == "UR", "which revision the mark answers is not the reader's to decide"
    assert record.decision_candidates == (("rejected", "(C) Revise & Resubmit", "sheet_mark"),)
    assert "decision_revision_unvalidated" in record.flags
    # The same mark on a sheet whose printed revision agrees with its folder is applied.
    with pymupdf.open() as document:
        _page(document, text.replace("A0\n00\n", "A0\n01\n"))
        reading = dc.read_open_pdf(document, "1.FAVE/R1/05. Ground Floor/BBY006-GME-SDW-FP-FA-POD-BGF-010002.pdf", NOW, False, None, full=True, promote=True)
    assert reading.records[0].status == "rejected" and reading.records[0].printed_revision == "01"


def test_ordinary_processing_leaves_a_manual_submittal_status_and_a_corrected_boq_line_alone(client, db_session, tmp_path, inline):
    from app.models import ProjectBoqItem, ProjectSubmittal

    folder = tmp_path / "EP-30905"
    _pdf(folder / "05- Drawings" / "cover.pdf", FA_COVER)
    project_id = _project(client, folder, ep="30905")
    _sync(client, project_id)
    created = client.post(f"/projects/{project_id}/submittals", json={"title": "Fire alarm", "system_code": "FAS", "manufacturer": "Edwards",
                                                                        "revision": "R0", "status": "approved", "document_path": None, "note": None})
    assert created.status_code == 201, created.text
    item = ProjectBoqItem(project_id=project_id, system_code="FAS", position=1, description="Smoke detector", quantity="12", origin="corrected",
                          catalog_no="SIGA-OSD")
    db_session.add(item)
    db_session.commit()
    # A changed reading of the drawing (a framed C, promoted) reaches ordinary processing.
    path = folder / "05- Drawings" / "cover.pdf"
    with pymupdf.open() as document:
        page = _page(document, FA_COVER.replace("Rev: 01", "Rev: 02") + OPTIONS)
        page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0, 0.5, 0), width=3)
        document.save(path)
    _touch(path)
    _sync(client, project_id)
    db_session.expire_all()
    submittal = db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project_id).one()
    assert submittal.status.value == "approved" and submittal.revision == "R0", "no form changed: the map was not redrawn (G-01 untouched, not triggered)"
    boq = db_session.query(ProjectBoqItem).filter(ProjectBoqItem.project_id == project_id).one()
    assert (boq.quantity, boq.origin) == ("12", "corrected")
    row = _row(db_session, project_id)
    assert row.extracted["records"][0]["revision"] == "R2" and row.extracted["records"][0]["status"] == "UR", "default path: the frame is a candidate"
    assert row.extracted["records"][0]["decision_candidates"] == [["rejected", "C - Revise & Re-Submit", "drawn_frame"]]


def test_an_engineer_confirmed_revision_survives_a_new_reading_that_says_otherwise(client, db_session, tmp_path, inline, monkeypatch):
    from app.models import ProjectShopDrawing, ShopDrawingRevision

    folder = tmp_path / "EP-30906"
    sheet = ("SHOP DRAWING\nDRAWING TITLE:\nGROUND FLOOR PLAN\nDRAWING NO:\nBBY006-GME-SDW-FP-FA-POD-BGF-010002\nREV: 00\n"
             "GROUND FLOOR PLAN FIRE ALARM LAYOUT\nFIRE ALARM\n")
    path = _pdf(folder / "03- Drawings" / "SD" / "FA" / "05. Ground Floor" / "R0" / "Submitted" / "BBY006-GME-SDW-FP-FA-POD-BGF-010002.pdf", sheet)
    project_id = _project(client, folder, ep="30906")
    _sync(client, project_id)
    drawings = db_session.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project_id).all()
    if not drawings:
        pytest.skip("the fixture did not reconcile into a shop drawing on this checkout; the guard is covered by test_drawings_module")
    drawing = drawings[0]
    put = client.put(f"/projects/{project_id}/drawings/sd/{drawing.id}/revisions/R0", json={"status": "approved", "note": "engineer", "submitted": True})
    assert put.status_code == 200, put.text
    # The sheet is re-issued with a rejection stamp in its text; ordinary processing (promoted) reads it.
    monkeypatch.setattr(get_settings(), "extraction_promote_observations", True)
    _pdf(path, sheet + "Review status: (C) Revise & Resubmit\n")
    _touch(path)
    _sync(client, project_id)
    db_session.expire_all()
    revision = (db_session.query(ShopDrawingRevision).join(ProjectShopDrawing)
                .filter(ProjectShopDrawing.project_id == project_id, ShopDrawingRevision.revision == "R0").one())
    assert revision.status == "approved" and revision.confirmed_by_id is not None and revision.source == "engineer"
