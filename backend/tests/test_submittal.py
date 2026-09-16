"""The material submittal: the BOQ's materials paired with their datasheets."""

import pymupdf

from app.core.config import get_settings
from app.services.datasheet_library import DatasheetLibrary

from .conftest import login, make_user

settings = get_settings()


def _library(tmp_path) -> DatasheetLibrary:
    root = tmp_path / "EST4"
    if root.exists():
        return DatasheetLibrary("EDWARDS", root)
    root.mkdir()
    for name, text in {"01- 4-CPU.pdf": "D A T A S H E E T E85014-0010", "manual.pdf": "Mentions SIGA-PS in passing"}.items():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=9)
        if name == "manual.pdf":
            page.insert_text((72, 100), "SIGA-PS", fontsize=9)
        doc.save(root / name)
        doc.close()
    return DatasheetLibrary("EDWARDS", root)


def _project(client) -> int:
    project_id = client.post("/projects", json={"ep_number": "30784", "project_name": "Skyblade", "design_sheets": []}).json()["id"]
    panel = "EST4 Main Fire Alarm Control Panel"
    lines = [
        {"system_code": "FAS", "group_heading": panel, "description": "Panel. Includes:", "quantity": "1"},
        {"system_code": "FAS", "group_heading": panel, "catalog_no": "4-CPU", "description": "CPU", "quantity": "1", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "catalog_no": "4-CPU", "description": "CPU, spare", "quantity": "2", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "catalog_no": "SIGA-PS", "description": "Smoke detector", "quantity": "Lot", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "catalog_no": "4-NOSUCH", "description": "Unknown module", "quantity": "3", "manufacturer": "EDWARDS"},
        {"system_code": "EML", "catalog_no": "SL210DI", "description": "Pictogram", "quantity": "33", "manufacturer": "MENVIER"},
    ]
    assert client.put(f"/projects/{project_id}/boq", json=lines).status_code == 200
    return project_id


def test_materials_are_paired_with_their_datasheets(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)

    body = client.get(f"/projects/{project_id}/submittal/materials").json()
    assert body["systems"] == ["ELS", "FAS"]
    assert (len(body["items"]), body["with_datasheet"]) == (4, 2)
    items = {i["part_no"]: i for i in body["items"]}

    # One material per part, its quantity summed over the BOQ's lines.
    assert items["4-CPU"]["quantity"] == 3
    assert items["4-CPU"]["groups"] == ["EST4 Main Fire Alarm Control Panel"]
    assert items["4-CPU"]["datasheet_filename"] == "01- 4-CPU.pdf"
    assert items["4-CPU"]["document_no"] == "E85014-0010"
    assert items["4-CPU"]["datasheet_named_for_part"] is True

    # A document that only mentions the part is offered, but marked as such.
    assert (items["SIGA-PS"]["datasheet_filename"], items["SIGA-PS"]["datasheet_named_for_part"]) == ("manual.pdf", False)
    # "Lot" is not a number: no quantity rather than a wrong one.
    assert items["SIGA-PS"]["quantity"] is None
    assert items["4-NOSUCH"]["datasheet_path"] is None


def test_materials_need_a_login(client, tmp_path):
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)
    client.post("/auth/logout")
    assert client.get(f"/projects/{project_id}/submittal/materials").status_code == 401


# --- the register ----------------------------------------------------------------


def _register(client, project_id):
    return client.get(f"/projects/{project_id}/submittals").json()


def test_the_register_starts_from_what_the_boq_implies(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)

    body = _register(client, project_id)
    assert body["items"] == [] and body["counts"]["total"] == 0
    # One suggestion per system with materials, with its materials counted.
    assert [(s["title"], s["system_code"], s["materials"], s["materials_with_datasheet"]) for s in body["suggestions"]] == [
        ("Emergency Lighting System", "ELS", 1, 0),
        ("Fire Alarm System", "FAS", 3, 2),
    ]

    created = client.post(
        f"/projects/{project_id}/submittals",
        json={"title": "Fire Alarm System", "system_code": "FAS", "manufacturer": "Edwards (EST4)"},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["revision"], item["status"], item["created_by"]) == ("R00", "not_submitted", "Platform Administrator")
    assert (item["materials"], item["materials_with_datasheet"]) == (3, 2)

    body = _register(client, project_id)
    assert body["counts"] == {"not_submitted": 1, "under_review": 0, "approved": 0, "rejected": 0, "total": 1}
    # The system it covers is no longer suggested.
    assert [s["system_code"] for s in body["suggestions"]] == ["ELS"]
    assert [(e["kind"], e["detail"], e["by"]) for e in body["activity"]] == [
        ("created", "Created R00", "Platform Administrator")
    ]


