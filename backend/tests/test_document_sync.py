"""The document index and the one sync that keeps it: the folder is walked
once, only new or changed content is read, what depends on a changed
document goes stale, and the pages read the database."""

from pathlib import Path

import pymupdf
import pytest

import app.routers.jobs as jobs_router
from app.ai import provider as provider_module
from app.ai.provider import RecordingProvider
from app.core.config import get_settings
from app.models import DocumentDependency, Project, ProjectDocument
from app.services import document_sync

from .conftest import login
from .test_submittal import _reading, _submittal_form

settings = get_settings()

FORM_TEXT = """Materials Submittal Form
MAS Reference No.
BBY006-GME-MAS-EL-FA-0002
MAS Rev.: 00
Material Submittal for Fire Alarm Sounders
"""


def _pdf(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), text, fontsize=8)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def ai(monkeypatch):
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    yield provider
    provider_module.set_provider(None)
    monkeypatch.setattr(settings, "ai_enabled", False)


def _project(client, folder: Path, ep="30811") -> int:
    folder.mkdir(parents=True, exist_ok=True)
    login(client, settings.default_admin_email, settings.default_admin_password)
    return client.post("/projects", json={"ep_number": ep, "project_name": "Skyblade", "source_folder_path": str(folder),
                                          "design_sheets": []}).json()["id"]


def test_the_folder_is_read_once_and_only_changes_after(client, db_session, tmp_path, ai):
    folder = tmp_path / "EP-30811"
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
    _pdf(folder / "05- Drawings" / "L01.pdf", "Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout")
    _pdf(folder / "09- Other" / "catalogue.pdf", "A product catalogue with nothing to register")
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, folder)

    # Before the first sync the pages have nothing, and say so, without touching the folder.
    logs = client.get(f"/projects/{project_id}/logs").json()
    assert logs["synced_at"] is None and logs["material_submittals"] == [] and any("not been synced" in w for w in logs["warnings"])
    assert client.get(f"/projects/{project_id}/documents/status").json()["synced_at"] is None

    started = client.post(f"/projects/{project_id}/jobs/sync-documents")
    assert started.status_code == 202, started.text
    result = started.json()["result"]
    assert (result["files"], result["new"], result["unchanged"], result["read_by_ai"]) == (3, 3, 0, 1)
    assert ai.calls == 1

    rows = {r.relative_path: r for r in db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id)}
    assert rows["03- MS/01- FA/form.pdf"].role == "submittal_form" and rows["03- MS/01- FA/form.pdf"].reading_id
    assert rows["03- MS/01- FA/form.pdf"].reference == "BBY006-GME-MAS-EL-FA-0001" and rows["03- MS/01- FA/form.pdf"].status == "UR"
    assert rows["05- Drawings/L01.pdf"].role == "document" and rows["09- Other/catalogue.pdf"].state == "fresh"
    assert all(r.sha256 and r.mtime and r.index_version for r in rows.values())

    # The pages read the index.
    logs = client.get(f"/projects/{project_id}/logs").json()
    assert logs["synced_at"] and logs["scanning"] is False
    assert [m["reference"] for m in logs["material_submittals"]] == ["BBY006-GME-MAS-EL-FA-0001"]
    status = client.get(f"/projects/{project_id}/documents/status").json()
    assert status["documents"] == 3 and status["by_state"] == {"fresh": 3} and status["stale"] == []
    submittal_map = client.get(f"/projects/{project_id}/submittals/map").json()
    assert submittal_map["checked_at"] and submittal_map["submittals"] == 1

    # Synced again with nothing changed: nothing is read, nothing is asked.
    again = client.post(f"/projects/{project_id}/jobs/sync-documents").json()["result"]
    assert (again["new"], again["changed"], again["unchanged"], again["read_by_ai"]) == (0, 0, 3, 0)
    assert ai.calls == 1

    # The consultant's reply arrives on the form: that one file is read again, the others are not.
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf", reply="(A) Approved")
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0, "approved", code="A")]
    third = client.post(f"/projects/{project_id}/jobs/sync-documents").json()["result"]
    assert (third["changed"], third["unchanged"], third["read_by_ai"]) == (1, 2, 1)
    assert ai.calls == 2
    row = db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id,
                                                   ProjectDocument.role == "submittal_form").one()
    db_session.refresh(row)
    assert row.status == "A"
    cell = client.get(f"/projects/{project_id}/submittals/map").json()["systems"][0]["rows"][0]["cells"]["R0"]
    assert cell["status"] == "A"

    # A file removed: its row says so; its data is kept, not deleted.
    (folder / "09- Other" / "catalogue.pdf").unlink()
    fourth = client.post(f"/projects/{project_id}/jobs/sync-documents").json()["result"]
    assert fourth["removed"] == 1
    gone = db_session.query(ProjectDocument).filter(ProjectDocument.relative_path == "09- Other/catalogue.pdf").one()
    db_session.refresh(gone)
    assert gone.state == "removed"


