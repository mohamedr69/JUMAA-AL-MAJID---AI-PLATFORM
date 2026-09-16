"""The equipment current table: a part in it is settled for every project,
and the battery page asks nobody to confirm it."""

from app.models import DesignRule, EquipmentCurrent, RoleEnum
from app.services import equipment_currents

from .conftest import login, make_user
from .test_battery_api import PANEL, _login_admin

PANEL_LINES = [
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-CPU", "description": "CPU", "quantity": "1"},
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-FIL", "description": "Blank Filler Plate", "quantity": "16"},
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "3-CABSB", "description": "Backbox, black. Supports five Local Rail Modules.", "quantity": "1"},
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-COMREL", "description": "Common Relay Module", "quantity": "1"},
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "BC-1 ,", "description": "Battery cabinet", "quantity": "1"},
    {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-TRIM", "description": "Trim ring", "quantity": "1"},
]


def _project(client, ep="30790", lines=PANEL_LINES) -> int:
    project_id = client.post("/projects", json={"ep_number": ep, "project_name": "Skyblade", "design_sheets": []}).json()["id"]
    assert client.put(f"/projects/{project_id}/boq", json=lines).status_code == 200
    return project_id


def _no_libraries(monkeypatch):
    import app.routers.design as design_router

    monkeypatch.setattr(design_router, "_datasheet_libraries", lambda: {})


def test_the_table_is_seeded_with_the_settled_edwards_parts(client, db_session):
    rows = {r.key: r for r in db_session.query(EquipmentCurrent).all()}
    assert {"3-CAB14B", "3-CHAS7", "4-FIL", "4-COMREL", "BC-1", "4-CAB24D"} <= set(rows)
    assert rows["3-CAB14B"].no_load and rows["3-CAB14B"].kind == "mechanical" and rows["3-CAB14B"].confirmed_by
    assert rows["4-COMREL"].included_in == "4-CPU" and rows["4-COMREL"].kind == "built_in"
    assert rows["APS6A/230"].no_load, "an APS's load is its amplifiers and modules"
    assert "3-CABSB" in rows["3-CAB5B"].aliases, "the scanned spelling resolves to the part"
    # Seeding again adds nothing and undoes nothing.
    rows["4-FIL"].description = "corrected by hand"
    db_session.commit()
    assert equipment_currents.seed(db_session) == 0
    assert db_session.get(EquipmentCurrent, rows["4-FIL"].id).description == "corrected by hand"


def test_parts_in_the_table_are_settled_without_a_confirmation(client, monkeypatch):
    _no_libraries(monkeypatch)
    _login_admin(client)
    project_id = _project(client)

    filled = client.post(f"/projects/{project_id}/design/battery/fill-currents").json()["filled"]
    body = client.get(f"/projects/{project_id}/design/battery").json()

    by_part = {f["part_no"]: f for f in filled}
    for part in ("4-FIL", "3-CABSB", "4-COMREL", "BC-1 ,"):
        assert part in by_part and by_part[part]["standby_ma"] == 0, part
        assert by_part[part]["source"].startswith("Equipment current table"), by_part[part]["source"]
    assert "built into 4-CPU" in by_part["4-COMREL"]["source"]
    # Nothing from the table is offered for confirmation; only the trim ring,
    # which the table does not know, is set automatically and asked about.
    asked = {item["part_no"] for item in body["needs_confirmation"]}
    assert asked == {"4-TRIM"}
    (panel,) = body["panels"]
    fil = next(line for line in panel["lines"] if line.get("part_no") == "4-FIL")
    assert (fil["standby_ma"], fil["alarm_ma"]) == (0, 0)


