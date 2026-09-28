"""M2 review 02 -- the corrections: a legacy reading (no parser version) is
kept in front of an attempt that did not complete, and a bounded re-read
carries the records of the pages it did not visit (A); the page's text, its
marks and its OCR are settled together, once, and disagreement is a
conflict the row shows (B); the extraction profile is part of a reading's
identity, so a reading made under one profile is never reused as the other's
(C). Everything runs in the isolated test environment (conftest); no live
data, model or worker; OCR is scripted where a test needs it."""
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from app.models import Project, ProjectDocument
from app.services import document_control as dc
from app.services import document_processing, document_sync
from .test_document_sync import _pdf, _project, _result
from .test_extraction_m2 import OPTIONS
from .test_extraction_m2_review import FA_COVER, SEPARATOR, _frame, _page, _row, _sync, _touch, inline  # noqa: F401 -- fixture

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
TEXT_APPROVED = "Review status: (A) Approved\n"
TEXT_REJECTED = "Review status: (C) Revise & Re-Submit\n"
COMMENT = "Approved refer to marked comments on drawing\n"
REFERENCE = "ABC-XYZ-SPM-SD-MEP-FA-0054"


def _annotate(page, phrase: str) -> None:
    annot = page.add_rect_annot(_frame(page, phrase))
    annot.set_colors(stroke=(0, 0.5, 0))
    annot.update()


def _draw(page, phrase: str) -> None:
    page.draw_rect(_frame(page, phrase), color=(0, 0.5, 0), width=3)


