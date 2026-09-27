"""The document processing worker: reads the documents the index found, in
a process of its own.

    venv\\Scripts\\python -m app.workers.document_worker      (start.bat starts it)

The sync worker (app.workers.sync_worker) keeps the index: a stat per file,
seconds a project. This worker does the reading -- the PDFs, the approval
boxes, OCR, the model on a material submittal form -- as `process_documents`
jobs (app.services.document_processing), one project at a time across every
document worker (the "documents" lane, app.services.jobs.LANES). The two run
side by side: a folder is indexed while another project's documents are
being read, and the engineer works on either meanwhile.

  * One project's documents are processed at a time; within it,
    SYNC_FILE_WORKERS reader processes read files in parallel.
  * A stop (Ctrl+C, closing the window) is honoured between documents: what
    was read is kept, the rest stays pending, and the job goes back in the
    queue to carry on when the worker next starts. A worker killed outright
    stops saying it is alive and its job is put back by the next worker to
    look (app.services.jobs.recover_stale); the documents it had finished
    are not read again.
  * It runs below normal Windows priority, as the sync worker does.
"""

from __future__ import annotations

import logging
import signal
import threading

from app.core.config import get_settings
from app.services import jobs
from app.workers.sync_worker import Worker, set_below_normal_priority, wait_for_schema

log = logging.getLogger("app.workers.document_worker")


def _run_processing(session, job, ctx) -> dict:
    from app.services import document_processing

    return document_processing.run_job(session, job, ctx)


RUNNERS = {"process_documents": _run_processing}


class DocumentWorker(Worker):
    """The documents lane's worker: `process_documents` jobs, one at a time
    across every document worker."""

    def __init__(self, **kwargs):
        kwargs.setdefault("runners", dict(RUNNERS))
        kwargs.setdefault("lane", "documents")
        kwargs.setdefault("limit", jobs.lane_limit("documents"))
        kwargs.setdefault("background_reading", False)
        super().__init__(**kwargs)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    stop = threading.Event()

    def ask_to_stop(signum, _frame):
        if stop.is_set():
            raise KeyboardInterrupt
        log.info("Stopping: the running job stops at its next document and is queued to carry on")
        stop.set()

    signal.signal(signal.SIGINT, ask_to_stop)
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), ask_to_stop)

    from app.workers import runtime

    runtime.start("document-worker")   # the fingerprint, and the import check; exit 2 on a mismatch
    if get_settings().worker_below_normal_priority and set_below_normal_priority():
        log.info("Running below normal priority")
    if not wait_for_schema(stop):
        return
    DocumentWorker(stop=stop).run_forever()


if __name__ == "__main__":
    main()
