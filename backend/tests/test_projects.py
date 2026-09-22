from pathlib import Path

from app.core.config import get_settings
from app.models import RoleEnum

from .conftest import login, make_user

settings = get_settings()


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake pdf bytes")


def _login_admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


# --- /projects/resolve ---


def test_resolve_requires_creator_role(client, db_session):
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")

    resp = client.post("/projects/resolve", json={"ep_number": "29495"})
    assert resp.status_code == 403


def test_resolve_without_projects_root_configured(client, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "projects_root", None)
    _login_admin(client)

    resp = client.post("/projects/resolve", json={"ep_number": "29495"})
    assert resp.status_code == 503


def test_resolve_happy_path(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    project = tmp_path / "Samana Developers" / "EP-29495 IVY Garden 2"
    touch(project / "Scan Document" / "EP-29495 DRF.pdf")
    touch(project / "Commercial Document" / "EP-29495 FAS Design.pdf")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))

    _login_admin(client)
    resp = client.post("/projects/resolve", json={"ep_number": "29495"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["folder_found"] is True
    assert body["is_ambiguous"] is False
    assert len(body["drf_candidates"]) == 1
    assert body["drf_candidates"][0]["filename"] == "EP-29495 DRF.pdf"
    assert len(body["design_sheet_candidates"]) == 1
    assert body["design_sheet_candidates"][0]["system_guess"] == "FAS"
    assert body["warnings"] == []
    # The fixture DRF is a fake (non-PDF) file -- extraction should fail
    # gracefully rather than break the resolve response.
    assert body["extracted_fields"] == {}
    assert body["extraction_warnings"]


def test_resolve_accepts_the_ep_number_with_its_prefix(client, monkeypatch, tmp_path):
    """Engineers type the number the way the folders spell it. The resolver
    adds the "EP" itself, so a typed prefix used to find nothing."""
    import app.routers.projects as projects_router

    touch(tmp_path / "Samana Developers" / "EP-29495 IVY Garden 2" / "Scan Document" / "EP-29495 DRF.pdf")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))

    _login_admin(client)
    for typed in ("EP-29495", "ep 29495", " 29495 "):
        body = client.post("/projects/resolve", json={"ep_number": typed}).json()
        assert body["ep_number"] == "29495", typed
        assert body["folder_found"] is True, typed


def test_resolve_rejects_a_blank_ep_number(client):
    _login_admin(client)
    assert client.post("/projects/resolve", json={"ep_number": " EP- "}).status_code == 422