def test_status_and_revision_changes_are_kept_as_history(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)
    sid = client.post(f"/projects/{project_id}/submittals", json={"title": "Fire Alarm System", "system_code": "FAS"}).json()["id"]

    assert client.patch(f"/projects/{project_id}/submittals/{sid}", json={"status": "under_review"}).status_code == 200
    rejected = client.patch(f"/projects/{project_id}/submittals/{sid}", json={"status": "rejected", "note": "Missing test certificates"})
    assert rejected.json()["status"] == "rejected"
    approved = client.patch(f"/projects/{project_id}/submittals/{sid}", json={"revision": "R01", "status": "approved"})
    assert (approved.json()["revision"], approved.json()["status"]) == ("R01", "approved")

    body = _register(client, project_id)
    assert body["counts"]["approved"] == 1
    # Newest first, and the rejection still on the record.
    assert [e["detail"] for e in body["activity"]] == [
        "Approved (was rejected)",
        "Revision R01 (was R00)",
        "Rejected (was under review)",
        "Under Review (was not submitted)",
        "Created R00",
    ]
    assert body["items"][0]["note"] == "Missing test certificates"


def test_the_register_exports_and_is_editor_only(client, db_session, tmp_path, monkeypatch):
    from io import BytesIO

    import openpyxl

    import app.routers.submittal as submittal_router
    from app.models import RoleEnum

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)
    sid = client.post(f"/projects/{project_id}/submittals", json={"title": "Fire Alarm System", "system_code": "FAS"}).json()["id"]

    resp = client.get(f"/projects/{project_id}/submittals/export.xlsx")
    wb = openpyxl.load_workbook(BytesIO(resp.content))
    assert wb.sheetnames == ["Register", "Materials"]
    assert wb["Register"].cell(row=6, column=2).value == "Fire Alarm System"
    assert wb["Materials"].cell(row=6, column=6).value in ("01- 4-CPU.pdf", "manual.pdf", "Not in the library")

    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert client.get(f"/projects/{project_id}/submittals").status_code == 200
    assert client.post(f"/projects/{project_id}/submittals", json={"title": "X"}).status_code == 403
    assert client.patch(f"/projects/{project_id}/submittals/{sid}", json={"status": "approved"}).status_code == 403
    assert client.delete(f"/projects/{project_id}/submittals/{sid}").status_code == 403


