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
