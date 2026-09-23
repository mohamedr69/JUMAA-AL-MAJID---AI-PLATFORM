import pytest

from app.models import RoleEnum
from tests.conftest import login, make_user


@pytest.mark.parametrize("division,role", [("fire-fighting", RoleEnum.fire_fighting_estimation_engineer), ("elv", RoleEnum.elv_estimation_engineer)])
def test_division_account_and_projects(client, db_session, division, role):
    make_user(db_session, "division-admin@example.com", RoleEnum.admin)
    login(client, "division-admin@example.com")
    email = f"{division}@example.com"
    assert client.post("/users", json={"email": email, "full_name": division, "role": role.value, "password": "Password123!"}).status_code == 201
    client.post("/auth/logout")
    assert login(client, email).json()["role"] == role.value
    base = f"/{division}/projects"
    assert client.get(base).json() == []
    result = client.post(base, json={"reference": " ref-1 ", "title": "New project", "client": "Client"})
    assert result.status_code == 201
    project = result.json()
    assert project["reference"] == "REF-1"
    assert client.get(f"{base}/{project['id']}").json() == project
    assert client.get(base).json() == [project]
    assert client.post(base, json={"reference": "REF-1", "title": "Duplicate"}).status_code == 409
    assert client.post(base, json={"reference": " ", "title": "Blank"}).status_code == 422
    assert client.get(f"{base}/999999").status_code == 404
    for path in ["/projects", "/estimation/projects", "/users", "/modules/design", "/elv/projects" if division == "fire-fighting" else "/fire-fighting/projects"]:
        assert client.get(path).status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_division_scoping_even_for_admin(client, db_session):
    assert client.get("/elv/projects").status_code == 401
    make_user(db_session, "division-admin@example.com", RoleEnum.admin)
    login(client, "division-admin@example.com")
    payload = {"reference": "SAME-REF", "title": "Project"}
    fire = client.post("/fire-fighting/projects", json=payload)
    elv = client.post("/elv/projects", json=payload)
    assert fire.status_code == elv.status_code == 201
    assert client.get("/fire-fighting/projects").json() == [fire.json()]
    assert client.get("/elv/projects").json() == [elv.json()]
    assert client.get(f"/elv/projects/{fire.json()['id']}").status_code == 404
    make_user(db_session, "design-div@example.com", RoleEnum.fire_alarm_design_engineer)
    login(client, "design-div@example.com")
    assert client.get("/elv/projects").status_code == 403
    assert client.get("/fire-fighting/projects").status_code == 403