def _save(document, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def _read(path: Path, *, promote: bool, ocr: bool = False):
    with pymupdf.open(path) as pdf:
        return dc.read_open_pdf(pdf, str(path), NOW, ocr, None, full=True, promote=promote)


def _scripted_ocr(monkeypatch, outcome) -> None:
    """OCR that returns `outcome` (text) or raises it (an exception) for every page, without Tesseract."""
    monkeypatch.setattr(dc, "_image_regions", lambda page: [1])
    monkeypatch.setattr(dc, "_prefer_full_page", lambda page, regions: True)

    def ocr(page, sha256, index, renders=None):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(dc, "_ocr_text", ocr)
    monkeypatch.setattr(document_processing.submittal_scanner, "ocr_available", lambda: True)


def _processed(client, result: dict) -> dict:
    return client.get(f"/jobs/{result['processing_job_id']}").json()["result"]


def _candidates(record) -> set:
    return {(status, method) for status, _label, method in record.decision_candidates}


# --- A: legacy readings are kept; a bounded run carries what it did not visit ---------------------


def test_a_legacy_reading_without_parser_version_survives_failures_and_a_successful_retry(client, db_session, tmp_path, inline, monkeypatch):
    """The live index holds 367 readings with records and no parser version.
    Missing provenance is unknown provenance: the records, the form evidence
    and the mirrors stay through an open failure, an OCR failure and a
    second failure; no parser version, hash or time is invented for them;
    a successful retry replaces them and keeps the form evidence."""
    folder = tmp_path / "EP-30911"
    path = _pdf(folder / "05- Drawings" / "cover.pdf", FA_COVER)
    project_id = _project(client, folder, ep="30911")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    legacy = {"records": list(row.extracted["records"]), "notes": [], "form": {"is_submittal": False, "note": "legacy form evidence"}}
    row.extracted = legacy
    db_session.commit()
    mirrors = (row.reference, row.revision, row.status)
    assert mirrors == (REFERENCE, "R1", "UR")

    def kept_as_it_was():
        assert row.extracted["records"] == legacy["records"], "the legacy records stand"
        assert row.extracted["form"] == legacy["form"], "the legacy form evidence stands"
        for key in ("parser_version", "read_sha256", "read_at", "coverage", "profile"):
            assert key not in row.extracted, f"no {key} is invented for a legacy reading"
        assert row.extracted["stale"] is None and row.extracted["source_identity"] == "unknown"
        assert (row.reference, row.revision, row.status) == mirrors

    # 1. the file is replaced by something that is not a PDF: an open failure
    path.write_bytes(b"not a PDF at all")
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert result.get("failed", 0) == 1 and row.state == "failed" and row.error
    kept_as_it_was()
    attempt = row.extracted["attempt"]
    assert attempt["outcome"] == "failed" and attempt["sha256"] == row.sha256 and attempt["parser_version"] == dc.PARSER_VERSION
    assert attempt["profile"] == "default"

    # 2. a scan whose OCR fails: a partial attempt
    with pymupdf.open() as document:
        document.new_page()
        path.write_bytes(document.tobytes())     # over the bytes in place (see step 4)
    _touch(path)
    _scripted_ocr(monkeypatch, RuntimeError("synthetic OCR timeout"))
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert _processed(client, result).get("partial", 0) == 1 and row.state == "fresh"
    kept_as_it_was()
    attempt = row.extracted["attempt"]
    assert attempt["outcome"] == "partial" and attempt["records"] == []
    assert attempt["coverage"]["ocr"]["failed"][0]["page"] == 1 and attempt["coverage"]["pages_visited"] == [1]
    project = db_session.get(Project, project_id)
    assert [r.id for r in document_processing.pending_rows(db_session, project)] == [row.id], "the incomplete work is retried"

    # 3. failed again (still not a PDF): still kept
    path.write_bytes(b"still not a PDF")
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert result["changed"] == 1, result
    assert _processed(client, result).get("failed", 0) == 1, _processed(client, result)
    assert row.state == "failed"
    kept_as_it_was()
    assert row.extracted["attempt"]["outcome"] == "failed"

    # 4. a good file: read, the attempt superseded, the form evidence carried
    # (written over the bytes in place: pymupdf's save replaces the file, which Windows refuses while a
    # handle of the earlier reads is still closing)
    path.write_bytes(_pdf(tmp_path / "good.pdf", FA_COVER.replace("Rev: 01", "Rev: 02")).read_bytes())
    _touch(path)
    monkeypatch.setattr(dc, "_image_regions", lambda page: [])      # the scripted OCR failure no longer applies
    _sync(client, project_id)
    db_session.refresh(row)
    assert row.state == "fresh" and "attempt" not in row.extracted and row.extracted.get("stale") is not True
    assert row.extracted["records"][0]["revision"] == "R2" and row.extracted["read_sha256"] == row.sha256
    assert row.extracted["parser_version"] == dc.PARSER_VERSION and row.extracted["profile"] == "default"
    assert row.extracted["form"] == legacy["form"]
    assert (row.reference, row.revision, row.status) == (REFERENCE, "R2", "UR")
    assert document_processing.pending_rows(db_session, project) == []


def test_reading_to_keep_and_staleness_tell_legacy_from_modern_from_nothing():
    legacy = {"records": [{"reference": "X"}], "notes": []}
    modern_empty = {"records": [], "parser_version": dc.PARSER_VERSION, "read_sha256": "abc"}
    only_an_attempt = {"records": [], "notes": [], "attempt": {"outcome": "failed"}, "read_sha256": None}
    assert document_sync.reading_to_keep(legacy) is legacy
    assert document_sync.reading_to_keep(modern_empty) is modern_empty, "an empty successful reading is a reading"
    assert document_sync.reading_to_keep(only_an_attempt) is None and document_sync.reading_to_keep(None) is None
    assert document_sync.staleness(legacy, "abc") is None, "unknown source identity is unknown, not fresh"
    assert document_sync.staleness(modern_empty, "abc") is False and document_sync.staleness(modern_empty, "def") is True


def _package(path: Path, first_page: str) -> Path:
    """Twelve neutral pages, then a valid cover on page 13."""
    with pymupdf.open() as document:
        _page(document, first_page)
        for _ in range(11):
            _page(document, SEPARATOR)
        _page(document, FA_COVER)
        return _save(document, path)


def test_a_bounded_re_read_carries_the_records_of_the_pages_it_did_not_visit(client, db_session, tmp_path, inline, monkeypatch):
    """A complete earlier reading holds a record from page 13. A later reader
    stops at its page budget before page 13: on the same bytes (the repair
    path) the record is carried as `carried_unvisited`; on changed bytes
    (ordinary processing) as `carried_unvisited` + `carried_unverified`. It
    is never called absent. A wider reader that reaches page 13 reads it
    itself and the flags go."""
    from scripts import repair_extraction as tool

    folder = tmp_path / "EP-30912"
    path = folder / "05- Drawings" / "package.pdf"
    _package(path, SEPARATOR)
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)      # the earlier, wider reader
    project_id = _project(client, folder, ep="30912")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    first = dict(row.extracted)
    assert [r["page"] for r in first["records"]] == [13] and first["coverage"]["outcome"] == "complete"
    assert row.reference == REFERENCE

    # same bytes, narrower budget: the repair path
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    monkeypatch.setattr(tool, "root_of", lambda r: str(folder))
    entry = tool.preview(db_session, row, ["selected by id"], False)
    assert entry["outcome"] in ("would_repair", "unchanged") and entry["carried_from_previous"] == [13]
    assert entry["new"]["reference"] == REFERENCE, "the mirror keeps the carried record's reference"
    tool.apply_row(db_session, row, entry)
    db_session.refresh(row)
    records = row.extracted["records"]
    assert [r["page"] for r in records] == [13] and records[0]["reference"] == REFERENCE
    assert records[0]["flags"] == ["carried_unvisited"], "same bytes: carried, not unverified"
    assert row.extracted["coverage"]["outcome"] == "bounded" and row.extracted["coverage"]["carried_from_previous"] == [13]
    assert 13 in [p["page"] for p in row.extracted["coverage"]["pages_skipped"]]
    assert any("not visited by this reading" in note for note in row.extracted["notes"])
    assert row.reference == REFERENCE and "attempt" not in row.extracted

    # changed bytes (page 1 rewritten), ordinary processing
    _package(path, SEPARATOR.replace("separator", "divider"))
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert _processed(client, result).get("failed", 0) == 0 and row.state == "fresh"
    records = row.extracted["records"]
    assert [r["page"] for r in records] == [13] and set(records[0]["flags"]) == {"carried_unvisited", "carried_unverified"}
    assert row.extracted["read_sha256"] == row.sha256 and row.extracted["coverage"]["outcome"] == "bounded"
    assert any("unverified" in note for note in row.extracted["notes"])
    assert records[0]["retained"]["source_sha256"] == first["read_sha256"] and records[0]["retained"]["profile"] == "default"
    assert row.reference == REFERENCE and "attempt" not in row.extracted
    assert document_processing.pending_rows(db_session, db_session.get(Project, project_id)) == [], "a bounded reading is not retried for ever"

    # a wider reader reaches page 13 again: read, not carried
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    _package(path, SEPARATOR.replace("separator", "sheet"))
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    records = row.extracted["records"]
    assert [r["page"] for r in records] == [13] and records[0]["flags"] == [] and row.extracted["coverage"]["outcome"] == "complete"
    assert "carried_from_previous" not in row.extracted["coverage"]


