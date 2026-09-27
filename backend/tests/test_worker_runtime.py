"""The workers' runtime: what a long-lived process runs is announced at
start and proven importable before it takes a job; a code change after a
worker started is caught at the next start, not on a job hours later;
per-document classification failures stay non-fatal; pending processing
jobs survive a worker restart without being duplicated."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models import BackgroundJob, Project, ProjectDocument
from app.services import document_processing, jobs
from app.workers import runtime
from app.workers.document_worker import DocumentWorker

from .test_document_sync import _pdf, _project, ai  # noqa: F401 -- fixture
from .test_file_sync_v2_processing import _processing_job, _rows, _states

settings = get_settings()


# --- TEST 1 / TEST 2 / TEST 3: the symbols and modules a worker needs import ---------------------


def test_the_classification_model_and_the_worker_modules_import():
    from app.models import DocumentClassification  # TEST 1

    assert DocumentClassification.__tablename__ == "document_classifications"
    for name in ("app.workers.sync_worker", "app.workers.document_worker", "app.workers.ifc_worker",
                 "app.services.document_classification", "app.services.document_processing", "app.services.project_deletion"):
        assert importlib.import_module(name)


@pytest.mark.parametrize("enabled", [True, False])
def test_the_document_worker_runtime_validates_with_classification_on_and_off(monkeypatch, enabled):
    monkeypatch.setattr(settings, "document_classification_v2", enabled)
    fp = runtime.validate("document-worker")
    assert fp["classification_v2"] is enabled
    assert (fp["classification_rules"] is not None) is enabled
    assert Path(fp["models"]).name == "models.py" and Path(fp["models"]).is_relative_to(Path(fp["root"]))


# --- TEST 5: a mismatch fails at start, with the paths in the message ----------------------------


def _stale_models(*missing: str) -> types.ModuleType:
    """app.models as a process started before a change holds it: the same
    file, without the symbols added since."""
    import app.models as real

    stale = types.ModuleType("app.models")
    stale.__file__ = real.__file__
    for name in dir(real):
        if name not in missing and not name.startswith("__"):
            setattr(stale, name, getattr(real, name))
    return stale


def test_a_worker_whose_loaded_models_lack_a_required_class_fails_at_start(monkeypatch):
    monkeypatch.setattr(settings, "document_classification_v2", True)
    with pytest.raises(runtime.RuntimeMismatch) as failure:
        runtime.validate("document-worker", models=_stale_models("DocumentClassification"), import_services=False)
    message = str(failure.value)
    assert "DocumentClassification" in message and "document-worker startup failed" in message
    assert str(Path(runtime.root()) / "app" / "models.py") in message and sys.executable in message
    assert "Restart the process" in message
    # With the feature off the class is not required: the stale worker still starts (TEST 3).
    monkeypatch.setattr(settings, "document_classification_v2", False)
    assert runtime.validate("document-worker", models=_stale_models("DocumentClassification"), import_services=False)
    # A model every job needs is required whatever the flag.
    with pytest.raises(runtime.RuntimeMismatch, match="ProjectDocument"):
        runtime.validate("sync-worker", models=_stale_models("ProjectDocument"), import_services=False)


def test_models_loaded_from_another_folder_are_a_mismatch(monkeypatch, tmp_path):
    elsewhere = _stale_models()
    elsewhere.__file__ = str(tmp_path / "other-checkout" / "backend" / "app" / "models.py")
    with pytest.raises(runtime.RuntimeMismatch, match="not from this backend"):
        runtime.validate("sync-worker", models=elsewhere, import_services=False)


def test_start_exits_with_status_2_on_a_mismatch(monkeypatch):
    def broken(process, **_kwargs):
        raise runtime.RuntimeMismatch("document-worker startup failed:\n  required model X unavailable")

    monkeypatch.setattr(runtime, "validate", broken)
    with pytest.raises(SystemExit) as stop:
        runtime.start("document-worker")
    assert stop.value.code == 2


# --- TEST 6: the fingerprint ----------------------------------------------------------------------


def test_the_fingerprint_names_the_process_interpreter_root_and_models(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="app.workers.runtime")
    fp = runtime.announce("document-worker")
    assert fp["process"] == "document-worker" and fp["python"] == sys.executable
    assert fp["models"].lower().endswith("models.py") and fp["root"] == str(runtime.root())
    assert fp["pid"] and fp["python_version"] and fp["started_at"]
    text = runtime.describe(fp)
    assert "Python:" in text and "Models:" in text and "Root:" in text and "PID:" in text
    assert "document-worker started" in caplog.text
    assert runtime.main(["document-worker"]) == 0


# --- TEST 4: a runtime classification error does not fail the processing --------------------------


def test_a_classification_exception_in_the_worker_leaves_the_job_successful(client, db_session, tmp_path, ai, monkeypatch):
    from app.services import document_classification

    monkeypatch.setattr(settings, "document_classification_v2", True)
    monkeypatch.setattr(document_classification, "assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    folder = tmp_path / "EP-30920"
    _pdf(folder / "05- Drawings" / "L01.pdf", "Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout")
    project_id = _project(client, folder, "30920")
    job = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert job["status"] == "succeeded"
    processed = _processing_job(db_session, project_id)
    assert processed.status == "succeeded" and processed.result["processed"] == 1 and processed.result["failed"] == 0
    assert _states(db_session, project_id) == {"fresh": 1}


# --- TEST 7 / TEST 8: pending jobs survive a worker restart, once ----------------------------------


def test_a_pending_processing_job_survives_a_worker_restart_and_is_not_duplicated(client, db_session, tmp_path, monkeypatch):
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    folder = tmp_path / "EP-30921"
    for n in range(3):
        _pdf(folder / "05- Drawings" / f"L{n:02d}.pdf", f"Drawing title\nBBY006-GME-SDW-EL-FA-{n:04d}\nREV. 00\nFloor {n} Layout")
    project_id = _project(client, folder, "30921")
    project = db_session.get(Project, project_id)
    from app.services import document_sync

    document_sync.sync(db_session, project)
    queued = _processing_job(db_session, project_id)
    assert queued.status == "queued" and _states(db_session, project_id) == {"pending": 3}

    # A worker claims the job and dies mid-way: the job is left running with a
    # heartbeat that goes stale, the rows as they were.
    first = DocumentWorker(progress_interval=0, worker_id="documents:test:1")
    claimed = jobs.claim(db_session, queued.id, first.id, tuple(first.runners), first.limit)
    assert claimed is not None and claimed.status == "running"
    from datetime import timedelta

    from app.core.timeutils import utc_now

    claimed.heartbeat_at = utc_now() - timedelta(minutes=10)
    db_session.commit()

    # The next worker to start recovers it: queued again, once.
    second = DocumentWorker(progress_interval=0, worker_id="documents:test:2")
    second.recover()
    db_session.expire_all()
    recovered = db_session.get(BackgroundJob, queued.id)
    assert recovered.status == "queued" and recovered.attempts == 1
    assert db_session.query(BackgroundJob).filter(BackgroundJob.project_id == project_id,
                                                  BackgroundJob.kind == document_processing.JOB_KIND).count() == 1
    # Asking for the pending documents to be processed again returns that job, not a second one.
    again, created = document_processing.enqueue(db_session, project, user_id=None)
    assert again.id == queued.id and created is False

    assert second.run_once() == queued.id
    db_session.expire_all()
    assert db_session.get(BackgroundJob, queued.id).status == "succeeded"
    assert _states(db_session, project_id) == {"fresh": 3}
    assert db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count() == 3, "no duplicate rows"
    assert db_session.query(BackgroundJob).filter(BackgroundJob.project_id == project_id,
                                                  BackgroundJob.kind == document_processing.JOB_KIND).count() == 1


# --- the API says what it runs too ---------------------------------------------------------------


def test_health_carries_the_api_runtime_fingerprint(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["runtime"]["process"] == "api" and body["runtime"]["python"] == sys.executable
    assert body["runtime"]["models"].lower().endswith("models.py") and body["runtime"]["root"] == str(runtime.root())
    assert set(body["flags"]) == {"document_classification_v2", "ai_read_full_second_pass"}
