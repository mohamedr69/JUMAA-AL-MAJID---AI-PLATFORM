"""File Sync v2, stage B: the file sync indexes in seconds and document
processing reads in the background -- two jobs, two workers, one index."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

import app.routers.jobs as jobs_router
from app.core.config import get_settings
from app.models import BackgroundJob, DocumentDependency, Project, ProjectDocument
from app.services import document_processing, document_sync, jobs
from app.workers.document_worker import DocumentWorker

from .test_document_sync import _DRAWINGS, _pdf, _project, _reading, _submittal_form, ai  # noqa: F401 -- fixture

settings = get_settings()
PROCESS = document_processing.JOB_KIND


def _rows(db_session, project_id) -> dict[str, ProjectDocument]:
    db_session.expire_all()
    return {r.relative_path: r for r in db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id)}


def _states(db_session, project_id) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in _rows(db_session, project_id).values():
        out[row.state] = out.get(row.state, 0) + 1
    return out


def _processing_job(db_session, project_id) -> BackgroundJob | None:
    db_session.expire_all()
    return (db_session.query(BackgroundJob).filter(BackgroundJob.project_id == project_id, BackgroundJob.kind == PROCESS)
            .order_by(BackgroundJob.id.desc()).first())


def _folder_of_drawings(tmp_path, ep: str, count: int = 6) -> Path:
    folder = tmp_path / f"EP-{ep}"
    for n in range(count):
        _pdf(folder / "05- Drawings" / f"L{n:02d}.pdf", f"Drawing title\nBBY006-GME-SDW-EL-FA-{n:04d}\nREV. 00\nFloor {n} Layout")
    return folder


# --- the two halves --------------------------------------------------------------------------


def test_the_first_sync_indexes_every_file_without_opening_one_and_queues_processing(client, db_session, tmp_path,
                                                                                    monkeypatch):
    """A new project with many documents: the sync records every file as
    pending, hashes nothing, opens nothing, queues one processing job and
    leaves the project synced. The processing job then reads them."""
    folder = _folder_of_drawings(tmp_path, "30851", 30)
    project_id = _project(client, folder, "30851")
    project = db_session.get(Project, project_id)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the file sync did heavy work")

    hashes = []
    monkeypatch.setattr(document_sync.document_control, "_read_pdf", must_not_run)
    monkeypatch.setattr(document_sync, "classify", must_not_run)
    monkeypatch.setattr(document_sync, "sha256_of", lambda path: hashes.append(path) or "never")
    started = time.perf_counter()
    result = document_sync.sync(db_session, project)
    assert time.perf_counter() - started < 20
    assert (result["files"], result["new"], result["pending"]) == (30, 30, 30)
    assert hashes == [], "no content hash during discovery"
    assert _states(db_session, project_id) == {"pending": 30}
    db_session.refresh(project)
    assert project.documents_synced_at is not None and project.documents_listing_sha256
    job = _processing_job(db_session, project_id)
    assert job is not None and job.status == "queued" and result["processing_job_id"] == job.id
    assert client.get(f"/projects/{project_id}/documents/status").json()["pending"] == 30
    summary = client.get(f"/projects/{project_id}/documents/sync-summary").json()
    assert summary["synced_at"] and summary["discovered"] == 30 and summary["counts"]["pending"] == 30
    assert summary["processing"]["status"] == "queued" and summary["processing"]["pending"] == 30
    # The pages answer meanwhile, from the index as it stands.
    assert client.get(f"/projects/{project_id}/logs").status_code == 200

    monkeypatch.undo()
    assert DocumentWorker(progress_interval=0).run_claimed(job.id) == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 30}
    processed = _processing_job(db_session, project_id)
    assert processed.result["processed"] == 30 and processed.result["failed"] == 0
    assert processed.result["telemetry"]["processed_count"] == 30
    rows = _rows(db_session, project_id)
    assert rows["05- Drawings/L03.pdf"].reference == "BBY006-GME-SDW-EL-FA-0003"
    summary = client.get(f"/projects/{project_id}/documents/sync-summary").json()
    assert summary["processing"]["status"] == "complete" and summary["processing"]["processed"] == 30
    assert summary["counts"]["processed"] == 30 and summary["counts"]["pending"] == 0
    assert summary["last_sync"] is None, "the sync ran outside a job here; the job-backed summary is tested below"


def test_a_second_sync_of_an_unchanged_project_does_no_heavy_work(client, db_session, tmp_path, ai, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    folder = tmp_path / "EP-30852"
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
    _pdf(folder / "05- Drawings" / "L01.pdf", _DRAWINGS["05- Drawings/L01.pdf"])
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, folder, "30852")
    first = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert first["status"] == "succeeded" and ai.calls == 1
    assert _states(db_session, project_id) == {"fresh": 2}
    summary = client.get(f"/projects/{project_id}/documents/sync-summary").json()
    assert summary["last_sync"]["files"] == 2 and summary["last_sync"]["new"] == 2 and summary["last_sync"]["pending"] == 2
    assert summary["processing"]["status"] == "complete" and summary["processing"]["total"] == 2
    assert summary["synced_at"] and summary["duration_s"] is not None

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("an unchanged project was read again")

    monkeypatch.setattr(document_sync.document_control, "_read_pdf", must_not_run)
    monkeypatch.setattr(document_sync, "sha256_of", must_not_run)
    monkeypatch.setattr(document_processing, "read_task", must_not_run)
    again = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert again["status"] == "succeeded", again
    result = again["result"]
    assert (result["new"], result["changed"], result["unchanged"], result["pending"]) == (0, 0, 2, 0)
    assert result.get("processing_job_id") is None, "nothing to process: no job queued"
    assert ai.calls == 1


def test_a_touched_file_with_the_same_content_is_hashed_but_not_read(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    folder = _folder_of_drawings(tmp_path, "30853", 3)
    project_id = _project(client, folder, "30853")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"
    before = dict(_rows(db_session, project_id)["05- Drawings/L01.pdf"].extracted)

    touched = folder / "05- Drawings" / "L01.pdf"
    os.utime(touched, (time.time() + 5, time.time() + 5))

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a file with the same content was read again")

    monkeypatch.setattr(document_sync.document_control, "_read_pdf", must_not_run)
    monkeypatch.setattr(document_sync, "classify", must_not_run)
    job = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert job["status"] == "succeeded", job
    assert (job["result"]["changed"], job["result"]["unchanged"], job["result"]["pending"]) == (1, 2, 1)
    processing = _processing_job(db_session, project_id)
    assert processing.status == "succeeded" and processing.result["unchanged_after_hash"] == 1
    assert processing.result["processed"] == 0
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.state == "fresh" and row.extracted == before and row.mtime == pytest.approx(touched.stat().st_mtime)


def test_a_changed_file_keeps_its_reading_until_the_new_one_is_written(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    folder = _folder_of_drawings(tmp_path, "30854", 2)
    project_id = _project(client, folder, "30854")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    before, old_sha = dict(row.extracted), row.sha256

    _pdf(folder / "05- Drawings" / "L01.pdf", "Drawing title\nBBY006-GME-SDW-EL-FA-0099\nREV. 01\nRoof Layout")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)   # the index only
    assert result["changed"] == 1 and result["pending"] == 1
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.state == "pending" and row.extracted == before and row.sha256 == old_sha, "the old reading stands"
    assert client.get(f"/projects/{project_id}/documents/sync-files", params={"status": "pending"}).json()[0]["name"] == "L01.pdf"

    assert DocumentWorker(progress_interval=0).run_claimed(result["processing_job_id"]) == "succeeded"
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.state == "fresh" and row.sha256 != old_sha
    assert row.extracted["records"][0]["reference"] == "BBY006-GME-SDW-EL-FA-0099" and row.revision == "R1"


def test_a_removed_file_is_marked_by_the_sync_and_its_dependents_go_stale(client, db_session, tmp_path, ai, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    folder = tmp_path / "EP-30855"
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
    _pdf(folder / "05- Drawings" / "L01.pdf", _DRAWINGS["05- Drawings/L01.pdf"])
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, folder, "30855")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"
    form = _rows(db_session, project_id)["03- MS/01- FA/form.pdf"]
    assert db_session.query(DocumentDependency).filter(DocumentDependency.source_document_id == form.id,
                                                        DocumentDependency.stale.is_(True)).count() == 0

    (folder / "03- MS" / "01- FA" / "form.pdf").unlink()
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    assert result["removed"] == 1 and result["pending"] == 0 and result.get("processing_job_id") is None
    assert result["forms_changed"] is True
    db_session.expire_all()
    assert db_session.get(ProjectDocument, form.id).state == "removed"
    assert db_session.query(DocumentDependency).filter(DocumentDependency.source_document_id == form.id,
                                                        DocumentDependency.stale.is_(True)).count() >= 1
    assert client.get(f"/projects/{project_id}/submittals/map").json()["submittals"] == 0
    assert [f["name"] for f in client.get(f"/projects/{project_id}/documents/sync-files").json()] == ["L01.pdf"]


# --- one job of each kind per project -----------------------------------------------------------


def test_one_active_sync_and_one_active_processing_job_per_project(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = _folder_of_drawings(tmp_path, "30856", 2)
    project_id = _project(client, folder, "30856")
    first = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    second = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert first["id"] == second["id"] and second["already_active"] is True

    # Nothing pending: there is nothing to process.
    refused = client.post(f"/projects/{project_id}/jobs/process-documents")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "nothing_to_process"

    db_session.add(ProjectDocument(project_id=project_id, role="document", path=str(folder / "x.pdf"), filename="x.pdf",
                                   relative_path="x.pdf", state="pending"))
    db_session.commit()
    one = client.post(f"/projects/{project_id}/jobs/process-documents")
    assert one.status_code == 202, one.text
    two = client.post(f"/projects/{project_id}/jobs/process-documents").json()
    assert two["id"] == one.json()["id"] and two["already_active"] is True and two["kind"] == PROCESS
    assert db_session.query(BackgroundJob).filter(BackgroundJob.project_id == project_id, BackgroundJob.kind == PROCESS).count() == 1
    # The database itself refuses a second active processing job of a project.
    db_session.add(BackgroundJob(kind=PROCESS, project_id=project_id, status="running", progress={}))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    # The two kinds run in different lanes: a sync and a processing job may run at once.
    assert jobs.lane_of("sync_documents") == "sync" and jobs.lane_of(PROCESS) == "documents"
    assert jobs.lane_limit("documents") == 1


# --- stopping, failing, restarting --------------------------------------------------------------


def _read_with(monkeypatch, hook):
    """`read_task` with `hook(relative, calls_so_far)` run before each file."""
    real = document_processing.read_task
    calls = []

    def read_task(path, relative, previous_sha, ocr, **kwargs):
        calls.append(relative)
        hook(relative, len(calls))
        return real(path, relative, previous_sha, ocr, **kwargs)

    monkeypatch.setattr(document_processing, "read_task", read_task)
    return calls


def test_stopping_processing_keeps_finished_documents_and_resume_reads_the_rest(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = _folder_of_drawings(tmp_path, "30857", 6)
    project_id = _project(client, folder, "30857")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    job_id = result["processing_job_id"]

    def ask_to_stop_after_two(_relative, count):
        if count == 3:
            from app.database import SessionLocal

            api = SessionLocal()
            try:
                jobs.request_cancel(api, api.get(BackgroundJob, job_id))
            finally:
                api.close()

    calls = _read_with(monkeypatch, ask_to_stop_after_two)
    assert DocumentWorker(progress_interval=0).run_claimed(job_id) == "cancelled"
    states = _states(db_session, project_id)
    assert states["fresh"] >= 2 and states.get("pending", 0) >= 3 and "processing" not in states
    assert len(calls) <= 4
    summary = client.get(f"/projects/{project_id}/documents/sync-summary").json()
    assert summary["processing"]["status"] == "stopped" and summary["processing"]["pending"] == states["pending"]

    # Resume: a new job reads only what is left.
    monkeypatch.setattr(document_processing, "read_task", document_processing.read_task.__wrapped__
                        if hasattr(document_processing.read_task, "__wrapped__") else document_processing.read_task)
    resumed = client.post(f"/projects/{project_id}/jobs/process-documents")
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["id"] != job_id
    assert DocumentWorker(progress_interval=0).run_claimed(resumed.json()["id"]) == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 6}
    assert _processing_job(db_session, project_id).result["planned"] == states["pending"]


def test_one_bad_document_does_not_stop_the_others(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = _folder_of_drawings(tmp_path, "30858", 4)
    project_id = _project(client, folder, "30858")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)

    def blow_up_on_one(relative, _count):
        if relative.endswith("L02.pdf"):
            raise RuntimeError("the reader fell over on this file")

    _read_with(monkeypatch, blow_up_on_one)
    assert DocumentWorker(progress_interval=0).run_claimed(result["processing_job_id"]) == "succeeded"
    rows = _rows(db_session, project_id)
    assert rows["05- Drawings/L02.pdf"].state == "failed" and "fell over" in rows["05- Drawings/L02.pdf"].error
    assert all(rows[f"05- Drawings/L{n:02d}.pdf"].state == "fresh" for n in (0, 1, 3))
    processed = _processing_job(db_session, project_id).result
    assert processed["processed"] == 3 and processed["failed"] == 1
    summary = client.get(f"/projects/{project_id}/documents/sync-summary").json()
    assert summary["counts"]["failed"] == 1 and summary["processing"]["status"] == "complete"

    # Retry failed puts it back in the queue and reads it.
    monkeypatch.setattr(document_processing, "read_task", _read_with.__globals__["document_processing"].read_task)
    monkeypatch.undo()
    retry = client.post(f"/projects/{project_id}/jobs/process-documents", json={"retry_failed": True})
    assert retry.status_code == 202, retry.text
    assert DocumentWorker(progress_interval=0).run_claimed(retry.json()["id"]) == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 4}


def test_a_worker_restart_carries_on_without_reading_finished_documents_again(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = _folder_of_drawings(tmp_path, "30859", 6)
    project_id = _project(client, folder, "30859")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    job_id = result["processing_job_id"]
    worker = DocumentWorker(progress_interval=0, stop=threading.Event())

    def close_the_window_after_two(_relative, count):
        if count == 3:
            worker.stop.set()     # Ctrl+C in the worker's window

    calls = _read_with(monkeypatch, close_the_window_after_two)
    assert worker.run_claimed(job_id) == "queued", "the job goes back in the queue"
    db_session.expire_all()
    job = db_session.get(BackgroundJob, job_id)
    assert job.status == "queued" and job.attempts == 0
    states = _states(db_session, project_id)
    assert states["fresh"] >= 2 and "processing" not in states
    first_run = len(calls)

    again = DocumentWorker(progress_interval=0)
    assert again.run_claimed(job_id) == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 6}
    assert len(calls) == 6 + (first_run - states["fresh"]), "each finished document was read once"


def test_a_job_left_by_a_dead_document_worker_is_recovered(client, db_session, tmp_path):
    from datetime import timedelta

    from app.core.timeutils import utc_now

    folder = _folder_of_drawings(tmp_path, "30860", 1)
    project_id = _project(client, folder, "30860")
    long_ago = utc_now() - timedelta(minutes=5)
    dead = BackgroundJob(kind=PROCESS, project_id=project_id, status="running", progress={}, heartbeat_at=long_ago,
                         started_at=long_ago, worker_id="gone", attempts=0)
    db_session.add(dead)
    db_session.commit()
    assert dict(jobs.recover_stale(db_session, kinds=(PROCESS,))) == {dead.id: "requeued"}
    db_session.refresh(dead)
    assert dead.status == "queued" and dead.attempts == 1


# --- the AI stage: the model never holds up the other documents (Document Processing V2, phase 3) --


def _forms_and_drawings(tmp_path, ep: str, forms: int = 2, drawings: int = 3) -> Path:
    folder = tmp_path / f"EP-{ep}"
    for n in range(forms):
        _submittal_form(folder / "03- MS" / f"0{n + 1}- FA" / f"form{n}.pdf",
                        reference=f"BBY006-GME-MAS-EL-FA-000{n + 1}")
    for n in range(drawings):
        _pdf(folder / "05- Drawings" / f"L{n:02d}.pdf", f"Drawing title\nBBY006-GME-SDW-EL-FA-{n:04d}\nREV. 00\nFloor {n} Layout")
    return folder


def test_ai_latency_does_not_hold_up_the_non_ai_documents(client, db_session, tmp_path, ai, monkeypatch):
    """Two forms the model must read (slowly) and three drawings: every
    drawing is written before the model is asked at all."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0), _reading("BBY006-GME-MAS-EL-FA-0002", 0)]
    ai.delay_s = 0.4
    folder = _forms_and_drawings(tmp_path, "30861")
    project_id = _project(client, folder, "30861")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    assert DocumentWorker(progress_interval=0).run_claimed(result["processing_job_id"]) == "succeeded"
    rows = _rows(db_session, project_id)
    assert _states(db_session, project_id) == {"fresh": 5} and ai.calls == 2
    forms = [r for r in rows.values() if r.role == "submittal_form"]
    drawings = [r for r in rows.values() if r.role == "document"]
    assert len(forms) == 2 and len(drawings) == 3
    assert max(d.last_processed_at for d in drawings) < min(f.last_processed_at for f in forms)
    assert all(f.reading_id and f.extracted.get("form") for f in forms)
    processed = _processing_job(db_session, project_id).result
    assert processed["read_by_ai"] == 2 and processed["processed"] == 5 and processed["telemetry"]["ai_stage_seconds"] > 0
    # One material submittal per system and brand: two Edwards fire alarm forms are one submittal, two forms.
    submittal_map = client.get(f"/projects/{project_id}/submittals/map").json()
    assert submittal_map["forms"] == 2 and submittal_map["submittals"] == 1