def test_resolve_ep_not_found(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))
    _login_admin(client)

    resp = client.post("/projects/resolve", json={"ep_number": "99999"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["folder_found"] is False
    assert any("No folder found" in w for w in body["warnings"])


# --- POST /projects ---


def _payload_without_documents(ep_number: str = "29495") -> dict:
    """A project whose BOQ is typed in: no DRF and no Design Sheet, so issuing
    a revision is not held up by documents these tests do not have on disk."""
    return {**_valid_project_payload(ep_number), "drf_document_path": None, "design_sheets": []}


def _valid_project_payload(ep_number: str = "29495") -> dict:
    return {
        "ep_number": ep_number,
        "project_name": "IVY Garden 2",
        "plot_number": "648-8523",
        "location": "Wadi Al Safa 5, DLRC, Dubai",
        "client": "Samana",
        "consultant": "Al Hilal",
        "contractor": "Samana Developers",
        "contact_person": "Mahammad Naushad Bennapade",
        "contact_phone": "971543079068",
        "contact_email": "mahammad.bennapade@samanadevelopers.com",
        "scope_of_work": "Design, Supply, T&C",
        "systems": [
            {
                "name": "Fire Alarm",
                "brand": "EDWARDS",
                "method_statement": True,
                "drawing": True,
            },
            {"name": "Central Battery System", "method_statement": True, "drawing": False},
        ],
        "other_information": "Quoted as per IFC drawing dated 18-06-2025 only",
        "source_folder_path": r"C:\archive\Samana Developers\EP-29495 IVY Garden 2",
        "drf_document_path": r"C:\archive\Samana Developers\EP-29495 IVY Garden 2\Scan Document\EP-29495 DRF.pdf",
        "design_sheets": [
            {
                "system_code": "FAS",
                "document_path": r"C:\archive\...\EP-29495 FAS Design.pdf",
            }
        ],
    }


def test_create_project_requires_creator_role(client, db_session):
    make_user(db_session, "draftsman@ep-platform.com", RoleEnum.draftsman)
    login(client, "draftsman@ep-platform.com")

    resp = client.post("/projects", json=_valid_project_payload())
    assert resp.status_code == 403


def test_create_and_fetch_project(client):
    _login_admin(client)

    create_resp = client.post("/projects", json=_valid_project_payload())
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["ep_number"] == "29495"
    assert created["status"] == "active"
    assert [(s["name"], s["brand"], s["method_statement"], s["drawing"]) for s in created["systems"]] == [
        ("Fire Alarm", "EDWARDS", True, True),
        ("Central Battery System", None, True, False),
    ]
    assert len(created["design_sheets"]) == 1
    assert created["design_sheets"][0]["system_code"] == "FAS"

    get_resp = client.get(f"/projects/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["ep_number"] == "29495"


def test_create_project_duplicate_ep_number_conflicts(client):
    _login_admin(client)
    first = client.post("/projects", json=_valid_project_payload("30000"))
    assert first.status_code == 201

    second = client.post("/projects", json=_valid_project_payload("30000"))
    assert second.status_code == 409


def test_create_project_ep_number_is_stored_without_prefix_or_padding(client):
    """Otherwise "EP-30100", "30100 " and "30100" are three separate projects
    for the same job."""
    _login_admin(client)
    first = client.post("/projects", json=_valid_project_payload("EP-30100 "))
    assert first.status_code == 201
    assert first.json()["ep_number"] == "30100"

    assert client.post("/projects", json=_valid_project_payload(" 30100")).status_code == 409


def test_list_projects_is_scoped_to_the_user(client, db_session):
    """The list holds the projects a user created or is the assigned design
    engineer of -- not every project on the platform. An admin sees all."""
    _login_admin(client)
    client.post("/projects", json=_valid_project_payload("31000"))
    client.post("/auth/logout")

    # Someone who neither created it nor is assigned to it does not see it.
    make_user(db_session, "viewer2@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer2@ep-platform.com")
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert all(p["ep_number"] != "31000" for p in resp.json())
    client.post("/auth/logout")

    # A design engineer sees the project they created, and only that one.
    make_user(db_session, "engineer2@ep-platform.com", RoleEnum.design_engineer)
    login(client, "engineer2@ep-platform.com")
    client.post("/projects", json=_valid_project_payload("31001"))
    assert [p["ep_number"] for p in client.get("/projects").json()] == ["31001"]
    client.post("/auth/logout")

    # The admin sees both.
    _login_admin(client)
    numbers = {p["ep_number"] for p in client.get("/projects").json()}
    assert {"31000", "31001"} <= numbers


def test_get_missing_project_404(client):
    _login_admin(client)
    resp = client.get("/projects/999999")
    assert resp.status_code == 404


# --- PUT /projects/{id} ---


def _details(**overrides) -> dict:
    payload = _valid_project_payload()
    for key in ("ep_number", "source_folder_path", "drf_document_path", "design_sheets"):
        payload.pop(key)
    payload.update(overrides)
    return payload


def test_update_project_corrects_details_and_replaces_systems(client):
    _login_admin(client)
    created = client.post("/projects", json=_valid_project_payload("32000")).json()

    resp = client.put(
        f"/projects/{created['id']}",
        json=_details(
            client="Samana Developers LLC",
            plot_number=None,
            systems=[{"name": "Fire Alarm", "brand": "Edwards EST4", "method_statement": True, "drawing": False}],
        ),
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["client"] == "Samana Developers LLC"
    assert updated["plot_number"] is None
    assert [(s["name"], s["brand"], s["drawing"]) for s in updated["systems"]] == [
        ("Fire Alarm", "Edwards EST4", False)
    ]
    # the documents the resolver found are untouched
    assert updated["design_sheets"] == created["design_sheets"]
    assert client.get(f"/projects/{created['id']}").json()["client"] == "Samana Developers LLC"


def test_update_project_cannot_change_the_ep_number(client):
    """It ties the project to its archive folder; a sent value is ignored."""
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("32100")).json()["id"]

    resp = client.put(f"/projects/{pid}", json=_details(ep_number="99999"))
    assert resp.status_code == 200
    assert resp.json()["ep_number"] == "32100"


def test_update_project_denied_to_viewer_and_404_when_missing(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("32200")).json()["id"]
    assert client.put("/projects/999999", json=_details()).status_code == 404
    client.post("/auth/logout")

    make_user(db_session, "viewer5@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer5@ep-platform.com")
    assert client.put(f"/projects/{pid}", json=_details(client="x")).status_code == 403


# --- /projects/{id}/boq ---


def _boq_payload() -> list[dict]:
    return [
        {
            "system_code": "FAS",
            "catalog_no": "4-CPU",
            "description": "Central Processor Module",
            "quantity": "1",
            "unit_price": "250.00",
            "total_price": "250.00",
        },
        {
            "system_code": "ELS",
            "manufacturer": "RP-Technik",
            "catalog_no": None,
            "description": "VisionGuard Basisversion & BACnet",
            "quantity": "Lot",
            "unit": "Set",
            "remarks": "Software licence",
        },
    ]


def test_boq_starts_empty_and_round_trips(client):
    _login_admin(client)
    project = client.post("/projects", json=_valid_project_payload("33000")).json()
    pid = project["id"]

    assert client.get(f"/projects/{pid}/boq").json() == []

    resp = client.put(f"/projects/{pid}/boq", json=_boq_payload())
    assert resp.status_code == 200
    items = resp.json()
    assert [i["description"] for i in items] == [
        "Central Processor Module",
        "VisionGuard Basisversion & BACnet",
    ]
    # position is assigned from list order, not sent by the client
    assert [i["position"] for i in items] == [0, 1]
    # a non-numeric quantity survives as written on the sheet
    assert items[1]["quantity"] == "Lot"
    assert items[1]["unit_price"] is None
    assert (items[1]["manufacturer"], items[1]["unit"], items[1]["remarks"]) == (
        "RP-Technik",
        "Set",
        "Software licence",
    )

    assert client.get(f"/projects/{pid}/boq").json() == items


def test_boq_put_replaces_rather_than_appends(client):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("33100")).json()["id"]
    client.put(f"/projects/{pid}/boq", json=_boq_payload())

    resp = client.put(
        f"/projects/{pid}/boq",
        json=[{"description": "Only line", "quantity": "2"}],
    )
    assert resp.status_code == 200
    assert [i["description"] for i in resp.json()] == ["Only line"]
    assert len(client.get(f"/projects/{pid}/boq").json()) == 1


def test_boq_is_readable_by_any_role_but_written_only_by_creators(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("33200")).json()["id"]
    client.put(f"/projects/{pid}/boq", json=_boq_payload())
    client.post("/auth/logout")

    make_user(db_session, "viewer3@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer3@ep-platform.com")

    assert len(client.get(f"/projects/{pid}/boq").json()) == 2
    assert client.put(f"/projects/{pid}/boq", json=[]).status_code == 403


def test_boq_goes_away_with_the_project(client, db_session):
    from app.models import ProjectBoqItem

    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("33300")).json()["id"]
    client.put(f"/projects/{pid}/boq", json=_boq_payload())

    assert client.delete(f"/projects/{pid}").status_code == 204
    assert db_session.query(ProjectBoqItem).filter_by(project_id=pid).count() == 0


def test_boq_on_missing_project_404(client):
    _login_admin(client)
    assert client.get("/projects/999999/boq").status_code == 404
    assert client.put("/projects/999999/boq", json=[]).status_code == 404


# --- POST /projects/{id}/boq/ensure ---


def _stub_extraction(monkeypatch, calls: list):
    import app.routers.projects as projects_router
    from app.services.design_sheet_extractor import ExtractedBoqLine

    def fake(path):
        calls.append(path)
        return [
            ExtractedBoqLine(
                catalog_no="4-CPU",
                description="Central Processor Module",
                quantity="1",
                group_heading="EST4 Main Fire Alarm Control Panel",
                confidence=93.0,
                page=1,
            )
        ]

    monkeypatch.setattr(projects_router, "extract_boq_lines", fake)


def test_boq_is_extracted_on_first_open_and_persisted(client, monkeypatch):
    calls: list = []
    _stub_extraction(monkeypatch, calls)

    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("34100")).json()["id"]

    resp = client.post(f"/projects/{pid}/boq/ensure")
    assert resp.status_code == 200
    body = resp.json()
    assert body["extracted"] is True
    assert body["items"][0]["description"] == "Central Processor Module"
    # the system code comes from the sheet the lines were read out of
    assert body["items"][0]["system_code"] == "FAS"
    assert body["items"][0]["group_heading"] == "EST4 Main Fire Alarm Control Panel"
    # the DRF gives the Fire Alarm system's brand; the FAS sheet's lines take it
    assert body["items"][0]["manufacturer"] == "EDWARDS"

    # unlike the DRF fields, these are stored rather than proposed
    assert len(client.get(f"/projects/{pid}/boq").json()) == 1