def test_a_form_the_title_block_reader_cannot_read_still_reaches_the_log(client, db_session, tmp_path, ai):
    """A submittal package is a scan of a form in front of a hundred
    datasheets: the document-control reader can come back from it with
    nothing. The model read the form, so the log lists it all the same --
    otherwise the project reports no submittal on file while holding one."""
    folder = tmp_path / "EP-30880"
    form = folder / "02- Material Submittals" / "FA" / "R0" / "EP-30880 - Material Submittal - FA - R0.pdf"
    _pdf(form, "scan")  # a scan: no text layer for the reader to work from
    ai.answers = [_reading("EP-30880", 0, title="Fire Alarm, Voice Evacuation & Fire Telephone System")]
    project_id = _project(client, folder, ep="30880")

    assert client.post(f"/projects/{project_id}/jobs/sync-documents").status_code == 202
    row = db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id,
                                                   ProjectDocument.role == "submittal_form").one()
    db_session.refresh(row)
    # Nothing was read off the page; what the model read stands in for it.
    assert [r for r in row.extracted["records"] if r["source"] == "submittal form"]

    logs = client.get(f"/projects/{project_id}/logs").json()
    assert [(m["reference"], m["revision"], m["system_code"]) for m in logs["material_submittals"]] == [("EP-30880", "R0", "FAS")]
    # The entry points at the form in the project folder, for the page to open.
    assert logs["material_submittals"][0]["path"] == "02- Material Submittals/FA/R0/EP-30880 - Material Submittal - FA - R0.pdf"


def test_a_log_entry_whose_reference_changed_is_not_reported_stale(client, db_session, tmp_path, ai):
    """A package the platform files is known first by the reference it was
    given (EP-30880-MAS-FA) and then by the one read off the form
    (EP-30880). Once the form has been read again the log is in step, and
    the page must not go on saying its source document changed."""
    folder = tmp_path / "EP-30880"
    form = folder / "02- Material Submittals" / "FA" / "R0" / "EP-30880 - Material Submittal - FA - R0.pdf"
    _pdf(form, "scan")
    ai.answers = [_reading("EP-30880-MAS-FA", 0)]
    project_id = _project(client, folder, ep="30880")
    client.post(f"/projects/{project_id}/jobs/sync-documents")

    # The form changes, and this time the model reads the form's own number.
    _pdf(form, "scan 2")
    ai.answers = [_reading("EP-30880", 0)]
    client.post(f"/projects/{project_id}/jobs/sync-documents")

    stale = client.get(f"/projects/{project_id}/documents/status").json()["stale"]
    assert [entry for entry in stale if entry["dependent_type"] == "log"] == []


