from app.models import RoleEnum
from tests.conftest import login, make_user


def test_admin_creates_estimation_account_and_engineer_uses_projects(client, db_session):
    make_user(db_session, "admin-est@example.com", RoleEnum.admin)
    login(client, "admin-est@example.com")
    response = client.post("/users", json={
        "email": "estimator@example.com", "full_name": "Estimation Engineer",
        "password": "Estimate123!", "role": "fire_alarm_estimation_engineer",
    })
    assert response.status_code == 201
    assert "hashed_password" not in response.json()
    client.post("/auth/logout")
    assert login(client, "estimator@example.com", "wrong").status_code == 401
    assert login(client, "estimator@example.com", "Estimate123!").json()["role"] == "fire_alarm_estimation_engineer"
    assert client.get("/auth/me").status_code == 200
    assert client.get("/estimation/projects").json() == []
    created = client.post("/estimation/projects", json={"reference": " est-001 ", "title": "Office", "client": "Client"})
    assert created.status_code == 201
    project = created.json()
    assert project["reference"] == "EST-001"
    assert client.get(f"/estimation/projects/{project['id']}").json() == project
    assert client.get("/estimation/projects").json() == [project]
    assert client.post("/estimation/projects", json={"reference": "EST-001", "title": "Duplicate"}).status_code == 409
    assert client.post("/estimation/projects", json={"reference": " ", "title": "Blank"}).status_code == 422
    assert client.get("/estimation/projects/99999").status_code == 404
    for path in ("/projects", "/projects/1", "/projects/1/boq", "/users", "/modules/design"):
        assert client.get(path).status_code == 403
    assert client.post("/projects", json={}).status_code == 403


def test_estimation_area_requires_correct_team(client, db_session):
    assert client.get("/estimation/projects").status_code == 401
    make_user(db_session, "design-est@example.com", RoleEnum.fire_alarm_design_engineer)
    login(client, "design-est@example.com")
    assert client.get("/projects").status_code == 200
    assert client.get("/estimation/projects").status_code == 403
    assert client.post("/estimation/projects", json={"reference": "EST-2", "title": "Test"}).status_code == 403
