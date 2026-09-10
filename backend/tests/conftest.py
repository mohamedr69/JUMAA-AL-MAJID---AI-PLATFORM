import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MAX_FAILED_LOGIN_ATTEMPTS"] = "3"
os.environ["LOCKOUT_MINUTES"] = "15"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import RoleEnum, User


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_user(
    db,
    email: str,
    role: RoleEnum,
    password: str = "Password123!",
    is_active: bool = True,
) -> User:
    user = User(
        email=email.lower(),
        full_name=f"{role.value} test user",
        hashed_password=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(client, email: str, password: str = "Password123!"):
    return client.post("/auth/login", json={"email": email, "password": password})