def test_manufacturer_is_prefilled_only_when_the_system_code_is_unambiguous():
    from app.models import ProjectSystem
    from app.routers.projects import _brand_for

    systems = [
        ProjectSystem(name="Fire Alarm", brand="EDWARDS"),
        ProjectSystem(name="Central Battery System", brand="CEAG"),
        ProjectSystem(name="Emergency Light Monitoring", brand="MENVIER"),
    ]
    assert _brand_for("FAS", systems) == "EDWARDS"
    # EML, CBS and ELS are one emergency lighting system, over both DRF rows:
    # here the two rows name different brands, so no brand is guessed.
    assert _brand_for("eml", systems) is None
    assert _brand_for("ELS", systems[:2]) == "CEAG"
    assert _brand_for("CBS", systems[:1]) is None
    assert _brand_for("NAC", systems) is None
    assert _brand_for(None, systems) is None


def test_boq_extraction_runs_only_once(client, monkeypatch):
    """Re-reading would duplicate lines or discard the engineer's edits, so a
    second open returns what is stored without touching the sheets."""
    calls: list = []
    _stub_extraction(monkeypatch, calls)

    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("34110")).json()["id"]

    first = client.post(f"/projects/{pid}/boq/ensure").json()
    assert first["extracted"] is True
    assert len(calls) == 1

    # An edit the engineer makes afterwards must survive the next open.
    client.put(
        f"/projects/{pid}/boq",
        json=[{"system_code": "FAS", "description": "Edited by hand", "quantity": "9"}],
    )

    second = client.post(f"/projects/{pid}/boq/ensure").json()
    assert second["extracted"] is False
    assert len(calls) == 1, "the Design Sheets were read a second time"
    assert [i["description"] for i in second["items"]] == ["Edited by hand"]


