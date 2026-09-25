"""Long work -- reading sheets, checking documents, syncing a folder -- as jobs the server keeps.

A re-read of a four-page scanned sheet takes a minute or more. Run inside the
request, the page could only spin, a refresh lost the answer, and there was
no stopping it. A job is stored: the page starts it, polls it, can ask it to
stop, and finds it again after a refresh. Progress is reported as done/total
steps with a sentence; cancellation is checked between steps, so a job stops
at the next page rather than mid-write.

Two ways a job runs:

  * On a thread of the API (`start`): the reads a page is waiting on -- a
    BOQ re-read, an IFC drawing, the AI checks. A job of this kind that was
    running when the API stopped cannot be resumed; on start it is marked
    failed with that reason (`fail_interrupted`), so no page waits for ever.

  * In the worker process (`WORKER_KINDS`, app.workers.sync_worker): the
    document sync, which reads a whole project folder and must never compete
    with page requests for the API's one Python interpreter. The API only
    puts the job in the queue (`enqueue`) and answers at once; the worker
    claims it (`claim_next`), runs it, and writes progress and a heartbeat
    here. This table is the queue and the one place the two processes agree
    on what is running.

Duplicates are refused by the database, not by a check in Python. A check
("is one running?") followed by an insert lets two requests that arrive
together both see "no" and both insert -- three clicks on Sync started three
syncs of one project, which then failed each other with "database is
locked". A partial unique index allows one queued-or-running sync per
project; `enqueue` inserts, and when the index refuses, returns the job that
won. Claiming is a compare-and-set: the row goes from queued to running only
if it is still queued and no other worker job is running, in one statement,
so two workers cannot take the same job and only one sync runs at a time.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.database import SessionLocal
from app.models import BackgroundJob, BackgroundWorker

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
FINISHED = ("succeeded", "failed", "cancelled")
# The kinds the worker process runs; the API never runs these itself.
WORKER_KINDS = ("sync_documents",)
QUEUED_MESSAGE = "Queued: waiting for the background worker"
# A worker says it is alive every HEARTBEAT seconds; a running job whose
# heartbeat is older than STALE_AFTER was left by a worker that died.
HEARTBEAT_SECONDS = 10
STALE_AFTER = timedelta(seconds=90)
# Left running this many times by a worker that died: the next time, failed
# rather than queued again -- something in it keeps taking the worker down.
MAX_ATTEMPTS = 2


class Cancelled(Exception):
    """Raised inside a job when a stop was asked for."""


class Interrupted(Exception):
    """Raised inside a worker job when the worker itself is shutting down:
    the job goes back in the queue rather than being failed or cancelled."""


@dataclass
class JobContext:
    """What a running job reports through. `min_interval` spaces the writes
    (the worker reports a step per file, and a commit per file per second is
    enough for a progress bar); the last step is always written. `stop` is
    the worker's own shutdown, checked at every step."""

    job_id: int
    min_interval: float = 0.0
    stop: threading.Event | None = None
    session_factory: Callable[[], Session] | None = None
    _last_write: float = field(default=0.0, init=False, repr=False)

    def _session(self) -> Session:
        return (self.session_factory or SessionLocal)()

    def progress(self, done: int, total: int, message: str, **extra) -> None:
        """`extra` is kept beside the step count: a stage's name, the
        seconds a job estimates it has left."""
        if self.stop is not None and self.stop.is_set():
            raise Interrupted()
        now = time.monotonic()
        if self.min_interval and self._last_write and now - self._last_write < self.min_interval and done < total:
            return
        self._last_write = now
        db = self._session()
        try:
            job = db.get(BackgroundJob, self.job_id)
            if job is None:
                return
            job.progress = {"done": done, "total": total, "message": message[:300], **extra}
            job.heartbeat_at = utc_now()
            cancel = job.cancel_requested
            db.commit()
        finally:
            db.close()
        if cancel:
            raise Cancelled()

    def check(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise Interrupted()
        db = self._session()
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


# --- jobs the API runs on a thread of its own ----------------------------------------


def start(db: Session, *, kind: str, project_id: int | None, user_id: int | None,
          work: Callable[[Session, JobContext], dict], run_inline: bool = False) -> BackgroundJob:
    """Store the job and run `work(db, ctx)` on a thread of its own, with a
    session of its own. `run_inline` runs it before returning (tests).
    Not for WORKER_KINDS: those are queued with `enqueue`."""
    if kind in WORKER_KINDS:
        raise ValueError(f"{kind} runs in the worker process; queue it with enqueue()")
    job = BackgroundJob(kind=kind, project_id=project_id, created_by_id=user_id, status="queued",
                        progress={"done": 0, "total": 0, "message": "Waiting to start"})
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    def run() -> None:
        session = SessionLocal()
        try:
            execute(session, job_id, work)
        finally:
            session.close()

    if run_inline:
        run()
    else:
        threading.Thread(target=run, name=f"job-{job_id}-{kind}", daemon=True).start()
    db.expire_all()
    return db.get(BackgroundJob, job_id)


def execute(session: Session, job_id: int, work: Callable[[Session, JobContext], dict], *,
            ctx: JobContext | None = None, claimed: bool = False) -> str:
    """Run `work` for a job and record how it ended. Returns the final
    status, or "queued" when a worker shutdown put it back in the queue.
    `claimed` means the worker already set it running (`claim_next`)."""
    ctx = ctx or JobContext(job_id)
    if not claimed:
        row = session.get(BackgroundJob, job_id)
        row.status, row.started_at = "running", utc_now()
        session.commit()
    try:
        result = work(session, ctx)
        row = session.get(BackgroundJob, job_id)
        row.status, row.result, row.finished_at = "succeeded", result, utc_now()
        row.progress = {**(row.progress or {}), "message": "Finished"}
        session.commit()
        return "succeeded"
    except Cancelled:
        session.rollback()
        row = session.get(BackgroundJob, job_id)
        row.status, row.finished_at = "cancelled", utc_now()
        row.progress = {**(row.progress or {}), "message": "Stopped on request"}
        session.commit()
        return "cancelled"
    except Interrupted:
        session.rollback()
        requeue(session, job_id, "Interrupted when the worker stopped; queued to carry on when it starts again")
        return "queued"
    except Exception as exc:  # noqa: BLE001 -- a job's failure is recorded, never lost with its thread
        session.rollback()
        log.exception("Job %s failed", job_id)
        row = session.get(BackgroundJob, job_id)
        row.status, row.finished_at = "failed", utc_now()
        row.error = f"{type(exc).__name__}: {exc}"[:2000]
        row.detail = {"trace": traceback.format_exc()[-4000:]}
        session.commit()
        return "failed"


# --- jobs the worker runs --------------------------------------------------------------


def enqueue(db: Session, *, kind: str, project_id: int, user_id: int | None,
            message: str = QUEUED_MESSAGE) -> tuple[BackgroundJob, bool]:
    """The project's active job of this kind, created if there is none:
    (job, created). Safe against any number of simultaneous callers, in
    any number of processes: the insert is refused by the partial unique
    index when another caller's job got in first, and that job is
    returned instead. Only for WORKER_KINDS, which the index covers."""
    if kind not in WORKER_KINDS:
        raise ValueError(f"{kind} is not a worker job kind")
    for attempt in range(20):
        existing = active_job(db, project_id, kind)
        if existing is not None:
            log.info("%s already active for project %s: returning job %s", kind, project_id, existing.id)
            return existing, False
        job = BackgroundJob(kind=kind, project_id=project_id, created_by_id=user_id, status="queued",
                            progress={"done": 0, "total": 0, "message": message})
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            # Another request inserted the project's job between our look and
            # our insert. Look again: that is the job to return. (If it has
            # already finished, the loop inserts afresh.)
            db.rollback()
            log.info("Concurrent duplicate %s request for project %s prevented", kind, project_id)
            continue
        except OperationalError as exc:
            # The write lock was held past the busy timeout; nothing was written.
            db.rollback()
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05 * (attempt + 1))
            continue
        db.refresh(job)
        log.info("Created %s job %s for project %s", kind, job.id, project_id)
        return job, True
    raise RuntimeError(f"could not queue {kind} for project {project_id}: the database stayed busy")


