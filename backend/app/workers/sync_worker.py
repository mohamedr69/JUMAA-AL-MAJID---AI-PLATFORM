"""The background worker: runs the document syncs, in a process of its own.

    venv\\Scripts\\python -m app.workers.sync_worker      (start.bat starts it)

A sync reads a whole project folder -- hundreds of PDFs, OCR, the drawings'
approval boxes, the AI for material submittal forms. On a thread of the API
it held the API's one Python interpreter for minutes at a time, and every
page waited behind it. Here it runs in another process: the API only queues
the job and answers at once (app.services.jobs.enqueue); this process claims
it, runs it, and writes its progress to the database, where the Jobs panel
reads it. Nothing is shared between the two but the database.

  * One sync at a time, across every project and every worker: a claim only
    succeeds while no other sync is running (app.services.jobs.claim).
  * A job that fails is recorded as failed; the worker carries on with the
    next. Only a stop -- Ctrl+C, closing the window -- ends it.
  * Stopping is cooperative. The running sync is asked to stop at its next
    file (everything read so far is already committed), and goes back in
    the queue to carry on when the worker next starts. A worker killed
    outright stops saying it is alive; its job is put back in the queue by
    the next worker to look (app.services.jobs.recover_stale).
  * It runs below normal Windows priority, and so do the processes it reads
    files in: the engineer's own programs and the API come first.

It also does the other background reading the API used to do when it
started -- the catch-up syncs after a change to the reading rules, the
datasheet library's index, and the archive index's periodic scan -- so the
API is ready for pages the moment it is up.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.services import jobs
from app.services.jobs import JobContext

log = logging.getLogger("app.workers.sync_worker")


def _run_sync(session: Session, job, ctx: JobContext) -> dict:
    from app.services.sync_service import run_project_sync

    return run_project_sync(session, job, ctx)


# What the worker runs, by job kind (app.services.jobs.WORKER_KINDS).
RUNNERS = {"sync_documents": _run_sync}


class Worker:
    def __init__(self, *, session_factory=SessionLocal, worker_id: str | None = None,
                 poll_seconds: float | None = None, progress_interval: float = 1.0,
                 stop: threading.Event | None = None, runners: dict | None = None,
                 background_reading: bool = True):
        self.session_factory = session_factory
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.id = worker_id or f"{self.hostname}:{self.pid}:{int(time.time())}"[:64]
        self.poll_seconds = poll_seconds if poll_seconds is not None else get_settings().worker_poll_seconds
        self.progress_interval = progress_interval
        self.stop = stop or threading.Event()
        self.runners = runners if runners is not None else RUNNERS
        # The catch-up syncs, the datasheet index and the archive scans at
        # start (off in tests that run a worker of their own).
        self.background_reading = background_reading
        self.current_job_id: int | None = None

    # --- one job ---------------------------------------------------------

    def run_job(self, db: Session, job) -> str:
        """Run a job this worker has claimed and record how it ended."""
        runner = self.runners.get(job.kind)
        job_id = job.id
        self.current_job_id = job_id
        try:
            self.beat()
            if runner is None:
                return jobs.execute(db, job_id, _unknown_kind(job.kind), claimed=True)
            ctx = JobContext(job_id, min_interval=self.progress_interval, stop=self.stop,
                             session_factory=self.session_factory)
            started = time.monotonic()
            status = jobs.execute(db, job_id, lambda session, c: runner(session, session.get(type(job), job_id), c),
                                  ctx=ctx, claimed=True)
            log.info("Job %s %s after %.0f s", job_id, "was put back in the queue" if status == "queued" else status,
                     time.monotonic() - started)
            return status
        finally:
            self.current_job_id = None
            self.beat()

    def run_once(self) -> int | None:
        """Claim the next queued job and run it. Returns its id, or None when
        there was nothing to claim."""
        db = self.session_factory()
        try:
            job = jobs.claim_next(db, self.id, tuple(self.runners))
            if job is None:
                return None
            self.run_job(db, job)
            return job.id
        finally:
            db.close()

    def run_claimed(self, job_id: int) -> str | None:
        """Claim this one queued job and run it (the tests' inline mode).
        None when it could not be claimed."""
        db = self.session_factory()
        try:
            job = jobs.claim(db, job_id, self.id, tuple(self.runners))
            return self.run_job(db, job) if job is not None else None
        finally:
            db.close()

    # --- staying alive ---------------------------------------------------

    def beat(self) -> None:
        db = self.session_factory()
        try:
            jobs.worker_beat(db, self.id, pid=self.pid, hostname=self.hostname, current_job_id=self.current_job_id)
        except Exception:  # noqa: BLE001 -- a missed beat is retried on the next
            log.warning("Could not record the worker's heartbeat", exc_info=True)
        finally:
            db.close()

    def _beat_forever(self) -> None:
        while not self.stop.wait(jobs.HEARTBEAT_SECONDS):
            self.beat()

    def recover(self) -> None:
        db = self.session_factory()
        try:
            jobs.recover_stale(db)
        except Exception:  # noqa: BLE001
            log.warning("Could not check for jobs left by a stopped worker", exc_info=True)
        finally:
            db.close()

    # --- the loop ----------------------------------------------------------

    def run_forever(self) -> None:
        log.info("Worker %s started (poll every %.1f s)", self.id, self.poll_seconds)
        self.beat()
        threading.Thread(target=self._beat_forever, name="worker-heartbeat", daemon=True).start()
        self.recover()
        if self.background_reading:
            _start_background_reading(self)
        last_recovery = time.monotonic()
        try:
            while not self.stop.is_set():
                if time.monotonic() - last_recovery > 30:
                    self.recover()
                    last_recovery = time.monotonic()
                try:
                    ran = self.run_once()
                except Exception:  # noqa: BLE001 -- the worker outlives any one job
                    log.exception("The worker could not run a job; carrying on")
                    ran = None
                if ran is None:
                    self.stop.wait(self.poll_seconds)
        finally:
            db = self.session_factory()
            try:
                jobs.worker_stopped(db, self.id)
            finally:
                db.close()
            if self.background_reading and get_settings().archive_index_enabled:
                from app.services import ep_directory

                ep_directory.stop_refresh()
            log.info("Worker %s stopped", self.id)


def _unknown_kind(kind: str):
    def work(_session, _ctx):
        raise RuntimeError(f"The worker has no runner for {kind} jobs")
    return work


def _start_background_reading(worker: Worker) -> None:
    """What the API used to start on a thread of its own when it came up."""
    settings = get_settings()
    # Projects read under older rules read themselves again: a sync each, queued.
    db = worker.session_factory()
    try:
        from app.services import document_sync

        queued = document_sync.queue_projects_on_old_rules(db)
        if queued:
            log.info("Queued %d sync(s) to read projects again under %s", len(queued), document_sync.INDEX_VERSION)
    except Exception:  # noqa: BLE001
        log.warning("Could not queue the projects read under older rules", exc_info=True)
    finally:
        db.close()
    # The datasheet libraries' index, written to its cache file, which the API
    # loads on its first lookup instead of reading every datasheet itself.
    from app.services.datasheet_library import get_libraries

    for library in get_libraries().values():
        threading.Thread(target=library.warm, name=f"warm-{library.name}", daemon=True).start()
    # The archive index's periodic scan of the synced archive.
    if settings.archive_index_enabled:
        from app.services import ep_directory

        ep_directory.start_refresh_thread(scan_now=settings.archive_index_scan_on_start)


def set_below_normal_priority() -> bool:
    """Below normal Windows priority for this process; the processes it
    starts (file readers, Tesseract) inherit it."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        below_normal = 0x00004000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declared, not left to ctypes' int default: the process handle is
        # 64 bits, and passed as a C int it arrives truncated and the call
        # fails without a word.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal):
            return True
        log.warning("Could not lower the worker's priority (Windows error %s)", ctypes.get_last_error())
        return False
    except Exception:  # noqa: BLE001 -- a normal-priority worker still works
        log.warning("Could not lower the worker's priority", exc_info=True)
        return False


def wait_for_schema(stop: threading.Event) -> bool:
    """Wait until the database is at the latest migration: the API migrates
    it when it starts, and a worker started first must not claim jobs from
    a table that is missing its columns. False if stopped while waiting."""
    from app.database import engine
    from app.migrations import pending_migrations

    said = False
    while not stop.is_set():
        try:
            current, head = pending_migrations(engine)
        except Exception:  # noqa: BLE001 -- no database file yet
            current, head = None, "?"
        if current == head:
            return True
        if not said:
            log.info("Waiting for the API to bring the database up to date (%s -> %s)", current, head)
            said = True
        stop.wait(3)
    return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The schema check asks Alembic, which is chatty at INFO.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    stop = threading.Event()

    def ask_to_stop(signum, _frame):
        if stop.is_set():
            raise KeyboardInterrupt   # asked twice: stop now; the job is recovered later
        log.info("Stopping: the running job stops at its next file and is queued to carry on")
        stop.set()

    signal.signal(signal.SIGINT, ask_to_stop)
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), ask_to_stop)

    if get_settings().worker_below_normal_priority and set_below_normal_priority():
        log.info("Running below normal priority")
    if not wait_for_schema(stop):
        return
    Worker(stop=stop).run_forever()


if __name__ == "__main__":
    main()
