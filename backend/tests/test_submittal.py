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


def test_a_submittal_filed_in_the_folder_shows_in_the_register(client, db_session, tmp_path):
    """A submittal prepared outside the platform and filed in the project
    folder is a submittal. The register that leaves it out reported "no
    material submittal is filed" over one sitting in the folder."""
    from datetime import datetime, timezone
    from app.models import Project, ProjectDocument
    from app.services import document_sync

    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40100"
    root.mkdir()
    created = client.post("/projects", json={
        "ep_number": "40100", "project_name": "Folder Filed", "source_folder_path": str(root),
        "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": False}],
    }).json()
    project = db_session.query(Project).filter(Project.id == created["id"]).one()

    # What the sync leaves behind for a form the model read: a submittal,
    # with no reference on it because nobody has numbered it.
    relative = "02- Material Submittals/FA/R0/Submitted/MS FAVE R00.pdf"
    extracted: dict = {"records": [], "notes": [],
                       "form": {"is_submittal": True, "reference": "", "revision": 0,
                                "title": "Fire Alarm, Voice Evacuation & Fire Telephone",
                                "system_code": "FAS", "reply": {"present": False}}}
    assert document_sync.record_for_the_log(
        extracted, extracted["form"], relative=relative,
        modified=datetime(2026, 8, 25, tzinfo=timezone.utc), ep_number="40100") is True

    db_session.add(ProjectDocument(
        project_id=project.id, role=document_sync.ROLE_SUBMITTAL, path=str(root / relative),
        relative_path=relative, filename="MS FAVE R00.pdf", state=document_sync.FRESH,
        system_code="FAS", extracted=extracted))
    db_session.commit()

    register = client.get(f"/projects/{project.id}/submittals").json()
    assert register["counts"]["total"] == 1
    item = register["items"][0]
    # Named the way the platform names its own packages, so the register
    # keys them alike.
    assert item["reference"] == "EP-40100-MAS-FAS"
    assert item["system_code"] == "FAS"
    # Read-only: the form on the drive is the record.
    assert item["from_folder"] is True
    # And the system no longer reads as one with nothing submitted.
    assert [s["system_code"] for s in register["suggestions"]] == []


def test_an_unnumbered_submittal_needs_the_project_to_name_it():
    """Without the project's number there is nothing to call it, and a
    record with no reference is worse than none: it cannot be matched to
    a revision or a reply."""
    from datetime import datetime, timezone
    from app.services import document_sync

    extracted: dict = {"records": []}
    reading = {"is_submittal": True, "reference": "", "revision": 0, "system_code": "FAS"}
    assert document_sync.record_for_the_log(
        extracted, reading, relative="x.pdf",
        modified=datetime(2026, 1, 1, tzinfo=timezone.utc), ep_number=None) is False
    assert extracted["records"] == []


def test_a_reply_filed_beside_a_submittal_answers_it():
    """The consultant's answer comes back as a scan with no reference on
    it. Where it sits is what ties it to what it answers: a reply under
    `FA/R0` answers the R0 of FA, and the letter the file is filed under
    is the code."""
    from app.services import submittal_replies

    class Row:
        def __init__(self, relative, extracted=None):
            self.relative_path = relative
            self.filename = relative.rsplit("/", 1)[-1]
            self.extracted = extracted or {}

    filed = submittal_replies.on_file([
        Row("02- Material Submittals/FA/R0/Received/23058.90-BHC-MAR-FF-0016_00_C.pdf"),
        Row("02- Material Submittals/FA/R0/Submitted/MS FAVE R00.pdf"),   # not a reply
    ])
    assert len(filed) == 1

    # The R0 of FA is answered; another system's R0 is not.
    assert submittal_replies.for_revision("02- Material Submittals/FA/R0", filed)[0] == "resubmit"
    assert submittal_replies.for_revision("02- Material Submittals/ELS/R0", filed)[0] is None

    # The words win over the file name where a reading has them.
    spoken = submittal_replies.on_file([Row(
        "02- Material Submittals/FA/R1/Received/reply_A.pdf",
        {"form": {"reply": {"evidence": "Approved as noted, work may proceed"}}},
    )])
    assert submittal_replies.for_revision("02- Material Submittals/FA/R1", spoken)[0] == "approved_as_noted"


