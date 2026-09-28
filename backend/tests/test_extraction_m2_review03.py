"""M2 review 03 -- a carried record keeps its own provenance and its
inherited uncertainty through repeated bounded reads (R3-01), and a record
read under another or an unknown profile never becomes this profile's
authoritative status by being carried (R3-02); both through ordinary
processing and the repair tool, persisted and reloaded. Isolated test
environment (conftest); no live data, model or worker."""
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from app.database import SessionLocal
from app.models import Project, ProjectDocument
from app.services import document_control as dc
from app.services import document_processing, document_sync
from .test_document_sync import _project
from .test_extraction_m2 import OPTIONS
from .test_extraction_m2_review import FA_COVER, SEPARATOR, _frame, _page, _row, _sync, _touch, inline  # noqa: F401 -- fixture
from .test_extraction_m2_review02 import _processed, _save

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
REFERENCE = "ABC-XYZ-SPM-SD-MEP-FA-0054"


def _package(path: Path, first_page: str, *, frame: bool = False) -> Path:
    """Twelve neutral pages, then a cover on page 13 (with a drawn frame on C when `frame`)."""
    with pymupdf.open() as document:
        _page(document, first_page)
        for _ in range(11):
            _page(document, SEPARATOR)
        page = _page(document, FA_COVER + (OPTIONS if frame else ""))
        if frame:
            page.draw_rect(_frame(page, "C - Revise & Re-Submit"), color=(0, 0.5, 0), width=3)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(document.tobytes())
        return path


def _reload(row_id: int) -> ProjectDocument:
    """The row as another session sees it on disk."""
    session = SessionLocal()
    try:
        row = session.get(ProjectDocument, row_id)
        session.expire_all()
        return session.get(ProjectDocument, row_id)
    finally:
        session.close()


def _carried(row) -> dict:
    [record] = [r for r in row.extracted["records"] if r.get("page") == 13]
    return record


# --- R3-01: provenance and uncertainty travel with the record --------------------------------------


def test_repeated_bounded_reads_keep_the_carried_records_provenance_and_uncertainty(client, db_session, tmp_path, inline, monkeypatch):
    from scripts import repair_extraction as tool

    folder = tmp_path / "EP-30921"
    path = _package(folder / "05- Drawings" / "package.pdf", SEPARATOR)
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    project_id = _project(client, folder, ep="30921")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    first = dict(row.extracted)
    original_sha, original_at = first["read_sha256"], first["read_at"]
    assert _carried(row).get("retained") is None and first["coverage"]["outcome"] == "complete"

    # the source changes and a narrower reader runs: carried, unverified, with the ORIGINAL provenance
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    _package(path, SEPARATOR.replace("separator", "divider"))
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    changed_sha = row.sha256
    record = _carried(row)
    assert set(record["flags"]) == {"carried_unvisited", "carried_unverified"}
    assert record["retained"] == {"source_sha256": original_sha, "parser_version": dc.PARSER_VERSION, "profile": "default", "read_at": original_at}
    assert row.extracted["read_sha256"] == changed_sha and row.extracted["retained"] == {"records": 1, "pages": [13], "unverified": 1, "other_profile": 0,
                                                                                         "sources": row.extracted["retained"]["sources"]}
    assert original_sha in row.extracted["retained"]["sources"][0]

    # the same new bytes read again, twice, by the repair tool: nothing certified, nothing lost
    monkeypatch.setattr(tool, "root_of", lambda r: str(folder))
    for _ in range(2):
        entry = tool.preview(db_session, row, ["selected by id"], False)
        assert entry["carried_from_previous"] == [13]
        tool.apply_row(db_session, row, entry)
        db_session.refresh(row)
        record = _carried(row)
        assert set(record["flags"]) == {"carried_unvisited", "carried_unverified"}, "the envelope's new hash certifies nothing about page 13"
        assert record["retained"]["source_sha256"] == original_sha and record["retained"]["read_at"] == original_at
        assert row.extracted["read_sha256"] == changed_sha and row.extracted["retained"]["unverified"] == 1
    reloaded = _reload(row.id)
    assert _carried(reloaded)["retained"]["source_sha256"] == original_sha and "carried_unverified" in _carried(reloaded)["flags"]
    assert (reloaded.reference, reloaded.revision, reloaded.status) == (REFERENCE, "R1", "UR")

    # the source changes again: still the original provenance, still unverified
    _package(path, SEPARATOR.replace("separator", "sheet"))
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert record["retained"]["source_sha256"] == original_sha and set(record["flags"]) == {"carried_unvisited", "carried_unverified"}
    assert document_processing.pending_rows(db_session, db_session.get(Project, project_id)) == [], "bounded work is not retried on its own"

    # a wider reader visits page 13: read afresh, no retained provenance
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    _package(path, SEPARATOR.replace("separator", "leaf"))
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert record["flags"] == [] and record.get("retained") is None and "retained" not in row.extracted
    assert row.extracted["coverage"]["outcome"] == "complete" and row.extracted["read_sha256"] == row.sha256


