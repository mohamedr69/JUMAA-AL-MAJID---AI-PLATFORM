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


def test_each_tab_exports_its_schedule_of_material_with_the_added_materials_on_it(client, db_session):
    _login(client)
    project_id = client.post("/projects", json={
        "ep_number": "30794", "project_name": "Titania", "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                    {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}],
    }).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[
        {"system_code": "FAS", "group_heading": "Main Panel", "catalog_no": "4-CPU", "description": "Central Processor Module", "quantity": "1", "manufacturer": "EDWARDS"},
        {"system_code": "ELS", "group_heading": "Lights", "catalog_no": "SL2NM65D3-M", "description": "Surface Emergency Light", "quantity": "6", "manufacturer": "MENVIER"},
    ])
    assert client.post(f"/projects/{project_id}/materials", json={"system_code": "FAS", "catalog_no": "SIGA-LED", "description": "Remote alarm LED"}).status_code == 201

    fire = client.get(f"/projects/{project_id}/materials/schedule.pdf?system_code=FAS")
    assert fire.status_code == 200 and fire.headers["content-type"] == "application/pdf"
    assert "Schedule of Material - FAS" in fire.headers["content-disposition"]
    text = "\n".join(page.get_text() for page in pymupdf.open(stream=fire.content, filetype="pdf"))
    assert "4-CPU" in text and "SIGA-LED" in text and "PROPOSED MATERIALS" in text
    assert "SL2NM65D3-M" not in text                      # the other system's

    lighting = client.get(f"/projects/{project_id}/materials/schedule.pdf?system_code=ELS")
    text = "\n".join(page.get_text() for page in pymupdf.open(stream=lighting.content, filetype="pdf"))
    assert "SL2NM65D3-M" in text and "4-CPU" not in text and "PROPOSED MATERIALS" not in text


def test_the_schedule_reads_panel_then_repeaters_then_aps_and_bps_then_field_devices(client, db_session):
    from app.models import Project
    from app.services.submittal_package import schedule_blocks

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30795", "project_name": "Titania", "design_sheets": []}).json()["id"]
    line = lambda heading, part: {"system_code": "FAS", "group_heading": heading, "catalog_no": part, "description": part,  # noqa: E731
                                  "quantity": "1", "manufacturer": "EDWARDS"}
    # The sheet's order: field devices first, the panel last.
    client.put(f"/projects/{project_id}/boq", json=[
        line("Field Devices", "SIGA-PS"),
        line("Booster Power Supply", "BPS10A"),
        line("Auxiliary Power Supply", "APS6A"),
        line("Repeater Panel", "4-2ANN"),
        line("EST4 Main Fire Alarm Control Panel", "4-CPU"),
        line("Notification Appliances", "G1AVRN"),
    ])
    client.post(f"/projects/{project_id}/materials", json={"system_code": "FAS", "catalog_no": "SIGA-LED"})
    project = db_session.get(Project, project_id)
    titles = [title for _letter, title, _items in schedule_blocks(project, "FAS")]
    assert titles == ["EST4 Main Fire Alarm Control Panel", "Repeater Panel", "Auxiliary Power Supply", "Booster Power Supply",
                      "Initiating Devices", "Notification Appliances", "Proposed materials (added beyond the BOQ)"]
    assert [letter for letter, _t, _i in schedule_blocks(project, "FAS")] == list("ABCDEFG")


def test_the_field_devices_are_scheduled_by_kind(client, db_session):
    """The sheet's one "Field Devices" block is scheduled as initiating
    devices, notification appliances, fire telephone, BMS gateway, modules
    and back boxes -- and a gateway quoted with the panel is the gateway's."""
    from app.models import Project
    from app.services.submittal_package import schedule_blocks

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30796", "project_name": "Titania", "design_sheets": []}).json()["id"]
    line = lambda heading, part, desc: {"system_code": "FAS", "group_heading": heading, "catalog_no": part, "description": desc,  # noqa: E731
                                        "quantity": "1", "manufacturer": "EDWARDS"}
    client.put(f"/projects/{project_id}/boq", json=[
        line("EST4 Main Fire Alarm Control Panel", "4-CPU", "Central Processor Module"),
        line("EST4 Main Fire Alarm Control Panel", "FSB-PC4", "EST4 to BMS Communications Bridge"),
        line("Super Duct", "SIGA-SD", "SuperDuct"),
        line("Field Devices", "SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector"),
        line("Field Devices", "SIGA-278", "Manual Pull Station"),
        line("Field Devices", "STI-3002", "Weatherresistant Gasket"),
        line("Field Devices", "G4SRN", "Wall Speaker, Red"),
        line("Field Devices", "757-7A-T", "15/75 cd Temporal Horn/Strobe"),
        line("Field Devices", "SIGA-LED", "Remote Alarm LED"),
        line("Field Devices", "6830-3", "Portable Telephone Handset"),
        line("Field Devices", "TCS-6", "Storage Enclosure for handsets"),
        line("Field Devices", "SIGA-CT2", "Dual Input Module"),
        line("Field Devices", "SIGA-UM", "Universal Class A/B Module"),
        line("Field Devices", "TP606", "GI Concealed Back Box"),
        line("Field Devices", "27193-11", "Surface Mount Box"),
        line("Field Devices", "GRSW-10", "Universal Wiring Plate"),
        line("Field Devices", "757A-WB", "Weatherproof Box, Cast"),
        line("Field Devices", "MYSTERY-1", "Something new"),
    ])
    blocks = {title: [i.catalog_no for i in items] for _l, title, items in schedule_blocks(db_session.get(Project, project_id), "FAS")}
    assert list(blocks) == ["EST4 Main Fire Alarm Control Panel", "Initiating Devices", "Notification Appliances", "Fire Telephone",
                            "BMS Gateway", "Modules", "Back Boxes", "Other Field Devices"]
    assert blocks["EST4 Main Fire Alarm Control Panel"] == ["4-CPU"] and blocks["BMS Gateway"] == ["FSB-PC4"]
    assert blocks["Initiating Devices"] == ["SIGA-SD", "SIGA-OSD-FCN", "SIGA-278", "STI-3002"]
    assert blocks["Notification Appliances"] == ["G4SRN", "757-7A-T", "SIGA-LED"]
    assert blocks["Fire Telephone"] == ["6830-3", "TCS-6"]
    assert blocks["Modules"] == ["SIGA-CT2", "SIGA-UM"]
    assert blocks["Back Boxes"] == ["TP606", "27193-11", "GRSW-10", "757A-WB"]
    assert blocks["Other Field Devices"] == ["MYSTERY-1"]
