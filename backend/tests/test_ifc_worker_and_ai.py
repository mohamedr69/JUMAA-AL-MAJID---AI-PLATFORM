"""BOQ as per IFC drawings, production side: uploads streamed and staged,
reads run by the IFC worker, jobs deduplicated, the revision chain kept
whole by the database, the symbol library's authority, and the AI symbol
review -- which may only ever cost less as the library grows.

AI tests use a fake provider that answers every symbol it is sent and
counts what it was asked: the point is what reaches the model, and what
the backend does with the answer."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import ezdxf
import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.ai import provider as ai_provider
from app.ai.provider import AiResponse, ImagePart, TextPart, Usage
from app.core.config import get_settings
from app.database import Base
from app.ifc import storage
from app.ifc.services import ai_symbol_review, library, revisions, upload, zip_import
from app.models import (BackgroundJob, IfcBlockAlias, IfcDeviceType, IfcSymbol, IfcSymbolReview, Project,
                        ProjectIfcDrawing, User)
from app.services import jobs

from .test_ifc_platform import _admin, _dxf, _project

settings = get_settings()


# --- helpers ------------------------------------------------------------------------------------


def _symbols_dxf(path: Path, symbols: list[tuple[str, str, int]], layer: str = "E-FIRE", shape: str = "circle") -> Path:
    """(block name, letters inside, extra overlapping lines, count): the extra
    lines change the symbol's signature without changing its picture."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for row, (name, letters, extra, count) in enumerate(symbols):
        block = doc.blocks.new(name)
        if shape == "circle":
            block.add_circle((0, 0), 200)
        else:
            block.add_lwpolyline([(-200, -200), (200, -200), (0, 200)], close=True)
        for _ in range(extra):
            block.add_line((-150, 0), (150, 0))
        block.add_text(letters, height=120).set_placement((-80, -60))
        for k in range(count):
            msp.add_blockref(name, (k * 1000, row * 3000), dxfattribs={"layer": layer})
    doc.saveas(path)
    return path


def _type(db, code: str) -> IfcDeviceType:
    return db.query(IfcDeviceType).filter(IfcDeviceType.code == code).one()


def _inline(monkeypatch):
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)


def _read(client, project_id: int, path: Path, **data):
    return client.post(f"/projects/{project_id}/ifc-drawings/jobs", data={k: str(v) for k, v in data.items()},
                       files={"file": (path.name, path.read_bytes(), "application/dxf")})


def _job(client, response) -> dict:
    assert response.status_code == 202, response.text
    return client.get(f"/jobs/{response.json()['id']}").json()


def _staged() -> list[Path]:
    folder = upload.staging_dir()
    return sorted(folder.iterdir()) if folder.is_dir() else []


class FakeAi:
    """Answers every symbol of a request; records what it was asked."""

    name = "fake"
    ready = True
    status = "a fake provider (tests)"

    def __init__(self, decide=None, fail: int = 0):
        self.decide = decide or (lambda item, request: _device(item["candidate_types"][0]["id"], 0.99))
        self.fail = fail
        self.requests = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def items(self, n: int) -> list[dict]:
        return json.loads(next(p for p in self.requests[n].parts if isinstance(p, TextPart)).text)

    def complete(self, request):
        self.requests.append(request)
        if self.fail:
            self.fail -= 1
            return AiResponse(data=None, error="transport", error_detail="the network went away", model="fake")
        items = json.loads(next(p for p in request.parts if isinstance(p, TextPart)).text)
        results = [{"key": item["key"], **self.decide(item, request)} for item in items]
        return AiResponse(data={"results": results}, usage=Usage(input_tokens=90 * len(items), output_tokens=25 * len(items)),
                          model="fake-model", latency_ms=5)


def _device(type_id, confidence=0.99, code="LABEL_MATCH"):
    return {"decision": "device", "device_type_id": type_id, "confidence": confidence, "reason_code": code,
            "requires_engineer": False}


UNCERTAIN = {"decision": "uncertain", "device_type_id": None, "confidence": 0.3, "reason_code": "INSUFFICIENT_EVIDENCE",
             "requires_engineer": True}


@pytest.fixture(autouse=True)
def _own_library_and_staging(tmp_path, monkeypatch):
    """Each test its own symbol library file (the platform loads it at every
    start, so one test's symbols would be known in the next) and an empty
    staging folder."""
    import shutil

    monkeypatch.setenv("BOQ_LIBRARY_PATH", str(tmp_path / "symbol_library.json"))
    shutil.rmtree(upload.staging_dir(), ignore_errors=True)
    yield
    shutil.rmtree(upload.staging_dir(), ignore_errors=True)


@pytest.fixture()
def ai(monkeypatch):
    """The AI switched on, with a fake provider the test can script."""
    monkeypatch.setattr(settings, "ai_enabled", True)
    fake = FakeAi()
    ai_provider.set_provider(fake)
    yield fake
    ai_provider.set_provider(None)


