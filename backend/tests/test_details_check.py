"""The AI check of project details against the DRF, and a Project Info edit
reaching the rest of the project.

The check is a suggestion layer: the model's reply is judged by Python
(fields and systems outside the form dropped, the scope one of the form's
options, an e-mail an e-mail, a correction that equals the held value not a
correction) and nothing is written. The edit is the ordinary PUT; what it
changes elsewhere is what held a copy of the old value.
"""

from pathlib import Path

import pymupdf

import app.ai.provider as provider_module
import app.routers.compliance as compliance_router
from app.ai.provider import ImagePart, RecordingProvider
from app.core.config import get_settings
from app.models import Project, ProjectBoqItem
from app.services import details_check

from .conftest import login
from .test_compliance import _project

settings = get_settings()

HELD = {"project_name": "Binghatti Skyblade", "plot_number": "", "location": "Burj Khalifa District", "client": "",
        "consultant": "", "contractor": "Granada Europe", "contact_person": "Eng. Qusai Ammari", "contact_phone": "",
        "contact_email": "qusai@example.com", "scope_of_work": "Supply Only", "other_information": ""}
SYSTEMS = [{"name": "Fire Alarm", "brand": "Edwards", "method_statement": True, "drawing": True},
           {"name": "CCTV", "brand": "Hikvision", "method_statement": False, "drawing": False}]


def _field(name, verdict, value="", reason="read on the form"):
    return {"field": name, "verdict": verdict, "value": value, "reason": reason}


def test_only_real_well_formed_differences_survive():
    reply = {
        "fields": [
            _field("project_name", "correct", "Binghatti Skyblade Tower"),
            _field("location", "correct", "burj khalifa district"),          # same value, other case: confirmed
            _field("contractor", "ok", "Granada Europe"),
            _field("contact_email", "correct", "not an email"),             # dropped
            _field("scope_of_work", "correct", "full package"),             # normalised to the option
            _field("client", "unreadable"),
            _field("project_name", "correct", "a second answer for the same field"),   # dropped
        ],
        "systems": [
            {"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},    # confirmed
            {"name": "Central Battery System", "brand": "Menvier", "method_statement": False, "drawing": True},
        ],
        "systems_readable": True,
        "notes": ["Plot number is blank on the form."],
    }
    result = details_check.validate(reply, HELD, SYSTEMS, model="claude-sonnet-5")
    suggested = {f["field"]: f["suggested"] for f in result.fields}
    assert suggested == {"project_name": "Binghatti Skyblade Tower", "scope_of_work": "Full Package"}
    assert result.unreadable == ["Client"]
    changes = {s["name"]: s["change"] for s in result.systems}
    assert changes == {"Central Battery System": "add", "CCTV": "remove"}
    assert result.confirmed == 3 and result.notes == ["Plot number is blank on the form."]

    # A table the model could not read proposes no system changes at all.
    unread = details_check.validate({**reply, "systems": [], "systems_readable": False}, HELD, SYSTEMS)
    assert unread.systems == [] and "Systems table" in unread.unreadable


def test_the_check_sends_the_drf_page_and_writes_nothing(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ai_enabled", True)
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, tmp_path / "EP-30784", systems=("Fire Alarm",))
    drf = tmp_path / "DRF.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "PROJECT TITLE: Binghatti Skyblade Tower")
    doc.save(drf)
    project = db_session.get(Project, project_id)
    project.drf_document_path = str(drf)
    db_session.commit()

    recording = RecordingProvider([{"fields": [_field("project_name", "correct", "Binghatti Skyblade Tower")],
                                    "systems": [{"name": "Fire Alarm", "brand": "", "method_statement": True, "drawing": True}],
                                    "systems_readable": True, "notes": []}])
    provider_module.set_provider(recording)
    try:
        held = client.get(f"/projects/{project_id}").json()
        body = {"details": {**{k: held[k] for k in HELD}, "systems": held["systems"]}}
        resp = client.post(f"/projects/{project_id}/details-check", json=body)
    finally:
        provider_module.set_provider(None)

    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["fields"] == [{"field": "project_name", "label": "Project Title", "current": "Binghatti Skyblade",
                                 "suggested": "Binghatti Skyblade Tower", "reason": "read on the form"}]
    assert recording.calls == 1 and any(isinstance(p, ImagePart) for p in recording.requests[0].parts)
    # Suggestions only: the project is as it was.
    assert client.get(f"/projects/{project_id}").json()["project_name"] == "Binghatti Skyblade"


def test_a_project_without_a_drf_or_without_ai_is_refused(client, tmp_path, monkeypatch):
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, tmp_path / "EP-30784")
    body = {"details": {**HELD, "systems": []}}
    assert client.post(f"/projects/{project_id}/details-check", json=body).status_code == 409
    assert client.post("/projects/details-check", json=body).status_code == 400


def test_a_project_info_edit_reaches_the_boq_and_the_compliance_search(client, db_session, tmp_path):
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, tmp_path / "EP-30784", systems=("Fire Alarm",))
    project = db_session.get(Project, project_id)
    project.systems[0].brand = "Edwards"
    db_session.add_all([
        ProjectBoqItem(project_id=project_id, position=1, description="Smoke detector", manufacturer="Edwards", system_code="FAS"),
        ProjectBoqItem(project_id=project_id, position=2, description="Beam detector", manufacturer="Fire Fighting Enterprises",
                       system_code="FAS"),
    ])
    db_session.commit()
    compliance_router._cache[project_id] = (None, [], [])

    held = client.get(f"/projects/{project_id}").json()
    systems = [{**s, "brand": "Notifier"} for s in held["systems"]]
    resp = client.put(f"/projects/{project_id}", json={**{k: held[k] for k in HELD}, "project_name": "Skyblade Tower",
                                                       "systems": systems})
    assert resp.status_code == 200, resp.text
    assert resp.json()["project_name"] == "Skyblade Tower"
    assert any("BOQ line" in note for note in resp.json()["propagated"])

    lines = {i["description"]: i["manufacturer"] for i in client.get(f"/projects/{project_id}/boq").json()}
    # The line that carried the DRF brand follows it; the one an engineer typed stays theirs.
    assert lines == {"Smoke detector": "Notifier", "Beam detector": "Fire Fighting Enterprises"}
    assert project_id not in compliance_router._cache
