"""Document Classification V2, the pilot hardening (rules classify-2026-09-27.2):
a classification write that fails in the database leaves the file sync and
its session whole; metadata is never reported as content; a changed file
waiting to be read does not show its old assessment as current; a file
merely named Design Sheet does not bypass its content; one current
assessment per document, refused by the database; the backfill is
idempotent and resumes; an engineer's confirmation stands."""

from __future__ import annotations

import logging
import os
import time

import pytest
from sqlalchemy.exc import IntegrityError

import app.routers.jobs as jobs_router
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.models import DocumentClassification, DocumentDependency, Project
from app.services import document_classification as dc
from app.services import document_sync, jobs

from .test_document_classification_v2 import (  # noqa: F401 -- fixtures and helpers
    DRAWING, _classifications, _folder, _row, _snapshot, _sync, classification_on,
)
from .test_document_sync import _pdf, _project, _reading, ai  # noqa: F401 -- fixture
from .test_file_sync_v2_processing import _processing_job, _rows, _states

settings = get_settings()
T = dc.DocumentType


# --- TEST 3 / TEST 4: a real database write failure during File Sync ------------------------------------


def test_a_classification_write_that_fails_in_the_database_leaves_the_sync_and_its_session_whole(
        client, db_session, tmp_path, ai, classification_on, monkeypatch, caplog):
    """`rules_version` is NOT NULL: with it None every hint's INSERT is
    refused by the database at flush. The index writes, the pending
    states, the sync time and the processing job survive; the session is
    usable afterwards; the failure is logged once with the document's
    identifiers, not read off an expired row."""
    folder = _folder(tmp_path, "30930")
    project_id = _project(client, folder, "30930")
    project = db_session.get(Project, project_id)
    monkeypatch.setattr(dc, "RULES_VERSION", None)
    caplog.set_level(logging.WARNING, logger="app.services.document_classification")

    result = document_sync.sync(db_session, project)

    assert result["files"] == 7 and result["new"] == 7 and result["classification_hints"] == 0
    assert "could not be written" in caplog.text and "document" in caplog.text
    assert sum(1 for r in caplog.records if r.levelno == logging.ERROR) == 1, "logged once, then quietly"
    db_session.expire_all()
    assert _states(db_session, project_id) == {"pending": 7}, "the index writes survived the failed hints"
    assert project.documents_synced_at is not None and project.documents_listing_sha256
    queued = _processing_job(db_session, project_id)
    assert queued is not None and queued.status == "queued", "the processing job was still created"
    assert db_session.query(DocumentClassification).count() == 0
    # The session goes on: a read and a write after the failure.
    assert db_session.query(Project).get(project_id).ep_number == "30930"
    project.other_information = "written after the classification failure"
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Project, project_id).other_information == "written after the classification failure"
    # And the sync can run again on the same session: unchanged, no hint, no error.
    again = document_sync.sync(db_session, project)
    assert again["already_pending"] == 7 and again["classification_hints"] == 0


def test_a_classification_write_failure_after_processing_leaves_the_reading_and_the_job_whole(
        client, db_session, tmp_path, ai, classification_on, monkeypatch):
    folder = tmp_path / "EP-30933"
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    project_id = _project(client, folder, "30933")
    monkeypatch.setattr(dc, "RULES_VERSION", None)   # every INSERT refused by the database
    _sync(client, project_id)
    assert _states(db_session, project_id) == {"fresh": 1}
    processed = _processing_job(db_session, project_id)
    assert processed.status == "succeeded" and processed.result["processed"] == 1 and "classified" not in processed.result
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.extracted["records"][0]["reference"] == "BBY006-GME-SDW-EL-FA-0001"
    assert db_session.query(DocumentClassification).count() == 0


# --- TEST 5 / TEST 6 / TEST 7 / TEST 8: metadata is never content; the intake association says its name ---