def claim(db: Session, job_id: int, worker_id: str, kinds: tuple[str, ...] = WORKER_KINDS) -> BackgroundJob | None:
    """Mark this queued job running for this worker, or None if it cannot be
    had. One statement decides it: the row changes only if it is still
    queued *and* no job of these kinds is running -- so two workers never
    take the same job, and never run two syncs at once."""
    table = BackgroundJob.__table__
    other = table.alias("other")
    now = utc_now()
    claimed = db.execute(
        update(table)
        .where(table.c.id == job_id, table.c.status == "queued", table.c.kind.in_(kinds),
               ~exists(select(other.c.id).where(other.c.kind.in_(kinds), other.c.status == "running")))
        .values(status="running", started_at=now, heartbeat_at=now, worker_id=worker_id)
    ).rowcount
    db.commit()
    if claimed != 1:
        return None
    db.expire_all()
    job = db.get(BackgroundJob, job_id)
    log.info("Worker %s claimed job %s (%s, project %s)", worker_id, job.id, job.kind, job.project_id)
    return job


def claim_next(db: Session, worker_id: str, kinds: tuple[str, ...] = WORKER_KINDS) -> BackgroundJob | None:
    """Take the oldest queued job of these kinds (`claim`), or None: nothing
    is queued, or a job of these kinds is running and finishes first."""
    table = BackgroundJob.__table__
    candidates = db.execute(
        select(table.c.id).where(table.c.kind.in_(kinds), table.c.status == "queued").order_by(table.c.id).limit(10)
    ).scalars().all()
    for job_id in candidates:
        job = claim(db, job_id, worker_id, kinds)
        if job is not None:
            return job
        still_running = db.execute(
            select(table.c.id).where(table.c.kind.in_(kinds), table.c.status == "running").limit(1)
        ).first()
        if still_running is not None:
            return None     # one at a time: the running one finishes first
        # Otherwise another worker took this one a moment ago: try the next.
    return None