def test_the_map_shows_a_revision_that_has_been_answered():
    """The map, the register and the logs all read the reply the same
    way, so they cannot disagree about whether a submittal is still under
    review. An answered R0 also asks for R1."""
    from app.ai import submittal_reader
    from app.services import submittal_replies

    class Row:
        relative_path = "02- Material Submittals/FA/R0/Received/x_00_C.pdf"
        filename = "x_00_C.pdf"
        extracted: dict = {}

    readings = [{
        "is_submittal": True, "reference": "EP-1-MAS-FAS", "revision": 0,
        "title": "Fire Alarm", "system_code": "FAS", "manufacturer": "EDWARDS",
        "relative": "02- Material Submittals/FA/R0/Submitted/MS.pdf",
        "modified": "2026-08-25T00:00:00", "in_approval_folder": False,
        "reply": {"present": False, "from_consultant": False, "status": "none"},
    }]
    # Without the filed reply the map can only say "under review".
    plain = submittal_reader.build_map(readings=[dict(readings[0])], ep_number="1")
    assert plain["systems"][0]["rows"][0]["latest_status"] == "UR"

    answered = submittal_reader.build_map(
        readings=[dict(readings[0])], ep_number="1",
        replies=submittal_replies.on_file([Row()]))
    row = answered["systems"][0]["rows"][0]
    assert row["latest_status"] == "RR"
    assert row["cells"]["R0"]["status"] == "RR"
    assert any("R1 is not filed" in action for action in answered["actions"])


def test_the_reply_sheet_is_kept_and_exported(client, db_session, tmp_path):
    """A returned submittal is answered comment by comment. The sheet is
    written, saved, come back to, and exported as the workbook the
    company sends."""
    import io
    import pymupdf
    from openpyxl import load_workbook

    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40200"
    root.mkdir()
    created = client.post("/projects", json={
        "ep_number": "40200", "project_name": "Reply Sheet", "source_folder_path": str(root),
        "design_sheets": [], "consultant": "M/s. Silver Stone",
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": False}],
    }).json()
    base = f"/projects/{created['id']}/submittals/EP-40200-MAS-FAS/R00/reply"

    opened = client.get(base).json()
    assert opened["saved"] is False
    assert opened["consultant"] == "M/s. Silver Stone"
    assert opened["reference"] == "EP-40200-MAS-FAS"
    # Nothing was read from a consultant, so it opens with a row to fill.
    assert len(opened["rows"]) == 1

    rows = [
        {"sn": 1, "comment": "Monitoring panels shall be distributed across each tower.",
         "reply": "Comply", "remark": "As per DCD requirements."},
        {"sn": 2, "comment": "Luminaires in wet areas must be IP65.", "reply": "Comply", "remark": ""},
    ]
    saved = client.put(base, json={"rows": rows}).json()
    assert saved["saved"] is True and len(saved["rows"]) == 2

    # Come back to it: what was written is still there.
    again = client.get(base).json()
    assert again["rows"][0]["remark"] == "As per DCD requirements."
    assert again["saved"] is True

    # And edited again, rather than started over.
    client.put(base, json={"rows": rows[:1]})
    assert len(client.get(base).json()["rows"]) == 1

    export = client.get(f"{base}.xlsx")
    assert export.status_code == 200
    sheet = load_workbook(io.BytesIO(export.content)).active
    assert sheet.cell(row=1, column=1).value == "Reply to Consultant Comments on Fire Alarm submittal"
    assert sheet.cell(row=3, column=1).value == "Ref No : EP-40200-MAS-FAS - R00"
    assert sheet.cell(row=5, column=1).value == "Consultant : M/s. Silver Stone"
    assert sheet.cell(row=6, column=2).value == "Consultant Comments"
    assert sheet.cell(row=7, column=3).value == "Comply"

    # And as the PDF the consultant is given: the same sheet, read rather
    # than edited, which is what the Export button sends.
    as_pdf = client.get(f"{base}.pdf")
    assert as_pdf.status_code == 200
    assert as_pdf.headers["content-type"] == "application/pdf"
    assert as_pdf.content[:5] == b"%PDF-"
    page = pymupdf.open(stream=as_pdf.content, filetype="pdf")[0].get_text()
    assert "Reply to Consultant Comments on Fire Alarm submittal" in page
    assert "EP-40200-MAS-FAS - R00" in page
    assert "M/s. Silver Stone" in page
    assert "Monitoring panels shall be distributed" in " ".join(page.split())