def test_boq_extraction_that_loses_the_race_returns_the_winners_lines(client, monkeypatch):
    """Two overlapping opens (React StrictMode fires every effect twice in dev)
    used to both pass the stamp check, both OCR, and store every line twice.

    Here a competing request -- another process, so the in-process lock does
    not stop it -- claims and stores the BOQ while this one is still reading
    the sheets. This one must not add its own copy, and must return the
    winner's lines rather than the empty BOQ it saw before the read."""
    import app.routers.projects as projects_router
    from app.core.timeutils import utc_now
    from app.database import SessionLocal
    from app.models import Project, ProjectBoqItem
    from app.services.design_sheet_extractor import ExtractedBoqLine

    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("34150")).json()["id"]

    def slower_than_the_other_request(path):
        other = SessionLocal()
        try:
            project = other.get(Project, pid)
            project.boq_extracted_at = utc_now()
            project.boq_items.append(
                ProjectBoqItem(system_code="FAS", position=0, description="Stored by the winner", quantity="3")
            )
            other.commit()
        finally:
            other.close()
        return [
            ExtractedBoqLine(
                catalog_no="4-CPU",
                description="Read by the loser",
                quantity="1",
                group_heading=None,
                confidence=93.0,
                page=1,
            )
        ]

    monkeypatch.setattr(projects_router, "extract_boq_lines", slower_than_the_other_request)

    body = client.post(f"/projects/{pid}/boq/ensure").json()
    assert body["extracted"] is False
    assert [i["description"] for i in body["items"]] == ["Stored by the winner"]
    assert [i["description"] for i in client.get(f"/projects/{pid}/boq").json()] == [
        "Stored by the winner"
    ]