def requeue(db: Session, job_id: int, message: str) -> None:
    db.execute(update(BackgroundJob.__table__).where(BackgroundJob.__table__.c.id == job_id).values(
        status="queued", started_at=None, worker_id=None, heartbeat_at=None,
        progress={"done": 0, "total": 0, "message": message}))
    db.commit()


def recover_stale(db: Session, *, stale_after: timedelta = STALE_AFTER, max_attempts: int = MAX_ATTEMPTS,
                  kinds: tuple[str, ...] = WORKER_KINDS) -> list[tuple[int, str]]:
    """Running worker jobs whose worker stopped saying it was alive -- a PC
    switched off, a worker window closed. A sync reads only what is not in
    the index yet, so starting it again carries on where it stopped: such a
    job goes back in the queue, counted in `attempts`. One already left
    behind `max_attempts` times is failed instead and says why: something in
    it keeps taking the worker down. (A worker that is stopped properly puts
    its job back itself, uncounted -- closing it at the end of the day
    costs a sync nothing.) A stop that was asked for is honoured. Returns
    (job id, what was done) for each."""
    table = BackgroundJob.__table__
    cutoff = utc_now() - stale_after
    stale = db.query(BackgroundJob).filter(
        BackgroundJob.kind.in_(kinds), BackgroundJob.status == "running",
        ((BackgroundJob.heartbeat_at.is_(None)) & (BackgroundJob.started_at < cutoff))
        | (BackgroundJob.heartbeat_at < cutoff),
    ).all()
    done: list[tuple[int, str]] = []
    for job in stale:
        if job.cancel_requested:
            values = {"status": "cancelled", "finished_at": utc_now(),
                      "progress": {**(job.progress or {}), "message": "Stopped on request"}}
            action = "cancelled"
        elif (job.attempts or 0) >= max_attempts:
            values = {"status": "failed", "finished_at": utc_now(),
                      "error": f"The worker stopped without finishing this job {job.attempts + 1} times, so it "
                               "was not started again. Start the sync again once the cause is fixed."}
            action = "failed"
        else:
            values = {"status": "queued", "started_at": None, "worker_id": None, "heartbeat_at": None,
                      "attempts": (job.attempts or 0) + 1,
                      "progress": {"done": 0, "total": 0,
                                   "message": "Interrupted when the worker stopped; queued to carry on"}}
            action = "requeued"
        # Compare-and-set on the heartbeat: a worker that was only slow, and
        # has just said it is alive, keeps its job.
        changed = db.execute(update(table).where(
            table.c.id == job.id, table.c.status == "running",
            (table.c.heartbeat_at == job.heartbeat_at) if job.heartbeat_at is not None else table.c.heartbeat_at.is_(None),
        ).values(**values)).rowcount
        db.commit()
        if changed:
            log.warning("Job %s was left running by a worker that stopped: %s", job.id, action)
            done.append((job.id, action))
    return done