def test_saving_a_reply_does_not_move_the_submittal_on():
    """Writing the answer is not filing the next revision: what the
    consultant said still stands until that revision appears."""
    from app.services import reply_sheet

    # The sheet opens from the consultant's own words, split to answer.
    rows = reply_sheet.seed_rows("COO of the products is not as per the vendor list. Re-submit the document.")
    assert [r["sn"] for r in rows] == [1, 2]
    assert rows[0]["reply"] == "Comply"
    # Nothing said: one row to fill rather than an invented one.
    assert reply_sheet.seed_rows(None) == [{"sn": 1, "comment": "", "reply": "Comply", "remark": ""}]
    # The company's name for the reply column, not the salutation.
    assert reply_sheet.supplier_name("M/S. ALARABIA FOR SAFETY AND SECURITY LLC.") == "Alarabia For Safety And Security"
    assert reply_sheet.supplier_name(None) == reply_sheet.DEFAULT_SUPPLIER


def test_a_long_reply_sheet_carries_its_headings_on_to_every_page():
    """Forty comments run on to more than one page, and a page of bare
    rows would reach the consultant with no idea what the columns are."""
    import pymupdf

    from app.services import reply_sheet

    rows = [{"sn": n, "comment": f"Comment {n}: " + "the submitted document does not comply. " * 4,
             "reply": "Comply", "remark": "Revised and attached."} for n in range(1, 41)]
    content = reply_sheet.pdf(project_name="Tower", reference="EP-1-MAS-FAS", revision="R00",
                              system_title="Fire Alarm", manufacturer="EDWARDS",
                              consultant="SSH", supplier=None, rows=rows)
    document = pymupdf.open(stream=content, filetype="pdf")
    assert document.page_count > 1
    pages = [page.get_text() for page in document]
    assert all("Consultant Comments" in page and "REMARKS" in page for page in pages)
    # The heading block belongs to the first page only.
    assert sum("MANUFACTURER" in page for page in pages) == 1
    # Every comment is there, and each one whole rather than cut in two.
    whole = " ".join(" ".join(pages).split())
    assert all(f"Comment {n}:" in whole for n in (1, 20, 40))


def test_a_comment_written_with_angle_brackets_is_text_not_markup():
    """The comments and remarks are typed by an engineer and the page is
    built from HTML, so what they write is escaped rather than obeyed."""
    import unicodedata

    import pymupdf

    from app.services import reply_sheet

    rows = [{"sn": 1, "comment": "Use <b>IP65</b> fittings & seals", "reply": "Comply",
             "remark": "R&D confirmed <see attached>"}]
    content = reply_sheet.pdf(project_name="T", reference="EP-1-MAS-FAS", revision="R00",
                              system_title="Fire Alarm", manufacturer=None, consultant=None,
                              supplier=None, rows=rows)
    # The PDF font sets "fi" as one glyph, so the text comes back with
    # ligatures in it; NFKC puts them back to the letters written.
    drawn = pymupdf.open(stream=content, filetype="pdf")[0].get_text()
    page = " ".join(unicodedata.normalize("NFKC", drawn).split())
    assert "Use <b>IP65</b> fittings & seals" in page
    assert "R&D confirmed <see attached>" in page


def test_a_remark_written_over_several_lines_keeps_them():
    """The cells on screen are textareas, so a remark arrives with real
    line breaks in it. They are the engineer's own paragraphing and the
    PDF keeps them rather than running the lines together."""
    import pymupdf

    from app.services import reply_sheet

    rows = [{"sn": 1, "comment": "First point." + chr(10) + "Second point.", "reply": "Comply",
             "remark": "Letter attached." + chr(10) + "Datasheet revised."}]
    content = reply_sheet.pdf(project_name="T", reference="EP-1-MAS-FAS", revision="R00",
                              system_title="Fire Alarm", manufacturer=None, consultant=None,
                              supplier=None, rows=rows)
    drawn = pymupdf.open(stream=content, filetype="pdf")[0].get_text().splitlines()
    assert "First point." in drawn and "Second point." in drawn
    assert "Letter attached." in drawn and "Datasheet revised." in drawn