def test_role_only_evidence_stays_a_hint_and_content_supports():
    spec = dc.assess(_row("06- Specifications/28 31 00 Fire Alarm.pdf", role="spec", records=[]))
    assert spec.primary_type == T.SPECIFICATION and spec.stage == dc.Stage.HINT and spec.strength == dc.Strength.WEAK
    assert spec.basis == dc.Basis.METADATA and "records" not in spec.evidence_sources and "role" in spec.evidence_sources
    transmittal = dc.assess(_row("07- Transmittals/T-001.docx", role="transmittal", records=[]))
    assert transmittal.primary_type == T.TRANSMITTAL and transmittal.stage == dc.Stage.HINT
    assert transmittal.strength == dc.Strength.WEAK and transmittal.basis == dc.Basis.METADATA
    unprocessed = dc.assess(_row("07- Transmittals/T-001.docx", role="transmittal"))
    assert unprocessed.stage == dc.Stage.HINT and unprocessed.basis == dc.Basis.METADATA
    # With controlled documents listed on it, the transmittal is supported by its content.
    sent = dc.assess(_row("07- Transmittals/T-002.docx", role="transmittal",
                          records=[{"category": "samples", "source": "document", "status": "UR", "reference": "X-SAR-1"}]))
    assert sent.primary_type == T.TRANSMITTAL and sent.stage == dc.Stage.SUPPORTED and sent.strength == dc.Strength.STRONG
    assert sent.basis == dc.Basis.CONTENT and "records" in sent.evidence_sources
    # A form the model read is supported by content, whatever the folder says.
    form = dc.assess(_row("03- MS/form.pdf", role="submittal_form",
                          records=[{"category": "submittals", "source": "document", "status": "UR"}],
                          form={"is_submittal": True, "reference": "X-MAS-1", "revision": 0, "system_code": "FAS",
                                "reply": {"present": False}}))
    assert form.stage == dc.Stage.SUPPORTED and form.basis == dc.Basis.CONTENT
    # A form the reader named but with nothing stored yet stays a hint.
    named = dc.assess(_row("03- MS/form.pdf", role="submittal_form", records=[]))
    assert named.primary_type == T.MATERIAL_SUBMITTAL and named.stage == dc.Stage.HINT and named.basis == dc.Basis.METADATA


def test_the_intake_association_is_supported_and_named_as_the_association():
    drf = dc.assess(_row("Scan/EP-1 DRF.pdf", role="drf", records=[]), intake_role="drf")
    assert drf.primary_type == T.DRF and drf.stage == dc.Stage.SUPPORTED and drf.strength == dc.Strength.STRONG
    assert drf.basis == dc.Basis.INTAKE_ASSOCIATION and drf.evidence_sources == ["intake_association"]
    sheet = dc.assess(_row("Commercial/EP-1 FAS Design.pdf", role="design_sheet"), intake_role="design_sheet", intake_system="FAS")
    assert sheet.primary_type == T.DESIGN_SHEET and sheet.basis == dc.Basis.INTAKE_ASSOCIATION and sheet.system_code == "FAS"
    assert "content" not in sheet.evidence_sources and "records" not in sheet.evidence_sources


# --- TEST 9: a name-only Design Sheet does not bypass the content --------------------------------------


def test_a_file_merely_named_design_sheet_does_not_bypass_its_content():
    submittal_record = [{"category": "submittals", "source": "document", "status": "UR", "system_code": "FAS"}]
    named = dc.assess(_row("EP-30931 Commercial/Design Sheet.pdf", records=submittal_record))
    assert named.primary_type == T.MATERIAL_SUBMITTAL, "the content is reported"
    assert named.stage == dc.Stage.AMBIGUOUS and named.strength == dc.Strength.CONFLICTING
    assert T.DESIGN_SHEET in named.component_types and named.component_support["DESIGN_SHEET"] == "metadata"
    assert any("name suggested design sheet" in e for e in named.evidence)
    drf_named = dc.assess(_row("x/EP-1 DRF.pdf", records=submittal_record))
    assert drf_named.stage == dc.Stage.AMBIGUOUS and T.DRF in drf_named.component_types
    # Nothing read off it: the name is a hint, and says it is the name only.
    empty = dc.assess(_row("x/Design Sheet.pdf", records=[]))
    assert empty.primary_type == T.DESIGN_SHEET and empty.stage == dc.Stage.HINT and empty.basis == dc.Basis.METADATA
    assert any("the name only" in e for e in empty.evidence)
    # The project's own Design Sheet is the association, whatever is stored on the row.
    real = dc.assess(_row("x/Design Sheet.pdf", role="design_sheet", records=submittal_record),
                     intake_role="design_sheet", intake_system="FAS")
    assert real.primary_type == T.DESIGN_SHEET and real.stage == dc.Stage.SUPPORTED and real.basis == dc.Basis.INTAKE_ASSOCIATION


