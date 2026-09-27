"""File Sync v2, stage A: files are written as their reading finishes, a
folder past the file limit fails clearly, a stalled reader does not hang the
sync, progress says which phase it is in, and a sync measures itself."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.routers.jobs as jobs_router
from app.core.config import get_settings
from app.models import BackgroundJob, Project, ProjectDocument
from app.services import document_sync, jobs

from .test_document_sync import _DRAWINGS, _pdf, _project, ai  # noqa: F401 -- fixture
from .test_sync_worker import file_db  # noqa: F401 -- fixture

settings = get_settings()


def _item(name: str, sha: str = "sha") -> tuple:
    return (Path("/p") / name, name, 1, 1.0, sha, None)


@pytest.fixture()
def thread_pool(monkeypatch):
    """Readers as threads, so a test can script how long each file takes
    without starting Python three times over."""
    monkeypatch.setattr(document_sync, "_pool_factory", ThreadPoolExecutor)


# --- completion order ---------------------------------------------------------------------


def test_fast_files_are_reported_without_waiting_for_a_slow_one(monkeypatch, thread_pool):
    """File A is slow, B and C are fast: B and C must come back, with their
    plan items, before A has finished -- the head of the line no longer
    blocks the files behind it."""
    release = threading.Event()

    def extract(path, relative, sha, ocr):
        if relative == "a.pdf":
            release.wait(10)
        return "document", (), (), 0.01

    monkeypatch.setattr(document_sync, "extract", extract)
    plan = [_item("a.pdf"), _item("b.pdf"), _item("c.pdf")]
    order = []
    for item, reading in document_sync.read_in_completion_order(plan, False, 2, in_flight=3):
        order.append(item[1])
        assert reading()[0] == "document"
        if len(order) == 2:
            assert set(order) == {"b.pdf", "c.pdf"}, "the fast files came back while the slow one was still reading"
            release.set()
    assert order[-1] == "a.pdf" and len(order) == 3


def test_a_files_failure_is_raised_with_its_own_item(monkeypatch, thread_pool):
    def extract(path, relative, sha, ocr):
        if relative == "bad.pdf":
            raise RuntimeError("cannot read this one")
        return "document", (), (), 0.0

    monkeypatch.setattr(document_sync, "extract", extract)
    plan = [_item("good.pdf"), _item("bad.pdf"), _item("also.pdf")]
    outcomes = {}
    for item, reading in document_sync.read_in_completion_order(plan, False, 2):
        try:
            outcomes[item[1]] = reading()[0]
        except RuntimeError as exc:
            outcomes[item[1]] = str(exc)
    assert outcomes == {"good.pdf": "document", "bad.pdf": "cannot read this one", "also.pdf": "document"}


def test_the_pool_is_kept_fed_but_never_given_the_whole_plan(monkeypatch):
    """About twice the readers in flight, not every file at once: a stop
    then has little to cancel and memory stays bounded."""
    state = {"outstanding": 0, "peak": 0}

    class RecordingPool(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            state["outstanding"] += 1
            state["peak"] = max(state["peak"], state["outstanding"])
            future = super().submit(fn, *args, **kwargs)
            future.add_done_callback(lambda _f: state.__setitem__("outstanding", state["outstanding"] - 1))
            return future

    monkeypatch.setattr(document_sync, "_pool_factory", RecordingPool)
    monkeypatch.setattr(document_sync, "extract", lambda *a: (time.sleep(0.01), ("document", (), (), 0.0))[1])
    plan = [_item(f"{n}.pdf") for n in range(12)]
    seen = [item[1] for item, reading in document_sync.read_in_completion_order(plan, False, 2, in_flight=4)]
    assert sorted(seen) == sorted(item[1] for item in plan)
    assert state["peak"] <= 4, state


def test_a_stop_is_checked_before_each_file_is_submitted(monkeypatch, thread_pool):
    monkeypatch.setattr(document_sync, "extract", lambda *a: ("document", (), (), 0.0))
    checks = []

    def check():
        checks.append(1)
        if len(checks) == 3:
            raise jobs.Cancelled()

    plan = [_item(f"{n}.pdf") for n in range(10)]
    got = []
    with pytest.raises(jobs.Cancelled):
        for item, _reading in document_sync.read_in_completion_order(plan, False, 2, in_flight=2, check=check):
            got.append(item[1])
    assert len(got) < 10, "the files not yet started were not read"


def test_readers_are_recycled_between_files_with_nothing_in_flight(monkeypatch):
    pools = []

    class CountingPool(ThreadPoolExecutor):
        def __init__(self, max_workers=None, **kwargs):
            super().__init__(max_workers=max_workers, **kwargs)
            pools.append(self)

    monkeypatch.setattr(document_sync, "_pool_factory", CountingPool)
    monkeypatch.setattr(document_sync, "extract", lambda *a: ("document", (), (), 0.0))
    plan = [_item(f"{n}.pdf") for n in range(6)]
    seen = [item[1] for item, reading in document_sync.read_in_completion_order(plan, False, 2, recycle_after=1)]
    assert sorted(seen) == sorted(item[1] for item in plan)
    assert len(pools) == 3, "two readers, one file each, then a fresh pool"


def test_a_reader_that_stops_answering_fails_its_file_and_the_rest_are_read(monkeypatch, thread_pool):
    """Nothing finishes for `stall_seconds` while a file is in flight: that
    file comes back failed with the reason, and the others are read by a
    fresh pool. The sync never sits for hours on one reading."""
    def extract(path, relative, sha, ocr):
        if relative == "hang.pdf":
            time.sleep(2.5)
        return "document", (), (), 0.0

    monkeypatch.setattr(document_sync, "extract", extract)
    plan = [_item("hang.pdf"), _item("b.pdf"), _item("c.pdf"), _item("d.pdf")]
    outcomes = {}
    started = time.monotonic()
    for item, reading in document_sync.read_in_completion_order(plan, False, 2, in_flight=2, stall_seconds=0.5):
        try:
            outcomes[item[1]] = reading()[0]
        except document_sync.SyncError as exc:
            outcomes[item[1]] = str(exc)
    assert time.monotonic() - started < 2.4, "the stall was not waited out"
    assert outcomes["b.pdf"] == outcomes["c.pdf"] == outcomes["d.pdf"] == "document"
    assert "did not finish within 0.5 seconds" in outcomes["hang.pdf"]


# --- the file limit -------------------------------------------------------------------------


def test_a_folder_past_the_file_limit_fails_the_sync_clearly(client, db_session, tmp_path, monkeypatch):
    """Never a shortened list: the sync fails, says so, and the project is
    not recorded as synced."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    monkeypatch.setattr(document_sync, "MAX_FILES", 3)
    folder = tmp_path / "EP-30841"
    for n in range(4):
        _pdf(folder / "05- Drawings" / f"L0{n}.pdf", f"Drawing {n}")
    with pytest.raises(document_sync.TooManyFilesError, match="more than 3 supported files; sync was not completed"):
        document_sync.listing(folder)
    project_id = _project(client, folder, "30841")
    job = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert job["status"] == "failed", job
    assert "Project contains more than 3 supported files; sync was not completed." in job["error"]
    project = db_session.get(Project, project_id)
    assert project.documents_synced_at is None and project.documents_listing_sha256 is None
    assert db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count() == 0
    assert client.get(f"/projects/{project_id}/documents/status").json()["synced_at"] is None
    assert document_sync.changes_since(db_session, project) is True


