"""Removing a user account, and when it must not be removed."""

from app.core.config import get_settings
from app.models import RoleEnum, User
from app.services import activity

from .conftest import login, make_user

settings = get_settings()


def _login_admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def test_an_unused_account_is_deleted(client, db_session):
    """The case this exists for: an account created by mistake, or for
    someone who never started."""
    user = make_user(db_session, "never-used@ep-platform.com", RoleEnum.fire_alarm_design_engineer)
    user_id = user.id  # read it before the row goes
    _login_admin(client)

    assert client.delete(f"/users/{user_id}").status_code == 204
    # The API deleted it in its own session; this one still has it cached.
    db_session.expire_all()
    assert db_session.get(User, user_id) is None


def test_an_account_with_history_is_refused_and_says_why(client, db_session):
    """The platform keeps who did what after the thing itself is gone, so
    an account that has done anything cannot be deleted away from its own
    record. The refusal names what is in the way."""
    user = make_user(db_session, "worked@ep-platform.com", RoleEnum.fire_alarm_design_engineer)
    activity.record(db_session, user, "auth.login", "Signed in")
    _login_admin(client)

    resp = client.delete(f"/users/{user.id}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "user_has_history"
    assert detail["history"] == {"recorded action": 1}
    assert "Deactivate" in detail["message"]
    assert db_session.get(User, user.id) is not None

    # What the refusal points at works, and the record still reads.
    assert client.patch(f"/users/{user.id}", json={"is_active": False}).status_code == 200
    db_session.refresh(user)
    assert user.is_active is False


def test_an_admin_cannot_delete_themselves(client, db_session):
    _login_admin(client)
    me = db_session.query(User).filter(User.email == settings.default_admin_email).one()
    resp = client.delete(f"/users/{me.id}")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "self_delete"


def test_another_admin_can_be_deleted(client, db_session):
    """There is no "last admin" case to guard here: only an admin may
    delete, and deleting yourself is refused, so the target is never the
    only active admin left."""
    other = make_user(db_session, "second-admin@ep-platform.com", RoleEnum.admin)
    _login_admin(client)
    assert client.delete(f"/users/{other.id}").status_code == 204


def test_only_an_admin_may_delete(client, db_session):
    victim = make_user(db_session, "victim@ep-platform.com", RoleEnum.viewer)
    make_user(db_session, "engineer@ep-platform.com", RoleEnum.fire_alarm_design_engineer)
    login(client, "engineer@ep-platform.com")
    assert client.delete(f"/users/{victim.id}").status_code == 403


def test_deleting_a_missing_user_is_a_404(client):
    _login_admin(client)
    assert client.delete("/users/999999").status_code == 404
