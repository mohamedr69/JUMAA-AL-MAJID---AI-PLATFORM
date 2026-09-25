"""The document sync as a queued job run by the worker process.

The API only queues a sync and answers at once; the worker claims it and
runs it. Two guarantees are the database's, not Python's: one queued-or-
running sync per project (a partial unique index), and one running sync at a
time across every worker (a compare-and-set claim). The concurrency tests
therefore run against a real SQLite file with a connection per caller -- the
suite's in-memory database is one shared connection and cannot show a race.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.routers.jobs as jobs_router
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import Base
from app.models import BackgroundJob
from app.services import document_sync, jobs
from app.workers.sync_worker import Worker

from .conftest import login

settings = get_settings()
BACKEND = Path(__file__).resolve().parents[1]
SYNC = "sync_documents"


@pytest.fixture()
def file_db(tmp_path):
    """A session factory over a real SQLite file in WAL mode, as the running
    platform has: every session its own connection."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    factory.url = engine.url
    yield factory
    engine.dispose()


def _project(client, tmp_path, ep="52000") -> int:
    folder = tmp_path / f"EP-{ep}"
    folder.mkdir(parents=True, exist_ok=True)
    login(client, settings.default_admin_email, settings.default_admin_password)
    return client.post("/projects", json={"ep_number": ep, "project_name": f"P{ep}", "source_folder_path": str(folder),
                                          "design_sheets": []}).json()["id"]


def _sync(client, project_id):
    response = client.post(f"/projects/{project_id}/jobs/sync-documents")
    assert response.status_code == 202, response.text
    return response.json()


def _set_status(db, job_id, status):
    job = db.get(BackgroundJob, job_id)
    job.status = status
    if status not in jobs.ACTIVE:
        job.finished_at = utc_now()
    db.commit()


# --- the API queues, and returns the one active job -------------------------------------------


def test_a_sync_request_queues_one_job_and_does_no_reading_itself(client, db_session, tmp_path, monkeypatch):
    """Tests 1 and 14: the request queues and answers; nothing is read in it."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the API read the folder itself")

    monkeypatch.setattr(document_sync, "sync", must_not_run)
    pid = _project(client, tmp_path)
    started = time.perf_counter()
    job = _sync(client, pid)
    assert time.perf_counter() - started < 2
    assert job["status"] == "queued" and job["kind"] == SYNC and job["already_active"] is False
    assert job["progress"]["message"] == jobs.QUEUED_MESSAGE
    assert db_session.query(BackgroundJob).filter(BackgroundJob.project_id == pid).count() == 1

    # The pages keep answering from the index while the sync waits and runs.
    _set_status(db_session, job["id"], "running")
    for path in (f"/projects/{pid}", f"/projects/{pid}/logs", f"/projects/{pid}/documents/status",
                 f"/projects/{pid}/jobs"):
        assert client.get(path).status_code == 200, path
    status = client.get(f"/projects/{pid}/documents/status").json()
    assert status["syncing"] is True and status["job"]["id"] == job["id"]
    assert status["worker_running"] is False, "no worker has said it is alive in this test"


def test_a_second_request_returns_the_queued_or_running_job(client, db_session, tmp_path, monkeypatch):
    """Tests 2 and 3."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    pid = _project(client, tmp_path)
    first = _sync(client, pid)
    again = _sync(client, pid)
    assert again["id"] == first["id"] and again["already_active"] is True and again["status"] == "queued"

    _set_status(db_session, first["id"], "running")
    while_running = _sync(client, pid)
    assert while_running["id"] == first["id"] and while_running["already_active"] is True
    assert while_running["status"] == "running"
    assert db_session.query(BackgroundJob).filter(BackgroundJob.project_id == pid).count() == 1