def test_a_confirmation_joins_the_table_and_is_never_asked_again(client, db_session, monkeypatch):
    _no_libraries(monkeypatch)
    _login_admin(client)
    first = _project(client, ep="30791")
    client.post(f"/projects/{first}/design/battery/fill-currents")
    assert {i["part_no"] for i in client.get(f"/projects/{first}/design/battery").json()["needs_confirmation"]} == {"4-TRIM"}

    body = client.post(f"/projects/{first}/design/battery/confirm-no-load", json={"part_no": "4-TRIM", "confirm": True}).json()
    assert body["needs_confirmation"] == []
    row = db_session.query(EquipmentCurrent).filter(EquipmentCurrent.key == "4-TRIM").one()
    assert row.no_load and row.confirmed_by

    # Another project quoting the same part: the catalogue already holds it
    # confirmed (there is nothing left to fill), and no question is asked.
    second = _project(client, ep="30792")
    assert all(f["part_no"] != "4-TRIM" for f in client.post(f"/projects/{second}/design/battery/fill-currents").json()["filled"])
    body = client.get(f"/projects/{second}/design/battery").json()
    assert body["needs_confirmation"] == []
    (panel,) = body["panels"]
    trim = next(line for line in panel["lines"] if line.get("part_no") == "4-TRIM")
    assert (trim["standby_ma"], trim["alarm_ma"]) == (0, 0) and "confirmed by" in trim["current_source"]


def test_a_rejection_is_recorded_as_draws_current_and_the_table_does_not_settle_it(client, db_session, monkeypatch):
    _no_libraries(monkeypatch)
    _login_admin(client)
    project_id = _project(client, ep="30793")
    client.post(f"/projects/{project_id}/design/battery/fill-currents")

    client.post(f"/projects/{project_id}/design/battery/confirm-no-load", json={"part_no": "4-TRIM", "confirm": False})

    row = db_session.query(EquipmentCurrent).filter(EquipmentCurrent.key == "4-TRIM").one()
    assert not row.no_load and row.standby_ma is None and row.kind == "unknown"
    assert not equipment_currents.settled(row)
    # A later fill does not set it back to no current.
    filled = client.post(f"/projects/{project_id}/design/battery/fill-currents").json()["filled"]
    assert all(f["part_no"] != "4-TRIM" for f in filled)
    (panel,) = client.get(f"/projects/{project_id}/design/battery").json()["panels"]
    assert panel["lower_bound"] is True