def test_listing_reports_progress_and_keeps_its_order(tmp_path):
    folder = tmp_path / "EP-30842"
    for name in ("b.pdf", "a.pdf", "sub/c.pdf", "note.txt"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 stub")
    counts = []
    files = document_sync.listing(folder, progress=counts.append)
    assert [relative for _p, relative, _s, _m in files] == ["a.pdf", "b.pdf", "sub/c.pdf"]


# --- progress and telemetry -----------------------------------------------------------------


def test_a_skipped_progress_step_is_written_at_the_next_check(file_db):  # noqa: F811
    """The spacing drops quick steps; the last one dropped must not stay
    unwritten through the next slow file."""
    db = file_db()
    queued, _ = jobs.enqueue(db, kind="sync_documents", project_id=41, user_id=None)
    ctx = jobs.JobContext(queued.id, min_interval=60, session_factory=file_db)
    ctx.progress(1, 349, "file 1")
    ctx.progress(147, 349, "file 147")          # within the interval: not written yet
    db.expire_all()
    assert db.get(BackgroundJob, queued.id).progress["done"] == 1
    ctx.check()
    db.expire_all()
    assert db.get(BackgroundJob, queued.id).progress == {"done": 147, "total": 349, "message": "file 147"}
    db.close()


class _Recorder:
    def __init__(self):
        self.steps = []

    def progress(self, done, total, message, **extra):
        self.steps.append((done, total, message, extra))

    def check(self):
        pass


TELEMETRY_KEYS = ("discovery_seconds", "hashing_seconds", "document_processing_seconds", "ai_seconds", "total_seconds",
                  "processed_count", "failed_count", "unavailable_count", "unchanged_after_hash_count", "slowest_files")


def test_the_sync_and_the_processing_name_their_phases_and_measure_themselves(client, db_session, tmp_path):
    """Progress says what is happening -- discovering, checking, processing
    which file, updating which records -- never just "Syncing"; and each
    job's result carries where its time went and its slowest files."""
    from app.services import document_processing

    folder = tmp_path / "EP-30843"
    for relative, text in _DRAWINGS.items():
        _pdf(folder / relative, text)
    project_id = _project(client, folder, "30843")
    project = db_session.get(Project, project_id)

    recorder = _Recorder()
    result = document_sync.sync(db_session, project, ctx=recorder)
    messages = [step[2] for step in recorder.steps]
    phases = [step[3].get("phase") for step in recorder.steps]
    assert messages[0] == "Discovering files" and phases[0] == "discovery"
    assert any(m.startswith("Checking changes — 1 of 4 files") for m in messages)
    assert messages[-1] == "File discovery complete — 4 files, 4 to process" and phases[-1] == "done"
    assert not any(m == "Syncing" for m in messages)
    assert result["pending"] == 4 and result["processing_job_id"]
    for key in ("discovery_seconds", "hashing_seconds", "total_seconds"):
        assert key in result["telemetry"], key

    recorder = _Recorder()
    processed = document_processing.run(db_session, project, ctx=recorder)
    messages = [step[2] for step in recorder.steps]
    assert messages[0] == "Checking changes — 4 files"
    assert any("Processing documents — 0 of 4 · reading" in m for m in messages)
    assert "Processing documents — 4 of 4" in messages
    assert "Updating drawing records" in messages
    assert messages[-1].startswith("Processing complete — 4 processed, 0 failed")
    current = [step[3].get("current") for step in recorder.steps if step[3].get("current")]
    assert "L01.pdf" in current

    telemetry = processed["telemetry"]
    for key in TELEMETRY_KEYS:
        assert key in telemetry, key
    assert telemetry["processed_count"] == 4 and telemetry["failed_count"] == 0
    assert telemetry["total_seconds"] >= telemetry["document_processing_seconds"]
    slowest = telemetry["slowest_files"]
    assert 1 <= len(slowest) <= document_sync.SLOWEST_KEPT
    assert set(slowest[0]) == {"path", "seconds", "role", "result"} and slowest[0]["result"] == "processed"
    assert slowest == sorted(slowest, key=lambda f: f["seconds"], reverse=True)

    # The same folder synced again with one file touched: the sync sees one
    # candidate; processing hashes it, finds the same content, reads nothing.
    (folder / "05- Drawings" / "L01.pdf").touch()
    again = document_sync.sync(db_session, project, ctx=_Recorder())
    assert (again["changed"], again["unchanged"], again["pending"]) == (1, 3, 1)
    processed = document_processing.run(db_session, project, ctx=_Recorder())
    assert processed["unchanged_after_hash"] == 1 and processed["processed"] == 0
    assert processed["telemetry"]["unchanged_after_hash_count"] == 1