@pytest.mark.parametrize("ended", ["succeeded", "failed", "cancelled"])
def test_a_sync_after_the_last_one_ended_queues_a_new_job(client, db_session, tmp_path, monkeypatch, ended):
    """Tests 4, 5 and 6."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    pid = _project(client, tmp_path)
    first = _sync(client, pid)
    _set_status(db_session, first["id"], ended)
    second = _sync(client, pid)
    assert second["id"] != first["id"] and second["status"] == "queued" and second["already_active"] is False


def test_each_project_has_its_own_queued_sync(client, db_session, tmp_path, monkeypatch):
    """Test 8."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    a, b = _project(client, tmp_path, "52001"), _project(client, tmp_path, "52002")
    job_a, job_b = _sync(client, a), _sync(client, b)
    assert job_a["id"] != job_b["id"] and job_a["status"] == job_b["status"] == "queued"


def test_the_migrated_database_refuses_a_second_active_sync(client, db_session, tmp_path):
    """The guarantee is the migration's index, not the model's."""
    index = db_session.execute(text(
        "SELECT sql FROM sqlite_master WHERE name = 'uq_background_jobs_one_active_sync'")).scalar()
    assert index and "status IN ('queued', 'running')" in index
    project_id = _project(client, tmp_path)
    db_session.add(BackgroundJob(kind=SYNC, project_id=project_id, status="queued", progress={}))
    db_session.commit()
    db_session.add(BackgroundJob(kind=SYNC, project_id=project_id, status="running", progress={}))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    # Other kinds are not restricted: two IFC drawings can be read at once.
    db_session.add_all([BackgroundJob(kind="ifc_read", project_id=project_id, status="running", progress={})
                        for _ in range(2)])
    db_session.commit()


# --- the database decides, whatever the timing ------------------------------------------------


def test_ten_simultaneous_requests_make_exactly_one_job(file_db):
    """Test 7: ten callers released together, each on its own connection."""
    barrier = threading.Barrier(10)
    results, errors = [], []

    def request():
        db = file_db()
        try:
            barrier.wait()
            job, created = jobs.enqueue(db, kind=SYNC, project_id=25, user_id=None)
            results.append((job.id, created))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=request) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len({job_id for job_id, _ in results}) == 1, results
    assert sum(created for _, created in results) == 1
    db = file_db()
    assert db.query(BackgroundJob).filter(BackgroundJob.project_id == 25, BackgroundJob.status.in_(jobs.ACTIVE)).count() == 1
    db.close()


def test_a_request_that_loses_the_race_returns_the_winners_job(file_db, monkeypatch):
    """The exact interleaving the check-then-insert guard lost to: this
    caller looks, sees no sync, and another's insert lands before its own."""
    winner_db = file_db()
    winner, _ = jobs.enqueue(winner_db, kind=SYNC, project_id=7, user_id=None)
    looks = iter([None])
    real = jobs.active_job
    monkeypatch.setattr(jobs, "active_job", lambda db, pid, kind: next(looks, None) or real(db, pid, kind))
    loser_db = file_db()
    job, created = jobs.enqueue(loser_db, kind=SYNC, project_id=7, user_id=None)
    assert (job.id, created) == (winner.id, False)
    winner_db.close()
    loser_db.close()


def test_one_sync_runs_at_a_time_across_projects(file_db):
    """Test 9."""
    db = file_db()
    a, _ = jobs.enqueue(db, kind=SYNC, project_id=1, user_id=None)
    b, _ = jobs.enqueue(db, kind=SYNC, project_id=2, user_id=None)
    c, _ = jobs.enqueue(db, kind=SYNC, project_id=3, user_id=None)
    first = jobs.claim_next(db, "worker-1")
    assert first.id == a.id and first.status == "running" and first.worker_id == "worker-1"
    assert jobs.claim_next(db, "worker-2") is None, "B waits while A runs"
    _set_status(db, a.id, "succeeded")
    second = jobs.claim_next(db, "worker-2")
    assert second.id == b.id
    db.expire_all()
    assert db.get(BackgroundJob, c.id).status == "queued"
    db.close()