def test_a_changed_design_sheet_marks_the_boq_stale_and_a_new_specification_the_compliance_page(client, db_session, tmp_path, ai):
    import app.routers.projects as projects_router
    from app.services.design_sheet_extractor import ExtractedBoqLine

    folder = tmp_path / "EP-30812"
    folder.mkdir()
    sheet = _pdf(folder / "01- Scan" / "EP-30812 FAS Design.pdf", "Design sheet")
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={
        "ep_number": "30812", "project_name": "Skyblade", "source_folder_path": str(folder),
        "design_sheets": [{"system_code": "FAS", "document_path": str(sheet)}],
    }).json()["id"]
    # The BOQ read (stubbed sheet lines) records what it stands on.
    lines = [ExtractedBoqLine(catalog_no="4-CPU", description="CPU", quantity="1", group_heading="Panel", confidence=90.0, page=1)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(projects_router, "extract_boq_lines", lambda path: list(lines))
        assert len(client.post(f"/projects/{project_id}/boq/ensure").json()["items"]) == 1
    client.post(f"/projects/{project_id}/jobs/sync-documents")
    project = db_session.get(Project, project_id)
    document_sync.register_intake_dependencies(db_session, project)
    assert db_session.query(DocumentDependency).filter(DocumentDependency.project_id == project_id,
                                                        DocumentDependency.dependent_type == "boq").count() == 1
    assert client.get(f"/projects/{project_id}/documents/status").json()["stale"] == []

    # The sheet is replaced: the BOQ built from it is stale, and nothing was re-read by itself.
    _pdf(sheet, "Design sheet, revised")
    result = client.post(f"/projects/{project_id}/jobs/sync-documents").json()["result"]
    assert result["changed"] == 1 and result["read_by_ai"] == 0 and ai.calls == 0
    stale = client.get(f"/projects/{project_id}/documents/status").json()["stale"]
    assert [s["dependent_type"] for s in stale] == ["boq"] and "FAS Design.pdf" in stale[0]["source"]

    # A specification arrives: the compliance page's stored search is dropped, so it searches again.
    db_session.refresh(project)
    project.spec_locations, project.specs_found_at = [], project.created_at
    db_session.commit()
    _pdf(folder / "02- Spec" / "283111 - FIRE DETECTION AND ALARM.pdf", "SECTION 28 31 11 FIRE DETECTION AND ALARM")
    client.post(f"/projects/{project_id}/jobs/sync-documents")
    db_session.refresh(project)
    assert project.spec_locations is None and project.specs_found_at is None
    assert any(s["dependent_type"] == "boq" for s in client.get(f"/projects/{project_id}/documents/status").json()["stale"])


def test_a_read_that_fails_keeps_the_previous_result_and_is_marked(client, db_session, tmp_path, ai, monkeypatch):
    folder = tmp_path / "EP-30813"
    _submittal_form(folder / "03- MS" / "form.pdf")
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, folder, ep="30813")
    client.post(f"/projects/{project_id}/jobs/sync-documents")
    row = db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).one()
    first = dict(row.extracted)

    _submittal_form(folder / "03- MS" / "form.pdf", reply="(C) Revise & Resubmit")

    def broken(*args, **kwargs):
        raise RuntimeError("the reader fell over")

    monkeypatch.setattr(document_sync.document_control, "_read_pdf", broken)
    result = client.post(f"/projects/{project_id}/jobs/sync-documents").json()["result"]
    assert result["failed"] == 1 and result["changed"] == 1
    db_session.refresh(row)
    assert row.state == "failed" and "fell over" in row.error and row.extracted == first
    status = client.get(f"/projects/{project_id}/documents/status").json()
    assert status["by_state"] == {"failed": 1} and status["failed"][0]["path"] == "03- MS/form.pdf"


def test_a_form_deleted_from_the_folder_leaves_the_map_not_the_sync(client, db_session, tmp_path, ai):
    """A submittal form removed from the folder (a filed package deleted by
    hand) is marked removed and left off the map; the sync still finishes."""
    folder = tmp_path / "EP-30814"
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
    _submittal_form(folder / "03- MS" / "02- ELS" / "form.pdf", reference="BBY006-GME-MAS-EL-EL-0001")
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0), _reading("BBY006-GME-MAS-EL-EL-0001", 0)]
    project_id = _project(client, folder, ep="30814")
    first = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert first["status"] == "succeeded", first
    assert client.get(f"/projects/{project_id}/submittals/map").json()["submittals"] == 2

    (folder / "03- MS" / "02- ELS" / "form.pdf").unlink()
    second = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert second["status"] == "succeeded", second.get("error")
    assert second["result"]["removed"] == 1 and second["result"]["forms_changed"] is True
    gone = db_session.query(ProjectDocument).filter(ProjectDocument.relative_path == "03- MS/02- ELS/form.pdf").one()
    db_session.refresh(gone)
    assert gone.state == "removed"
    submittal_map = client.get(f"/projects/{project_id}/submittals/map").json()
    assert submittal_map["submittals"] == 1
    assert [r["reference"] for s in submittal_map["systems"] for r in s["rows"]] == ["BBY006-GME-MAS-EL-FA-0001"]
    # The register follows: the reference with no form left is removed from it.
    assert [s["reference"] for s in client.get(f"/projects/{project_id}/submittals").json()["items"]] == ["BBY006-GME-MAS-EL-FA-0001"]
