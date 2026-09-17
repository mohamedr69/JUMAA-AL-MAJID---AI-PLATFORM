"""Emergency lighting by its kind, the battery calculation only where it
applies, and the Proposed Materials tab with its part-number catalogue."""

from pathlib import Path

import pymupdf

from app.core.config import get_settings
from app.models import Project, ProjectSystem
from app.services import part_catalog, system_rules

from .conftest import login
from .test_submittal_package import _datasheets, _library

settings = get_settings()


def _login(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def _system(name, brand):
    return ProjectSystem(name=name, brand=brand, method_statement=True, drawing=True)


def test_emergency_lighting_is_self_contained_or_central_battery_and_only_the_fire_alarm_has_a_battery_calculation():
    self_contained = Project(ep_number="1", project_name="A", systems=[_system("Fire Alarm", "EDWARDS"), _system("Emergency Light Monitoring", "MENVIER")])
    central = Project(ep_number="2", project_name="B", systems=[_system("Fire Alarm", "EDWARDS"), _system("Central Battery System", "MENVIER")])
    assert system_rules.emergency_lighting_kinds(self_contained.systems) == {"self_contained"}
    assert system_rules.emergency_lighting_kinds(central.systems) == {"central_battery"}
    assert system_rules.system_display_name(self_contained, "ELS") == "Monitored Self-Contained Emergency Light System"
    assert system_rules.system_display_name(central, "EML") == "Central Battery System"
    assert system_rules.system_display_name(central, "FAS") == "Fire Alarm"

    assert system_rules.battery_calculation_applies(self_contained, "FAS") == (True, None)
    applies, why = system_rules.battery_calculation_applies(self_contained, "ELS")
    assert applies is False and "self-contained" in why
    applies, why = system_rules.battery_calculation_applies(central, "CBS")
    assert applies is False and "not built yet" in why


def test_the_package_plan_offers_no_battery_section_for_a_self_contained_system(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login(client)
    project_id = client.post("/projects", json={
        "ep_number": "30790", "project_name": "Titania", "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                    {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}],
    }).json()["id"]

    fire = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=FAS").json()
    assert fire["battery_calculation"]["applies"] is True
    assert any(s["number"] == 6 for s in fire["sections"])

    lighting = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=ELS").json()
    assert lighting["battery_calculation"]["applies"] is False and "self-contained" in lighting["battery_calculation"]["reason"]
    assert all(s["number"] != 6 for s in lighting["sections"])          # no checkbox to tick


def test_the_part_catalogue_knows_every_number_on_file_for_a_brand_and_completes_it(client, db_session, tmp_path, monkeypatch):
    import app.routers.materials as materials_router
    import app.services.part_catalog as catalog_module
    from app.services.datasheet_library import DatasheetLibrary

    edwards = tmp_path / "EST4"
    menvier = tmp_path / "MENVIER"
    for path, lines in [
        (edwards / "01- PANEL" / "01- 4-CPU.pdf", ["4-CPU Central Processor Module"]),
        (edwards / "01- PANEL" / "3-SDDC2 & 3-SSDC2.pdf", ["Loop controllers"]),
        (edwards / "02- Initiating Devices" / "China" / "01- SIGA-OSD-FCN.pdf", ["Smoke detector"]),
        (edwards / "00- submittal" / "EST4 Technical Reference Manual.pdf", ["Manual"]),
        (menvier / "CTR160CGL2KS-M.pdf", ["Menvier luminaire"]),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        doc.new_page().insert_text((40, 50), "\n".join(lines), fontsize=9)
        doc.save(path)
        doc.close()
    libraries = {"EDWARDS": DatasheetLibrary("EDWARDS", edwards), "MENVIER": DatasheetLibrary("MENVIER", menvier)}
    monkeypatch.setattr(catalog_module, "get_libraries", lambda: libraries)
    monkeypatch.setattr(materials_router, "get_libraries", lambda: libraries)
    _login(client)
    project_id = client.post("/projects", json={
        "ep_number": "30791", "project_name": "Titania", "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                    {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}],
    }).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[
        {"system_code": "FAS", "group_heading": "Panel", "catalog_no": "4-CPU", "description": "Central Processor Module", "quantity": "1", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "group_heading": "Field", "catalog_no": "SIGA-PS", "description": "Photoelectric smoke detector", "quantity": "120", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "group_heading": "Field", "catalog_no": "12", "description": "GRSW-10", "quantity": "1", "manufacturer": "EDWARDS"},
    ])

    numbers = {e.part_no for e in part_catalog.catalog(db_session, "EDWARDS")}
    assert {"4-CPU", "3-SDDC2", "3-SSDC2", "SIGA-OSD-FCN", "SIGA-PS"} <= numbers    # datasheet names, the BOQ
    assert "12" not in numbers and "EST4 Technical Reference Manual" not in numbers    # not parts
    assert "CTR160CGL2KS-M" not in numbers                                            # the other brand's
    assert {e.part_no for e in part_catalog.catalog(db_session, "MENVIER")} >= {"CTR160CGL2KS-M"}
    by_number = {e.part_no: e for e in part_catalog.catalog(db_session, "EDWARDS")}
    assert by_number["SIGA-PS"].description == "Photoelectric smoke detector" and "BOQ" in by_number["SIGA-PS"].sources

    found = client.get("/parts/search?brand=EDWARDS&q=sig").json()
    assert [s["part_no"] for s in found][:2] == ["SIGA-OSD-FCN", "SIGA-PS"]
    assert client.get("/parts/search?brand=EDWARDS&q=3-s").json()[0]["part_no"] in ("3-SDDC2", "3-SSDC2")
    assert [s["part_no"] for s in client.get("/parts/search?brand=MENVIER&q=ctr").json()] == ["CTR160CGL2KS-M"]

    # The tab: the BOQ's parts per system, then one added with no quantity.
    page = client.get(f"/projects/{project_id}/materials").json()
    assert [(s["code"], s["brand"]) for s in page["systems"]] == [("FAS", "EDWARDS"), ("ELS", "MENVIER")]
    assert next(s for s in page["systems"] if s["code"] == "ELS")["title"] == "Monitored Self-Contained Emergency Light System"
    assert [(i["part_no"], i["source"]) for i in page["items"] if i["system_code"] == "FAS"] == [("12", "boq"), ("4-CPU", "boq"), ("SIGA-PS", "boq")]

    added = client.post(f"/projects/{project_id}/materials", json={"system_code": "FAS", "catalog_no": "SIGA-OSD-FCN"})
    assert added.status_code == 201, added.text
    assert added.json()["manufacturer"] == "EDWARDS" and added.json()["quantity"] is None and added.json()["source"] == "added"
    assert added.json()["datasheet_filename"] == "01- SIGA-OSD-FCN.pdf"
    page = client.get(f"/projects/{project_id}/materials").json()
    assert ("SIGA-OSD-FCN", "added") in [(i["part_no"], i["source"]) for i in page["items"]]
    # Withdrawn again.
    assert client.delete(f"/projects/{project_id}/materials/{added.json()['id']}").status_code == 204
    assert all(i["source"] == "boq" for i in client.get(f"/projects/{project_id}/materials").json()["items"])


def test_a_part_the_file_names_do_not_carry_is_documented_by_the_datasheet_linked_to_it(client, db_session, tmp_path, monkeypatch):
    """NEXI300-3H-CGL-IPM is on NEXI300-3H-CGL.pdf and every SL2 exit sign
    on SL2-42D3D-CGL-M.pdf: linked once, found on every project, and a
    link set from the tab holds the same way."""
    import app.routers.materials as materials_router
    import app.routers.submittal as submittal_router
    from app.models import PartDatasheetLink
    from app.services import datasheet_links
    from app.services.datasheet_library import DatasheetLibrary

    menvier = tmp_path / "MENVIER"
    for name in ("NEXI300-3H-CGL.pdf", "SL2-42D3D-CGL-M.pdf", "CTR160CGL2KS-M.pdf"):
        doc = pymupdf.open()
        doc.new_page().insert_text((40, 50), name, fontsize=9)
        menvier.mkdir(parents=True, exist_ok=True)
        doc.save(menvier / name)
        doc.close()
    libraries = {"MENVIER": DatasheetLibrary("MENVIER", menvier)}
    monkeypatch.setattr(materials_router, "get_libraries", lambda: libraries)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: libraries)
    assert datasheet_links.seed(db_session) >= 0                      # the seeds (startup ran them already, or not)
    _login(client)
    project_id = client.post("/projects", json={
        "ep_number": "30792", "project_name": "Titania", "design_sheets": [],
        "systems": [{"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}],
    }).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[
        {"system_code": "ELS", "group_heading": "Lights", "catalog_no": "NEXI300-3H-CGL-IPM", "description": "Nexi IP65", "quantity": "377", "manufacturer": "MENVIER"},
        {"system_code": "ELS", "group_heading": "Lights", "catalog_no": "SL2-65D3D-CGL-M+SL23I", "description": "Wall exit", "quantity": "33", "manufacturer": "MENVIER"},
        {"system_code": "ELS", "group_heading": "Lights", "catalog_no": "SL2NM65D3-M", "description": "Self contained", "quantity": "6", "manufacturer": "MENVIER"},
        {"system_code": "ELS", "group_heading": "Lights", "catalog_no": "XYZ-999", "description": "Unknown", "quantity": "1", "manufacturer": "MENVIER"},
    ])

    by_part = {i["part_no"]: i for i in client.get(f"/projects/{project_id}/materials").json()["items"]}
    assert by_part["NEXI300-3H-CGL-IPM"]["datasheet_filename"] == "NEXI300-3H-CGL.pdf" and by_part["NEXI300-3H-CGL-IPM"]["datasheet_linked"] is True
    assert by_part["SL2-65D3D-CGL-M+SL23I"]["datasheet_filename"] == "SL2-42D3D-CGL-M.pdf"
    assert by_part["SL2NM65D3-M"]["datasheet_filename"] == "SL2-42D3D-CGL-M.pdf"
    assert by_part["XYZ-999"]["datasheet_path"] is None
    # The material submittal's own list reads the same link.
    materials = {i["part_no"]: i for i in client.get(f"/projects/{project_id}/submittal/materials").json()["items"]}
    assert materials["SL2NM65D3-M"]["datasheet_filename"] == "SL2-42D3D-CGL-M.pdf"

    # A link set from the tab: for every project from then on.
    linked = client.put("/materials/datasheet-link", json={"manufacturer": "MENVIER", "part_no": "XYZ-999", "library": "MENVIER", "path": "CTR160CGL2KS-M.pdf"})
    assert linked.status_code == 200, linked.text
    assert client.get(f"/projects/{project_id}/materials").json()["items"][-1]["datasheet_filename"] == "CTR160CGL2KS-M.pdf"
    other = client.post("/projects", json={"ep_number": "30793", "project_name": "Other", "design_sheets": []}).json()["id"]
    client.put(f"/projects/{other}/boq", json=[{"system_code": "ELS", "group_heading": "L", "catalog_no": "xyz 999", "description": "u", "quantity": "1", "manufacturer": "Menvier Brand"}])
    assert client.get(f"/projects/{other}/materials").json()["items"][0]["datasheet_filename"] == "CTR160CGL2KS-M.pdf"
    # A file not in the library is refused; unlinking is for good.
    assert client.put("/materials/datasheet-link", json={"manufacturer": "MENVIER", "part_no": "XYZ-999", "library": "MENVIER", "path": "nope.pdf"}).status_code == 404
    assert client.delete("/materials/datasheet-link?manufacturer=MENVIER&part_no=XYZ-999").status_code == 204
    assert db_session.query(PartDatasheetLink).filter(PartDatasheetLink.key == "XYZ999").count() == 0
