"""Where this PC keeps its data: the database in use, whether it is shared
between PCs, and what it holds."""

from pathlib import Path

from app.core.config import get_settings

from .conftest import login

settings = get_settings()


def test_the_data_location_says_which_database_this_pc_uses(client, monkeypatch):
    login(client, settings.default_admin_email, settings.default_admin_password)
    client.post("/projects", json={"ep_number": "30820", "project_name": "Titania", "design_sheets": []})

    body = client.get("/data-location").json()

    assert body["projects"] >= 1 and body["users"] >= 1 and body["machine"]
    assert body["shared"] is False                      # the tests run on their own database
    assert "DATA_ROOT" in body["advice"] and "same projects" in body["advice"]

    # With a shared folder the advice is the other way round.
    monkeypatch.setattr(settings, "data_root", str(Path.home() / "EP Platform"))
    shared = client.get("/data-location").json()
    assert shared["shared"] is True and shared["data_root"].endswith("EP Platform")
    assert "one PC at a time" in shared["advice"]


def test_the_data_location_needs_a_signed_in_user(client):
    assert client.get("/data-location").status_code == 401
