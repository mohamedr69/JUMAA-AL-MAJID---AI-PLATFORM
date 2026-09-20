"""The Drawings Log: IFC floor plans x shop-drawing revisions (app/services/drawing_log.py)."""
from datetime import datetime
from pathlib import Path

import ezdxf

from app.core.config import get_settings
from app.services.document_control import ControlledDocument
from app.services.drawing_log import build, floor_label, floors_named

from .conftest import login

settings = get_settings()


def _sheet(name, floor_name, floors=(), mult=1):
    return {"name": name, "title": floor_name + " PLAN", "kind": "plan", "floor_name": floor_name,
            "floors": list(floors), "multiplier": mult}


IFC = [{"id": 1, "filename": "FIRE ALARM LAYOUT.dwg", "revision": "R1", "sheets": [
    _sheet("FA 101", "3RD BASEMENT FLOOR"),
    _sheet("FA 104", "GROUND FLOOR"),
    _sheet("FA 105", "1ST PODIUM FLOOR"),
    _sheet("FA 109", "1ST FLOOR", [1]),
    _sheet("FA 111", "TYPICAL 3RD TO 16TH FLOOR", range(3, 17), 14),
    _sheet("FA 113", "119 STRUCTURAL SLAB"),
    {"name": "FA 119", "title": "SCHEMATIC DIAGRAM", "kind": "diagram", "floor_name": "SCHEMATIC DIAGRAM", "floors": [], "multiplier": 0},
]}]


def _doc(floor, revision, status, day=1, system="FAS", ref="EP-SDW-FA-001", reply=None):
    return ControlledDocument(system, f"{ref}.pdf", f"03- Drawings/SD/FA/{revision}/{ref}.pdf", datetime(2026, 9, day),
                              ref, revision, status, floor=floor, reply_text=reply, category="drawings")


def test_floors_named_by_a_shop_drawing():
    assert floors_named("TYPICAL 3RD TO 16TH FLOOR") == {f"L{n}" for n in range(3, 17)}
    assert floors_named("Level 5") == {"L5"} and floors_named("3rd Basement") == {"B3"}
    assert floors_named("1st Podium Floor") == {"P1"} and floors_named("1st Floor") == {"L1"}
    assert floors_named("B1 to B3 Basement") == floors_named("1ST TO 3RD BASEMENT") == {"B1", "B2", "B3"}
    assert floor_label("119 STRUCTURAL SLAB") == "Structural Slab" and floor_label("3RD BASEMENT FLOOR") == "3rd Basement Floor"


def test_each_ifc_floor_plan_is_a_row_with_its_shop_drawing_at_every_revision():
    records = [
        _doc("3rd Basement", "R0", "rejected", reply="Revise as per comments"),
        _doc("3rd Basement", "R1", "UR", day=2),
        _doc("3rd Basement", "R1", "approved", day=3),          # the reply: decided beats under review
        _doc("Ground Floor", "R0", "ANN"),
        _doc("1st Podium Floor", "R0", "UR"),                  # a podium, not floor 1
        _doc("TYPICAL 3RD TO 16TH FLOOR", "R0", "approved"),
        _doc("Level 7", "R1", "rejected", day=5),              # one floor of the typical range
        _doc(None, "R0", "UR", ref="EP-SDW-FA-099"),            # no floor on it: listed apart
        _doc("Level 40", "R0", "UR", ref="EP-SDW-FA-098"),      # a floor no IFC plan has
        _doc("Ground Floor", "R0", "approved", system="ELS"),   # another system's drawing
    ]
    out = build(IFC, records)
    assert out["revisions"] == ["R0", "R1", "R2"] and out["submissions"] == 9
    rows = {r["sheet"]: r for r in out["rows"]}
    assert list(rows) == ["FA 101", "FA 104", "FA 105", "FA 109", "FA 111", "FA 113"]   # plans only, sheet order

    b3 = rows["FA 101"]
    assert [b3["cells"][r]["status"] for r in ("R0", "R1", "R2")] == ["not_approved", "approved", "not_submitted"]
    assert b3["latest_revision"] == "R1" and b3["latest_status"] == "approved"
    assert rows["FA 104"]["cells"]["R0"]["status"] == "approved_as_noted"
    assert rows["FA 105"]["cells"]["R0"]["status"] == "under_review"
    assert rows["FA 109"]["latest_status"] == "not_submitted" and rows["FA 109"]["latest_revision"] is None

    typical = rows["FA 111"]
    assert typical["floors"] == 14 and typical["floor"] == "Typical 3rd to 16th Floor"
    assert typical["cells"]["R0"]["status"] == "approved" and typical["cells"]["R1"]["status"] == "not_approved"
    assert rows["FA 113"]["floor"] == "Structural Slab"

    assert sorted(u["reference"] for u in out["unplaced"]) == ["EP-SDW-FA-098", "EP-SDW-FA-099"]
    assert out["counts"] == {"approved": 1, "approved_as_noted": 1, "under_review": 1, "not_approved": 1, "not_submitted": 2}


