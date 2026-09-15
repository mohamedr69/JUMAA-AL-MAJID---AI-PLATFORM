"""Long work as stored jobs; knowledge-base eligibility made explainable and
repairable by review, without loosening the policy."""

import pytest

import app.routers.jobs as jobs_router
import app.routers.projects as projects_router
from app.core.config import get_settings
from app.models import BackgroundJob, KnowledgeMapping, KnowledgeResponse, KnowledgeResponseSource
from app.services import jobs
from app.services.design_sheet_extractor import ExtractedBoqLine

from .test_compliance_knowledge import knowledge  # noqa: F401 -- fixture
from .test_projects import _login_admin, _valid_project_payload

settings = get_settings()


@pytest.fixture()
def inline_jobs(monkeypatch):
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)


def test_a_reread_runs_as_a_job_with_progress(client, db_session, monkeypatch, inline_jobs):
    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: [
        ExtractedBoqLine(catalog_no="4-CPU", description="CPU", quantity="1", group_heading=None, confidence=90, page=1)])
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("90001")).json()["id"]

    started = client.post(f"/projects/{pid}/jobs/boq-reread")
    assert started.status_code == 202, started.text
    job = client.get(f"/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded" and job["result"]["candidate_id"]
    assert job["progress"]["message"] == "Finished"
    assert client.get(f"/projects/{pid}/boq/candidates/{job['result']['candidate_id']}").status_code == 200
    assert [j["kind"] for j in client.get(f"/projects/{pid}/jobs").json()] == ["boq_reread"]


def test_a_cancelled_job_stops_at_the_next_step(client, db_session):
    _login_admin(client)
    steps = []

    def work(session, ctx):
        for step in range(5):
            steps.append(step)
            if step == 1:
                job = session.get(BackgroundJob, ctx.job_id)
                job.cancel_requested = True
                session.commit()
            ctx.progress(step, 5, f"step {step}")
        return {"done": True}

    job = jobs.start(db_session, kind="test", project_id=None, user_id=None, work=work, run_inline=True)
    assert job.status == "cancelled" and steps == [0, 1]
    assert job.progress["message"] == "Stopped on request"


def test_a_failing_job_is_recorded_and_interrupted_jobs_fail_on_start(client, db_session):
    def work(session, ctx):
        raise RuntimeError("the sheet is gone")

    job = jobs.start(db_session, kind="test", project_id=None, user_id=None, work=work, run_inline=True)
    assert job.status == "failed" and "the sheet is gone" in job.error

    stale = BackgroundJob(kind="test", status="running", progress={})
    db_session.add(stale)
    db_session.commit()
    assert jobs.fail_interrupted(db_session) == 1
    db_session.refresh(stale)
    assert stale.status == "failed" and "restarted" in stale.error


def test_a_second_job_of_the_same_kind_is_refused_while_one_runs(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("90002")).json()["id"]
    db_session.add(BackgroundJob(project_id=pid, kind="documents_intake", status="running", progress={}))
    db_session.commit()
    refused = client.post(f"/projects/{pid}/jobs/documents-intake")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "job_running"


def test_verifying_a_pairing_promotes_only_what_the_policy_allows(client, db_session, knowledge):  # noqa: F811
    _login_admin(client)
    summary = client.get("/admin/knowledge/eligibility").json()
    assert summary["responses"] > 0 and summary["reasons"]
    assert any(m["method"] == "D_annot_ocr" for m in summary["mappings_by_method"])

    # The medium-confidence OCR mapping carries RSP-000000003, which also has
    # a review flag: verifying its pairing must not make it eligible.
    queue = client.get("/admin/knowledge/eligibility/review-queue", params={"method": "D_annot_ocr", "confidence": "medium"}).json()
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["mapping_id"] == "MAP-000000003" and item["source"]["filename"] and item["responses"]

    result = client.post("/admin/knowledge/eligibility/reviews",
                         json={"mapping_ids": ["MAP-000000003"], "verdict": "verified", "note": "checked against the PDF"}).json()
    assert result["reviewed"] == 1 and result["now_eligible"] == 0
    response = db_session.get(KnowledgeResponse, "RSP-000000003")
    db_session.refresh(response)
    assert response.autofill_eligibility == "blocked" and "review flag" in response.eligibility_reasons
    assert client.get("/admin/knowledge/eligibility/review-queue",
                      params={"method": "D_annot_ocr", "confidence": "medium"}).json()["total"] == 0


def test_a_verified_native_text_pairing_becomes_eligible_and_a_rejected_one_blocks(client, db_session, knowledge):  # noqa: F811
    _login_admin(client)
    # An eligible record's mapping made medium-confidence, as the real collection's A_table pairings are.
    mapping = db_session.get(KnowledgeMapping, "MAP-000000001")
    mapping.pairing_confidence = "medium"
    db_session.commit()
    from app.knowledge import eligibility

    eligibility.recompute(db_session, ["RSP-000000001"])
    response = db_session.get(KnowledgeResponse, "RSP-000000001")
    assert response.autofill_eligibility == "blocked" and "native text" in response.eligibility_reasons

    verified = client.post("/admin/knowledge/eligibility/reviews",
                           json={"mapping_ids": ["MAP-000000001"], "verdict": "verified"}).json()
    assert verified["now_eligible"] == 1
    db_session.refresh(response)
    assert response.autofill_eligibility == "eligible"

    rejected = client.post("/admin/knowledge/eligibility/reviews",
                           json={"mapping_ids": ["MAP-000000001"], "verdict": "rejected"}).json()
    assert rejected["now_blocked"] == 1
    db_session.refresh(response)
    assert response.autofill_eligibility == "blocked"
    # Viewers and engineers cannot review.
    assert db_session.query(KnowledgeResponseSource).filter_by(mapping_id="MAP-000000001").count() == 1
