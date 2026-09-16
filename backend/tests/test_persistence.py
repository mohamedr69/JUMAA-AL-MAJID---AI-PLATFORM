"""What a project keeps in the database so its folder is not searched on
every open: where its specifications are, and whether its submittal folder
changed since the last check -- and the data folder a user carries between
PCs."""

from pathlib import Path

from app.core.config import Settings, get_settings
from app.models import Project
from app.services import document_sync
from app.services.spec_finder import SpecMatch

from .conftest import login

settings = get_settings()


def _admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def _project(client, folder: Path, ep="30801") -> int:
    folder.mkdir(parents=True, exist_ok=True)
    return client.post("/projects", json={
        "ep_number": ep, "project_name": "Skyblade", "source_folder_path": str(folder), "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}],
    }).json()["id"]


def test_the_specifications_are_searched_for_once_and_read_from_the_database_after(client, db_session, tmp_path, monkeypatch):
    import app.routers.compliance as compliance_router

    folder = tmp_path / "EP-30801"
    folder.mkdir()
    (folder / "283111 - FIRE DETECTION.pdf").write_bytes(b"%PDF-1.4 spec")
    calls = []

    def fake_find(root, wanted, **kwargs):
        calls.append(root)
        return [SpecMatch(system_code="FAS", path="283111 - FIRE DETECTION.pdf", filename="283111 - FIRE DETECTION.pdf",
                          section_no="283111", heading="FIRE DETECTION", first_page=1, last_page=12, snippet="...")], []

    monkeypatch.setattr(compliance_router, "find_specs", fake_find)
    monkeypatch.setattr(compliance_router, "_verify_matches", lambda project, matches: None)
    _admin(client)
    project_id = _project(client, folder)

    first = client.get(f"/projects/{project_id}/compliance").json()
    assert len(calls) == 1 and first["from_database"] is False and first["found_at"]
    assert first["systems"][0]["specs"][0]["filename"] == "283111 - FIRE DETECTION.pdf"

    # Opened again: the location comes from the database, the folder is not searched.
    compliance_router._cache.clear()
    again = client.get(f"/projects/{project_id}/compliance").json()
    assert len(calls) == 1 and again["from_database"] is True and again["found_at"] == first["found_at"]
    assert again["systems"][0]["specs"][0]["path"] == first["systems"][0]["specs"][0]["path"]

    # Asked to search again: it does, and the record is renewed.
    fresh = client.get(f"/projects/{project_id}/compliance?refresh=true").json()
    assert len(calls) == 2 and fresh["from_database"] is False

    # The file moved: the stored location is no longer good, so the folder is searched.
    (folder / "283111 - FIRE DETECTION.pdf").rename(folder / "moved.pdf")
    compliance_router._cache.clear()
    client.get(f"/projects/{project_id}/compliance").json()
    assert len(calls) == 3


def test_the_submittal_folder_is_checked_again_only_when_it_changed(client, db_session, tmp_path, monkeypatch):
    import app.routers.jobs as jobs_router
    import app.routers.submittal as submittal_router
    from app.ai import provider as provider_module
    from app.ai.provider import RecordingProvider
    from tests.test_submittal import _reading, _submittal_form

    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {})
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    provider = RecordingProvider([_reading("BBY006-GME-MAS-EL-FA-0001", 0)])
    provider_module.set_provider(provider)
    try:
        _admin(client)
        folder = tmp_path / "EP-30802"
        _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
        project_id = _project(client, folder, ep="30802")

        before = client.get(f"/projects/{project_id}/submittals/map").json()
        assert before["checked_at"] is None

        assert client.post(f"/projects/{project_id}/submittals/scan").status_code == 202
        after = client.get(f"/projects/{project_id}/submittals/map").json()
        assert after["checked_at"] and after["listing_files"] == 1 and provider.calls == 1

        # A submittal received: opening the map reads the database (nothing
        # changes, nothing is read); the shared sync is what notices the file.
        _submittal_form(folder / "08- approval" / "MS" / "form.pdf", reply="(A) Approved")
        opened = client.get(f"/projects/{project_id}/submittals/map").json()
        assert opened["checked_at"] == after["checked_at"] and provider.calls == 1
        assert document_sync.changes_since(db_session, db_session.get(Project, project_id)) is True
    finally:
        provider_module.set_provider(None)


def test_a_data_root_gathers_the_database_uploads_backups_and_caches(tmp_path, monkeypatch):
    # The test process sets these for the suite; a fresh Settings must not see them.
    for name in ("DATABASE_URL", "UPLOADS_ROOT", "CACHE_ROOT", "DATA_ROOT", "BACKUPS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "EP Platform"
    s = Settings(data_root=str(root), secret_key="x")
    assert s.database_url == "sqlite:///" + (root / "ep_platform.db").as_posix()
    assert Path(s.uploads_root) == root / "uploads"
    assert Path(s.cache_root) == root / ".cache"
    assert Path(s.backups_root) == root / "backups"
    # Set explicitly, a location is kept as set.
    s = Settings(data_root=str(root), database_url="sqlite:///elsewhere.db", uploads_root="here", secret_key="x")
    assert s.database_url == "sqlite:///elsewhere.db" and s.uploads_root == "here"
    # Without it, nothing moves.
    s = Settings(secret_key="x")
    assert s.data_root is None and s.database_url == "sqlite:///./ep_platform.db" and s.backups_root.endswith("backups")