def test_revisions_grow_with_what_was_submitted():
    out = build(IFC, [_doc("Ground Floor", "R4", "UR")])
    assert out["revisions"] == ["R0", "R1", "R2", "R3", "R4"]


def test_the_drawings_log_endpoint(client, tmp_path):
    assert login(client, settings.default_admin_email, settings.default_admin_password).status_code == 200
    project_id = client.post("/projects", json={"ep_number": "91040", "project_name": "Log", "design_sheets": []}).json()["id"]
    empty = client.get(f"/projects/{project_id}/drawings/log").json()
    assert empty["rows"] == [] and empty["revisions"] == ["R0", "R1", "R2"]

    doc = ezdxf.new("R2018")
    block = doc.blocks.new("SD")
    block.add_circle((0, 0), 200)
    doc.modelspace().add_blockref("SD", (0, 0))
    path = Path(tmp_path / "GROUND FLOOR FIRE ALARM LAYOUT.dxf")
    doc.saveas(path)
    client.post(f"/projects/{project_id}/ifc-drawings", files={"file": (path.name, path.read_bytes(), "application/dxf")})
    log = client.get(f"/projects/{project_id}/drawings/log").json()
    assert len(log["rows"]) == 1 and log["rows"][0]["latest_status"] == "not_submitted"
    assert log["ifc"][0]["revision"] == "R0"
    export = client.get(f"/projects/{project_id}/drawings/log/export.xlsx")
    assert export.status_code == 200 and "Drawings Log FAS.xlsx" in export.headers["content-disposition"]
    # Folders open only on the PC the platform runs on; the test client is not it.
    assert client.post(f"/projects/{project_id}/drawings/open-folder", json={}).status_code == 403


# --- Actions Required: the contractor's drawings, by folder ------------------------------------------