# --- TEST 10 / TEST 11 / TEST 18: a changed file waiting to be read ----------------------------------------


def _change(path, text: str) -> None:
    _pdf(path, text)
    later = time.time() + 60
    os.utime(path, (later, later))


def test_a_changed_file_waiting_to_be_read_does_not_show_its_old_assessment_as_current(
        client, db_session, tmp_path, ai, monkeypatch):
    folder = tmp_path / "EP-30932"
    path = _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    monkeypatch.setattr(settings, "document_classification_v2", True)
    project_id = _project(client, folder, "30932")
    _sync(client, project_id)   # synced and read inline
    before = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    assert before.stage == "supported" and before.assessment["source_state"] == "fresh" and before.assessment["basis"] == "content"
    old_sha = _rows(db_session, project_id)["05- Drawings/L01.pdf"].sha256
    stamps = {d.id: (d.stale, d.updated_at) for d in db_session.query(DocumentDependency).filter(DocumentDependency.project_id == project_id)}

    # The file changes. The sync (the feature off for this one, so no hint
    # is written and the old assessment stays the current row) records it
    # pending, keeping the old hash and the old reading on the row; the
    # processing does not run.
    monkeypatch.setattr(settings, "document_classification_v2", False)
    _change(path, DRAWING + "\nRevised")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    assert result["changed"] == 1
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.state == "pending" and row.sha256 == old_sha and row.extracted["records"], "the old hash and reading stay meanwhile"

    monkeypatch.setattr(settings, "document_classification_v2", True)
    listing = {r["path"]: r for r in client.get(f"/projects/{project_id}/documents/classification").json()}
    shown = listing["05- Drawings/L01.pdf"]["classification"]
    assert shown["id"] == before.id and shown["freshness"] == "source_changed" and shown["stale"] is True
    assert shown["needs_review"] is True and any("waits to be read" in r for r in shown["review_reasons"])
    files = {f["path"]: f for f in client.get(f"/projects/{project_id}/documents/sync-files").json()}
    assert files["05- Drawings/L01.pdf"]["classification"]["freshness"] == "source_changed"
    metrics = client.get(f"/projects/{project_id}/documents/classification/metrics").json()
    assert metrics["stale"] == 1 and metrics["current"] == 0 and metrics["by_freshness"] == {"source_changed": 1}

    # TEST 11: the backfill does not relabel the old reading as evidence for the new content.
    job = client.post(f"/projects/{project_id}/jobs/classify-documents").json()
    assert job["status"] == "succeeded" and job["result"]["assessed"] == 1 and job["result"]["failed"] == 0
    now = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    assert now.id != before.id and now.stage == "hint" and now.assessment["basis"] == "metadata"
    assert now.assessment["source_state"] == "pending" and now.content_sha256 == old_sha
    assert any("waits to be read again" in e for e in now.evidence) and "records" not in now.evidence_sources
    db_session.refresh(before)
    assert before.superseded_at is not None, "the earlier assessment is history, kept"
    listing = {r["path"]: r for r in client.get(f"/projects/{project_id}/documents/classification").json()}
    assert listing["05- Drawings/L01.pdf"]["classification"]["freshness"] == "current"
    assert listing["05- Drawings/L01.pdf"]["classification"]["stage"] == "hint"
    # TEST 18: nothing about dependencies moved.
    db_session.expire_all()
    assert {d.id: (d.stale, d.updated_at) for d in db_session.query(DocumentDependency)
            .filter(DocumentDependency.project_id == project_id)} == stamps
    assert _rows(db_session, project_id)["05- Drawings/L01.pdf"].state == "pending", "the row is as the sync left it"


def test_the_sync_hint_for_a_changed_file_supersedes_the_old_assessment(client, db_session, tmp_path, ai, classification_on):
    folder = tmp_path / "EP-30934"
    path = _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    project_id = _project(client, folder, "30934")
    _sync(client, project_id)
    before = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    assert before.stage == "supported"
    _change(path, DRAWING + "\nRevised")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    assert result["changed"] == 1 and result["classification_hints"] == 1
    now = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    assert now.id != before.id and now.stage == "hint" and now.source == "hint" and now.assessment["source_state"] == "pending"
    db_session.refresh(before)
    assert before.superseded_at is not None
    history = db_session.query(DocumentClassification).filter(DocumentClassification.document_id == now.document_id).count()
    assert history == 3, "the first hint, the assessment, the new hint: history kept, one current"


