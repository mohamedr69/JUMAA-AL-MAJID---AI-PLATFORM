"""A background job runs on a thread of its own with a session of its own
(app.services.jobs.start); the requests that poll it open and close sessions
of their own. Each must hold its transaction alone: on one shared database
connection a request session closing rolled back the job's flushed rows
(M2 review 02, D: test_the_first_read_runs_as_a_job_the_page_follows failed
by timing and order with `FOREIGN KEY constraint failed` on the BOQ items or
a job row read back as None). This test is the race itself: the job flushes a
row, waits, commits, while the test polls it through the API."""
import time

from app.core.config import get_settings
from app.models import BackgroundJob
from app.services import jobs
from .conftest import login


def test_a_background_job_keeps_its_flushed_rows_while_requests_open_and_close_sessions(client, db_session):
    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)

    def work(session, ctx):
        probe = BackgroundJob(kind="probe_of_the_job_thread", project_id=None, created_by_id=None, status="queued", progress={})
        session.add(probe)
        session.flush()            # written to the connection, not yet committed
        probe_id = probe.id
        time.sleep(0.8)            # requests come and go meanwhile
        session.commit()
        return {"probe_id": probe_id}

    job = jobs.start(db_session, kind="job_thread_probe", project_id=None, user_id=None, work=work)
    deadline = time.monotonic() + 15
    polls = 0
    while True:
        body = client.get(f"/jobs/{job.id}")
        assert body.status_code == 200, body.text
        polls += 1
        if body.json()["status"] not in ("queued", "running") or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    assert polls >= 3, "the job must have been polled while it ran"
    assert body.json()["status"] == "succeeded", body.json()
    probe_id = body.json()["result"]["probe_id"]
    db_session.expire_all()
    assert db_session.get(BackgroundJob, probe_id) is not None, "the job's flushed row survived the request sessions"