def test_a_device_entered_in_the_table_is_used_by_every_project(client, db_session, monkeypatch):
    _no_libraries(monkeypatch)
    _login_admin(client)
    saved = client.post("/design-rules/equipment-currents", json={
        "part_no": "SIGA-PS", "description": "Photoelectric smoke detector", "standby_ma": 0.045, "alarm_ma": 0.045,
        "source": "Edwards datasheet 85001-0559, p.2",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["kind"] == "device" and saved.json()["settled"] is True
    # Also in the catalogue, confirmed, so a project computes from it at once.
    rule = db_session.query(DesignRule).filter(DesignRule.category == "part.current", DesignRule.key == "SIGA-PS",
                                               DesignRule.superseded_at.is_(None)).one()
    assert rule.data["standby_ma"] == 0.045 and rule.data["confirmed_by"] == "Equipment current table"

    project_id = _project(client, ep="30794", lines=PANEL_LINES + [
        {"system_code": "FAS", "group_heading": PANEL, "catalog_no": "SIGA-PS", "description": "Smoke detector", "quantity": "10"}])
    client.post(f"/projects/{project_id}/design/battery/fill-currents")
    (panel,) = client.get(f"/projects/{project_id}/design/battery").json()["panels"]
    detector = next(line for line in panel["lines"] if line.get("part_no") == "SIGA-PS")
    assert detector["standby_ma"] == 0.045

    # Half a figure is refused; a listing shows the row to everyone.
    assert client.post("/design-rules/equipment-currents", json={"part_no": "X-1", "standby_ma": 1, "source": "test only"}).status_code == 422
    rows = client.get("/design-rules/equipment-currents").json()
    assert any(r["part_no"] == "SIGA-PS" for r in rows)


def test_a_datasheet_read_stores_its_link_and_the_audit_reads_it_back(client, db_session, tmp_path, monkeypatch):
    """A figure read off a datasheet on the battery page carries the sheet
    into the table as a link -- library, file, pages, how it was matched --
    and the audit re-reads that sheet and finds nothing to say."""
    import app.routers.design as design_router
    from .test_battery_api import _fill_project, _library

    library = _library(tmp_path)
    monkeypatch.setattr(design_router, "_datasheet_libraries", lambda: {"EDWARDS": library})
    monkeypatch.setattr(equipment_currents, "get_libraries", lambda: {"EDWARDS": library}, raising=False)
    import app.services.datasheet_library as datasheet_library

    monkeypatch.setattr(datasheet_library, "get_libraries", lambda *a, **k: {"EDWARDS": library})
    _login_admin(client)
    project_id = _fill_project(client)
    client.post(f"/projects/{project_id}/design/battery/fill-currents")

    cpu = db_session.query(EquipmentCurrent).filter(EquipmentCurrent.key == "4-CPU").one()
    assert cpu.datasheet_library == "EDWARDS" and cpu.datasheet_path.endswith("4-CPU.pdf")
    assert cpu.datasheet_pages == [1] and cpu.datasheet_match == "filename" and cpu.datasheet_sha256
    listed = next(r for r in client.get("/design-rules/equipment-currents").json() if r["part_no"] == "4-CPU")
    assert listed["datasheet_path"] == cpu.datasheet_path and listed["datasheet_pages"] == [1]

    body = client.post("/design-rules/equipment-currents/audit").json()
    assert body["rows"] >= 1
    by_part = {f["part_no"]: f for f in body["findings"]}
    assert "4-CPU" not in by_part, by_part.get("4-CPU")
    # A seeded no-load part with no sheet in this small library is reported as such.
    assert by_part["3-CAB28B"]["status"] == "no_datasheet"


def test_a_sheet_that_only_mentions_the_part_is_not_a_source(tmp_path):
    """The figures on a sheet are the part's only when placed under its
    name; a sheet found by a mention of the part in its text with unowned
    figures is a candidate to confirm, not a source."""
    from tests.test_power_supply_currents import _pdf
    from app.services.datasheet_currents import read_part_current

    class _Match:
        matched_on = "text"

    own = _pdf(tmp_path / "own.pdf", [(250, 100, "SIGA-CT2", 10), (40, 140, "Standby", 8), (250, 140, "396 uA", 8),
                                     (40, 160, "Activated", 8), (250, 160, "680 uA", 8)])
    reading = read_part_current(own, "SIGA-CT2", doc_named_for_part=False)
    assert reading is not None and reading.owned and equipment_currents.trusted(_Match(), reading)
    other = _pdf(tmp_path / "other.pdf", [(40, 100, "Flasher, compatible with SIGA-CT2", 8), (40, 140, "Standby", 8),
                                         (250, 140, "175 mA", 8), (40, 160, "Alarm", 8), (250, 160, "175 mA", 8)])
    reading = read_part_current(other, "SIGA-CT2", doc_named_for_part=True)
    assert reading is not None and not reading.owned and not equipment_currents.trusted(_Match(), reading)


def test_the_table_is_written_by_editors_and_deleted_by_admins_only(client, db_session):
    make_user(db_session, "viewer@x.com", RoleEnum.viewer)
    login(client, "viewer@x.com")
    assert client.get("/design-rules/equipment-currents").status_code == 200
    assert client.post("/design-rules/equipment-currents", json={"part_no": "Z-9", "no_load": True, "source": "viewer"}).status_code == 403

    make_user(db_session, "eng@x.com", RoleEnum.design_engineer)
    login(client, "eng@x.com")
    row = client.post("/design-rules/equipment-currents", json={"part_no": "Z-9", "no_load": True, "source": "engineer says so"}).json()
    assert client.delete(f"/design-rules/equipment-currents/{row['id']}").status_code == 403
    _login_admin(client)
    assert client.delete(f"/design-rules/equipment-currents/{row['id']}").status_code == 204
    assert db_session.query(EquipmentCurrent).filter(EquipmentCurrent.key == "Z-9").count() == 0