def test_a_legacy_record_carried_into_a_bounded_reading_has_unknown_provenance_and_no_projected_decision(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30922"
    path = _package(folder / "05- Drawings" / "package.pdf", SEPARATOR)
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    project_id = _project(client, folder, ep="30922")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    legacy_record = {**_carried(row), "status": "approved"}
    row.extracted = {"records": [legacy_record], "notes": []}      # a legacy reading: no provenance at all, a decision on page 13
    row.status = "approved"
    db_session.commit()

    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    _package(path, SEPARATOR.replace("separator", "divider"))
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert set(record["flags"]) == {"carried_unvisited", "carried_unverified", "carried_other_profile"}
    assert record["retained"] == {"source_sha256": None, "parser_version": None, "profile": None, "read_at": None}, "unknown stays unknown"
    assert record["status"] == "UR" and ["approved", "retained from a unknown-profile reading of other bytes", "retained"] in record["decision_candidates"]
    assert row.status == "UR", "the mirror does not project a decision of unknown provenance"
    assert row.extracted["retained"]["other_profile"] == 1 and document_processing.parser_current(row) is False


def test_carry_unvisited_follows_the_records_own_provenance_not_the_envelope():
    kept = {"records": [{"reference": "A", "page": 13, "status": "approved", "flags": ["carried_unvisited", "carried_unverified"],
                         "retained": {"source_sha256": "original", "parser_version": "p1", "profile": "default", "read_at": "t0"},
                         "decision_candidates": [["approved", "retained from a default reading of other bytes", "retained"]]}],
            "read_sha256": "new", "parser_version": dc.PARSER_VERSION, "profile": "default", "read_at": "t1"}
    coverage = {"pages_skipped": [{"page": 13, "reason": "page scan limit"}]}
    [again], _ = document_sync.carry_unvisited(kept, coverage, "new", "default")
    assert again["retained"]["source_sha256"] == "original" and set(again["flags"]) == {"carried_unvisited", "carried_unverified"}
    assert again["status"] == "UR" and len(again["decision_candidates"]) == 1, "carried again: still unverified, the decision still held"
    # the original bytes come back under the same profile: verified, the decision restored
    [back], _ = document_sync.carry_unvisited(kept, coverage, "original", "default")
    assert back["flags"] == ["carried_unvisited"] and back["status"] == "approved" and back["decision_candidates"] == []
    # the original bytes but the other profile: held for the profile
    [other], _ = document_sync.carry_unvisited(kept, coverage, "original", "promoted")
    assert set(other["flags"]) == {"carried_unvisited", "carried_other_profile"} and other["status"] == "UR"


# --- R3-02: a promoted record is not a default status by being carried ----------------------------


def test_a_promoted_record_on_an_unvisited_page_is_held_by_a_default_bounded_reading(client, db_session, tmp_path, inline, monkeypatch):
    from scripts import repair_extraction as tool

    folder = tmp_path / "EP-30923"
    path = _package(folder / "05- Drawings" / "package.pdf", SEPARATOR, frame=True)
    duplicate = folder / "05- Drawings" / "Archive" / "package.pdf"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(path.read_bytes())
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    project_id = _project(client, folder, ep="30923")
    _sync(client, project_id)
    rows = {r.relative_path: r for r in db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).all()}
    row = rows["05- Drawings/package.pdf"]
    assert row.extracted["profile"] == "promoted" and _carried(row)["status"] == "rejected" and row.status == "rejected"
    promoted_sha = row.sha256

    # the gate off, a narrower reader, the same bytes: the promoted decision is not a default one
    monkeypatch.setattr(dc, "_promote_default", lambda: False)
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    _touch(path)
    _touch(duplicate)
    result = _sync(client, project_id)
    for r in rows.values():
        db_session.refresh(r)
    assert _processed(client, result).get("failed", 0) == 0
    record = _carried(row)
    assert row.extracted["profile"] == "default" and set(record["flags"]) == {"carried_unvisited", "carried_other_profile"}
    assert record["status"] == "UR" and ["rejected", "retained from a promoted reading of these bytes", "retained"] in record["decision_candidates"]
    assert record["retained"]["profile"] == "promoted" and record["retained"]["source_sha256"] == promoted_sha
    assert row.status == "UR" and row.reference == REFERENCE, "the mirror shows no default status the reader did not read"
    assert row.extracted["retained"]["other_profile"] == 1
    assert document_processing.parser_current(row) is False and document_processing._previous_sha(row) is None, "a mixed reading is not a current default reading"
    loaded, _notes = document_sync.log_records(db_session, db_session.get(Project, project_id))
    assert all(r.status == "UR" for r in loaded if getattr(r, "reference", None) == REFERENCE), "what the log is built from says under review"
    copy = rows["05- Drawings/Archive/package.pdf"]
    assert _processed(client, result)["timing"].get("counts", {}).get("duplicate_reused", 0) == 0 if "timing" in _processed(client, result) else True
    assert "retained" not in copy.extracted or copy.extracted["retained"]["other_profile"] == 1, "the duplicate got its own reading, not a copy of this row's history"
    reloaded = _reload(row.id)
    assert _carried(reloaded)["status"] == "UR" and reloaded.status == "UR"

    # the repair writer, same bytes, gate still off: the same projection, idempotent
    monkeypatch.setattr(tool, "root_of", lambda r: str(folder))
    selected = dict((r.id, reasons) for r, reasons in tool.select_rows(db_session, db_session.get(Project, project_id), {"parser-outdated"}, []))
    assert any(reason.startswith("carries records read under another") for reason in selected.get(row.id, [])), "the mixed reading is selected for repair"
    entry = tool.preview(db_session, row, selected[row.id], False)
    tool.apply_row(db_session, row, entry)
    db_session.refresh(row)
    record = _carried(row)
    assert record["status"] == "UR" and set(record["flags"]) == {"carried_unvisited", "carried_other_profile"} and record["retained"]["profile"] == "promoted"
    assert row.status == "UR"

    # the gate on again, the same bytes, still the narrow reader: its own profile and bytes -- the decision is back
    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert row.extracted["profile"] == "promoted" and record["flags"] == ["carried_unvisited"] and record["status"] == "rejected"
    assert row.status == "rejected" and record["decision_candidates"] == [] and "other_profile" not in (row.extracted.get("retained") or {"other_profile": 0}) or row.extracted["retained"]["other_profile"] == 0
    assert document_processing.parser_current(row) is True, "a reading of its own profile and bytes, carried or not, is current"

    # a reading whose profile was never recorded: carried as unknown, held
    without = dict(row.extracted)
    without.pop("profile")
    row.extracted = without
    db_session.commit()
    monkeypatch.setattr(dc, "_promote_default", lambda: False)
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert "carried_other_profile" in record["flags"] and record["status"] == "UR" and row.status == "UR"