def test_carry_unvisited_keeps_records_of_unknown_page_and_flags_by_source_identity():
    kept = {"records": [{"reference": "A", "page": 1}, {"reference": "B", "page": 13}, {"reference": "C"}], "read_sha256": "same"}
    coverage = {"pages_skipped": [{"page": 13, "reason": "page scan limit"}]}
    carried, note = document_sync.carry_unvisited(kept, coverage, "same")
    assert [(r["reference"], r["flags"]) for r in carried] == [("B", ["carried_unvisited"]), ("C", ["carried_unvisited"])]
    assert "2 records" in note and "unverified" not in note
    carried, note = document_sync.carry_unvisited(kept, coverage, "other")
    assert all(r["flags"] == ["carried_unvisited", "carried_unverified"] for r in carried) and "unverified" in note
    carried, note = document_sync.carry_unvisited({"records": kept["records"]}, coverage, "same")
    assert all("carried_unverified" in r["flags"] for r in carried), "unknown source identity: unverified"
    assert document_sync.carry_unvisited(kept, {"pages_skipped": []}, "same") == ([], None)


# --- B: text, marks and OCR settled together ---------------------------------------------------------


@pytest.mark.parametrize("promote", [False, True])
def test_a_status_in_the_text_and_a_frame_on_another_option_are_a_conflict(tmp_path, promote):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        _draw(page, "C - Revise & Re-Submit")
        path = _save(document, tmp_path / "text-vs-frame.pdf")
    reading = _read(path, promote=promote)
    [record] = reading.records
    assert record.status == "UR" and "decision_conflict" in record.flags
    assert _candidates(record) == {("approved", "text"), ("rejected", "drawn_frame")}
    assert ("decision_method_unpromoted" in record.flags) is (not promote)
    assert [o["kind"] for o in reading.observations] == ["decision_conflict"]
    # the same text with no other evidence is the status, as always
    with pymupdf.open() as document:
        _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        plain = _save(document, tmp_path / "text-only.pdf")
    [record] = _read(plain, promote=promote).records
    assert record.status == "approved" and record.flags == () and record.decision_candidates == ()


@pytest.mark.parametrize("promote", [False, True])
def test_marks_that_conflict_are_not_settled_by_what_ocr_reads_afterwards(tmp_path, monkeypatch, promote):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        _annotate(page, "A - Approved")
        _draw(page, "C - Revise & Re-Submit")
        path = _save(document, tmp_path / "a-annot-c-frame.pdf")
    _scripted_ocr(monkeypatch, TEXT_APPROVED)
    reading = _read(path, promote=promote, ocr=True)
    [record] = reading.records
    assert record.status == "UR" and "decision_conflict" in record.flags
    assert _candidates(record) == {("approved", "annotation"), ("rejected", "drawn_frame"), ("approved", "ocr")}
    assert reading.coverage["ocr"]["attempted"] == 1
    # the reverse: annotation C, frame A, OCR C
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        _annotate(page, "C - Revise & Re-Submit")
        _draw(page, "A - Approved")
        path = _save(document, tmp_path / "c-annot-a-frame.pdf")
    _scripted_ocr(monkeypatch, TEXT_REJECTED)
    [record] = _read(path, promote=promote, ocr=True).records
    assert record.status == "UR" and _candidates(record) == {("rejected", "annotation"), ("approved", "drawn_frame"), ("rejected", "ocr")}


