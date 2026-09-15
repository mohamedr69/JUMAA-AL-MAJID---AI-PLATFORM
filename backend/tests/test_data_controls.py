"""Production data controls: foreign keys, clean deletion, masking, the AI
policy, upload content, request ids, backups that restore, exports on record."""

import io
from pathlib import Path

from sqlalchemy import text

import app.migrations as migrations
import app.routers.backups as backups_router
from app.core.config import get_settings
from app.database import engine
from app.models import (
    ActivityEvent,
    BoqCandidate,
    ComplianceStatement,
    ExtractionIssue,
    ExtractionRun,
    Project,
    ProjectBoqItem,
    RoleEnum,
)

from .conftest import login, make_user
from .test_projects import _login_admin, _payload_without_documents

settings = get_settings()


def test_foreign_keys_are_enforced(client, db_session):
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_deleting_a_project_removes_its_rows_child_first(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json=_payload_without_documents("91001")).json()["id"]
    run = ExtractionRun(project_id=pid, kind="design_sheet", document_path="x.pdf", parser_version="t", outcome="VALID")
    run.issues = [ExtractionIssue(code="AMBIGUOUS_OCR", severity="medium", target="t", detail={}, state="open")]
    db_session.add(run)
    db_session.commit()
    db_session.add(ProjectBoqItem(project_id=pid, position=0, description="Read", quantity="1", origin="extracted",
                                  extraction_run_id=run.id))
    db_session.add(BoqCandidate(project_id=pid, base_boq_version=0, parser_version="t", lines=[], changes=[], summary={}))
    db_session.add(ComplianceStatement(project_id=pid, kind="prepare", system_code="FAS", spec={"sha256": "x"}, rows=[], summary={}))
    db_session.commit()

    assert client.delete(f"/projects/{pid}").status_code == 204
    db_session.expire_all()
    assert db_session.get(Project, pid) is None
    for model in (ExtractionRun, ExtractionIssue, ProjectBoqItem, BoqCandidate, ComplianceStatement):
        assert db_session.query(model).count() == 0, model.__name__
    assert db_session.query(ActivityEvent).filter_by(action="project.deleted").count() == 1


def test_viewers_see_contact_details_masked(client, db_session):
    _login_admin(client)
    body = {**_payload_without_documents("91002"), "contact_email": "mahammad.bennapade@samanadevelopers.com",
            "contact_phone": "971543079068"}
    pid = client.post("/projects", json=body).json()["id"]
    assert client.get(f"/projects/{pid}").json()["contact_email"] == body["contact_email"]
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    seen = client.get(f"/projects/{pid}").json()
    assert seen["contact_email"] == "m•••@samanadevelopers.com"
    assert seen["contact_phone"].startswith("9715") and "3079068" not in seen["contact_phone"]
    assert all("bennapade" not in (p["contact_email"] or "") for p in client.get("/projects").json())


def test_a_project_can_block_ai_and_every_ai_feature_says_so(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json={**_payload_without_documents("91003"), "ai_policy": "blocked"}).json()["id"]
    project = client.get(f"/projects/{pid}").json()
    assert project["ai_policy"] == "blocked"
    extraction = client.get(f"/projects/{pid}/extraction").json()
    assert extraction["ai_enabled"] is False and "switched off" in extraction["ai_status"]
    assist = client.post(f"/projects/{pid}/extraction/assist")
    assert assist.status_code == 409 and "switched off" in assist.json()["detail"]
    check = client.post(f"/projects/{pid}/details-check", json={"details": {"systems": []}})
    assert check.status_code == 409 and "switched off" in check.json()["detail"]


def test_an_upload_whose_content_is_not_its_type_is_refused(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _login_admin(client)
    pid = client.post("/projects", json=_payload_without_documents("91004")).json()["id"]
    fake = client.post(f"/projects/{pid}/documents/drf", files={"file": ("DRF.pdf", io.BytesIO(b"MZ\x90\x00 not a pdf"), "application/pdf")})
    assert fake.status_code == 400 and "not a PDF" in fake.json()["detail"]
    real = client.post(f"/projects/{pid}/documents/drf", files={"file": ("DRF.pdf", io.BytesIO(b"%PDF-1.7\n%%EOF"), "application/pdf")})
    assert real.status_code == 200


def test_every_response_carries_a_request_id(client):
    response = client.get("/health")
    assert len(response.headers["X-Request-ID"]) >= 8
    assert client.get("/health", headers={"X-Request-ID": "trace-abc-12345"}).headers["X-Request-ID"] == "trace-abc-12345"
    assert client.get("/health", headers={"X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"] != "bad id with spaces"


def test_a_backup_restores_with_the_projects_and_revisions_it_holds(client, db_session, tmp_path, monkeypatch):
    database = tmp_path / "live.db"
    from sqlalchemy import create_engine

    file_engine = create_engine(f"sqlite:///{database.as_posix()}")
    migrations.upgrade_to_head(file_engine)
    with file_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (email, full_name, hashed_password, role, is_active, failed_login_attempts, "
                                "created_at, updated_at) VALUES ('a@b.c', 'A', 'x', 'admin', 1, 0, '2026-01-01', '2026-01-01')"))
        connection.execute(text("INSERT INTO projects (ep_number, status, created_by_id, created_at, updated_at, separate_ve_panel, "
                                "boq_version, details_version, ai_policy) VALUES ('30208', 'active', 1, '2026-01-01', '2026-01-01', 0, 0, 0, 'allowed')"))
        connection.execute(text("INSERT INTO project_boq_revisions (project_id, number, items, issued_by_id, issued_at) "
                                "VALUES (1, 0, '[]', 1, '2026-01-01')"))
    monkeypatch.setattr(migrations, "BACKUPS_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backups_router, "BACKUPS_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backups_router, "engine", file_engine)
    monkeypatch.setattr(backups_router, "backup_sqlite", lambda eng, label: migrations.backup_sqlite(eng, label, root=tmp_path / "backups"))

    _login_admin(client)
    taken = client.post("/admin/backups")
    assert taken.status_code == 201, taken.text
    listed = client.get("/admin/backups").json()
    assert [b["name"] for b in listed] == [taken.json()["name"]]
    verified = client.post(f"/admin/backups/{taken.json()['name']}/verify").json()
    assert verified["restores"] is True and verified["integrity"] == "ok"
    assert verified["counts"]["projects"] == 1 and verified["latest_revision"] == {"ep_number": "30208", "number": 0}
    assert client.post("/admin/backups/..%2Fsecrets.db/verify").status_code in (400, 404)
    file_engine.dispose()


def test_exports_are_on_the_record(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json=_payload_without_documents("91005")).json()["id"]
    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "description": "CPU", "quantity": "1"}])
    assert client.get(f"/projects/{pid}/boq/export.xlsx").status_code == 200
    assert client.get(f"/projects/{pid}/submittals/export.xlsx").status_code == 200
    actions = {e.action for e in db_session.query(ActivityEvent).filter(ActivityEvent.project_id == pid)}
    assert {"boq.exported", "submittal.register_exported"} <= actions