def test_a_stop_during_the_ai_stage_leaves_the_unread_forms_pending(client, db_session, tmp_path, ai, monkeypatch):
    from app.ai import provider as provider_module
    from app.ai.provider import RecordingProvider

    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = _forms_and_drawings(tmp_path, "30862")
    project_id = _project(client, folder, "30862")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)
    job_id = result["processing_job_id"]

    class StopAfterTheFirst(RecordingProvider):
        def complete(self, request):
            from app.database import SessionLocal

            api = SessionLocal()
            try:
                jobs.request_cancel(api, api.get(BackgroundJob, job_id))
            finally:
                api.close()
            return super().complete(request)

    provider = StopAfterTheFirst([_reading("BBY006-GME-MAS-EL-FA-0001", 0), _reading("BBY006-GME-MAS-EL-FA-0002", 0)])
    provider_module.set_provider(provider)
    assert DocumentWorker(progress_interval=0).run_claimed(job_id) == "cancelled"
    states = _states(db_session, project_id)
    assert states == {"fresh": 4, "pending": 1}, states
    assert provider.calls == 1
    rows = _rows(db_session, project_id)
    assert all(r.state == "fresh" for r in rows.values() if r.role == "document")

    provider_module.set_provider(RecordingProvider([_reading("BBY006-GME-MAS-EL-FA-0002", 0)]))
    resumed = client.post(f"/projects/{project_id}/jobs/process-documents")
    assert resumed.status_code == 202, resumed.text
    assert DocumentWorker(progress_interval=0).run_claimed(resumed.json()["id"]) == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 5}


