from pathlib import Path

from app.core.config import get_settings
from app.models import RoleEnum

from .conftest import login, make_user

settings = get_settings()


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake pdf bytes")


def _login_admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


# --- /projects/resolve ---


def test_resolve_requires_creator_role(client, db_session):
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")

    resp = client.post("/projects/resolve", json={"ep_number": "29495"})
    assert resp.status_code == 403


def test_resolve_without_projects_root_configured(client, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "projects_root", None)
    _login_admin(client)

    resp = client.post("/projects/resolve", json={"ep_number": "29495"})
    assert resp.status_code == 503


def test_resolve_happy_path(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    project = tmp_path / "Samana Developers" / "EP-29495 IVY Garden 2"
    touch(project / "Scan Document" / "EP-29495 DRF.pdf")
    touch(project / "Commercial Document" / "EP-29495 FAS Design.pdf")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))

    _login_admin(client)
    resp = client.post("/projects/resolve", json={"ep_number": "29495"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["folder_found"] is True
    assert body["is_ambiguous"] is False
    assert len(body["drf_candidates"]) == 1
    assert body["drf_candidates"][0]["filename"] == "EP-29495 DRF.pdf"
    assert len(body["design_sheet_candidates"]) == 1
    assert body["design_sheet_candidates"][0]["system_guess"] == "FAS"
    assert body["warnings"] == []
    # The fixture DRF is a fake (non-PDF) file -- extraction should fail
    # gracefully rather than break the resolve response.
    assert body["extracted_fields"] == {}
    assert body["extraction_warnings"]


def test_resolve_ep_not_found(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))
    _login_admin(client)

    resp = client.post("/projects/resolve", json={"ep_number": "99999"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["folder_found"] is False
    assert any("No folder found" in w for w in body["warnings"])


# --- POST /projects ---


def _valid_project_payload(ep_number: str = "29495") -> dict:
    return {
        "ep_number": ep_number,
        "project_name": "IVY Garden 2",
        "plot_number": "648-8523",
        "location": "Wadi Al Safa 5, DLRC, Dubai",
        "client": "Samana",
        "consultant": "Al Hilal",
        "contractor": "Samana Developers",
        "contact_person": "Mahammad Naushad Bennapade",
        "contact_phone": "971543079068",
        "contact_email": "mahammad.bennapade@samanadevelopers.com",
        "scope_of_work": "Design, Supply, T&C",
        "systems": ["Fire Alarm", "Emergency Light"],
        "other_information": "Quoted as per IFC drawing dated 18-06-2025 only",
        "source_folder_path": r"C:\archive\Samana Developers\EP-29495 IVY Garden 2",
        "drf_document_path": r"C:\archive\Samana Developers\EP-29495 IVY Garden 2\Scan Document\EP-29495 DRF.pdf",
        "design_sheets": [
            {
                "system_code": "FAS",
                "document_path": r"C:\archive\...\EP-29495 FAS Design.pdf",
            }
        ],
    }


def test_create_project_requires_creator_role(client, db_session):
    make_user(db_session, "draftsman@ep-platform.com", RoleEnum.draftsman)
    login(client, "draftsman@ep-platform.com")

    resp = client.post("/projects", json=_valid_project_payload())
    assert resp.status_code == 403


def test_create_and_fetch_project(client):
    _login_admin(client)

    create_resp = client.post("/projects", json=_valid_project_payload())
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["ep_number"] == "29495"
    assert created["status"] == "active"
    assert created["systems"] == "Fire Alarm,Emergency Light"
    assert len(created["design_sheets"]) == 1
    assert created["design_sheets"][0]["system_code"] == "FAS"

    get_resp = client.get(f"/projects/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["ep_number"] == "29495"


def test_create_project_duplicate_ep_number_conflicts(client):
    _login_admin(client)
    first = client.post("/projects", json=_valid_project_payload("30000"))
    assert first.status_code == 201

    second = client.post("/projects", json=_valid_project_payload("30000"))
    assert second.status_code == 409


def test_list_projects_visible_to_any_authenticated_role(client, db_session):
    _login_admin(client)
    client.post("/projects", json=_valid_project_payload("31000"))
    client.post("/auth/logout")

    make_user(db_session, "viewer2@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer2@ep-platform.com")

    resp = client.get("/projects")
    assert resp.status_code == 200
    assert any(p["ep_number"] == "31000" for p in resp.json())


def test_get_missing_project_404(client):
    _login_admin(client)
    resp = client.get("/projects/999999")
    assert resp.status_code == 404