def test_a_resubmission_is_not_answered_by_the_comments_it_answers():
    """An R1 form prints the consultant's remarks on R0 -- that is what
    it is answering -- and the reader takes them off the page. Read
    plainly that marks R1 'revise and resubmit' before the consultant
    has seen it. The comments name the revision they are on, and
    nothing has come back into R1's Received folder, so R1 is still
    under review and R0 keeps the answer that is really its own."""
    from app.ai import submittal_reader
    from app.services import submittal_replies

    class Row:
        def __init__(self, relative):
            self.relative_path = relative
            self.filename = relative.rsplit("/", 1)[-1]
            self.extracted: dict = {}

    # As the reader stored it on EP-29387.
    evidence = ("Employer's Representative's Consultant's Comments (on the attached MAR sheet, Reference No. 23058.90-BHC-GEC-SSH-MAR-FF-0016, Revision 0, dated 02.09.2026): 'COO of majority of the products is not as per the project Vendor list. Re-submit the document with full compliance to the project Specifications for further review.'")
    def form(revision, reply):
        return {"is_submittal": True, "reference": "EP-1-MAS-FAS", "revision": revision,
                "title": "Fire Alarm", "system_code": "FAS", "manufacturer": "EDWARDS",
                "relative": f"02- Material Submittals/FA/R{revision}/Submitted/MS R{revision}.pdf",
                "modified": "2026-09-24T00:00:00", "in_approval_folder": False, "reply": reply}

    readings = [
        form(0, {"present": False, "from_consultant": False, "status": "none"}),
        form(1, {"present": True, "from_consultant": True, "status": "resubmit", "code": "C",
                 "consultant": "M/s SSHIC", "date": "19/09/26", "evidence": evidence}),
    ]
    # Only R0 was answered: R1's Received folder is empty.
    filed = submittal_replies.on_file([Row("02- Material Submittals/FA/R0/Received/x_00_C.pdf")])
    built = submittal_reader.build_map(readings=readings, ep_number="1", replies=filed)
    cells = built["systems"][0]["rows"][0]["cells"]
    assert cells["R0"]["status"] == "RR"
    assert cells["R1"]["status"] == "UR", "R1 has not been answered"
    assert built["systems"][0]["rows"][0]["latest_status"] == "UR"
    # Nothing of the earlier answer is left printed beside R1.
    assert cells["R1"]["reply_code"] == "" and cells["R1"]["evidence"] == ""
    assert cells["R1"]["consultant"] == ""
    # And it does not ask for an R2 over a revision no one has answered.
    assert not any("R2" in action for action in built["actions"])


def test_comments_naming_this_revision_are_its_own_answer():
    """The check is against the revision the comments name, not against
    the form carrying them: a reply on R1 still answers R1."""
    from app.services import submittal_replies

    on_this_one = {"revision": 1,
                   "relative": "02- Material Submittals/FA/R1/Submitted/MS R1.pdf",
                   "reply": {"present": True, "from_consultant": True, "status": "resubmit",
                             "evidence": "Comments on MAR sheet, Revision 1, dated 02.10.2026"}}
    assert submittal_replies.carried_over(on_this_one, []) is False
    assert submittal_replies.vetted(on_this_one, [])["reply"]["status"] == "resubmit"

    # "revise and resubmit" is not a revision number.
    assert submittal_replies.commented_revision("revise and resubmit") is None
    assert submittal_replies.commented_revision("on MAR sheet, Revision 0, dated x") == 0


def test_an_old_project_without_a_received_folder_keeps_its_reply():
    """A project that files the form loose in the revision folder has no
    Received folder to be empty, so the reply read off the form is all
    there is and it stands."""
    from app.services import submittal_replies

    loose = {"revision": 1, "relative": "02- Material Submittals/FA/R1/MS R1.pdf",
             "reply": {"present": True, "from_consultant": True, "status": "approved",
                       "evidence": "Approved, work may proceed"}}
    assert submittal_replies.carried_over(loose, []) is False

    # But where the project does file the two halves, an empty Received
    # folder means the comments on the form came in with it.
    halves = {"revision": 1, "relative": "02- Material Submittals/FA/R1/Submitted/MS R1.pdf",
              "reply": {"present": True, "from_consultant": True, "status": "approved",
                        "evidence": "Approved, work may proceed"}}
    assert submittal_replies.carried_over(halves, []) is True