def test_boq_extraction_is_not_retried_after_an_unreadable_sheet(client, monkeypatch):
    import app.routers.projects as projects_router
    from app.services.design_sheet_extractor import DesignSheetExtractionError

    calls: list = []

    def unreadable(path):
        calls.append(path)
        raise DesignSheetExtractionError("layout is not one this extractor recognises")

    monkeypatch.setattr(projects_router, "extract_boq_lines", unreadable)

    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("34200")).json()["id"]

    first = client.post(f"/projects/{pid}/boq/ensure").json()
    assert first["items"] == []
    assert any("recognises" in w for w in first["warnings"])
    assert len(calls) == 1

    # Not re-attempted on every visit -- but still reported: the read cannot
    # be repeated, so a warning that only reached the first response would be
    # lost with it, and the sheet's lines would be silently missing.
    second = client.post(f"/projects/{pid}/boq/ensure").json()
    assert second["extracted"] is False
    assert len(calls) == 1
    assert second["warnings"] == first["warnings"]
    assert client.get(f"/projects/{pid}").json()["boq_extraction_warnings"] == first["warnings"]


def test_boq_ensure_denied_to_viewer(client, db_session):
    _login_admin(client)
    created = client.post("/projects", json=_valid_project_payload("34300")).json()
    client.post("/auth/logout")

    make_user(db_session, "viewer4@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer4@ep-platform.com")

    assert client.post(f"/projects/{created['id']}/boq/ensure").status_code == 403


# --- DELETE /projects/{id} ---


def test_delete_project_removes_it_and_its_children(client, db_session):
    from app.models import ProjectDesignSheet, ProjectSystem

    _login_admin(client)
    created = client.post("/projects", json=_valid_project_payload("32000")).json()
    project_id = created["id"]

    resp = client.delete(f"/projects/{project_id}")
    assert resp.status_code == 204

    assert client.get(f"/projects/{project_id}").status_code == 404
    # The cascade matters: orphaned system/design-sheet rows would otherwise
    # accumulate and be silently attached to a later project reusing the id.
    assert (
        db_session.query(ProjectSystem).filter_by(project_id=project_id).count() == 0
    )
    assert (
        db_session.query(ProjectDesignSheet).filter_by(project_id=project_id).count() == 0
    )


def test_delete_project_frees_the_ep_number_for_reuse(client):
    _login_admin(client)
    first = client.post("/projects", json=_valid_project_payload("32100")).json()
    assert client.delete(f"/projects/{first['id']}").status_code == 204

    again = client.post("/projects", json=_valid_project_payload("32100"))
    assert again.status_code == 201


def test_delete_project_denied_to_design_engineer(client, db_session):
    _login_admin(client)
    created = client.post("/projects", json=_valid_project_payload("32200")).json()
    client.post("/auth/logout")

    make_user(db_session, "engineer@ep-platform.com", RoleEnum.design_engineer)
    login(client, "engineer@ep-platform.com")

    assert client.delete(f"/projects/{created['id']}").status_code == 403
    # still there
    assert client.get(f"/projects/{created['id']}").status_code == 200


def test_delete_project_allowed_for_design_manager(client, db_session):
    _login_admin(client)
    created = client.post("/projects", json=_valid_project_payload("32300")).json()
    client.post("/auth/logout")

    make_user(db_session, "manager@ep-platform.com", RoleEnum.design_manager)
    login(client, "manager@ep-platform.com")

    assert client.delete(f"/projects/{created['id']}").status_code == 204


def test_delete_missing_project_404(client):
    _login_admin(client)
    assert client.delete("/projects/999999").status_code == 404


