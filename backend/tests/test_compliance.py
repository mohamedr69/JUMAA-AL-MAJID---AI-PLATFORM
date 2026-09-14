"""The compliance statement tab: a specification per system, or the two ways
forward when the project has none."""

from pathlib import Path

from app.core.config import get_settings
from app.models import RoleEnum

from .conftest import login, make_user
from .test_spec_finder import _pdf, _spec

settings = get_settings()


def _project(client, folder: Path, systems=("Fire Alarm", "Emergency Light Monitoring")) -> int:
    resp = client.post(
        "/projects",
        json={
            "ep_number": "30784",
            "project_name": "Binghatti Skyblade",
            "location": "Burj Khalifa District",
            "contractor": "Granada Europe",
            "contact_person": "Eng. Qusai Ammari",
            "contact_email": "qusai@example.com",
            "source_folder_path": str(folder),
            "systems": [{"name": name, "method_statement": True, "drawing": True} for name in systems],
            "design_sheets": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _clear_cache():
    import app.routers.compliance as compliance_router

    compliance_router._cache.clear()


def test_the_specification_of_each_system_is_found(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    folder = tmp_path / "EP-30784"
    _spec(folder / "02- inputs" / "Specification" / "283111 - ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION.pdf", "283111", "ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION")
    _spec(folder / "02- inputs" / "Specification" / "211313 - SPRINKLER SYSTEMS.pdf", "211313", "SPRINKLER SYSTEMS")
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, folder)

    body = client.get(f"/projects/{project_id}/compliance").json()
    assert [(s["code"], len(s["specs"])) for s in body["systems"]] == [("FAS", 1), ("ELS", 0)]
    spec = body["systems"][0]["specs"][0]
    assert (spec["section_no"], spec["kind"], spec["uploaded"]) == ("283111", "document", False)

    # The specification opens, out of the project folder.
    resp = client.get(f"/projects/{project_id}/compliance/file", params={"path": spec["path"]})
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert client.get(f"/projects/{project_id}/compliance/file", params={"path": "../secret.pdf"}).status_code == 404


def test_a_missing_specification_offers_a_draft_mail(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    folder = tmp_path / "EP-30784"
    _pdf(folder / "04- Drawings" / "FIRE ALARM LAYOUT.pdf", ["FIRE ALARM LAYOUT"])
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, folder)

    body = client.get(f"/projects/{project_id}/compliance").json()
    assert [(s["code"], s["specs"]) for s in body["systems"]] == [("FAS", []), ("ELS", [])]

    mail = client.get(f"/projects/{project_id}/compliance/draft-mail", params={"system_code": "EML"}).json()
    assert mail["to"] == "qusai@example.com" and mail["to_name"] == "Eng. Qusai Ammari"
    assert "Emergency Lighting specification" in mail["subject"]
    assert "EP-30784" in mail["subject"]
    # It asks for the specification and says what happens if there is none.
    assert "26 52 00" in mail["body"]
    assert "confirm that in writing" in mail["body"]
    assert "Burj Khalifa District" in mail["body"]
    assert client.get(f"/projects/{project_id}/compliance/draft-mail", params={"system_code": "XX"}).status_code == 400


def test_uploading_the_specification_the_project_lacks(client, tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(settings, "uploads_root", str(uploads))
    _clear_cache()
    folder = tmp_path / "EP-30784"
    folder.mkdir()
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, folder)
    assert client.get(f"/projects/{project_id}/compliance").json()["systems"][0]["specs"] == []

    spec = _spec(tmp_path / "build" / "283111 - FIRE DETECTION.pdf", "283111", "ADDRESSABLE FIRE DETECTION")
    resp = client.post(
        f"/projects/{project_id}/compliance/specs",
        files={"file": ("283111 - FIRE DETECTION.pdf", spec.read_bytes(), "application/pdf")},
        data={"system_code": "FAS"},
    )
    assert resp.status_code == 200, resp.text
    (found,) = resp.json()["systems"][0]["specs"]
    assert found["uploaded"] is True and found["section_no"] == "283111"
    # And it opens from where the platform keeps it.
    assert client.get(f"/projects/{project_id}/compliance/file", params={"path": found["path"]}).status_code == 200


def test_uploading_is_for_editors_and_known_systems(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    folder = tmp_path / "EP-30784"
    folder.mkdir()
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client, folder)
    files = {"file": ("spec.pdf", b"%PDF-1.4 x", "application/pdf")}
    assert client.post(f"/projects/{project_id}/compliance/specs", files=files, data={"system_code": "ZZ"}).status_code == 400

    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert client.get(f"/projects/{project_id}/compliance").status_code == 200
    assert client.post(f"/projects/{project_id}/compliance/specs", files=files, data={"system_code": "FAS"}).status_code == 403