def test_a_default_record_is_held_by_a_promoted_bounded_reading_too(client, db_session, tmp_path, inline, monkeypatch):
    folder = tmp_path / "EP-30924"
    path = _package(folder / "05- Drawings" / "package.pdf", SEPARATOR + "Review status: (A) Approved\n" if False else SEPARATOR, frame=False)
    # a default complete reading with a text decision on page 13
    with pymupdf.open() as document:
        _page(document, SEPARATOR)
        for _ in range(11):
            _page(document, SEPARATOR)
        _page(document, FA_COVER + "Review status: (A) Approved\n")
        path.write_bytes(document.tobytes())
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 20)
    project_id = _project(client, folder, ep="30924")
    _sync(client, project_id)
    row = _row(db_session, project_id)
    assert row.extracted["profile"] == "default" and row.status == "approved"

    monkeypatch.setattr(dc, "_promote_default", lambda: True)
    monkeypatch.setattr(dc, "PAGE_SCAN_LIMIT", 12)
    _touch(path)
    _sync(client, project_id)
    db_session.refresh(row)
    record = _carried(row)
    assert row.extracted["profile"] == "promoted" and set(record["flags"]) == {"carried_unvisited", "carried_other_profile"}
    assert record["status"] == "UR" and row.status == "UR" and ["approved", "retained from a default reading of these bytes", "retained"] in record["decision_candidates"]
    assert document_processing.parser_current(row) is False
