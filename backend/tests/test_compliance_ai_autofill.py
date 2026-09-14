"""Auto-fill with AI: the model answers, the engineer is told what to check.

The knowledge-base auto-fill is deterministic and stays that way; this is the
separate button beside it. What matters here is not that a row gets filled --
it is which rows come back flagged, because that is the whole review gate.
"""

import pytest

import app.ai.provider as provider_module
from app.ai.provider import RecordingProvider
from app.core.config import get_settings

from .conftest import login
from .test_compliance import _clear_cache, _project
from .test_compliance_statements import spec_pdf

settings = get_settings()


def _answers(rows: list[tuple[str, str, str]]) -> dict:
    """One scripted `answer_clauses` reply."""
    return {"answers": [{"id": i, "response": r, "remark": k} for i, r, k in rows]}


@pytest.fixture()
def prepared(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_root", str(tmp_path))
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    folder = tmp_path / "EP-30784"
    spec_pdf(folder / "Specification" / "283111 - FIRE DETECTION.pdf")
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, folder)
    spec = client.get(f"/projects/{project_id}/compliance").json()["systems"][0]["specs"][0]
    statement = client.post(f"/projects/{project_id}/compliance/prepare",
                            json={"system_code": "FAS", "path": spec["path"]}).json()
    return project_id, statement


def _unanswered(statement: dict) -> list[dict]:
    return [r for r in statement["rows"]
            if not r.get("heading") and r["source"] != "lead_in" and not r["response"]]


def test_ai_autofill_fills_and_flags_what_needs_the_engineer(client, prepared):
    project_id, statement = prepared
    base = f"/projects/{project_id}/compliance/statements/{statement['id']}"
    empty = _unanswered(statement)
    assert empty, "the prepared statement should have rows for the model to answer"

    # Three answers of different kinds: a plain Comply, one that turns on the
    # project's scope, and one the model could not decide.
    scripted = [(empty[0]["id"], "Comply", "Comply with proposed UL Listed & DCD approved EST4 panel.")]
    if len(empty) > 1:
        scripted.append((empty[1]["id"], "By others", "Will be coordinated with MEP contractor."))
    if len(empty) > 2:
        scripted.append((empty[2]["id"], "Clarification required", "BOQ does not state the model."))

    provider = RecordingProvider([_answers(scripted)] * 40)
    provider_module.set_provider(provider)
    try:
        resp = client.post(f"{base}/ai-autofill?wait=true", json={"scope": "unanswered"})
    finally:
        provider_module.set_provider(None)

    assert resp.status_code == 200, resp.text
    filled = {r["id"]: r for r in resp.json()["rows"]}

    plain = filled[empty[0]["id"]]
    assert plain["response"] == "Comply"
    assert plain["remark"] == "Comply with proposed UL Listed & DCD approved EST4 panel."
    assert plain["source"] == "ai" and plain["workflow"] == "ai_pending"
    # A plain Comply is filled, not flagged: the engineer's attention is finite.
    assert plain["ai_class"] == "filled" and plain["state"] == "ok"

    if len(empty) > 1:
        scoped = filled[empty[1]["id"]]
        # By others turns on the project's scope, so it always comes back flagged.
        assert scoped["ai_class"] == "needs_review" and scoped["state"] == "review"
        assert scoped["note"]

    if len(empty) > 2:
        undecided = filled[empty[2]["id"]]
        assert undecided["ai_class"] == "needs_review" and undecided["state"] == "review"

    assert provider.calls >= 1


def test_ai_autofill_leaves_the_engineers_own_answers_alone(client, prepared):
    project_id, statement = prepared
    base = f"/projects/{project_id}/compliance/statements/{statement['id']}"
    mine = _unanswered(statement)[0]["id"]
    client.patch(f"/projects/{project_id}/compliance/statements/{statement['id']}",
                 json={"rows": [{"id": mine, "response": "Deviation", "remark": "mine"}]})

    provider = RecordingProvider([_answers([(mine, "Comply", "overwritten")])] * 40)
    provider_module.set_provider(provider)
    try:
        resp = client.post(f"{base}/ai-autofill?wait=true", json={"scope": "all"})
    finally:
        provider_module.set_provider(None)

    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["rows"] if r["id"] == mine)
    assert row["response"] == "Deviation" and row["remark"] == "mine"
    assert row["source"] == "engineer"


def test_ai_autofill_needs_a_provider(client, prepared, monkeypatch):
    project_id, statement = prepared
    # Off, whatever the developer's .env says: no provider to build.
    monkeypatch.setattr(settings, "ai_enabled", False)
    provider_module.set_provider(None)
    resp = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/ai-autofill?wait=true",
                       json={"scope": "unanswered"})
    provider_module.set_provider(None)
    # Without a configured provider the request is refused, not silently empty.
    assert resp.status_code == 400
    assert "AI is not configured" in resp.json()["detail"]


def test_plain_autofill_still_calls_no_model(client, prepared):
    """The knowledge-base button must stay deterministic."""
    project_id, statement = prepared
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    try:
        resp = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill")
    finally:
        provider_module.set_provider(None)
    assert resp.status_code == 200, resp.text
    assert provider.calls == 0
