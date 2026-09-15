"""Long work -- reading sheets, checking documents -- as jobs the server keeps.

A re-read of a four-page scanned sheet takes a minute or more. Run inside the
request, the page could only spin, a refresh lost the answer, and there was
no stopping it. A job is stored: the page starts it, polls it, can ask it to
stop, and finds it again after a refresh. Progress is reported as done/total
steps with a sentence; cancellation is checked between steps, so a job stops
at the next page rather than mid-write.

A job that was running when the server stopped cannot be resumed; on start it
is marked failed with that reason, so no page waits on it forever.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.database import SessionLocal
from app.models import BackgroundJob

ACTIVE = ("queued", "running")


class Cancelled(Exception):
    """Raised inside a job when a stop was asked for."""


@dataclass
class JobContext:
    job_id: int

    def progress(self, done: int, total: int, message: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(BackgroundJob, self.job_id)
            if job is None:
                return
            job.progress = {"done": done, "total": total, "message": message[:300]}
            cancel = job.cancel_requested
            db.commit()
        finally:
            db.close()
        if cancel:
            raise Cancelled()

    def check(self) -> None:
        db = SessionLocal()
        try:
            job = db.get(BackgroundJob, self.job_id)
            if job is not None and job.cancel_requested:
                raise Cancelled()
        finally:
            db.close()


def active_job(db: Session, project_id: int, kind: str) -> BackgroundJob | None:
    return (db.query(BackgroundJob)
            .filter(BackgroundJob.project_id == project_id, BackgroundJob.kind == kind, BackgroundJob.status.in_(ACTIVE))
            .order_by(BackgroundJob.id.desc()).first())


def start(db: Session, *, kind: str, project_id: int | None, user_id: int | None,
          work: Callable[[Session, JobContext], dict], run_inline: bool = False) -> BackgroundJob:
    """Store the job and run `work(db, ctx)` on a thread of its own, with a
    session of its own. `run_inline` runs it before returning (tests)."""
    job = BackgroundJob(kind=kind, project_id=project_id, created_by_id=user_id, status="queued",
                        progress={"done": 0, "total": 0, "message": "Waiting to start"})
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    def run() -> None:
        session = SessionLocal()
        try:
            row = session.get(BackgroundJob, job_id)
            row.status, row.started_at = "running", utc_now()
            session.commit()
            result = work(session, JobContext(job_id))
            row = session.get(BackgroundJob, job_id)
            row.status, row.result, row.finished_at = "succeeded", result, utc_now()
            row.progress = {**(row.progress or {}), "message": "Finished"}
            session.commit()
        except Cancelled:
            session.rollback()
            row = session.get(BackgroundJob, job_id)
            row.status, row.finished_at = "cancelled", utc_now()
            row.progress = {**(row.progress or {}), "message": "Stopped on request"}
            session.commit()
        except Exception as exc:  # noqa: BLE001 -- a job's failure is recorded, never lost with its thread
            session.rollback()
            row = session.get(BackgroundJob, job_id)
            row.status, row.finished_at = "failed", utc_now()
            row.error = f"{type(exc).__name__}: {exc}"[:2000]
            row.detail = {"trace": traceback.format_exc()[-4000:]}
            session.commit()
        finally:
            session.close()

    if run_inline:
        run()
    else:
        threading.Thread(target=run, name=f"job-{job_id}-{kind}", daemon=True).start()
    db.expire_all()
    return db.get(BackgroundJob, job_id)


def request_cancel(db: Session, job: BackgroundJob) -> BackgroundJob:
    if job.status in ACTIVE:
        job.cancel_requested = True
        db.commit()
        db.refresh(job)
    return job


def fail_interrupted(db: Session) -> int:
    """Jobs left running by a server that stopped: they will never finish."""
    stale = db.query(BackgroundJob).filter(BackgroundJob.status.in_(ACTIVE)).all()
    for job in stale:
        job.status, job.finished_at = "failed", utc_now()
        job.error = "The server restarted while this job was running. Start it again."
    db.commit()
    return len(stale)