def test_agreeing_text_marks_and_ocr_settle_one_status_with_every_method_recorded(tmp_path, monkeypatch):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        _annotate(page, "A - Approved")
        path = _save(document, tmp_path / "all-agree.pdf")
    _scripted_ocr(monkeypatch, TEXT_APPROVED)
    [record] = _read(path, promote=False, ocr=True).records
    assert record.status == "approved" and record.flags == ()
    assert _candidates(record) == {("approved", "text"), ("approved", "annotation"), ("approved", "ocr")}
    # a drawn frame on the same answer beside them, default path: the promoted evidence settles it, the frame is listed
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        _draw(page, "A - Approved")
        path = _save(document, tmp_path / "text-and-frame-agree.pdf")
    [record] = _read(path, promote=False).records
    assert record.status == "approved" and record.flags == () and _candidates(record) == {("approved", "text"), ("approved", "drawn_frame")}


def test_an_unpromoted_frame_does_not_return_as_a_status_through_ocr_of_the_options_list(tmp_path, monkeypatch):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        _draw(page, "C - Revise & Re-Submit")
        path = _save(document, tmp_path / "frame-then-ocr.pdf")
    _scripted_ocr(monkeypatch, OPTIONS)      # OCR reads the printed list of options: it answers nothing
    reading = _read(path, promote=False, ocr=True)
    [record] = reading.records
    assert record.status == "UR" and record.flags == ("decision_method_unpromoted",)
    assert _candidates(record) == {("rejected", "drawn_frame")} and [o["kind"] for o in reading.observations] == ["decision_unpromoted"]
    [record] = _read(path, promote=True, ocr=True).records
    assert record.status == "rejected", "on the evaluation path the frame is the decision"


def test_an_ocr_failure_leaves_the_text_and_mark_evidence_to_settle_the_page(tmp_path, monkeypatch):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        _annotate(page, "A - Approved")
        path = _save(document, tmp_path / "ocr-fails.pdf")
    _scripted_ocr(monkeypatch, RuntimeError("synthetic OCR failure"))
    reading = _read(path, promote=False, ocr=True)
    [record] = reading.records
    assert record.status == "approved" and _candidates(record) == {("approved", "text"), ("approved", "annotation")}
    assert reading.coverage["outcome"] == "partial" and reading.coverage["ocr"]["failed"][0]["page"] == 1
    assert reading.coverage["pages_visited"] == [1] and reading.coverage["pages_failed"] == [], "the page was read; its OCR was not"


def test_a_frame_round_a_comment_naming_an_option_is_noise_beside_the_text_status(tmp_path):
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + COMMENT + TEXT_APPROVED)
        _draw(page, "Approved refer to marked comments on drawing")
        path = _save(document, tmp_path / "comment-frame.pdf")
    [record] = _read(path, promote=True).records
    assert record.status == "approved" and record.flags == () and record.decision_candidates == ()


def test_a_conflict_reaches_the_row_and_its_register_row_as_under_review(client, db_session, tmp_path, inline):
    folder = tmp_path / "EP-30913"
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS + TEXT_APPROVED)
        _draw(page, "C - Revise & Re-Submit")
        _save(document, folder / "05- Drawings" / "conflict.pdf")
    project_id = _project(client, folder, ep="30913")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    [stored] = row.extracted["records"]
    assert row.status == "UR" and stored["status"] == "UR" and "decision_conflict" in stored["flags"]
    assert {(c[0], c[2]) for c in stored["decision_candidates"]} == {("approved", "text"), ("rejected", "drawn_frame")}
    loaded, _notes = document_sync.log_records(db_session, db_session.get(Project, project_id))
    ours = [r for r in loaded if getattr(r, "reference", None) == REFERENCE]
    assert ours and all(r.status == "UR" for r in ours), "what the log is built from says under review"
    status = client.get(f"/projects/{project_id}/documents/status").json()
    assert status["by_state"] == {"fresh": 1}


# --- C: the extraction profile is part of a reading's identity ------------------------------------