# --- uploading a document the resolver did not find ---


def _upload(client, project_id, kind, name="EP-30784 DRF.pdf", content=b"%PDF-1.4 fake", data=None):
    return client.post(
        f"/projects/{project_id}/documents/{kind}",
        files={"file": (name, content, "application/pdf")},
        data=data or {},
    )


def test_uploading_a_drf_the_search_missed(client, tmp_path, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "uploads_root", str(tmp_path))
    _login_admin(client)
    project = client.post("/projects", json={"ep_number": "30784", "design_sheets": []}).json()
    assert project["drf_document_path"] is None

    body = _upload(client, project["id"], "drf").json()
    stored = Path(body["drf_document_path"])
    assert stored.parent.name == "EP-30784" and stored.suffix == ".pdf"
    assert stored.read_bytes() == b"%PDF-1.4 fake"
    # It is the project's DRF from now on.
    assert client.get(f"/projects/{project['id']}").json()["drf_document_path"] == body["drf_document_path"]


def test_uploading_a_design_sheet_and_removing_one(client, tmp_path, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "uploads_root", str(tmp_path))
    _login_admin(client)
    project = client.post("/projects", json={"ep_number": "30784", "design_sheets": []}).json()

    body = _upload(client, project["id"], "design-sheets", name="EML sheet.pdf", data={"system_code": "eml"}).json()
    (sheet,) = body["design_sheets"]
    assert sheet["system_code"] == "ELS"
    assert Path(sheet["document_path"]).exists()

    after = client.delete(f"/projects/{project['id']}/documents/design-sheets/{sheet['id']}").json()
    assert after["design_sheets"] == []
    # Detaching a sheet does not delete the file.
    assert Path(sheet["document_path"]).exists()
    assert client.delete(f"/projects/{project['id']}/documents/design-sheets/{sheet['id']}").status_code == 404


def test_only_formats_the_platform_reads_are_accepted(client, tmp_path, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "uploads_root", str(tmp_path))
    _login_admin(client)
    project = client.post("/projects", json={"ep_number": "30784", "design_sheets": []}).json()

    assert _upload(client, project["id"], "drf", name="notes.docx").status_code == 400
    assert _upload(client, project["id"], "drf", content=b"").status_code == 400
    # A workbook is a zip: its content has to start as one.
    assert _upload(client, project["id"], "design-sheets", name="sheet.xlsx", content=b"PK\x03\x04 fake").status_code == 200
    assert _upload(client, project["id"], "design-sheets", name="sheet.xlsx").status_code == 400


def test_uploading_is_for_editors(client, db_session, tmp_path, monkeypatch):
    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router.settings, "uploads_root", str(tmp_path))
    _login_admin(client)
    project = client.post("/projects", json={"ep_number": "30784", "design_sheets": []}).json()
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert _upload(client, project["id"], "drf").status_code == 403


def test_a_project_carrying_every_kind_of_row_can_still_be_deleted(client, db_session):
    """Every table that hangs off a project by key must be cleared when the
    project is.

    The foreign keys are enforced, so a table nobody remembered does not
    fail quietly -- the project simply cannot be deleted at all. This
    walks the mappers rather than naming tables, so the next per-project
    table added fails here rather than on someone's machine.
    """
    from sqlalchemy import inspect

    from app.database import Base
    from app.models import Project
    from app.services import project_deletion

    cleared = open(project_deletion.__file__, encoding="utf-8").read()
    cascaded = {
        relationship.mapper.class_.__name__
        for relationship in inspect(Project).relationships
        if "delete" in str(relationship.cascade)
    }
    forgotten = []
    for mapper in Base.registry.mappers:
        model = mapper.class_
        columns = model.__table__.columns
        if "project_id" not in columns:
            continue
        if not any(key.column.table.name == "projects" for key in columns["project_id"].foreign_keys):
            continue
        if model.__name__ not in cascaded and model.__name__ not in cleared:
            forgotten.append(model.__name__)
    assert forgotten == [], (
        f"{forgotten} hang off a project but are neither cascaded nor cleared in project_deletion.py; "
        "deleting a project that has one of these rows fails on a foreign key."
    )
