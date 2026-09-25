"""The IFC worker: reads the uploaded IFC drawings, in a process of its own.

    venv\\Scripts\\python -m app.workers.ifc_worker      (start.bat starts it)

Converting a DWG, walking half a million CAD entities, outlining an
architect's Revit model, a zip of thirty floors: minutes of CPU and up to
a GB of memory a drawing. On a thread of the API that held the API's one
Python interpreter while every page waited, and an API restart lost the
read. Here the API only streams the upload to the staging folder and
queues the job (HTTP 202); this process claims it, reads it with database
sessions of its own, and writes its progress where the tab reads it.

  * At most IFC_WORKER_CONCURRENCY reads run at once, across every IFC
    worker on the database (app.services.jobs.claim): ten uploads are two
    reading and eight queued, not ten drawings in memory together. This
    process runs that many slots; start a second process on another PC
    for CPU in parallel -- the limit still holds across both.
  * A stop (Ctrl+C, closing the window) asks each read to stop at its next
    stage; it goes back in the queue, with its upload kept, and is read
    from the start when the worker next starts. A worker killed outright
    stops saying it is alive, and its read is put back by the next worker
    to look (app.services.jobs.recover_stale).
  * Staged uploads no job will read -- a job cancelled while it waited, a
    read failed by recovery -- are removed after a grace period.
  * It runs below normal Windows priority, as the sync worker does.
"""
from __future__ import annotations

import logging
import signal
import threading

from app.core.config import get_settings
from app.services import jobs
from app.workers.sync_worker import Worker, set_below_normal_priority, wait_for_schema

log = logging.getLogger("app.workers.ifc_worker")


def runners() -> dict:
    from app.ifc.services.runners import RUNNERS

    return dict(RUNNERS)


class IfcWorker(Worker):
    """A slot of the IFC worker: the IFC lane's jobs, one at a time."""

    def __init__(self, *, slot: int = 0, **kwargs):
        kwargs.setdefault("runners", runners())
        kwargs.setdefault("lane", "ifc")
        kwargs.setdefault("limit", jobs.lane_limit("ifc"))
        kwargs.setdefault("background_reading", False)
        super().__init__(**kwargs)
        self.slot = slot
        if "worker_id" not in kwargs:
            self.id = f"{self.id}:{slot}"[:64]

    def housekeeping(self) -> None:
        if self.slot != 0:
            return
        from app.ifc.services import upload

        db = self.session_factory()
        try:
            upload.sweep(db)
        except Exception:  # noqa: BLE001 -- tidying up never stops the worker
            log.warning("Could not sweep the staged IFC uploads", exc_info=True)
        finally:
            db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    stop = threading.Event()

    def ask_to_stop(signum, _frame):
        if stop.is_set():
            raise KeyboardInterrupt
        log.info("Stopping: each read stops at its next stage and is queued to carry on")
        stop.set()

    signal.signal(signal.SIGINT, ask_to_stop)
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), ask_to_stop)

    settings = get_settings()
    if settings.worker_below_normal_priority and set_below_normal_priority():
        log.info("Running below normal priority")
    if not wait_for_schema(stop):
        return
    slots = max(1, settings.ifc_worker_concurrency)
    log.info("IFC worker: %d slot(s)", slots)
    threads = [threading.Thread(target=IfcWorker(stop=stop, slot=i).run_forever, name=f"ifc-slot-{i}")
               for i in range(slots)]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