def test_a_reply_sheet_is_seeded_from_its_own_revisions_comments(client, db_session, tmp_path):
    """Opening the R1 sheet must not hand the engineer R0's remarks to
    answer a second time. R0's sheet opens with the comments that came
    back on R0; R1's opens empty, because nothing has come back yet."""
    from pathlib import Path

    from app.models import Project, ProjectDocument
    from app.routers.submittal import _consultant_words

    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40300"
    root.mkdir()
    created = client.post("/projects", json={
        "ep_number": "40300", "project_name": "Two Revisions", "source_folder_path": str(root),
        "design_sheets": [], "consultant": "M/s. Silver Stone",
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": False}],
    }).json()

    comments = ("Comments on the attached MAR sheet, Revision 0: COO of majority of the "
                "products is not as per the vendor list. Re-submit for further review.")
    filed = [
        # The R0 form, and the consultant's answer that came back on it.
        ("submittal_form", "02- Material Submittals/FA/R0/Submitted/MS R00.pdf",
         {"form": {"reference": "EP-40300-MAS-FAS", "revision": 0}}),
        ("document", "02- Material Submittals/FA/R0/Received/x_00_C.pdf",
         {"form": {"reply": {"evidence": comments}}}),
        # The R1 form, which prints those same comments because it answers
        # them. Nothing has come back into R1.
        ("submittal_form", "02- Material Submittals/FA/R1/Submitted/MS R01.pdf",
         {"form": {"reference": "EP-40300-MAS-FAS", "revision": 1,
                   "reply": {"present": True, "from_consultant": True, "status": "resubmit",
                             "evidence": comments}}}),
    ]
    for role, relative, extracted in filed:
        db_session.add(ProjectDocument(
            project_id=created["id"], role=role, path=str(root / relative), filename=Path(relative).name,
            relative_path=relative, extracted=extracted, state="present"))
    db_session.commit()

    project = db_session.get(Project, created["id"])
    assert "COO of majority" in (_consultant_words(db_session, project, "EP-40300-MAS-FAS", "R00") or "")
    assert _consultant_words(db_session, project, "EP-40300-MAS-FAS", "R01") is None

    # And the sheet the engineer opens follows: R1 opens with a row to fill.
    opened = client.get(f"/projects/{created['id']}/submittals/EP-40300-MAS-FAS/R01/reply").json()
    assert len(opened["rows"]) == 1 and opened["rows"][0]["comment"] == ""


def test_a_map_drawn_by_older_rules_is_out_of_date_though_no_file_moved(monkeypatch, tmp_path):
    """The folder can be untouched and the map still be wrong: what the
    platform makes of the same forms changes when the rules do. Without
    this a project keeps showing a map no one can refresh, because
    nothing in the folder has changed to ask for it."""
    from app.ai import submittal_reader
    from app.models import Project

    root = tmp_path / "EP-1"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-")
    project = Project(id=1, ep_number="1", project_name="T", source_folder_path=str(root))
    listing = submittal_reader.listing_fingerprint(root)[0]

    def stored(version):
        monkeypatch.setattr(submittal_reader, "latest_map",
                            lambda db, p: {"listing_sha256": listing, "map_version": version})

    # Drawn by the rules in force: the folder is unchanged, so is the map.
    stored(submittal_reader.MAP_VERSION)
    assert submittal_reader.changes(None, project)["changed"] is False

    # The very same folder, drawn by older rules: out of date.
    stored("map-2026-01-01.1")
    outcome = submittal_reader.changes(None, project)
    assert outcome["changed"] is True
    assert "reads submittals differently" in outcome["reason"]

