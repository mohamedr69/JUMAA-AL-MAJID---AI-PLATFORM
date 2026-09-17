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
    # A system that never has a battery calculation says nothing on the page.
    assert lighting["battery_calculation"]["note"] is None and fire["battery_calculation"]["note"] is None

    # A central battery system's calculation is coming: that one is worth saying.
    central = client.post("/projects", json={
        "ep_number": "30807", "project_name": "Other", "design_sheets": [],
        "systems": [{"name": "Central Battery System", "brand": "MENVIER", "method_statement": True, "drawing": True}],
    }).json()["id"]
    note = client.get(f"/projects/{central}/submittal/package/plan?system_code=ELS").json()["battery_calculation"]
    assert note["applies"] is False and note["note"] == "The central battery system's battery calculation is not built yet."


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


def test_a_file_name_with_a_stray_space_still_documents_its_part(tmp_path):
    from app.services.datasheet_library import DatasheetLibrary

    root = tmp_path / "EST4"
    root.mkdir()
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "Universal Class A/B Module", fontsize=9)
    doc.save(root / "SIGA -UM.pdf")
    doc.close()
    match = DatasheetLibrary("EDWARDS", root).find("SIGA-UM")
    assert match and match[0].filename == "SIGA -UM.pdf" and match[0].matched_on == "filename"


def test_the_batteries_the_calculation_selects_are_materials_and_on_the_schedule(client, db_session):
    """The BOQ quotes 12V10A; the calculation selects the ROCKET unit that
    covers the panel: that unit is proposed, on the tab and on the schedule."""
    from app.models import Project
    from app.services.submittal_package import schedule_blocks
    from .test_battery_api import _current

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30797", "project_name": "Titania", "design_sheets": [],
                                               "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}]}).json()["id"]
    heading = "EST4 Main Fire Alarm Control Panel"
    client.put(f"/projects/{project_id}/boq", json=[
        {"system_code": "FAS", "group_heading": heading, "catalog_no": "4-CPU", "description": "CPU", "quantity": "1", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "group_heading": heading, "catalog_no": "12V10A", "description": "Battery, 12 V @ 10 AH", "quantity": "2", "manufacturer": "EDWARDS"},
        {"system_code": "FAS", "group_heading": "Field Devices", "catalog_no": "SIGA-PS", "description": "Smoke detector", "quantity": "10", "manufacturer": "EDWARDS"},
    ])
    assert _current(client, "4-CPU", 211, 211).status_code == 200
    assert _current(client, "SIGA-PS", 0.045, 0.045).status_code == 200
    for part, ah in (("ES7-12", 7), ("ES18-12", 18), ("ES26-12", 26)):
        assert client.post("/design-rules/battery-units", json={"part_no": part, "capacity_ah": ah, "voltage": 12, "brand": "ROCKET",
                                                                 "source": "ROCKET datasheet"}).status_code == 200
    calculation = client.get(f"/projects/{project_id}/design/battery").json()
    panel = next(p for p in calculation["panels"] if p["kind"] == "panel")
    assert panel["selected"], panel["status"]
    chosen = panel["selected"][0]["part_no"]

    items = client.get(f"/projects/{project_id}/materials").json()["items"]
    battery = next(i for i in items if i["source"] == "battery")
    assert battery["part_no"] == chosen and battery["system_code"] == "FAS" and battery["quantity"] == panel["selected"][0]["units"]
    assert battery["groups"][0].startswith("Battery calculation: ")
    assert "12V10A" not in [i["part_no"] for i in items]          # the BOQ's battery gave way to the selection

    # On the schedule the selection stands where the BOQ's battery was: in the panel's own block.
    blocks = {title: [i.catalog_no for i in lines] for _l, title, lines in schedule_blocks(db_session.get(Project, project_id), "FAS")}
    assert list(blocks) == [heading, "Initiating Devices"]
    assert blocks[heading] == ["4-CPU", chosen]