def test_two_worker_processes_cannot_both_claim(file_db, tmp_path):
    """Test 10, with two real processes released at the same moment. Two
    projects are queued, so each process has a job it could take: only one
    may run at all."""
    db = file_db()
    first, _ = jobs.enqueue(db, kind=SYNC, project_id=1, user_id=None)
    second, _ = jobs.enqueue(db, kind=SYNC, project_id=2, user_id=None)
    queued_ids = {first.id, second.id}
    db.close()
    script = (
        "import sys, time\n"
        "from app.database import SessionLocal\n"
        "from app.services import jobs\n"
        "go = float(sys.argv[2])\n"
        "while time.time() < go: time.sleep(0.0005)\n"
        "db = SessionLocal()\n"
        "job = jobs.claim_next(db, sys.argv[1])\n"
        "print('claimed', job.id if job else None)\n"
    )
    env = {**os.environ, "DATABASE_URL": str(file_db.url), "AI_ENABLED": "false"}
    go = time.time() + 4
    workers = [subprocess.Popen([sys.executable, "-c", script, f"worker-{n}", str(go)], cwd=BACKEND, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in (1, 2)]
    outputs = [w.communicate(timeout=60) for w in workers]
    claims = [out.strip().splitlines()[-1] for out, _err in outputs]
    assert all(w.returncode == 0 for w in workers), [err for _out, err in outputs]
    taken = [line.split()[1] for line in claims if line.split()[1] != "None"]
    assert len(taken) == 1, claims
    db = file_db()
    running = db.query(BackgroundJob).filter(BackgroundJob.status == "running").all()
    assert [j.id for j in running] == [int(taken[0])] and running[0].id in queued_ids
    db.close()


# --- the worker ---------------------------------------------------------------------------------


def _worker(file_db, runner, **kwargs):
    return Worker(session_factory=file_db, worker_id="test-worker", poll_seconds=0.05, progress_interval=0,
                  runners={SYNC: runner}, background_reading=False, **kwargs)


def test_a_stop_asked_for_while_running_cancels_at_the_next_step(file_db):
    """Test 11: cooperative -- the job sees the flag at its next step."""
    db = file_db()
    queued, _ = jobs.enqueue(db, kind=SYNC, project_id=3, user_id=None)
    seen = []

    def runner(session, job, ctx):
        for step in range(50):
            if step == 3:
                api = file_db()
                jobs.request_cancel(api, api.get(BackgroundJob, job.id))
                api.close()
            seen.append(step)
            ctx.progress(step, 50, f"file {step}")
        return {}

    assert _worker(file_db, runner).run_once() == queued.id
    db.expire_all()
    job = db.get(BackgroundJob, queued.id)
    assert job.status == "cancelled" and job.finished_at and seen == [0, 1, 2, 3]
    # A sync still in the queue is cancelled on the spot: nothing is running to ask.
    waiting, _ = jobs.enqueue(db, kind=SYNC, project_id=4, user_id=None)
    assert jobs.request_cancel(db, waiting).status == "cancelled"
    db.close()


def test_a_failed_job_is_recorded_and_the_worker_carries_on(file_db):
    """Test 12: the loop outlives a job that raises."""
    db = file_db()
    bad, _ = jobs.enqueue(db, kind=SYNC, project_id=10, user_id=None)
    good, _ = jobs.enqueue(db, kind=SYNC, project_id=11, user_id=None)

    def runner(session, job, ctx):
        if job.project_id == 10:
            raise RuntimeError("OneDrive has not brought the folder down")
        ctx.progress(1, 1, "done")
        return {"files": 1}

    worker = _worker(file_db, runner)
    loop = threading.Thread(target=worker.run_forever)
    loop.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        db.expire_all()
        if db.get(BackgroundJob, good.id).status == "succeeded":
            break
        time.sleep(0.05)
    worker.stop.set()
    loop.join(10)
    db.expire_all()
    assert db.get(BackgroundJob, bad.id).status == "failed"
    assert "OneDrive has not brought the folder down" in db.get(BackgroundJob, bad.id).error
    assert db.get(BackgroundJob, good.id).status == "succeeded"
    assert db.get(BackgroundJob, good.id).result == {"files": 1}
    assert not loop.is_alive()
    db.close()


def test_a_worker_shutdown_puts_the_running_job_back_in_the_queue(file_db):
    db = file_db()
    queued, _ = jobs.enqueue(db, kind=SYNC, project_id=12, user_id=None)
    worker = None

    def runner(session, job, ctx):
        ctx.progress(1, 10, "file 1")
        worker.stop.set()               # Ctrl+C in the worker's window
        ctx.progress(2, 10, "file 2")
        raise AssertionError("carried on after the stop")

    worker = _worker(file_db, runner)
    worker.run_once()
    db.expire_all()
    job = db.get(BackgroundJob, queued.id)
    assert job.status == "queued" and job.worker_id is None and "carry on" in job.progress["message"]
    assert job.attempts == 0, "a proper stop is not counted against the job"
    db.close()


def test_a_job_left_running_by_a_dead_worker_is_recovered(file_db):
    """Test 13: requeued, and counted; failed when it keeps taking the
    worker down; left alone while its worker is alive."""
    db = file_db()
    long_ago = utc_now() - timedelta(minutes=5)
    dead = BackgroundJob(kind=SYNC, project_id=20, status="running", progress={}, heartbeat_at=long_ago,
                         started_at=long_ago, worker_id="gone", attempts=1)
    again = BackgroundJob(kind=SYNC, project_id=21, status="running", progress={}, heartbeat_at=long_ago,
                          started_at=long_ago, worker_id="gone", attempts=jobs.MAX_ATTEMPTS)
    stopping = BackgroundJob(kind=SYNC, project_id=22, status="running", progress={}, heartbeat_at=long_ago,
                             started_at=long_ago, worker_id="gone", attempts=1, cancel_requested=True)
    alive = BackgroundJob(kind=SYNC, project_id=23, status="running", progress={}, heartbeat_at=utc_now(),
                          started_at=long_ago, worker_id="busy", attempts=1)
    db.add_all([dead, again, stopping, alive])
    db.commit()
    done = dict(jobs.recover_stale(db))
    assert done == {dead.id: "requeued", again.id: "failed", stopping.id: "cancelled"}
    db.expire_all()
    assert db.get(BackgroundJob, dead.id).status == "queued" and db.get(BackgroundJob, dead.id).worker_id is None
    assert "not started again" in db.get(BackgroundJob, again.id).error
    assert db.get(BackgroundJob, alive.id).status == "running"
    # The requeued one is counted, and claimed again.
    assert db.get(BackgroundJob, dead.id).attempts == 2
    _set_status(db, alive.id, "succeeded")
    assert jobs.claim_next(db, "new-worker").id == dead.id
    db.close()


def test_an_api_restart_leaves_the_workers_jobs_alone(file_db):
    db = file_db()
    waiting, _ = jobs.enqueue(db, kind=SYNC, project_id=30, user_id=None)
    thread_job = BackgroundJob(kind="boq_reread", project_id=30, status="running", progress={})
    db.add(thread_job)
    db.commit()
    assert jobs.fail_interrupted(db) == 1
    db.expire_all()
    assert db.get(BackgroundJob, thread_job.id).status == "failed"
    assert db.get(BackgroundJob, waiting.id).status == "queued"
    db.close()


def test_the_worker_heartbeat_tells_the_api_it_is_running(file_db):
    db = file_db()
    assert jobs.worker_running(db) is False
    worker = _worker(file_db, lambda *a: {})
    worker.beat()
    assert jobs.worker_running(db) is True
    jobs.worker_stopped(db, worker.id)
    assert jobs.worker_running(db) is False
    db.close()


def test_progress_writes_are_spaced_but_the_last_step_is_always_written(file_db):
    db = file_db()
    queued, _ = jobs.enqueue(db, kind=SYNC, project_id=40, user_id=None)
    ctx = jobs.JobContext(queued.id, min_interval=60, session_factory=file_db)
    ctx.progress(1, 349, "file 1")
    ctx.progress(2, 349, "file 2")          # within the interval: not written
    db.expire_all()
    assert db.get(BackgroundJob, queued.id).progress["done"] == 1
    ctx.progress(349, 349, "last file")
    db.expire_all()
    assert db.get(BackgroundJob, queued.id).progress["done"] == 349
    db.close()
