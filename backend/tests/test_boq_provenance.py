"""Provenance on every BOQ line, and stale saves refused."""

from app.core.config import get_settings
from app.models import ProjectBoqItem

from .conftest import login
from .test_ai_assist import GOOD_ROWS, _project_with_sheet
from .test_design_sheet_extractor import requires_tesseract

settings = get_settings()


def _put(client, project_id, rows, version=None):
    headers = {"If-Match": f'"{version}"'} if version is not None else {}
    return client.put(f"/projects/{project_id}/boq", json=rows, headers=headers)


def _editable(item: dict) -> dict:
    keys = ("id", "system_code", "group_heading", "manufacturer", "catalog_no", "description", "quantity", "unit",
            "unit_price", "total_price", "remarks")
    return {key: item.get(key) for key in keys}


@requires_tesseract
def test_extracted_lines_carry_where_they_were_read_and_keep_it_through_saves(client, db_session, tmp_path):
    project, sheet = _project_with_sheet(db_session, tmp_path, GOOD_ROWS)
    login(client, settings.default_admin_email, settings.default_admin_password)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()
    items = body["items"]
    assert len(items) == 6 and body["version"] == 1
    first = items[0]
    assert first["origin"] == "extracted" and first["status"] == "extracted"
    assert first["extraction_run_id"] and first["source_page"] == 1 and len(first["source_region"]) == 4
    assert len(first["source_document_sha256"]) == 64 and first["parser_version"]
    assert first["raw_values"]["quantity_parse"]["status"] == "ok"
    assert first["extracted_values"]["quantity"] == first["quantity"]

    # Save with one quantity corrected and one line typed in.
    rows = [_editable(item) for item in items]
    rows[0]["quantity"] = "11"
    rows.append({"system_code": "PAVA", "description": "Rack", "quantity": "1"})
    saved = _put(client, project.id, rows, version=body["version"])
    assert saved.status_code == 200, saved.text
    assert saved.headers["X-Resource-Version"] == "2"
    out = saved.json()
    assert out[0]["status"] == "corrected" and out[0]["extracted_values"]["quantity"] == first["quantity"]
    assert out[0]["edited_at"] is not None and out[0]["source_page"] == 1
    assert out[1]["status"] == "extracted" and out[1]["edited_at"] is None
    assert out[-1]["origin"] == "manual" and out[-1]["extraction_run_id"] is None

    # Putting the machine's value back is no longer a correction.
    rows = [_editable(item) for item in out]
    rows[0]["quantity"] = first["quantity"]
    again = _put(client, project.id, rows, version=2).json()
    assert again[0]["status"] == "extracted"


def test_a_stale_boq_save_is_refused(client, db_session):
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={"ep_number": "50001", "project_name": "P", "systems": [],
                                                 "design_sheets": []}).json()["id"]
    loaded = client.get(f"/projects/{project_id}/boq")
    version = int(loaded.headers["X-Resource-Version"])

    first = _put(client, project_id, [{"system_code": "FAS", "description": "Panel", "quantity": "1"}], version)
    assert first.status_code == 200
    # A second editor who loaded the same version saves after the first.
    second = _put(client, project_id, [{"system_code": "FAS", "description": "Other", "quantity": "2"}], version)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "stale_write" and detail["current_version"] == version + 1
    assert [i["description"] for i in client.get(f"/projects/{project_id}/boq").json()] == ["Panel"]
    # Without If-Match (a script), the save is accepted.
    assert _put(client, project_id, [{"system_code": "FAS", "description": "Panel", "quantity": "1"}]).status_code == 200


def test_provenance_is_never_taken_from_another_projects_line(client, db_session):
    login(client, settings.default_admin_email, settings.default_admin_password)
    a = client.post("/projects", json={"ep_number": "50002", "project_name": "A", "systems": [], "design_sheets": []}).json()["id"]
    b = client.post("/projects", json={"ep_number": "50003", "project_name": "B", "systems": [], "design_sheets": []}).json()["id"]
    db_session.add(ProjectBoqItem(project_id=a, position=0, description="Read line", quantity="5", origin="extracted",
                                  source_page=3, extracted_values={"quantity": "5"}))
    db_session.commit()
    foreign_id = client.get(f"/projects/{a}/boq").json()[0]["id"]

    out = _put(client, b, [{"id": foreign_id, "description": "Read line", "quantity": "5"}]).json()
    assert out[0]["origin"] == "manual" and out[0]["source_page"] is None


def test_a_stale_project_information_save_is_refused(client, db_session):
    login(client, settings.default_admin_email, settings.default_admin_password)
    created = client.post("/projects", json={"ep_number": "50004", "project_name": "P", "systems": [], "design_sheets": []}).json()
    version = created["details_version"]
    ok = client.put(f"/projects/{created['id']}", json={"project_name": "First"}, headers={"If-Match": str(version)})
    assert ok.status_code == 200 and ok.json()["details_version"] == version + 1
    stale = client.put(f"/projects/{created['id']}", json={"project_name": "Second"}, headers={"If-Match": str(version)})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_write"
    assert client.get(f"/projects/{created['id']}").json()["project_name"] == "First"


def test_a_scanned_code_is_settled_against_the_equipment_table(db_session):
    """A Design Sheet scan's "SIGA-AASO" is SIGA-AA50 -- S for 5, O for 0 --
    and the BOQ carries the catalogue's number with the reading kept."""
    from app.extraction import identity
    from app.services import boq_provenance, equipment_currents

    equipment_currents.seed(db_session)
    library = boq_provenance.part_library(db_session)
    assert library[identity.part_key("SIGA-AA50")] == "SIGA-AA50"
    assert library[identity.part_key("SIGA-AAS0")] == "SIGA-AA50"          # the alias the table holds

    code, record = boq_provenance.catalogued("SIGA-AASO", library)
    assert code == "SIGA-AA50" and record["read_as"] == "SIGA-AASO" and "confusion" in record["reason"]
    code, record = boq_provenance.catalogued("SIGA-AA50", library)
    assert code == "SIGA-AA50" and record["reason"].endswith("exactly")
    code, record = boq_provenance.catalogued("XYZ-9999", library)
    assert code == "XYZ-9999" and record is None
    assert boq_provenance.catalogued(None, library) == (None, None)