def test_the_map_rebuild_takes_the_hashes_from_the_index(client, db_session, tmp_path, ai, monkeypatch):
    """Redrawing the map used to hash every form again (a hundred megabytes
    on EP-30784): the index already holds the hashes."""
    from app.ai import submittal_reader

    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0), _reading("BBY006-GME-MAS-EL-FA-0002", 0),
                  _reading("BBY006-GME-MAS-EL-FA-0001", 0, "approved", code="A")]
    folder = _forms_and_drawings(tmp_path, "30863", drawings=1)
    project_id = _project(client, folder, "30863")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"

    def must_not_hash(path):
        raise AssertionError(f"the map rebuild hashed {path} again")

    monkeypatch.setattr(submittal_reader.pipeline, "sha256_of", must_not_hash)
    _submittal_form(folder / "03- MS" / "01- FA" / "form0.pdf", reference="BBY006-GME-MAS-EL-FA-0001", reply="(A) Approved")
    job = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert job["status"] == "succeeded", job
    processing = _processing_job(db_session, project_id)
    assert processing.status == "succeeded" and processing.result["forms_changed"] is True
    assert processing.result["telemetry"]["reconcile_map_seconds"] >= 0
    assert client.get(f"/projects/{project_id}/submittals/map").json()["systems"][0]["rows"][0]["cells"]["R0"]["status"] == "A"