def _framed_cover(path: Path) -> Path:
    with pymupdf.open() as document:
        page = _page(document, FA_COVER + OPTIONS)
        _draw(page, "C - Revise & Re-Submit")
        return _save(document, path)


def test_a_reading_carries_its_profile_and_freshness_checks_it(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30914"
    path = _framed_cover(folder / "05- Drawings" / "framed.pdf")
    project_id = _project(client, folder, ep="30914")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    assert row.extracted["profile"] == "default" and row.extracted["coverage"]["promoted"] is False
    assert row.status == "UR" and row.extracted["records"][0]["flags"] == ["decision_method_unpromoted"]
    assert document_processing.parser_current(row) is True and document_processing._previous_sha(row) == row.sha256

    # the gate turned on: the stored default reading is not the promoted one, whatever the bytes
    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    assert dc.extraction_profile() == "promoted"
    assert document_processing.parser_current(row) is False and document_processing._previous_sha(row) is None
    read = document_processing.read_task(row.path, row.relative_path, document_processing._previous_sha(row), False)
    assert read.get("unchanged") is not True and read["coverage"]["promoted"] is True
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert _processed(client, result)["unchanged_after_hash"] == 0
    assert row.extracted["profile"] == "promoted" and row.status == "rejected" and row.extracted["records"][0]["flags"] == []
    promoted_at = row.extracted["read_at"]

    # the same profile again, same bytes: reused, idempotent
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert _processed(client, result)["unchanged_after_hash"] == 1 and row.extracted["read_at"] == promoted_at

    # the gate turned off again: the promoted reading does not stand for the default one
    monkeypatch.setattr(dc, "_promote_default", lambda: False)
    assert document_processing.parser_current(row) is False
    _touch(path)
    result = _sync(client, project_id)
    db_session.refresh(row)
    assert _processed(client, result)["unchanged_after_hash"] == 0
    assert row.extracted["profile"] == "default" and row.status == "UR" and row.extracted["records"][0]["decision_candidates"]

    # a persisted reading whose profile is not recorded is not assumed to be either profile's
    without = dict(row.extracted)
    without.pop("profile")
    row.extracted = without
    db_session.commit()
    assert document_processing.parser_current(row) is False and document_processing._previous_sha(row) is None


def test_a_duplicate_copy_is_not_given_the_other_profiles_reading(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30915"
    a = _framed_cover(folder / "05- Drawings" / "framed.pdf")
    b = folder / "05- Drawings" / "Archive" / "framed.pdf"
    b.parent.mkdir(parents=True)
    b.write_bytes(a.read_bytes())
    project_id = _project(client, folder, ep="30915")
    _sync(client, project_id)
    rows = db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).all()
    assert {r.extracted["profile"] for r in rows} == {"default"} and {r.status for r in rows} == {"UR"}

    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    _touch(a)
    _touch(b)
    result = _sync(client, project_id)
    for r in rows:
        db_session.refresh(r)
    processed = _processed(client, result)
    assert processed["unchanged_after_hash"] == 0 and processed.get("failed", 0) == 0
    assert {r.extracted["profile"] for r in rows} == {"promoted"} and {r.status for r in rows} == {"rejected"}, \
        "neither copy inherited the default-profile reading"


def test_the_repair_tool_selects_readings_of_another_profile_and_is_idempotent_within_one(client, db_session, tmp_path, inline, monkeypatch):
    from scripts import repair_extraction as tool

    folder = tmp_path / "EP-30916"
    _framed_cover(folder / "05- Drawings" / "framed.pdf")
    project_id = _project(client, folder, ep="30916")
    _sync(client, project_id)
    project = db_session.get(Project, project_id)
    row = _row(db_session, project_id)
    monkeypatch.setattr(tool, "root_of", lambda r: str(folder))
    assert tool.select_rows(db_session, project, {"parser-outdated"}, []) == [], "same parser, same profile: nothing to repair"

    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    selected = tool.select_rows(db_session, project, {"parser-outdated"}, [])
    assert [(r.id, reasons[0].startswith("read under another extraction profile")) for r, reasons in selected] == [(row.id, True)]
    entry = tool.preview(db_session, row, selected[0][1], False)
    assert entry["outcome"] == "would_repair" and entry["new"]["status"] == "rejected"
    tool.apply_row(db_session, row, entry)
    db_session.refresh(row)
    assert row.extracted["profile"] == "promoted" and row.status == "rejected"
    assert tool.select_rows(db_session, project, {"parser-outdated"}, []) == [], "repaired under this profile: idempotent"
    again = tool.preview(db_session, row, ["selected by id"], False)
    assert again["outcome"] == "unchanged"