def test_a_self_contained_emergency_light_schedule_is_panel_lights_and_exit_lights(client, db_session):
    from app.models import Project
    from app.services.submittal_package import schedule_blocks

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30798", "project_name": "Titania", "design_sheets": [],
                                               "systems": [{"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]
    line = lambda part, desc: {"system_code": "ELS", "group_heading": "Emergency Lighting", "catalog_no": part, "description": desc,  # noqa: E731
                               "quantity": "1", "manufacturer": "MENVIER"}
    client.put(f"/projects/{project_id}/boq", json=[
        line("SL2-42D3D-CGL-M+SL23I", "Wall Mounted Exit, 20 metre viewing distance"),
        line("NEXI300-3H-CGL-IPM", "Surface mounted Nexi Emergency Light, IP65"),
        line("CTR400CGL2KS-M", "Menvier Brand CGLine+ Web Compact Controller"),
        line("RT2RHEO200CGL3HIPM", "RTECH MR HEO CGL+ 200 MNM 3H IP65"),
        line("SL2NM65D3-M", "Surface Emergency Light - SELF CONTAINED"),
    ])
    blocks = {title: [i.catalog_no for i in lines] for _l, title, lines in schedule_blocks(db_session.get(Project, project_id), "ELS")}
    assert list(blocks) == ["Emergency Light Panel", "Emergency Light", "Exit Light"]
    assert blocks["Emergency Light Panel"] == ["CTR400CGL2KS-M"]
    assert blocks["Emergency Light"] == ["NEXI300-3H-CGL-IPM", "RT2RHEO200CGL3HIPM", "SL2NM65D3-M"]
    assert blocks["Exit Light"] == ["SL2-42D3D-CGL-M+SL23I"]


def test_a_full_package_project_has_fire_rated_cables_as_a_system_with_nothing_in_it_yet(client, db_session, tmp_path):
    from app.models import Project
    from app.services import project_folders

    _login(client)
    root = tmp_path / "EP-30799"
    root.mkdir()
    project_id = client.post("/projects", json={
        "ep_number": "30799", "project_name": "Titania", "design_sheets": [], "source_folder_path": str(root),
        "scope_of_work": "Full Package",
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}],
    }).json()["id"]
    project = db_session.get(Project, project_id)
    assert system_rules.is_full_package(project)
    assert system_rules.project_codes(project) == ["FAS", "FRC"]
    assert system_rules.system_display_name(project, "FRC") == "Fire Rated Cables"
    # Its folders were made with the project.
    assert (root / "02- Material Submittals" / "FRC" / "R0").is_dir() and (root / "02- Material Submittals" / "Approved" / "FRC").is_dir()
    assert project_folders.system_folder("FRC") == "FRC"
    # The tabs: proposed materials and the register both show FRC, empty.
    materials = client.get(f"/projects/{project_id}/materials").json()
    assert [(s["code"], s["title"]) for s in materials["systems"]] == [("FAS", "Fire Alarm"), ("FRC", "Fire Rated Cables")]
    assert [i for i in materials["items"] if i["system_code"] == "FRC"] == []
    register = client.get(f"/projects/{project_id}/submittals").json()
    assert "FRC" in register["systems"]

    # A supply-only project has no FRC.
    other = client.post("/projects", json={"ep_number": "30800", "project_name": "Other", "design_sheets": [], "scope_of_work": "Supply Only",
                                          "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}]}).json()["id"]
    assert system_rules.project_codes(db_session.get(Project, other)) == ["FAS"]


def test_the_frc_cables_are_chosen_by_brand_and_size_and_a_size_against_the_standard_is_warned(client, db_session):
    from app.models import Project
    from app.services.submittal_package import schedule_blocks

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30801", "project_name": "Titania", "design_sheets": [], "scope_of_work": "Full Package",
                                               "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}]}).json()["id"]
    empty = client.get(f"/projects/{project_id}/frc-cables").json()
    assert empty["brand"] is None and empty["brands"] == ["FIREGUARD", "SOLARTI", "FRONTIER", "TIANJIE"] and empty["sizes"] == ["2Cx1.5mm", "2Cx2.5mm"]
    assert [c["name"] for c in empty["cables"]] == ["Fire alarm loop cable", "Voice evacuation cable", "24 VDC power cable", "Fire telephone power cable"]

    saved = client.put(f"/projects/{project_id}/frc-cables", json={"brand": "fireguard", "fire_alarm_loop": "2Cx2.5mm", "voice_evacuation": "2Cx1.5mm",
                                                                    "power_24vdc": "2Cx1.5mm", "fire_telephone": "2Cx2.5mm"}).json()
    assert saved["brand"] == "FIREGUARD"
    warnings = {c["field"]: c["warning"] for c in saved["cables"]}
    assert "1.5 mm2 shall be used" in warnings["fire_alarm_loop"]
    assert "voltage drop" in warnings["voice_evacuation"] and "voltage drop" in warnings["power_24vdc"]
    assert "1.5 mm2 shall be used" in warnings["fire_telephone"]

    standard = client.put(f"/projects/{project_id}/frc-cables", json={"brand": "FIREGUARD", "fire_alarm_loop": "2Cx1.5mm", "voice_evacuation": "2Cx2.5mm",
                                                                       "power_24vdc": "2Cx2.5mm", "fire_telephone": "2Cx1.5mm"}).json()
    assert all(c["warning"] is None for c in standard["cables"])
    assert client.put(f"/projects/{project_id}/frc-cables", json={"brand": "ACME"}).status_code == 400
    assert client.put(f"/projects/{project_id}/frc-cables", json={"brand": "SOLARTI", "fire_alarm_loop": "4Cx1.5mm"}).status_code == 400

    # The cables are the FRC system's materials, on the tab and on its schedule:
    # the sizes as chosen, one line per size naming the systems it serves, no prefix.
    items = [i for i in client.get(f"/projects/{project_id}/materials").json()["items"] if i["system_code"] == "FRC"]
    assert [(i["part_no"], i["manufacturer"], i["source"]) for i in items] == [("2Cx1.5mm", "FIREGUARD", "cable"), ("2Cx2.5mm", "FIREGUARD", "cable")]
    assert items[0]["description"] == "Fire rated cable, 2C x 1.5 mm2, for the fire alarm & fire telephone systems"
    assert items[1]["description"] == "Fire rated cable, 2C x 2.5 mm2, for the voice evacuation & 24 VDC power systems"
    blocks = {title: [(l.catalog_no, l.manufacturer) for l in lines] for _l, title, lines in schedule_blocks(db_session.get(Project, project_id), "FRC")}
    assert list(blocks) == ["Fire Rated Cables"] and blocks["Fire Rated Cables"] == [("2Cx1.5mm", "FIREGUARD"), ("2Cx2.5mm", "FIREGUARD")]
    # One size for all four: one line.
    client.put(f"/projects/{project_id}/frc-cables", json={"brand": "FIREGUARD", "fire_alarm_loop": "2Cx1.5mm", "voice_evacuation": "2Cx1.5mm",
                                                            "power_24vdc": "2Cx1.5mm", "fire_telephone": "2Cx1.5mm"})
    items = [i for i in client.get(f"/projects/{project_id}/materials").json()["items"] if i["system_code"] == "FRC"]
    assert [i["part_no"] for i in items] == ["2Cx1.5mm"]
    assert items[0]["description"] == "Fire rated cable, 2C x 1.5 mm2, for the fire alarm, voice evacuation, 24 VDC power & fire telephone systems"


def test_the_schedule_prints_the_whole_part_number(client, db_session):
    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30802", "project_name": "Titania", "design_sheets": [],
                                               "systems": [{"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[{"system_code": "ELS", "group_heading": "Lights", "catalog_no": "SL2-65D3D-CGL-M +SL2CD +SL2DC3I",
                                                     "description": "Exit Directional, Hanging", "quantity": "36", "manufacturer": "MENVIER"}])
    pdf = client.get(f"/projects/{project_id}/materials/schedule.pdf?system_code=ELS").content
    text = "\n".join(page.get_text() for page in pymupdf.open(stream=pdf, filetype="pdf"))
    assert "SL2-65D3D-CGL-M +SL2CD +SL2DC3I" in text


def test_a_monitored_self_contained_system_has_its_monitoring_cable_taken_as_given(client, db_session):
    from app.models import Project
    from app.services.submittal_package import schedule_blocks

    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30803", "project_name": "Titania", "design_sheets": [], "scope_of_work": "Full Package",
                                               "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                                                           {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]
    cables = client.get(f"/projects/{project_id}/frc-cables").json()
    monitoring = cables["monitoring"]
    assert (monitoring["applies"], monitoring["name"], monitoring["brand"], monitoring["brands"], monitoring["size"]) == (
        True, "Emergency light monitoring cable", "RAMCRO", ["RAMCRO"], "2Cx1.5mm")
    # Nothing chosen yet, and the monitoring cable is already a material of the FRC system.
    items = [i for i in client.get(f"/projects/{project_id}/materials").json()["items"] if i["system_code"] == "FRC"]
    assert [(i["part_no"], i["manufacturer"], i["groups"][0]) for i in items] == [("2Cx1.5mm", "RAMCRO", "Emergency light monitoring cable")]
    blocks = {title: [(l.catalog_no, l.manufacturer) for l in lines] for _l, title, lines in schedule_blocks(db_session.get(Project, project_id), "FRC")}
    assert blocks == {"Fire Rated Cables": [("2Cx1.5mm", "RAMCRO")]}
    # Another brand or size is refused: there is one of each.
    assert client.put(f"/projects/{project_id}/frc-cables", json={"monitoring_brand": "ACME"}).status_code == 400
    assert client.put(f"/projects/{project_id}/frc-cables", json={"monitoring_size": "2Cx2.5mm"}).status_code == 400
    assert client.put(f"/projects/{project_id}/frc-cables", json={"brand": "SOLARTI", "monitoring_brand": "ramcro"}).json()["monitoring"]["brand"] == "RAMCRO"

    # A project with a central battery system, or none, has no monitoring cable.
    other = client.post("/projects", json={"ep_number": "30804", "project_name": "Other", "design_sheets": [], "scope_of_work": "Full Package",
                                          "systems": [{"name": "Central Battery System", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]
    assert client.get(f"/projects/{other}/frc-cables").json()["monitoring"]["applies"] is False
    assert [i for i in client.get(f"/projects/{other}/materials").json()["items"] if i["system_code"] == "FRC"] == []


def test_the_supplier_of_a_brand_is_on_file_for_every_project_and_shown_beside_the_brand(client, db_session):
    from app.services import suppliers

    suppliers.seed(db_session)
    _login(client)
    listed = {s["brand"]: s for s in client.get("/suppliers").json()}
    assert listed["FIREGUARD"]["supplier"] == "AL RAYAN SECURITY AND SAFETY TRADING" and "sales1@alrayandxb.com" in listed["FIREGUARD"]["emails"]
    assert listed["RAMCRO"]["contact"] == "Suresh K.S, Marketing Manager" and listed["RAMCRO"]["phone"] == "+971 55 5792971"

    project_id = client.post("/projects", json={"ep_number": "30805", "project_name": "Titania", "design_sheets": [], "scope_of_work": "Full Package",
                                               "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                                                           {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]
    cables = client.put(f"/projects/{project_id}/frc-cables", json={"brand": "FIREGUARD"}).json()
    assert cables["supplier"]["supplier"] == "AL RAYAN SECURITY AND SAFETY TRADING" and cables["supplier"]["website"] == "alrayandxb.com"
    assert cables["monitoring"]["supplier"]["supplier"] == "ubemirates" and cables["monitoring"]["supplier"]["emails"] == "suresh@ubemirates.com"
    assert client.put(f"/projects/{project_id}/frc-cables", json={"brand": "SOLARTI"}).json()["supplier"] is None   # none on file yet

    # An engineer's edit holds for every project; a seed never overwrites it.
    changed = client.put("/suppliers/solarti", json={"supplier": "Some Trading LLC", "emails": "info@some.ae"})
    assert changed.status_code == 200 and changed.json()["brand"] == "SOLARTI"
    assert client.get(f"/projects/{project_id}/frc-cables").json()["supplier"]["supplier"] == "Some Trading LLC"
    assert client.put("/suppliers/FIREGUARD", json={"supplier": "AL RAYAN SECURITY AND SAFETY TRADING", "phone": "+971 4 000 0000"}).json()["phone"] == "+971 4 000 0000"
    assert suppliers.seed(db_session) == 0
    assert client.get("/suppliers?brand=fireguard").json()[0]["phone"] == "+971 4 000 0000"
    assert client.put("/suppliers/ACME", json={"supplier": "  "}).status_code == 422 or client.put("/suppliers/ACME", json={"supplier": "  "}).status_code == 400


def _frc_library(tmp_path):
    """A submittal builder with the COMMON documents and FIREGUARD's own folder."""
    from .test_submittal_package import _pdf

    root = tmp_path / "submittal builder"
    _pdf(root / "COMMON" / "Company Profile" / "Company Profile.pdf", ["Company Profile"])
    _pdf(root / "COMMON" / "Trade License" / "Trade Licence.pdf", ["Trade Licence"])
    _pdf(root / "COMMON" / "ISO Certificates" / "ISO 9001.pdf", ["ISO 9001"])
    _pdf(root / "FIREGUARD" / "ISO" / "ALRAYAN ISO.pdf", ["Manufacturer ISO"])
    _pdf(root / "FIREGUARD" / "Civil defence certificate" / "Dubai" / "DCD.pdf", ["Civil Defence"])
    _pdf(root / "FIREGUARD" / "TEST CERTIFICATES" / "FIREGUARD_TEST_CERTIFICATE.pdf", ["Test Certificate"])
    _pdf(root / "FIREGUARD" / "AUTH" / "FIREGUARD_AUTH.pdf", ["Authorization"])
    _pdf(root / "FIREGUARD" / "PREVIOUS APPROVAL" / "FIREGUARD_PREVIOUS_APPROVAL.pdf", ["Previous Approval"])
    return root


def test_the_fire_rated_cable_submittal_follows_its_own_index_from_the_brands_folder(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router
    from app.models import Project
    from app.services.datasheet_library import DatasheetLibrary
    from app.services.submittal_package import FRC_SECTIONS, index_for, warranty_replacements

    assert [n for n, _ in FRC_SECTIONS] == list(range(1, 12))
    library = _frc_library(tmp_path)
    sheets = tmp_path / "FIREGUARD datasheets"
    sheets.mkdir()
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), "FIREGUARD fire rated cable", fontsize=9)
    doc.save(sheets / "FIREGUARD.pdf")
    doc.close()
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"FIREGUARD": DatasheetLibrary("FIREGUARD", sheets)})
    _login(client)
    project_id = client.post("/projects", json={"ep_number": "30806", "project_name": "Titania", "design_sheets": [], "scope_of_work": "Full Package",
                                               "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                                                           {"name": "Emergency Light Monitoring", "brand": "MENVIER", "method_statement": True, "drawing": True}]}).json()["id"]

    # No brand yet: the plan says what to do first.
    plan = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=FRC").json()
    assert [(s["number"], s["name"]) for s in plan["sections"]] == FRC_SECTIONS
    assert any("Choose the cable brand" in w for w in plan["warnings"])

    client.put(f"/projects/{project_id}/frc-cables", json={"brand": "FIREGUARD", "fire_alarm_loop": "2Cx1.5mm", "voice_evacuation": "2Cx1.5mm",
                                                            "power_24vdc": "2Cx1.5mm", "fire_telephone": "2Cx1.5mm"})
    plan = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=FRC").json()
    by_number = {s["number"]: s for s in plan["sections"]}
    assert [d["name"] for d in by_number[1]["documents"]] == ["Company Profile.pdf"]          # COMMON
    assert [d["name"] for d in by_number[3]["documents"]] == ["ISO 9001.pdf"]                 # COMMON
    assert [d["name"] for d in by_number[4]["documents"]] == ["ALRAYAN ISO.pdf"]              # FIREGUARD/ISO
    assert [d["name"] for d in by_number[5]["documents"]] == ["Schedule of Material"]
    assert [d["name"] for d in by_number[6]["documents"]] == ["FIREGUARD.pdf"]                # the brand's datasheet library
    assert [d["name"] for d in by_number[7]["documents"]] == ["DCD.pdf"]
    assert [d["name"] for d in by_number[8]["documents"]] == ["FIREGUARD_TEST_CERTIFICATE.pdf"]
    assert [d["name"] for d in by_number[9]["documents"]] == ["FIREGUARD_AUTH.pdf"]
    assert [d["name"] for d in by_number[10]["documents"]] == ["FIREGUARD_PREVIOUS_APPROVAL.pdf"]
    assert [d["name"] for d in by_number[11]["documents"]] == ["Warranty Certificate"]
    assert plan["battery_calculation"]["applies"] is False and plan["battery_calculation"]["note"] is None
    assert 6 in by_number    # 6 is the datasheets here, never a battery section

    # The warranty is worded for the cable, its brands the cable's.
    project = db_session.get(Project, project_id)
    replace = warranty_replacements(project, 1, "FRC")
    assert replace("DRAFT WARRANTY FOR FIRE ALARM & VOICE EVACUATION SYSTEM") == "DRAFT WARRANTY FOR FIRE RATED CABLE"
    assert replace("all fire alarm system materials manufactured & supplied by M/s. EDWARDS are warranted for a period of TWO YEARS") == \
        "all fire rated cable materials manufactured & supplied by M/s. Fireguard & M/s. Ramcro are warranted for a period of ONE YEAR"
    assert submittal_router._system_title(project, "FRC") == "Fire Rated Cable (M/s. Fireguard & M/s. Ramcro)"
    assert index_for("FAS") is not index_for("FRC")

    # Built: a PDF, the schedule on one line, the warranty in it.
    built = client.post(f"/projects/{project_id}/submittal/package", json={"sections": list(range(1, 12)), "system_code": "FRC", "revision": "R0", "file": False})
    assert built.status_code == 200, built.text
    text = "\n".join(page.get_text() for page in pymupdf.open(stream=built.content, filetype="pdf"))
    assert "2Cx1.5mm" in text and "FR 2C" not in text
    assert "FIRE RATED CABLE" in text.upper()
    assert "Manufacturer ISO Certificate".upper() in text.upper() or "MANUFACTURER ISO" in text.upper()