def test_the_register_note_empties_when_the_revision_has_no_comments(monkeypatch):
    """The note is the consultant's words for the revision on show. When
    a revision turns out not to have been answered, the words that were
    there described a different revision and must not stay on it."""
    from app.ai import submittal_reader
    from app.models import Project, ProjectSubmittal

    class FakeQuery:
        """No forms left in the folder, as far as this stands in for."""
        def filter(self, *conditions):
            return self
        def all(self):
            return []

    class FakeDb:
        def __init__(self):
            self.deleted = []
        def add(self, row):
            pass
        def delete(self, row):
            self.deleted.append(row)
        def query(self, *columns):
            return FakeQuery()
        def commit(self):
            pass
        def flush(self):
            pass

    # The actions and change rows are app.services.project_state's, tested
    # with a real session (tests/test_project_state.py).
    from app.services import project_state
    monkeypatch.setattr(project_state, "submittals_changed", lambda *a, **k: None)
    project = Project(id=1, ep_number="1", project_name="T")
    standing = ProjectSubmittal(
        project_id=1, title="Fire Alarm", reference="EP-1-MAS-FAS", system_code="FAS",
        revision="R01", status="rejected", reply_code="C",
        note="Comments on MAR sheet, Revision 0: re-submit for review.")
    project.submittals = [standing]

    # The map now says R1 is under review, with no words of its own.
    built = {"systems": [{"rows": [{
        "reference": "EP-1-MAS-FAS", "system_code": "FAS", "title": "Fire Alarm",
        "manufacturer": "EDWARDS", "latest": "R1", "latest_status": "UR",
        "cells": {"R1": {"status": "UR", "file": "x.pdf", "evidence": ""}},
    }]}]}
    counts = submittal_reader.sync_register(FakeDb(), project, built, None)
    assert counts["updated"] == 1
    assert standing.status.value if hasattr(standing.status, "value") else standing.status
    assert standing.note is None, "R0's comments do not belong on R1"
    assert standing.reply_code in (None, "")


def test_a_submittal_just_filed_is_not_deleted_by_the_map_drawn_after_it(client, db_session, tmp_path):
    """Filing a package enters it in the register and redraws the map in
    the same breath. If anything stops the new form being read, the map
    comes back without it -- and the register row made a moment earlier
    must not be swept away with it. The folder is what decides: the form
    is still in it."""
    from app.ai import submittal_reader
    from app.models import Project, ProjectDocument, ProjectSubmittal
    from app.services import document_sync

    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40400"
    root.mkdir()
    created = client.post("/projects", json={
        "ep_number": "40400", "project_name": "Filed", "source_folder_path": str(root),
        "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": False}],
    }).json()
    project = db_session.get(Project, created["id"])

    # The form is in the folder and in the index, as filing leaves it.
    relative = "02- Material Submittals/FA/R0/Submitted/MS R00.pdf"
    db_session.add(ProjectDocument(
        project_id=project.id, role=document_sync.ROLE_SUBMITTAL, path=str(root / relative),
        filename="MS R00.pdf", relative_path=relative, reference="EP-40400-MAS-FAS",
        state="present"))
    db_session.add(ProjectSubmittal(
        project_id=project.id, title="Fire Alarm", reference="EP-40400-MAS-FAS",
        system_code="FAS", revision="R00", status="under_review", document_path=relative))
    db_session.commit()

    # A map that came back without it -- the reading did not call it a form.
    counts = submittal_reader.sync_register(db_session, project, {"systems": []}, None)
    assert counts["removed"] == 0, "the form is still in the folder"
    kept = db_session.query(ProjectSubmittal).filter_by(project_id=project.id).all()
    assert [s.reference for s in kept] == ["EP-40400-MAS-FAS"]

    # Once the form really has left the folder, the row goes with it.
    for row in db_session.query(ProjectDocument).filter_by(project_id=project.id).all():
        row.state = document_sync.REMOVED
    db_session.commit()
    counts = submittal_reader.sync_register(db_session, project, {"systems": []}, None)
    assert counts["removed"] == 1
    assert db_session.query(ProjectSubmittal).filter_by(project_id=project.id).count() == 0


def test_the_log_record_of_a_resubmission_is_not_answered_by_the_comments_it_carries():
    """The logs are built from the document index, not from the map, so
    the same carried-over comments have to be caught again here. An R1
    form printing R0's remarks is logged under review, and the pass that
    reads the Received folders then says what really came back on it."""
    from datetime import datetime, timezone

    from app.services import document_sync

    evidence = ("Employer's Representative's Consultant's Comments (on the attached MAR sheet, Reference No. 23058.90-BHC-GEC-SSH-MAR-FF-0016, Revision 0, dated 02.09.2026): 'Re-submit the document with full compliance for further review.'")

    def logged(revision):
        extracted: dict = {}
        reading = {"is_submittal": True, "reference": "EP-1-MAS-FAS", "revision": revision,
                   "title": "Fire Alarm",
                   "reply": {"present": True, "from_consultant": True, "status": "resubmit",
                             "code": "C", "evidence": evidence}}
        document_sync.record_for_the_log(
            extracted, reading,
            relative=f"02- Material Submittals/FA/R{revision}/Submitted/MS.pdf",
            modified=datetime.now(timezone.utc), ep_number="1")
        return extracted["records"][0]

    # The R1 form carries R0's comments: it is not itself answered.
    one = logged(1)
    assert one["status"] == "UR" and one["reply_text"] is None
    # The R0 form carries its own answer, which stands.
    zero = logged(0)
    assert zero["status"] == "RR" and zero["reply_text"]