def request_cancel(db: Session, job: BackgroundJob) -> BackgroundJob:
    """Ask a job to stop. A worker job still waiting in the queue is
    cancelled on the spot -- nothing is running to ask."""
    if job.status not in ACTIVE:
        return job
    table = BackgroundJob.__table__
    if job.kind in WORKER_KINDS and job.status == "queued":
        cancelled = db.execute(update(table).where(table.c.id == job.id, table.c.status == "queued").values(
            status="cancelled", cancel_requested=True, finished_at=utc_now(),
            progress={**(job.progress or {}), "message": "Stopped on request"})).rowcount
        db.commit()
        if cancelled:
            log.info("Cancelled queued job %s", job.id)
            db.refresh(job)
            return job
    job.cancel_requested = True
    db.commit()
    log.info("Cancellation requested for job %s", job.id)
    db.refresh(job)
    return job


def fail_interrupted(db: Session) -> int:
    """Jobs the API was running on its own threads when it stopped: they will
    never finish. Worker jobs are not the API's to judge -- a queued sync
    waits for the worker, and a running one is the worker's (see
    `recover_stale`) -- so an API restart leaves them alone."""
    stale = (db.query(BackgroundJob)
             .filter(BackgroundJob.status.in_(ACTIVE), BackgroundJob.kind.notin_(WORKER_KINDS)).all())
    for job in stale:
        job.status, job.finished_at = "failed", utc_now()
        job.error = "The server restarted while this job was running. Start it again."
    db.commit()
    return len(stale)


# --- the workers themselves ------------------------------------------------------------


def worker_beat(db: Session, worker_id: str, *, pid: int, hostname: str, current_job_id: int | None) -> None:
    """The worker's heartbeat, and its current job's."""
    now = utc_now()
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        worker = BackgroundWorker(id=worker_id, pid=pid, hostname=hostname, started_at=now)
        db.add(worker)
    worker.heartbeat_at, worker.current_job_id, worker.stopped_at = now, current_job_id, None
    if current_job_id is not None:
        db.execute(update(BackgroundJob.__table__).where(
            BackgroundJob.__table__.c.id == current_job_id, BackgroundJob.__table__.c.status == "running",
            BackgroundJob.__table__.c.worker_id == worker_id).values(heartbeat_at=now))
    db.commit()


def worker_stopped(db: Session, worker_id: str) -> None:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is not None:
        worker.stopped_at, worker.current_job_id = utc_now(), None
        db.commit()


def worker_running(db: Session, *, within: timedelta = timedelta(seconds=HEARTBEAT_SECONDS * 3)) -> bool:
    """Whether a worker has said it is alive recently."""
    return db.query(BackgroundWorker).filter(BackgroundWorker.stopped_at.is_(None),
                                             BackgroundWorker.heartbeat_at >= utc_now() - within).first() is not None