@pytest.fixture()
def file_db(tmp_path):
    """A real SQLite file in WAL mode, every session its own connection, as
    the running platform has it -- for races between threads."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()


def _upload_file(data: bytes, name: str, chunk_reads: list | None = None) -> UploadFile:
    stream = io.BytesIO(data)
    if chunk_reads is not None:
        real = stream.read

        def read(n=-1):
            chunk_reads.append(n)
            return real(n)

        stream.read = read
    return UploadFile(file=stream, filename=name)


# --- uploads: streamed, hashed on the way in, one limit ---------------------------------------------


def test_an_upload_is_streamed_to_staging_and_hashed_as_it_arrives(client, tmp_path):
    data = _dxf(tmp_path / "FA-201.dxf").read_bytes()
    reads: list = []
    staged = asyncio.run(upload.stream_to_staging(_upload_file(data, "FA-201.dxf", reads), allowed=("dxf", "dwg"),
                                                  default_name="x.dxf"))
    try:
        assert staged.sha256 == hashlib.sha256(data).hexdigest() and staged.size == len(data)
        assert staged.path.read_bytes() == data and staged.path.parent == upload.staging_dir()
        assert reads and all(n == upload.CHUNK for n in reads)      # read in chunks, never whole
    finally:
        upload.discard(staged.path)


def test_the_limit_is_one_number_and_the_message_follows_it(client, monkeypatch):
    monkeypatch.setattr(upload, "MAX_BYTES", 2 * upload.MIB)
    at_limit = b"0\nSECTION\n" + b" " * (2 * upload.MIB - 10)
    assert len(at_limit) == upload.MAX_BYTES
    staged = asyncio.run(upload.stream_to_staging(_upload_file(at_limit, "edge.dxf"), allowed=("dxf",), default_name="x"))
    upload.discard(staged.path)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(upload.stream_to_staging(_upload_file(at_limit + b" ", "big.dxf"), allowed=("dxf",), default_name="x"))
    assert refused.value.status_code == 413 and refused.value.detail == "big.dxf is larger than 2 MB"
    assert _staged() == []                  # what was written of it is gone


def test_what_is_not_a_drawing_is_refused_at_the_door_and_leaves_nothing(client, monkeypatch, tmp_path):
    _admin(client)
    project_id = _project(client, "92001")
    for name, data, code in [("plan.pdf", b"%PDF-1.7", 415), ("fake.dwg", b"not a dwg at all", 422),
                             ("empty.dxf", b"", 422)]:
        response = client.post(f"/projects/{project_id}/ifc-drawings/jobs", files={"file": (name, data, "application/x")})
        assert response.status_code == code, (name, response.text)
    zip_refused = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                              files={"file": ("floors.zip", b"not a zip", "application/zip")})
    assert zip_refused.status_code == 422
    assert _staged() == [] and client.get(f"/projects/{project_id}/jobs").json() == []


def test_capabilities_give_the_page_the_same_limit(client):
    _admin(client)
    caps = client.get("/ifc/capabilities").json()
    assert caps["max_upload_mb"] == upload.MAX_BYTES // upload.MIB and caps["ai_symbol_review"] is False


# --- the worker: the API queues, the worker reads -------------------------------------------------------


def test_the_api_queues_and_answers_at_once_and_the_worker_reads(client, db_session, tmp_path):
    from app.workers.ifc_worker import IfcWorker

    _admin(client)
    project_id = _project(client, "92010")
    started = time.monotonic()
    response = _read(client, project_id, _dxf(tmp_path / "FA-210.dxf"))
    assert response.status_code == 202 and time.monotonic() - started < 5
    job = response.json()
    assert job["status"] == "queued" and job["kind"] == "ifc_read" and not job["already_active"]
    assert job["progress"]["stage"] == "waiting_for_worker"
    assert db_session.query(ProjectIfcDrawing).count() == 0          # nothing read in the request
    assert len(_staged()) == 1

    sessions = []

    def factory():
        from app.database import SessionLocal

        s = SessionLocal()
        sessions.append(s)
        return s

    worker = IfcWorker(session_factory=factory, worker_id="ifc-test", poll_seconds=0.05, progress_interval=0)
    assert worker.run_once() == job["id"]
    done = client.get(f"/jobs/{job['id']}").json()
    assert done["status"] == "succeeded" and done["progress"]["stage"] == "done"
    drawing = client.get(f"/projects/{project_id}/ifc-drawings/{done['result']['drawing_id']}").json()
    assert drawing["reference"] == "FA-210" and drawing["analysis"]["total_occurrences"] == 3
    assert sessions, "the worker read with sessions of its own"
    assert _staged() == []                                            # the upload became the working copy


def test_repeated_clicks_are_one_job(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "92011")
    path = _dxf(tmp_path / "FA-211.dxf")
    first, second = _read(client, project_id, path).json(), _read(client, project_id, path).json()
    assert second["id"] == first["id"] and second["already_active"] and not first["already_active"]
    assert db_session.query(BackgroundJob).filter(BackgroundJob.kind == "ifc_read").count() == 1
    assert len(_staged()) == 1                    # the second upload's copy was dropped


def test_the_database_refuses_a_second_active_job_with_the_same_key(client, db_session):
    for status in ("queued", "running"):
        db_session.add(BackgroundJob(kind="ifc_read", status="queued", progress={}, dedup_key=f"k-{status}"))
    db_session.commit()
    db_session.add(BackgroundJob(kind="ifc_read", status="queued", progress={}, dedup_key="k-queued"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_finished_read_does_not_block_a_later_legitimate_one(client, db_session, monkeypatch, tmp_path):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92012")
    path = _dxf(tmp_path / "FA-212.dxf")
    done = _job(client, _read(client, project_id, path))
    assert done["status"] == "succeeded"
    again = _read(client, project_id, path)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "duplicate_drawing"
    # Removed as a mistaken import, the same file may be read again.
    assert client.delete(f"/projects/{project_id}/ifc-drawings/{done['result']['drawing_id']}").status_code == 204
    third = _job(client, _read(client, project_id, path))
    assert third["status"] == "succeeded" and third["id"] != done["id"]


def test_a_read_cancelled_while_it_waits_removes_its_upload(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "92013")
    job = _read(client, project_id, _dxf(tmp_path / "FA-213.dxf")).json()
    assert len(_staged()) == 1
    cancelled = client.post(f"/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and _staged() == []


def test_a_broken_drawing_fails_the_job_plainly_and_leaves_nothing(client, db_session, monkeypatch, tmp_path):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92014")
    broken = tmp_path / "FA-214.dxf"
    broken.write_text("0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n", encoding="ascii")
    job = _job(client, _read(client, project_id, broken))
    assert job["status"] == "failed" and "Could not read this drawing" in job["error"]
    assert str(tmp_path) not in job["error"] and "Traceback" not in job["error"]
    project = db_session.get(Project, project_id)
    assert db_session.query(ProjectIfcDrawing).count() == 0 and _staged() == []
    assert not storage.project_folder(project).exists() or not any(storage.project_folder(project).iterdir())


def test_the_api_stays_responsive_while_the_worker_reads(client, monkeypatch, tmp_path):
    """A slow read in the worker, and the API answering pages meanwhile."""
    from app.ifc.services import processing
    from app.workers.ifc_worker import IfcWorker

    _admin(client)
    project_id = _project(client, "92015")
    job = _read(client, project_id, _dxf(tmp_path / "FA-215.dxf")).json()
    real = processing.extract
    reading = threading.Event()

    def slow(*args, **kwargs):
        reading.set()
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(processing, "extract", slow)
    worker = IfcWorker(worker_id="ifc-slow", poll_seconds=0.05, progress_interval=0)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert reading.wait(10)
    started = time.monotonic()
    assert client.get("/ifc/capabilities").status_code == 200
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "running"
    assert time.monotonic() - started < 1.0
    thread.join(20)
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "succeeded"


def test_the_ifc_lane_runs_its_limit_and_no_more(file_db, monkeypatch):
    monkeypatch.setattr(settings, "ifc_worker_concurrency", 2)
    db = file_db()
    for n in range(4):
        db.add(BackgroundJob(kind="ifc_read", project_id=n + 1, status="queued", progress={}))
    db.add(BackgroundJob(kind="sync_documents", project_id=9, status="queued", progress={}))
    db.commit()
    kinds, limit = jobs.LANES["ifc"], jobs.lane_limit("ifc")
    claimed = [jobs.claim_next(db, f"w{n}", kinds, limit) for n in range(4)]
    assert [j is not None for j in claimed] == [True, True, False, False]
    # A sync is its own lane: IFC reads running do not hold it up.
    assert jobs.claim_next(db, "sync", jobs.LANES["sync"], 1) is not None
    db.close()


def test_an_ifc_read_left_by_a_dead_worker_is_queued_again_with_its_upload(file_db, tmp_path):
    from datetime import timedelta

    from app.core.timeutils import utc_now

    staged = tmp_path / "staged.dxf"
    staged.write_bytes(b"x")
    db = file_db()
    old = utc_now() - timedelta(minutes=5)
    job = BackgroundJob(kind="ifc_read", project_id=1, status="running", progress={}, started_at=old, heartbeat_at=old,
                        worker_id="dead", params={"staged_path": str(staged)})
    db.add(job)
    db.commit()
    assert jobs.recover_stale(db) == [(job.id, "requeued")]
    assert staged.exists()                                   # read again when a worker takes it
    db.refresh(job)
    job.status, job.heartbeat_at, job.started_at, job.attempts = "running", old, old, jobs.MAX_ATTEMPTS
    db.commit()
    import app.ifc.services.runners  # noqa: F401 -- registers the tidy-up of staged uploads

    assert jobs.recover_stale(db) == [(job.id, "failed")]
    assert not staged.exists()                               # given up: its upload goes too
    db.close()


def test_a_read_run_again_after_its_worker_died_keeps_the_drawing_it_made(client, db_session, tmp_path):
    """The worker saved the drawing and died before marking the job done:
    run again, the job finds its own drawing instead of failing on it."""
    from app.workers.ifc_worker import IfcWorker

    _admin(client)
    project_id = _project(client, "92016")
    job = _read(client, project_id, _dxf(tmp_path / "FA-216.dxf")).json()
    staged = Path(db_session.get(BackgroundJob, job["id"]).params["staged_path"])
    kept = staged.read_bytes()
    assert IfcWorker(worker_id="first").run_once() == job["id"]
    drawing_id = client.get(f"/jobs/{job['id']}").json()["result"]["drawing_id"]
    # As if the worker had died just before recording success: queued again, upload still there.
    row = db_session.get(BackgroundJob, job["id"])
    db_session.refresh(row)
    row.status, row.result, row.finished_at = "queued", None, None
    db_session.commit()
    staged.write_bytes(kept)
    assert IfcWorker(worker_id="second").run_once() == job["id"]
    again = client.get(f"/jobs/{job['id']}").json()
    assert again["status"] == "succeeded" and again["result"]["drawing_id"] == drawing_id
    assert db_session.query(ProjectIfcDrawing).count() == 1


def test_an_api_restart_leaves_ifc_reads_to_the_worker(client, db_session):
    job = BackgroundJob(kind="ifc_read", project_id=None, status="running", progress={})
    db_session.add(job)
    db_session.commit()
    assert jobs.fail_interrupted(db_session) == 0
    db_session.refresh(job)
    assert job.status == "running"


# --- zip: sequential, and one bad floor loses nothing ---------------------------------------------


def _zip(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_broken_floor_is_reported_and_the_others_are_read(client, monkeypatch, tmp_path):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92020")
    archive = _zip([("Level 01.dxf", _dxf(tmp_path / "a.dxf").read_bytes()),
                    ("Level 02.dxf", b"0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n"),
                    ("Level 03.dxf", _dxf(tmp_path / "c.dxf", count=4).read_bytes())])
    response = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                           files={"file": ("floors.zip", archive, "application/zip")})
    job = _job(client, response)
    assert job["status"] == "succeeded"
    assert [r["filename"] for r in job["result"]["read"]] == ["Level 01.dxf", "Level 03.dxf"]
    assert [f["filename"] for f in job["result"]["failed"]] == ["Level 02.dxf"]
    assert _staged() == []                   # the archive and every unpacked floor are gone


def test_a_member_that_unpacks_past_the_limit_is_stopped(client, monkeypatch):
    data = _zip([("Level 01.dxf", b"0" * 5000)])
    monkeypatch.setattr(upload, "MAX_BYTES", 1000)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(zip_import.MemberTooLarge):
            zip_import.unpack(archive, archive.infolist()[0])
    assert _staged() == []


# --- revisions: one chain, whatever happens at once ---------------------------------------------------


def test_two_workers_revising_r0_at_once_make_one_r1(file_db):
    """The database decides, not a look before the insert: both threads
    see R0 unrevised, both insert, one wins."""
    db = file_db()
    r0 = ProjectIfcDrawing(project_id=1, filename="FA-101.dxf", stored_path="x", revision="R0", drawing_reference="FA-101")
    db.add(r0)
    db.commit()
    r0_id = r0.id
    db.close()
    barrier = threading.Barrier(2)
    outcomes: list = []

    def worker(tag: str):
        session = file_db()
        try:
            assert r0_id not in revisions.superseded(session, 1)        # both see R0 in force
            barrier.wait()
            d = ProjectIfcDrawing(project_id=1, filename=f"FA-101-R1-{tag}.dxf", stored_path=tag, revision="R1",
                                  supersedes_id=r0_id, drawing_reference="FA-101")
            revisions.insert(session, d)
            session.commit()
            outcomes.append("saved")
        except revisions.RevisionConflict as exc:
            outcomes.append(str(exc))
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(t,)) for t in "AB"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("saved") == 1
    lost = next(o for o in outcomes if o != "saved")
    assert "revised by another process" in lost and "Current latest revision: R1" in lost
    db = file_db()
    assert db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.supersedes_id == r0_id).count() == 1
    db.close()


def test_the_revision_that_loses_leaves_no_row_and_no_file(client, db_session, tmp_path):
    """Both reads planned R1 of the same R0 before either saved: the second
    is refused at the database, and what it made is removed."""
    from app.ifc.services import processing

    _admin(client)
    folder = tmp_path / "EP-92030"
    project_id = _project(client, "92030", folder)
    r0 = client.post(f"/projects/{project_id}/ifc-drawings",
                     files={"file": ("FA-130.dxf", _dxf(tmp_path / "FA-130.dxf").read_bytes(), "application/dxf")}).json()
    project, user = db_session.get(Project, project_id), db_session.query(User).first()
    previous = db_session.get(ProjectIfcDrawing, r0["id"])
    plans = [revisions.plan(db_session, project_id, filename=f"FA-130-R1{t}.dxf", sha256=None, revision="R1",
                            supersedes_id=previous.id) for t in "ab"]
    files_before = set(storage.project_folder(project).iterdir())
    for tag, plan in zip("ab", plans):
        source = tmp_path / f"FA-130-R1{tag}.dxf"
        _dxf(source, count=4 if tag == "a" else 5)
        try:
            processing.read_drawing(db_session, project, user, source=source, name=source.name, ext="dxf",
                                    sha256=tag * 64, plan=plan, use_ai=False)
        except revisions.RevisionConflict as exc:
            assert tag == "b" and "Current latest revision: R1" in str(exc)
    live = revisions.live(db_session, project_id).all()
    assert sorted(d.revision for d in live) == ["R0", "R1"]
    made = set(storage.project_folder(project).iterdir()) - files_before
    assert len(made) == 1                                      # the winner's working copy only
    filed = sorted(p.name for p in (folder / "03- Drawings/IFC/Electrical/FA").iterdir())
    assert filed == ["FA-130-R1a.dxf", "FA-130.dxf"]            # the loser filed nothing it kept


def test_a_file_that_may_revise_a_drawing_asks_first(client, monkeypatch, tmp_path):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92031")
    r0 = _job(client, _read(client, project_id, _dxf(tmp_path / "FA-131-R00.dxf")))
    assert client.get(f"/projects/{project_id}/ifc-drawings/{r0['result']['drawing_id']}").json()["revision"] == "R0"
    asked = _read(client, project_id, _dxf(tmp_path / "FA-131-R01.dxf", count=4))
    assert asked.status_code == 409
    detail = asked.json()["detail"]
    assert detail["code"] == "revision_confirmation_required" and detail["suggested_revision"] == "R1"
    assert detail["candidates"][0]["reference"] == "FA-131"
    # The engineer says it is the revision: R1, read from the name, and the chain holds.
    r1 = _job(client, _read(client, project_id, tmp_path / "FA-131-R01.dxf",
                            supersedes_id=detail["candidates"][0]["id"]))
    assert r1["status"] == "succeeded" and r1["result"]["revision"] == "R1"


def test_history_is_archived_not_rewritten(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "92032")

    def issue(name, supersedes=None, count=3):
        data = {"supersedes_id": str(supersedes)} if supersedes else {}
        return client.post(f"/projects/{project_id}/ifc-drawings", data=data,
                           files={"file": (name, _dxf(tmp_path / name, count=count).read_bytes(), "application/dxf")}).json()

    r0 = issue("FA-132.dxf")
    r1 = issue("FA-132.dxf", r0["id"], 4)
    r2 = issue("FA-132.dxf", r1["id"], 5)
    # R1 has R2 after it: kept.
    refused = client.delete(f"/projects/{project_id}/ifc-drawings/{r1['id']}")
    assert refused.status_code == 409 and "history" in refused.json()["detail"]
    # R2, the revision in force, is archived: R1 is in force again, and R0 -> R1 -> R2 is still in the database.
    assert client.delete(f"/projects/{project_id}/ifc-drawings/{r2['id']}").status_code == 204
    archived = db_session.get(ProjectIfcDrawing, r2["id"])
    db_session.refresh(archived)
    assert archived.deleted_at is not None and archived.supersedes_id == r1["id"]
    listed = {d["id"]: d for d in client.get(f"/projects/{project_id}/ifc-drawings").json()}
    assert set(listed) == {r0["id"], r1["id"]} and listed[r1["id"]]["current"]
    assert client.get(f"/projects/{project_id}/ifc-drawings/{r2['id']}").status_code == 404
    # A new R2 may now revise R1: the archived one does not hold its place.
    again = issue("FA-132.dxf", r1["id"], 6)
    assert again["revision"] == "R2" and again["supersedes_id"] == r1["id"]


def test_a_drawing_is_known_by_its_reference_not_its_file_name():
    assert revisions.reference("FA-101-R00.dwg") == revisions.reference("FA-101 Rev 1.dwg") == "FA-101"
    assert revisions.reference("EP-30880 FA-105 (uploaded 2026-09-19 1405).dxf") == "FA-105"
    assert revisions.revision_from_name("FA-101-R01.dwg") == "R1" and revisions.revision_from_name("FA-101.dwg") is None
    assert revisions.reference("Level 2.dxf") == "LEVEL 2"


# --- device types and answers: validated ---------------------------------------------------------------


def test_blank_codes_and_names_are_refused(client):
    _admin(client)
    for body in ({"code": "", "name": "X", "category": "fire_alarm"}, {"code": "   ", "name": "X", "category": "fire_alarm"},
                 {"code": "QQ", "name": "", "category": "fire_alarm"}, {"code": "QQ", "name": "    ", "category": "fire_alarm"}):
        assert client.post("/ifc/device-types", json=body).status_code == 422, body
    made = client.post("/ifc/device-types", json={"code": " qq1 ", "name": " Quad Sensor ", "category": "fire_alarm"})
    assert made.status_code == 201 and made.json()["code"] == "QQ1" and made.json()["name"] == "Quad Sensor"
    assert client.post("/ifc/device-types", json={"code": "Qq1", "name": "Again", "category": "fire_alarm"}).status_code == 409
    type_id = made.json()["id"]
    for body in ({"code": "   "}, {"name": "  "}, {"unit": " "}):
        assert client.patch(f"/ifc/device-types/{type_id}", json=body).status_code == 422, body
    assert client.patch(f"/ifc/device-types/{type_id}", json={"code": "sd"}).status_code == 409   # SD, whatever the case
    ok = client.patch(f"/ifc/device-types/{type_id}", json={"code": "qq2", "name": " Quad "}).json()
    assert ok["code"] == "QQ2" and ok["name"] == "Quad"


def test_an_answer_is_one_thing(client, tmp_path):
    _admin(client)
    project_id = _project(client, "92040")
    d = client.post(f"/projects/{project_id}/ifc-drawings",
                    files={"file": ("FA-140.dxf", _dxf(tmp_path / "FA-140.dxf").read_bytes(), "application/dxf")}).json()
    sig = d["groups"][0]["signature"]
    base = f"/projects/{project_id}/ifc-drawings/{d['id']}"
    sd = next(t["id"] for t in client.get("/ifc/device-types").json() if t["code"] == "SD")
    assert client.post(f"{base}/verify", json={"signatures": [sig], "device_type_id": sd, "ignore": True}).status_code == 422
    assert client.post(f"{base}/review", json={"answers": [{"signature": sig, "device_type_id": sd, "skip": True}]}).status_code == 422
    assert client.post(f"{base}/verify", json={"signatures": [sig], "device_type_id": 99999}).status_code == 404
    assert client.post(f"{base}/verify", json={"signatures": ["nope"], "device_type_id": sd}).status_code == 404
    assert client.post(f"{base}/review", json={"answers": [{"signature": sig, "skip": True}]}).json()["review"]["skipped"] == 1
    assert client.post(f"{base}/verify", json={"signatures": [sig], "ignore": True}).json()["groups"][0]["status"] == "ignored"
    counted = client.post(f"{base}/verify", json={"signatures": [sig], "device_type_id": sd}).json()
    assert counted["totals"]["fire_alarm"] == 3 and counted["groups"][0]["source"] == "engineer"


# --- the library: who decided wins ----------------------------------------------------------------------


def _group(sig="abc", names=("FA_SD",), label="SD"):
    return {"signature": sig, "label": label, "inner_label": "", "raster_hex": "", "svg": "", "entity_counts": {"CIRCLE": 1},
            "block_names": {n: 1 for n in names}}


def test_the_ai_never_overwrites_an_engineer_and_an_engineer_overrides_the_ai(client, db_session):
    sd, hd = _type(db_session, "SD"), _type(db_session, "HD")
    assert library.remember(db_session, _group("e1"), sd.id, source=library.ENGINEER, drawing_name="x") is not None
    assert library.remember(db_session, _group("e1"), hd.id, source=library.AI, drawing_name="x", confidence=0.99) is None
    assert library.remember(db_session, _group("e1"), hd.id, source=library.DETERMINISTIC, drawing_name="x") is None
    ai_answer = library.remember(db_session, _group("a1", ("OTHER",)), sd.id, source=library.AI, drawing_name="x", confidence=0.98)
    assert ai_answer.source == "ai" and ai_answer.confidence == 0.98
    engineer = library.remember(db_session, _group("a1", ("OTHER",)), hd.id, source=library.ENGINEER, drawing_name="x")
    assert engineer.source == "engineer" and engineer.device_type_id == hd.id and engineer.confidence is None
    db_session.commit()


def test_a_block_name_with_two_meanings_becomes_ambiguous_not_overwritten(client, db_session):
    sd, hd = _type(db_session, "SD"), _type(db_session, "HD")
    first = library.remember(db_session, _group("n1", ("FA_DET",)), sd.id, source=library.ENGINEER, drawing_name="a")
    library.remember(db_session, _group("n2", ("FA_DET",)), sd.id, source=library.ENGINEER, drawing_name="b")
    alias = db_session.query(IfcBlockAlias).filter(IfcBlockAlias.block_name == "FA_DET").one()
    assert alias.symbol_id == first.id and not alias.is_ambiguous          # same meaning: nothing to flag
    library.remember(db_session, _group("n3", ("FA_DET",)), hd.id, source=library.ENGINEER, drawing_name="c")
    db_session.refresh(alias)
    assert alias.symbol_id == first.id and alias.is_ambiguous              # kept, and flagged
    from app.ifc.resolve import library as load

    assert "FA_DET" not in load(db_session)[1]                              # an ambiguous name suggests nothing
    db_session.commit()


# --- the AI: only what nothing else knows, only once per signature -------------------------------------------


def test_a_thousand_occurrences_of_one_symbol_are_one_question(client, db_session, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92050")
    path = _symbols_dxf(tmp_path / "FA-150.dxf", [("DET-A", "SD", 0, 1000)])
    job = _job(client, _read(client, project_id, path))
    assert job["status"] == "succeeded"
    assert ai.calls == 1 and len(ai.items(0)) == 1
    sent = ai.items(0)[0]
    assert sent["label"] == "SD" and "occurrences" not in sent and len(sent["candidate_types"]) <= settings.ifc_ai_max_candidates
    assert not any(isinstance(p, ImagePart) for p in ai.requests[0].parts)        # confident on the words: no picture
    text = ai.requests[0].parts[0].text + ai.requests[0].system
    assert "92050" not in text and "FA-150" not in text and str(tmp_path) not in text   # the symbol, not the project
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    g = d["groups"][0]
    assert g["status"] == "verified" and g["source"] == "ai" and g["device_type"]["code"] == "SD"
    assert d["totals"]["fire_alarm"] == 1000                                      # Python counted them
    assert d["analysis"]["ai_verified"] == 1 and d["analysis"]["total_occurrences"] == 1000
    assert d["analysis"]["unique_symbols"] == 1 and d["analysis"]["boq_status"] == "verified"
    review = db_session.query(IfcSymbolReview).one()
    assert review.validation == "accepted" and review.stage == "metadata" and review.input_tokens == 90

    # The same symbol on another project, another drawing: known, no call.
    other = _project(client, "92051")
    again = _job(client, _read(client, other, _symbols_dxf(tmp_path / "FA-151.dxf", [("DET-B", "SD", 0, 40)])))
    assert again["status"] == "succeeded" and ai.calls == 1


def test_an_engineer_changes_the_ais_answer_for_good(client, db_session, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92052")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-152.dxf", [("DET-C", "SD", 1, 5)])))
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    sig = d["groups"][0]["signature"]
    assert d["groups"][0]["source"] == "ai"
    hd = _type(db_session, "HD")
    changed = client.post(f"/projects/{project_id}/ifc-drawings/{d['id']}/verify",
                          json={"signatures": [sig], "device_type_id": hd.id}).json()
    assert changed["groups"][0]["source"] == "engineer" and changed["groups"][0]["device_type"]["code"] == "HD"
    review = db_session.query(IfcSymbolReview).filter(IfcSymbolReview.signature == sig).one()
    db_session.refresh(review)
    assert review.outcome == "corrected" and review.outcome_device_type_id == hd.id
    # The next drawing with it: the engineer's answer, no call.
    before = ai.calls
    other = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-153.dxf", [("DET-D", "SD", 1, 2)])))
    g = client.get(f"/projects/{project_id}/ifc-drawings/{other['result']['drawing_id']}").json()["groups"][0]
    assert ai.calls == before and g["device_type"]["code"] == "HD" and g["source"] == "engineer"


def test_a_symbol_the_rules_answer_costs_no_call(client, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    monkeypatch.setattr(settings, "ifc_deterministic_auto_verify", True)
    _admin(client)
    project_id = _project(client, "92053")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-154.dxf",
                                                              [("SMOKE DETECTOR", "SD", 2, 6)])))
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    assert ai.calls == 0 and d["groups"][0]["source"] == "deterministic" and d["analysis"]["deterministic"] == 1


def test_words_alone_are_never_an_answer_by_default(client, monkeypatch, tmp_path):
    """IFC_DETERMINISTIC_AUTO_VERIFY is off: with no AI, letters and a block
    name that agree still wait for the engineer."""
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92054")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-155.dxf", [("SMOKE DETECTOR", "SD", 3, 6)])))
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    g = d["groups"][0]
    assert g["status"] != "verified" and g["queue"]["reason"] == "ai_disabled"
    assert d["analysis"]["boq_status"] == "review_required" and d["analysis"]["review_required"] == 1


def test_the_cache_answers_a_signature_asked_before(client, db_session, monkeypatch, tmp_path, ai):
    """Uncertain is an answer too: the same signature with the same
    candidates is not asked again, on any drawing."""
    _inline(monkeypatch)
    monkeypatch.setattr(settings, "ifc_ai_visual_review_enabled", False)
    ai.decide = lambda item, request: UNCERTAIN
    _admin(client)
    project_id = _project(client, "92055")
    first = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-156.dxf", [("DET-E", "SD", 4, 3)])))
    assert first["status"] == "succeeded" and ai.calls == 1
    second = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-157.dxf", [("DET-E", "SD", 4, 7)])))
    assert second["status"] == "succeeded" and ai.calls == 1
    d = client.get(f"/projects/{project_id}/ifc-drawings/{second['result']['drawing_id']}").json()
    g = d["groups"][0]
    assert g["queue"]["reason"] == "ai_uncertain" and g["review"] in ("answer", "suggested")
    assert d["analysis"]["review_required"] == 1
    assert (client.get(f"/projects/{project_id}/ifc-drawings/{second['result']['drawing_id']}").json()
            ["analysis"]["processing"]["ai_cache_hits"] == 1)


def test_uncertain_words_get_one_look_at_the_picture(client, db_session, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)

    def decide(item, request):
        pictured = any(isinstance(p, ImagePart) for p in request.parts)
        return _device(item["candidate_types"][0]["id"], 0.99, "GEOMETRY_MATCH") if pictured else UNCERTAIN

    ai.decide = decide
    _admin(client)
    project_id = _project(client, "92056")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-158.dxf", [("DET-F", "SD", 5, 4)])))
    assert ai.calls == 2
    assert not any(isinstance(p, ImagePart) for p in ai.requests[0].parts)
    images = [p for p in ai.requests[1].parts if isinstance(p, ImagePart)]
    assert len(images) == 1 and images[0].png.startswith(b"\x89PNG")
    g = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()["groups"][0]
    assert g["source"] == "ai" and g["status"] == "verified"
    stages = [r.stage for r in db_session.query(IfcSymbolReview).order_by(IfcSymbolReview.id)]
    assert stages == ["metadata", "visual"]


def test_what_the_ai_is_not_sure_of_goes_to_the_engineer(client, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    ai.decide = lambda item, request: UNCERTAIN
    _admin(client)
    project_id = _project(client, "92057")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-159.dxf", [("DET-G", "SD", 6, 4)])))
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    g = d["groups"][0]
    assert ai.calls == 2 and g["queue"]["reason"] == "ai_uncertain" and g["queue"]["ai"]["decision"] == "uncertain"
    assert not d["review"]["ready"]
    assert client.get(f"/projects/{project_id}/ifc-drawings/{d['id']}/export").status_code == 409


def test_an_ai_outage_never_fails_the_read(client, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    ai.fail = 100
    _admin(client)
    project_id = _project(client, "92058")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-160.dxf", [("DET-H", "SD", 7, 4)])))
    assert job["status"] == "succeeded"
    assert ai.calls == 1 + settings.ifc_ai_retries                        # retried, and then no more
    d = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    assert d["groups"][0]["queue"]["reason"] == "ai_unavailable"
    assert d["analysis"]["ai_note"] == "AI symbol classification is temporarily unavailable"


@pytest.mark.parametrize("answer, why", [
    (lambda item: _device(99999, 0.99), "not one of the candidates"),
    (lambda item: _device(item["candidate_types"][0]["id"], 0.80), "below"),
    (lambda item: {"decision": "maybe", "device_type_id": None, "confidence": 0.99, "reason_code": "LABEL_MATCH",
                   "requires_engineer": False}, "not one of device"),
])
def test_confidence_alone_is_never_enough(client, monkeypatch, tmp_path, ai, answer, why):
    _inline(monkeypatch)
    monkeypatch.setattr(settings, "ifc_ai_visual_review_enabled", False)
    ai.decide = lambda item, request: answer(item)
    _admin(client)
    project_id = _project(client, "92059")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-161.dxf", [("DET-I", "SD", 8, 2)])))
    g = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()["groups"][0]
    assert g["status"] != "verified" and why in g["queue"]["ai"]["validation_reason"]


def test_the_ai_is_not_asked_about_a_device_family_its_words_rule_out(client, db_session):
    hd = _type(db_session, "HD")
    item = ai_symbol_review.Item(key="s1", group={
        "signature": "x", "label": "SD", "layers": {"E-FIRE": 1}, "block_names": {},
        "name_hint": {"device_type": {"id": 1, "code": "SD", "name": "Smoke Detector"}, "family": "smoke"}},
        candidates=[{"id": hd.id, "code": hd.code, "name": hd.name}])
    verdict, why = ai_symbol_review.validate(_device(hd.id, 0.99), item, threshold=0.97, flagged=False, known=False)
    assert verdict == "rejected" and "own words" in why


def test_a_revision_asks_only_about_its_new_symbols(client, monkeypatch, tmp_path, ai):
    """R0: 98 symbols, all new, all asked. R1: the same 98 and 2 new ones:
    the 98 are known, only the 2 are asked."""
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92060")
    r0_symbols = [(f"DET-{n:03d}", "SD", n, 1) for n in range(98)]
    r0 = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-170.dxf", r0_symbols)))
    assert r0["status"] == "succeeded"
    asked_r0 = sum(len(ai.items(n)) for n in range(ai.calls))
    assert asked_r0 == 98 and ai.calls == -(-98 // settings.ifc_ai_batch_size)
    calls_before = ai.calls
    r1_symbols = r0_symbols + [(f"HEAT-{n}", "HD", n, 1) for n in range(2)]
    r1 = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-170-R1.dxf", r1_symbols, shape="circle"),
                            supersedes_id=r0["result"]["drawing_id"]))
    assert r1["status"] == "succeeded" and r1["result"]["revision"] == "R1"
    asked_r1 = [item for n in range(calls_before, ai.calls) for item in ai.items(n)]
    assert len(asked_r1) == 2 and {i["label"] for i in asked_r1} == {"HD"}
    d = client.get(f"/projects/{project_id}/ifc-drawings/{r1['result']['drawing_id']}").json()
    assert d["analysis"]["unique_symbols"] == 100 and d["analysis"]["known"] == 98 and d["analysis"]["ai_verified"] == 2


def test_reprocessing_uses_what_is_known(client, monkeypatch, tmp_path, ai):
    from app.workers.ifc_worker import IfcWorker

    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92061")
    _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-171.dxf", [("DET-X", "SD", 9, 3)])))
    calls = ai.calls
    started = client.post("/ifc/reprocess/jobs")
    assert started.status_code == 202
    IfcWorker(worker_id="reprocess").run_claimed(started.json()["id"])
    done = client.get(f"/jobs/{started.json()['id']}").json()
    assert done["status"] == "succeeded" and done["result"]["drawings"] >= 1 and ai.calls == calls


def test_unverifying_forgets_the_ais_answer_too(client, db_session, monkeypatch, tmp_path, ai):
    _inline(monkeypatch)
    _admin(client)
    project_id = _project(client, "92062")
    job = _job(client, _read(client, project_id, _symbols_dxf(tmp_path / "FA-172.dxf", [("DET-Y", "SD", 10, 3)])))
    sig = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()["groups"][0]["signature"]
    assert client.post("/ifc/symbols/unverify", json={"signatures": [sig]}).json()["deleted"] == 1
    review = db_session.query(IfcSymbolReview).filter(IfcSymbolReview.signature == sig).one()
    db_session.refresh(review)
    assert review.invalidated_at is not None
    assert db_session.query(IfcSymbol).filter(IfcSymbol.signature == sig).first() is None
