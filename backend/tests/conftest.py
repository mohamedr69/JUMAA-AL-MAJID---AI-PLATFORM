import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MAX_FAILED_LOGIN_ATTEMPTS"] = "3"
os.environ["LOCKOUT_MINUTES"] = "15"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
# No real library: the tests build their own. The company library is pointed
# at an empty temporary folder and the archive fallback is turned off, so
# app startup never indexes anything -- without this, a developer who has
# synced the real Edwards library would have the whole suite read it, and
# the live-archive tests would stop being opt-in.
os.environ["DATASHEET_LIBRARIES"] = "{}"
# No archive either: a test that needs one points PROJECTS_ROOT at its own
# tmp_path. Without this the developer's .env archive leaked in, and the
# source-folder validation on project creation checked test paths against
# a real OneDrive tree.
os.environ["PROJECTS_ROOT"] = ""
os.environ["PROJECTS_ROOT_AUTODETECT"] = "false"
os.environ["ARCHIVE_DATASHEET_LIBRARIES"] = "{}"
os.environ["ARCHIVE_SUBMITTAL_LIBRARY"] = ""
os.environ["LIBRARY_ROOT"] = tempfile.mkdtemp(prefix="ep-test-library-")
os.environ["CACHE_ROOT"] = tempfile.mkdtemp(prefix="ep-test-cache-")
# A library built during a test is read back in the same test: no throttle.
os.environ["LIBRARY_RESCAN_SECONDS"] = "0"
# The repository's own `data base` folder is the developer's knowledge base,
# not the tests': they build their own, and nothing imports on startup.
os.environ["COMPLIANCE_KNOWLEDGE_SOURCE"] = ""
os.environ["COMPLIANCE_KNOWLEDGE_AUTODETECT"] = "false"
os.environ["COMPLIANCE_KNOWLEDGE_IMPORT_ON_START"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import RoleEnum, User


def reset_database() -> None:
    """Empty the database, migration history included, so app startup
    rebuilds it through the migrations -- the tests then run against the
    schema the migrations produce, not the one the models describe."""
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture()
def client():
    reset_database()
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