def test_an_ocr_failure_on_one_page_is_noted_and_the_batch_continues(client, db_session, tmp_path, monkeypatch):
    """Tesseract gives up on a page (a timeout): that page is noted, the
    document is read from its text, and the other documents are untouched."""
    from app.services import document_control
    from .test_document_processing_v2 import DRAWING_TEXT, _pdf_with_image, _png_with_text

    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = tmp_path / "EP-30864"
    _pdf_with_image(folder / "05- Drawings" / "stamped.pdf", DRAWING_TEXT, _png_with_text("STAMP", (200, 80)))
    # Sheets with a full text layer (a page of under 80 characters is read as a scan, and OCRed).
    for n in range(2):
        _pdf(folder / "05- Drawings" / f"L{n:02d}.pdf", DRAWING_TEXT.replace("FA-0001", f"FA-100{n}"))
    project_id = _project(client, folder, "30864")
    project = db_session.get(Project, project_id)
    result = document_sync.sync(db_session, project)

    def timed_out(page, images):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(document_control, "_ocr_images", timed_out)
    assert DocumentWorker(progress_interval=0).run_claimed(result["processing_job_id"]) == "succeeded"
    rows = _rows(db_session, project_id)
    assert _states(db_session, project_id) == {"fresh": 3}
    stamped = rows["05- Drawings/stamped.pdf"]
    assert any(note.startswith("Could not OCR") for note in stamped.extracted["notes"])
    assert stamped.extracted["records"][0]["reference"] == "BBY006-GME-SDW-EL-FA-0001"
    processed = _processing_job(db_session, project_id).result
    assert processed["partial"] == 1 and processed["processed"] == 2 and processed["failed"] == 0