def test_an_item_is_received_when_its_folder_holds_a_file(client, tmp_path):
    assert login(client, settings.default_admin_email, settings.default_admin_password).status_code == 200
    folder = tmp_path / "EP-91041 Tower"
    folder.mkdir()
    project_id = client.post("/projects", json={"ep_number": "91041", "project_name": "Tower", "design_sheets": [],
                                                 "source_folder_path": str(folder), "contractor": "ACME Contracting"}).json()["id"]
    client.get(f"/projects/{project_id}")                                    # opening makes the structure
    fa = folder / "03- Drawings" / "IFC" / "Electrical" / "FA"
    fa.mkdir(parents=True, exist_ok=True)
    (fa / "FA LAYOUT.dwg").write_bytes(b"AC1032")
    (fa / "FA LAYOUT.dwl").write_bytes(b"lock")                              # AutoCAD's lock file is not a drawing
    sm = folder / "03- Drawings" / "IFC" / "Mechanical" / "SM"
    sm.mkdir(parents=True, exist_ok=True)
    (sm / "desktop.ini").write_text("x")                                    # nor is Windows' own file
    title = folder / "03- Drawings" / "Title Block"
    title.mkdir(parents=True, exist_ok=True)
    (title / "Title block A1.dwg").write_bytes(b"AC1032")

    out = client.get(f"/projects/{project_id}/drawings/required").json()
    items = {i["key"]: i for g in out["groups"] for i in g["items"]}
    assert [g["name"] for g in out["groups"]] == ["Electrical IFC Drawings", "Mechanical IFC Drawings", "Others"]
    assert out["total"] == 6 and out["received"] == 2 and out["not_received"] == 4
    assert items["fa_ifc"]["received"] and items["fa_ifc"]["file_count"] == 1 and items["fa_ifc"]["received_date"]
    assert items["title_block"]["received"] and not items["sm_ifc"]["received"] and not items["acs_ifc"]["received"]

    sent = client.post(f"/projects/{project_id}/drawings/required/request", json={"keys": ["sm_ifc", "acs_ifc", "nope"]}).json()
    assert sent["items"] == ["Smoke Management IFC Drawings", "Access Control System"]
    assert "Dear ACME Contracting" in sent["body"] and "EP-91041" in sent["subject"]
    items = {i["key"]: i for g in client.get(f"/projects/{project_id}/drawings/required").json()["groups"] for i in g["items"]}
    assert items["sm_ifc"]["requested_at"] and items["sm_ifc"]["remarks"].startswith("Requested from the contractor")
    assert items["ff_ifc"]["requested_at"] is None
    assert client.post(f"/projects/{project_id}/drawings/required/request", json={"keys": []}).status_code == 422


def test_each_system_has_its_own_list(client, tmp_path):
    assert login(client, settings.default_admin_email, settings.default_admin_password).status_code == 200
    folder = tmp_path / "EP-91042 Mall"
    folder.mkdir()
    project_id = client.post("/projects", json={"ep_number": "91042", "project_name": "Mall", "design_sheets": [],
                                                 "source_folder_path": str(folder)}).json()["id"]
    client.get(f"/projects/{project_id}")                                    # opening makes the structure
    assert (folder / "03- Drawings" / "IFC" / "Electrical" / "Load Schedule").is_dir()
    (folder / "03- Drawings" / "IFC" / "Electrical" / "Load Schedule" / "DB schedule.xlsx").write_bytes(b"PK")

    els = client.get(f"/projects/{project_id}/drawings/required?system=ELS").json()
    assert els["system"] == "ELS" and [g["name"] for g in els["groups"]] == ["Electrical Drawings", "Others"]
    items = {i["key"]: i for g in els["groups"] for i in g["items"]}
    assert set(items) == {"els_lighting_ifc", "els_fa_ifc", "els_load_schedule"} and els["received"] == 1
    assert items["els_load_schedule"]["format"] == "PDF / XLS" and items["els_load_schedule"]["received"]
    assert items["els_lighting_ifc"]["remarks"] == "Required for ELS design coordination"

    fas = client.get(f"/projects/{project_id}/drawings/required").json()
    assert fas["system"] == "FAS" and fas["total"] == 6                     # the fire alarm list is its own
    sent = client.post(f"/projects/{project_id}/drawings/required/request", json={"keys": ["els_lighting_ifc"]}).json()
    assert "emergency lighting shop drawings" in sent["body"] and "(IFC / DWG)" in sent["body"]
    items = {i["key"]: i for g in client.get(f"/projects/{project_id}/drawings/required?system=ELS").json()["groups"] for i in g["items"]}
    assert items["els_lighting_ifc"]["requested_at"]
    xlsx = client.get(f"/projects/{project_id}/drawings/required/export.xlsx?system=ELS")
    assert xlsx.status_code == 200 and "Actions Required ELS.xlsx" in xlsx.headers["content-disposition"]
    assert client.get(f"/projects/{project_id}/drawings/required?system=XYZ").status_code == 404