def test_one_system_has_one_submittal_however_many_forms_are_on_file():
    """A submittal is filed twice over: the copy we prepared, under our
    own reference, and the one that came back from the consultant under
    the main contractor's. They are one submittal, and listing both
    showed the system twice -- once under review and once answered.

    What separates two real submittals of one system is the supplier:
    fire rated cables are quoted from two makers at the same revision."""
    from app.routers.submittal import same_submittal, submittal_supplier

    # Menvier is Eaton's name for monitored self-contained emergency
    # lighting, so a form saying either is the same supplier.
    assert submittal_supplier("MENVIER") == submittal_supplier("EATON")
    assert same_submittal("ELS", "R00", "MENVIER") == same_submittal("ELS", "R0", "EATON")
    assert same_submittal("FAS", "R00", "EDWARDS") == same_submittal("FAS", "R0", "EDWARDS")

    # Two makers of fire rated cable at the same revision are two
    # submittals, and a brand the knowledge base does not know keeps its
    # own name rather than folding in with every other unknown one.
    assert submittal_supplier("FIREGUARD") == "FIREGUARD"
    assert submittal_supplier("TIANJIE") == "TIANJIE"
    assert same_submittal("FRC", "R00", "TIANJIE") != same_submittal("FRC", "R00", "FRONTIER")
    # A revision apart is a revision apart.
    assert same_submittal("FRC", "R00", "FIREGUARD") != same_submittal("FRC", "R01", "FIREGUARD")


def test_the_form_we_filed_is_not_listed_beside_the_one_that_came_back(client, db_session, tmp_path):
    """The register holds the answered submittal. The folder also holds
    our own copy of the same form, under our reference and with no reply
    on it. Only the answered one is listed -- it is the one carrying what
    the consultant said."""
    from pathlib import Path

    from app.models import Project, ProjectDocument, ProjectSubmittal
    from app.routers.submittal import _filed_in_the_folder, same_submittal
    from app.services import document_sync

    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40600"
    root.mkdir()
    created = client.post("/projects", json={
        "ep_number": "40600", "project_name": "Two Forms", "source_folder_path": str(root),
        "design_sheets": [],
        "systems": [{"name": "Monitored Emergency Lighting", "brand": "MENVIER",
                     "method_statement": True, "drawing": False}],
    }).json()
    project = db_session.get(Project, created["id"])

    # What the consultant answered, in the register.
    db_session.add(ProjectSubmittal(
        project_id=project.id, title="Monitored Self Contained Emergency Lighting",
        reference="BBY006-GME-MAS-EL-LI-0001", system_code="ELS", revision="R00",
        status="approved", reply_code="B", manufacturer="EATON"))

    # Our own copy of the same form, on file under our own reference.
    for reference, relative, maker in [
        ("EP-40600/SK/EM/201", "03- MS/02- EML/MS EML R0.pdf", "M/s. MENVIER"),
        # A different maker of the same system is a submittal of its own.
        ("BBY006-GME-MAS-EL-FA-0003", "03- MS/03- FRC/Frontier R0.pdf", "M/s. FRONTIER"),
    ]:
        db_session.add(ProjectDocument(
            project_id=project.id, role=document_sync.ROLE_SUBMITTAL, path=str(root / relative),
            filename=Path(relative).name, relative_path=relative, state="present",
            extracted={"form": {"is_submittal": True, "reference": reference, "revision": 0,
                                "title": "Material Submittal", "manufacturer": maker,
                                "system_code": "ELS" if "EML" in relative else "FRC"}}))
    db_session.commit()

    covered = {same_submittal(s.system_code, s.revision, s.manufacturer) for s in project.submittals}
    listed = _filed_in_the_folder(
        db_session, project, {s.reference for s in project.submittals if s.reference}, covered=covered)
    references = [row.reference for row in listed]
    assert "EP-40600/SK/EM/201" not in references, "our copy of the answered submittal"
    assert "BBY006-GME-MAS-EL-FA-0003" in references, "a different maker is its own submittal"