# --- order ---------------------------------------------------------------------------------------


def test_processing_order_puts_what_the_registers_wait_on_first():
    priority = document_processing.priority
    assert priority("03- MS/01- FA/form.pdf", "submittal_form") == 0
    assert priority("08- approval/MS/FA/BBY006-GME-MAS-EL-FA-0001.pdf", "document") == 0
    assert priority("Transmittal/EP-29495 FA Sam B.docx", "transmittal") == 0
    assert priority("03- MS/03- FRC/Reply to consultant comments.pdf", "document") == 0
    assert priority("05- Drawings/1.FAVE/R1/05. Ground Floor/BBY006-GME-SDW-EL-FA-0001.pdf", "document") == 1
    assert priority("02- Material Submittals/FA/R0/EP-30880 - Material Submittal - FA - R0.pdf", "document") == 0
    assert priority("07- Letters/site instruction.pdf", "document") == 2
    assert priority("09- Other/catalogue.pdf", "document") == 3
    assert priority("02- Spec/283111 - FIRE DETECTION AND ALARM.pdf", "spec") == 3
    assert priority("07- Letters/huge scan.pdf", "document", size=60 * 1024 * 1024) == 3
    rows = [ProjectDocument(project_id=1, role="document", path=p, filename=Path(p).name, relative_path=p, state="pending")
            for p in ("09- Other/catalogue.pdf", "05- Drawings/R1/L01.pdf", "03- MS/reply.pdf")]
    ordered = sorted(rows, key=lambda r: (priority(r.relative_path, r.role, r.size), r.relative_path.lower()))
    assert [r.relative_path for r in ordered] == ["03- MS/reply.pdf", "05- Drawings/R1/L01.pdf", "09- Other/catalogue.pdf"]
