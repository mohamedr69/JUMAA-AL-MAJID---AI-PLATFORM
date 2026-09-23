from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM
from app.models import RoleEnum

from .conftest import login, make_user

settings = get_settings()


def test_login_success_returns_user_and_sets_cookie(client):
    resp = login(client, settings.default_admin_email, settings.default_admin_password)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == settings.default_admin_email
    assert body["role"] == "admin"
    assert settings.cookie_name in resp.cookies


def test_login_wrong_password_rejected_with_generic_error(client):
    resp = login(client, settings.default_admin_email, "totally-wrong-password")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_nonexistent_email_rejected(client):
    resp = login(client, "nobody@ep-platform.com", "whatever")
    assert resp.status_code == 401


def test_repeated_wrong_passwords_lock_the_account(client, db_session):
    user = make_user(db_session, "design.eng@ep-platform.com", RoleEnum.fire_alarm_design_engineer)

    for _ in range(3):
        resp = login(client, user.email, "wrong-password")
        assert resp.status_code == 401

    locked_resp = login(client, user.email, "wrong-password")
    assert locked_resp.status_code == 423

    still_locked_even_with_correct_password = login(client, user.email, "Password123!")
    assert still_locked_even_with_correct_password.status_code == 423


def test_successful_login_resets_failed_attempts(client, db_session):
    user = make_user(db_session, "draftsman@ep-platform.com", RoleEnum.draftsman)

    login(client, user.email, "wrong-password")
    login(client, user.email, "wrong-password")
    good = login(client, user.email, "Password123!")
    assert good.status_code == 200

    db_session.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


def test_inactive_user_cannot_login(client, db_session):
    user = make_user(db_session, "disabled@ep-platform.com", RoleEnum.viewer, is_active=False)
    resp = login(client, user.email, "Password123!")
    assert resp.status_code == 403


def test_me_requires_authentication(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user_when_logged_in(client):
    login(client, settings.default_admin_email, settings.default_admin_password)
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == settings.default_admin_email


def test_logout_clears_session(client):
    login(client, settings.default_admin_email, settings.default_admin_password)
    assert client.get("/auth/me").status_code == 200

    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 200

    assert client.get("/auth/me").status_code == 401


def test_expired_token_is_rejected(client):
    expired_payload = {
        "sub": "1",
        "role": "admin",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    expired_token = jwt.encode(expired_payload, settings.secret_key, algorithm=JWT_ALGORITHM)
    client.cookies.set(settings.cookie_name, expired_token)

    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_tampered_token_is_rejected(client):
    client.cookies.set(settings.cookie_name, "not-a-real-jwt")
    resp = client.get("/auth/me")
    assert resp.status_code == 401