# --- one current assessment per document, refused by the database -----------------------------------------


def test_the_database_refuses_a_second_current_assessment_for_a_document(client, db_session, tmp_path, ai, classification_on):
    folder = tmp_path / "EP-30935"
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    project_id = _project(client, folder, "30935")
    _sync(client, project_id)
    entry = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    duplicate = DocumentClassification(
        project_id=project_id, document_id=entry.document_id, content_sha256=entry.content_sha256,
        context_fingerprint=entry.context_fingerprint, rules_version=entry.rules_version, stage=entry.stage,
        primary_type=entry.primary_type, component_types=[], evidence_strength=entry.evidence_strength, evidence=[],
        evidence_sources=[], reason="a second current row", source="backfill", assessment={}, created_at=utc_now(),
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    current = (db_session.query(DocumentClassification)
               .filter(DocumentClassification.document_id == entry.document_id, DocumentClassification.superseded_at.is_(None)).count())
    assert current == 1


# --- TEST 13 / TEST 14: the backfill is idempotent and resumes -------------------------------------------------


def test_the_backfill_is_idempotent_and_resumes_where_it_stopped(client, db_session, tmp_path, ai, monkeypatch):
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    monkeypatch.setattr(settings, "document_classification_v2", False)
    project_id = _project(client, _folder(tmp_path, "30936"), "30936")
    _sync(client, project_id)
    before = _snapshot(db_session, project_id)
    project = db_session.get(Project, project_id)
    monkeypatch.setattr(settings, "document_classification_v2", True)

    class StopAfterFirstBatch:
        def __init__(self):
            self.batches = 0

        def progress(self, done, total, message, **_extra):
            pass

        def check(self):
            self.batches += 1
            if self.batches > 1:
                raise jobs.Cancelled()

    with pytest.raises(jobs.Cancelled):
        dc.backfill(db_session, project, ctx=StopAfterFirstBatch(), batch=3)
    db_session.expire_all()
    assert len(_classifications(db_session, project_id)) == 3, "the first batch is committed"

    resumed = dc.backfill(db_session, project)
    assert resumed["eligible"] == 7 and resumed["assessed"] == 4 and resumed["skipped"] == 3 and resumed["failed"] == 0
    assert resumed["eligible"] == resumed["assessed"] + resumed["skipped"] + resumed["failed"]
    stored = db_session.query(DocumentClassification).filter(DocumentClassification.project_id == project_id).count()
    assert stored == 7 and len(_classifications(db_session, project_id)) == 7

    first = client.post(f"/projects/{project_id}/jobs/classify-documents").json()
    second = client.post(f"/projects/{project_id}/jobs/classify-documents").json()
    assert first["id"] != second["id"]
    for job in (first, second):
        assert job["status"] == "succeeded" and job["result"]["assessed"] == 0 and job["result"]["skipped"] == 7
    assert db_session.query(DocumentClassification).filter(DocumentClassification.project_id == project_id).count() == stored
    assert _snapshot(db_session, project_id) == before, "no legacy output moved"
    metrics = client.get(f"/projects/{project_id}/documents/classification/metrics").json()
    assert metrics["eligible"] == 7 and metrics["assessed"] == 7 and metrics["duplicate_current"] == 0
    assert metrics["current"] == 7 and metrics["stale"] == 0 and metrics["rules_version"] == dc.RULES_VERSION
    assert metrics["metadata_only"] + metrics["content_supported"] + metrics["by_basis"].get("intake_association", 0) == 7


# --- TEST 15: an engineer's confirmation stands -----------------------------------------------------------


def test_an_engineer_confirmed_assessment_is_never_superseded_by_the_backfill(client, db_session, tmp_path, ai, classification_on):
    folder = tmp_path / "EP-30937"
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    project_id = _project(client, folder, "30937")
    _sync(client, project_id)
    project = db_session.get(Project, project_id)
    automatic = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    automatic.superseded_at = utc_now()
    confirmed = DocumentClassification(
        project_id=project_id, document_id=automatic.document_id, content_sha256=automatic.content_sha256,
        context_fingerprint=automatic.context_fingerprint, rules_version="classify-earlier", stage="supported",
        primary_type="IFC_DRAWING", component_types=[], evidence_strength="strong", evidence=["the engineer said so"],
        evidence_sources=["engineer"], reason="confirmed by the engineer", source="engineer", engineer_confirmed=True,
        assessment={"primary_type": "IFC_DRAWING"}, created_at=utc_now(),
    )
    db_session.add(confirmed)
    db_session.commit()

    result = dc.backfill(db_session, project)
    assert result["assessed"] == 1, "the rules moved on, so the row is assessed again"
    db_session.expire_all()
    current = _classifications(db_session, project_id)["05- Drawings/L01.pdf"]
    assert current.id == confirmed.id and current.engineer_confirmed and current.primary_type == "IFC_DRAWING"
    stored = (db_session.query(DocumentClassification).filter(DocumentClassification.document_id == automatic.document_id)
              .order_by(DocumentClassification.id).all())
    assert stored[-1].source == "backfill" and stored[-1].superseded_at is not None, "the automatic one is history only"
    listing = {r["path"]: r for r in client.get(f"/projects/{project_id}/documents/classification").json()}
    assert listing["05- Drawings/L01.pdf"]["classification"]["engineer_confirmed"] is True


# --- TEST 16: rows without an assessment are listed, off and on -------------------------------------------


def test_rows_without_an_assessment_are_listed_with_none(client, db_session, tmp_path, ai, monkeypatch):
    monkeypatch.setattr(settings, "document_classification_v2", False)
    project_id = _project(client, _folder(tmp_path, "30938"), "30938")
    _sync(client, project_id)
    listing = client.get(f"/projects/{project_id}/documents/classification").json()
    assert len(listing) == 7 and all(r["classification"] is None for r in listing)
    assert all(r["document_id"] and r["name"] and r["role"] and r["state"] for r in listing)
    metrics = client.get(f"/projects/{project_id}/documents/classification/metrics").json()
    assert metrics["eligible"] == 7 and metrics["assessed"] == 0 and metrics["unassessed"] == 7
    monkeypatch.setattr(settings, "document_classification_v2", True)
    listing = client.get(f"/projects/{project_id}/documents/classification").json()
    assert all(r["classification"] is None for r in listing), "on, with nothing stored yet: still listed"


# --- the review flags -------------------------------------------------------------------------------------


def test_the_listing_flags_what_a_person_should_look_at(client, db_session, tmp_path, ai, classification_on):
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    folder = _folder(tmp_path, "30939")
    # Named as a section (role spec by name), with nothing on its page that reads as one.
    _pdf(folder / "06- Specifications" / "28 31 05 Notes.pdf", "General notes for the works")
    project_id = _project(client, folder, "30939")
    _sync(client, project_id)
    listing = {r["path"]: r["classification"] for r in client.get(f"/projects/{project_id}/documents/classification").json()}
    spec = listing["06- Specifications/28 31 05 Notes.pdf"]
    assert spec["stage"] == "hint" and spec["basis"] == "metadata" and spec["needs_review"] is True
    assert any("path and role only" in r for r in spec["review_reasons"])
    read_spec = listing["06- Specifications/28 31 00 Fire Alarm.pdf"]
    assert read_spec["stage"] == "supported" and read_spec["basis"] == "content" and read_spec["needs_review"] is False
    form = listing["03- MS/01- FA/form.pdf"]
    assert form["stage"] == "supported" and form["basis"] == "content" and form["needs_review"] is False
    assert form["component_support"]["MATERIAL_SUBMITTAL"] == "content"
    catalogue = listing["09- Other/catalogue.pdf"]
    assert catalogue["stage"] == "supported" and catalogue["needs_review"] is False, "the page names a catalogue"
    assert all(c["freshness"] == "current" and c["source_state"] == "fresh" for c in listing.values())
    metrics = client.get(f"/projects/{project_id}/documents/classification/metrics").json()
    assert metrics["needs_review"] >= 1 and metrics["duplicate_current"] == 0 and metrics["metadata_only"] >= 1
