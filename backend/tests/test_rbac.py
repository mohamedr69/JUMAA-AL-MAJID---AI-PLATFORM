import pytest

from app.models import DESIGN_ROLES, RoleEnum

from .conftest import login, make_user

ALL_ROLES = list(RoleEnum)

# module -> (roles expected to be allowed in)
EXPECTATIONS = {
    "/modules/admin": {RoleEnum.admin},
    # Every design engineer, whatever their discipline: the design module
    # is the work they are here to do. Only the estimation roles are
    # confined to their own area (app/deps.py).
    "/modules/design": {RoleEnum.admin, RoleEnum.design_manager, *DESIGN_ROLES, RoleEnum.draftsman},
    "/modules/viewer": set(ALL_ROLES) - {RoleEnum.fire_alarm_estimation_engineer, RoleEnum.fire_fighting_estimation_engineer, RoleEnum.elv_estimation_engineer},
}


@pytest.mark.parametrize("module_path", list(EXPECTATIONS.keys()))
@pytest.mark.parametrize("role", ALL_ROLES)
def test_role_access_to_module(client, db_session, module_path, role):
    email = f"role.{role.value}@ep-platform.com"
    make_user(db_session, email, role)
    login_resp = login(client, email)
    assert login_resp.status_code == 200, f"login should succeed for {role.value}"

    resp = client.get(module_path)

    if role in EXPECTATIONS[module_path]:
        assert resp.status_code == 200, f"{role.value} should access {module_path}"
    else:
        assert resp.status_code == 403, f"{role.value} should NOT access {module_path}"

    client.post("/auth/logout")


def test_anonymous_cannot_access_any_module(client):
    for module_path in EXPECTATIONS:
        resp = client.get(module_path)
        assert resp.status_code == 401


def test_only_admin_can_list_users(client, db_session):
    make_user(db_session, "viewer1@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer1@ep-platform.com")

    resp = client.get("/users")
    assert resp.status_code == 403


def test_admin_can_manage_users(client):
    from app.core.config import get_settings

    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)

    create_resp = client.post(
        "/users",
        json={
            "email": "new.engineer@ep-platform.com",
            "full_name": "New Engineer",
            "password": "StrongPass123!",
            "role": "fire_alarm_design_engineer",
        },
    )
    assert create_resp.status_code == 201

    list_resp = client.get("/users")
    assert list_resp.status_code == 200
    emails = [u["email"] for u in list_resp.json()]
    assert "new.engineer@ep-platform.com" in emails


def test_cannot_demote_or_deactivate_last_admin(client):
    from app.core.config import get_settings

    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)

    me = client.get("/auth/me").json()

    demote_resp = client.patch(f"/users/{me['id']}", json={"role": "viewer"})
    assert demote_resp.status_code == 400

    deactivate_resp = client.patch(f"/users/{me['id']}", json={"is_active": False})
    assert deactivate_resp.status_code == 400


def test_can_demote_admin_when_another_admin_remains(client, db_session):
    from app.core.config import get_settings

    settings = get_settings()
    second_admin = make_user(db_session, "second.admin@ep-platform.com", RoleEnum.admin)
    login(client, settings.default_admin_email, settings.default_admin_password)

    resp = client.patch(f"/users/{second_admin.id}", json={"role": "viewer"})
    assert resp.status_code == 200