def test_deleting_a_project_takes_its_submittals(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router
    from app.models import ProjectSubmittal, ProjectSubmittalEvent

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = _project(client)
    client.post(f"/projects/{project_id}/submittals", json={"title": "Fire Alarm System", "system_code": "FAS"})
    assert db_session.query(ProjectSubmittal).count() == 1
    assert client.delete(f"/projects/{project_id}").status_code == 204
    assert db_session.query(ProjectSubmittal).count() == 0
    assert db_session.query(ProjectSubmittalEvent).count() == 0


# --- scanning the project folder --------------------------------------------------


def _project_with_folder(client, folder) -> int:
    resp = client.post(
        "/projects",
        json={"ep_number": "30784", "project_name": "Skyblade", "source_folder_path": str(folder), "design_sheets": []},
    )
    return resp.json()["id"]


def _submittal_form(path, reference="BBY006-GME-MAS-EL-FA-0001", revision="00", reply=""):
    from tests.test_submittal_scanner import _form

    return _form(path, reference=reference, revision=revision, reply=reply)


def _reading(reference, revision, status="none", from_consultant=True, title="Material Submittal for Fire Alarm System",
             system="Fire Alarm", code=""):
    return {"is_submittal": True, "reference": reference, "revision": revision, "title": title, "system": system,
            "supplier": "AL ARABIA", "manufacturer": "EDWARDS", "submitted": "20 April 2026",
            "reply": {"present": status != "none", "from_consultant": from_consultant and status != "none", "status": status,
                      "code": code, "consultant": "Al Hilal" if status != "none" else "", "date": "", "evidence": f"stamp {code}" if code else ""}}


def test_the_ai_check_reads_the_forms_into_the_map_and_the_register(client, db_session, tmp_path, monkeypatch):
    """The model reads each form (the OCR read of the stamp is gone); the map
    settles a revision filed twice by the copy carrying the consultant's
    reply; the register follows the map; a second check reads nothing again."""
    import app.routers.jobs as jobs_router
    import app.routers.submittal as submittal_router
    from app.ai import provider as provider_module
    from app.ai.provider import RecordingProvider

    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {})
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    try:
        login(client, settings.default_admin_email, settings.default_admin_password)
        folder = tmp_path / "EP-30784"
        _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf")
        _submittal_form(folder / "03- MS" / "02- EML" / "eml.pdf", reference="BBY006-GME-MAS-EL-LI-0001")
        _submittal_form(folder / "03- MS" / "03- FRC" / "tianjie.pdf", reference="BBY006-GME-MAS-EL-FA-0004", reply="(C) Revise & Resubmit")
        _submittal_form(folder / "08- approval" / "MS" / "FA" / "form.pdf", reply="(B) Approved As Noted")
        project_id = _project_with_folder(client, folder)
        # In the folder's sorted order: the prepared FA copy, the EML form, the
        # FRC form the consultant returned, and the FA copy that came back.
        provider.answers = [
            _reading("BBY006-GME-MAS-EL-FA-0001", 0),
            _reading("BBY006-GME-MAS-EL-LI-0001", 0, title="Material Submittal for Emergency Lighting", system="Emergency lighting"),
            _reading("BBY006-GME-MAS-EL-FA-0004", 0, "resubmit", code="C", title="Material Submittal for Fire Rated Cables", system="FRC"),
            _reading("BBY006-GME-MAS-EL-FA-0001", 0, "approved_as_noted", code="B"),
        ]

        started = client.post(f"/projects/{project_id}/submittals/scan")
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "succeeded" and started.json()["result"]["submittals"] == 3
        assert provider.calls == 4

        body = client.get(f"/projects/{project_id}/submittals/map").json()
        assert body["available"] and body["checked_at"] and body["revisions"] == ["R0"]
        rows = {row["reference"]: row for system in body["systems"] for row in system["rows"]}
        assert rows["BBY006-GME-MAS-EL-FA-0001"]["cells"]["R0"]["status"] == "ANN", "the copy with the consultant's reply wins"
        assert rows["BBY006-GME-MAS-EL-FA-0001"]["cells"]["R0"]["file"].startswith("08- approval")
        assert rows["BBY006-GME-MAS-EL-FA-0001"]["cells"]["R0"]["copies"] == 2
        assert rows["BBY006-GME-MAS-EL-FA-0004"]["cells"]["R0"]["status"] == "RR"
        assert rows["BBY006-GME-MAS-EL-LI-0001"]["cells"]["R0"]["status"] == "UR"
        assert body["actions"] == ["Material submittal required: BBY006-GME-MAS-EL-FA-0004 R0 was returned revise and resubmit; R1 is not filed"]
        assert {s["system_code"] for s in body["systems"]} == {"FAS", "ELS", "FRC"}

        register = {i["reference"]: i for i in client.get(f"/projects/{project_id}/submittals").json()["items"]}
        assert (register["BBY006-GME-MAS-EL-FA-0001"]["status"], register["BBY006-GME-MAS-EL-FA-0001"]["reply_code"]) == ("approved", "B")
        assert register["BBY006-GME-MAS-EL-FA-0001"]["document_path"].startswith("08- approval")
        assert register["BBY006-GME-MAS-EL-FA-0004"]["status"] == "rejected"
        assert register["BBY006-GME-MAS-EL-LI-0001"]["status"] == "under_review"

        # Checked again: every form is read from the database, no call.
        again = client.post(f"/projects/{project_id}/submittals/scan").json()
        assert again["status"] == "succeeded" and provider.calls == 4
        assert again["result"]["reused"] == 4 and again["result"]["unchanged"] == 3

        # The FRC resubmission arrives as R1: read once, under review, and the action is gone.
        _submittal_form(folder / "03- MS" / "03- FRC" / "tianjie R1.pdf", reference="BBY006-GME-MAS-EL-FA-0004", revision="01")
        provider.answers = [_reading("BBY006-GME-MAS-EL-FA-0004", 1, title="Material Submittal for Fire Rated Cables", system="FRC")]
        third = client.post(f"/projects/{project_id}/submittals/scan").json()
        assert third["status"] == "succeeded" and provider.calls == 5
        body = client.get(f"/projects/{project_id}/submittals/map").json()
        frc = next(row for system in body["systems"] for row in system["rows"] if row["reference"] == "BBY006-GME-MAS-EL-FA-0004")
        assert body["revisions"] == ["R0", "R1"] and frc["cells"]["R1"]["status"] == "UR" and frc["cells"]["R0"]["status"] == "RR"
        assert body["actions"] == []
        frc_register = {i["reference"]: i for i in client.get(f"/projects/{project_id}/submittals").json()["items"]}["BBY006-GME-MAS-EL-FA-0004"]
        assert (frc_register["revision"], frc_register["status"]) == ("R01", "under_review")
    finally:
        provider_module.set_provider(None)


def test_scanning_needs_a_reachable_folder_and_an_editor(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router
    from app.models import RoleEnum

    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {})
    monkeypatch.setattr(settings, "ai_enabled", True)
    login(client, settings.default_admin_email, settings.default_admin_password)
    no_folder = client.post("/projects", json={"ep_number": "11111", "design_sheets": []}).json()["id"]
    assert client.post(f"/projects/{no_folder}/submittals/scan").status_code == 409
    missing = _project_with_folder(client, tmp_path / "nope")
    assert client.post(f"/projects/{missing}/submittals/scan").status_code == 503

    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert client.post(f"/projects/{no_folder}/submittals/scan").status_code == 403
