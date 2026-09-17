"""Handing drawings in: the floor-wise schedule they make is stored and
read back from the database, and a DWG says what it needs when this
server has no converter."""

from pathlib import Path

import ezdxf

from app.core.config import get_settings
from app.models import DeviceSymbol, ProjectFloorBoq

from .conftest import login
from .test_floor_devices import DEVICES, _blocks, _devices, _legend, _plan, _save

settings = get_settings()


def _login(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def _project(client) -> int:
    return client.post("/projects", json={"ep_number": "30810", "project_name": "Titania", "design_sheets": []}).json()["id"]


def _drawing(tmp_path: Path, title: str, blocks: list[str], name: str) -> Path:
    doc = ezdxf.new(setup=True)
    _blocks(doc, blocks)
    space = doc.modelspace()
    _plan(space, 0, 0)
    space.add_text(title, dxfattribs={"height": 400}).set_placement((1500, -400))
    _devices(space, 0, 0, blocks)
    _legend(space, 6000, 2500, blocks)
    return _save(doc, tmp_path / name)


def _upload(client, project_id: int, paths: list[Path]):
    files = [("files", (path.name, path.read_bytes(), "application/octet-stream")) for path in paths]
    return client.post(f"/projects/{project_id}/floor-boq", files=files)


def test_the_drawings_make_a_schedule_that_the_tab_reads_from_the_database(client, db_session, tmp_path):
    _login(client)
    project_id = _project(client)
    assert client.get(f"/projects/{project_id}/floor-boq").json()["result"] is None

    ground = _drawing(tmp_path, "GROUND FLOOR PLAN", ["SD", "SD", "MCP"], "GF.dxf")
    typical = _drawing(tmp_path, "TYPICAL 2ND TO 4TH FLOOR PLAN", DEVICES, "typical.dxf")
    response = _upload(client, project_id, [ground, typical])

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [row["floor"] for row in result["floors"]] == ["GROUND FLOOR PLAN", "Level 2", "Level 3", "Level 4"]
    assert result["floors"][0]["devices"] == {"Smoke detector": 2, "Manual call point": 1}
    assert result["floors"][1]["typical"] is True and result["floors"][1]["covers"][-1] == "Level 4"
    assert result["devices"] == ["Heat detector", "Manual call point", "Smoke detector", "Sounder"]
    assert result["legend_excluded"] == 2 + 4          # one of each symbol on each drawing
    assert [entry["file"] for entry in result["files"]] == ["GF.dxf", "typical.dxf"]

    # Read back from the database: the tab opens without a drawing being read again.
    stored = client.get(f"/projects/{project_id}/floor-boq").json()
    assert stored["result"] == result and stored["updated_at"]
    assert db_session.query(ProjectFloorBoq).filter(ProjectFloorBoq.project_id == project_id).count() == 1

    # Handing drawings in again replaces the schedule.
    again = _upload(client, project_id, [ground])
    assert [row["floor"] for row in again.json()["result"]["floors"]] == ["GROUND FLOOR PLAN"]

    assert client.delete(f"/projects/{project_id}/floor-boq").status_code == 204
    assert client.get(f"/projects/{project_id}/floor-boq").json()["result"] is None


def test_a_dwg_says_what_it_needs_when_no_converter_is_installed(client, tmp_path, monkeypatch):
    from app.services import dwg_convert

    monkeypatch.setattr(dwg_convert, "converter", lambda: None)
    _login(client)
    project_id = _project(client)
    drawing = tmp_path / "FA layout.dwg"
    drawing.write_bytes(b"AC1032 not really a drawing")

    result = _upload(client, project_id, [drawing]).json()["result"]

    assert result["floors"] == []
    assert any("ODA File Converter" in warning for warning in result["warnings"])
    assert client.get(f"/projects/{project_id}/floor-boq").json()["converter"]


def test_what_is_not_a_drawing_is_refused(client, tmp_path):
    _login(client)
    project_id = _project(client)
    note = tmp_path / "notes.txt"
    note.write_text("not a drawing", encoding="utf-8")

    response = _upload(client, project_id, [note])

    assert response.status_code == 400 and "not a drawing" in response.json()["detail"]


# --- the symbol library: what one project teaches, the next one knows ---------------------------

# Two symbols as a real drawing draws them: the detector is a circle with
# an S in it, the detector with a sounder base the same circle inside a
# second one. Their block names say nothing.
def _drawn(doc, name: str, *, circles: int) -> None:
    block = doc.blocks.new(name=name)
    for index in range(circles):
        block.add_circle((0, 0), radius=50 + index * 30)
    block.add_text("S", dxfattribs={"height": 20}).set_placement((0, 0))


def _anonymous(doc, name: str) -> None:
    """A symbol nothing can speak for: no legend, no name, no shape the
    platform has seen."""
    block = doc.blocks.new(name=name)
    block.add_lwpolyline([(-40, -40), (40, -40), (0, 40), (-40, -40)])


def _explained(tmp_path: Path, title: str, name: str, *, legend: bool) -> Path:
    """A floor plan whose devices are the two symbols above, with or
    without the legend that explains them."""
    doc = ezdxf.new(setup=True)
    _drawn(doc, "AB01", circles=1)
    _drawn(doc, "AB02", circles=2)
    _anonymous(doc, "AB03")
    space = doc.modelspace()
    _plan(space, 0, 0)
    space.add_text(title, dxfattribs={"height": 400}).set_placement((1500, -400))
    for index, block in enumerate(["AB01", "AB01", "AB01", "AB02", "AB02", "AB03"]):
        space.add_blockref(block, (200 + index * 300, 500), dxfattribs={"layer": "FA-DEVICES"})
    if legend:
        space.add_text("LEGEND", dxfattribs={"height": 250}).set_placement((6000, 2500))
        for index, (block, description) in enumerate([
            ("AB01", "SMOKE DETECTOR"),
            ("AB02", "SMOKE DETECTOR WITH SOUNDER BASE"),
        ]):
            y = 2200 - index * 300
            space.add_blockref(block, (6100, y), dxfattribs={"layer": "LEGEND"})
            space.add_text(description, dxfattribs={"height": 120}).set_placement((6400, y))
    return _save(doc, tmp_path / name)


def test_a_legend_teaches_the_platform_and_the_next_project_needs_none(client, db_session, tmp_path):
    _login(client)

    # The first project's drawing explains its own symbols.
    explained = _explained(tmp_path, "GROUND FLOOR PLAN", "taught.dxf", legend=True)
    first = _upload(client, _project(client), [explained]).json()["result"]

    assert first["floors"][0]["devices"]["Smoke detector"] == 3
    assert first["floors"][0]["devices"]["Smoke detector with sounder base"] == 2
    named = {symbol["block"]: symbol for symbol in first["symbols"]}
    assert named["AB01"]["state"] == "accepted" and named["AB01"]["confidence"] == 100.0
    assert "legend" in named["AB01"]["method"]

    # What it taught is kept, by geometry -- not by the block's name alone.
    taught = {row.device: row for row in db_session.query(DeviceSymbol).all()}
    assert set(taught) >= {"Smoke detector", "Smoke detector with sounder base"}
    assert taught["Smoke detector"].block_names == ["AB01"] and taught["Smoke detector"].fingerprints
    assert taught["Smoke detector"].source == "legend" and taught["Smoke detector"].learned_from == "taught.dxf"

    # Another project, the same symbols drawn again with no legend at all.
    plain = _explained(tmp_path, "FIRST FLOOR PLAN", "untaught.dxf", legend=False)
    second_id = client.post("/projects", json={"ep_number": "30811", "project_name": "Oberon", "design_sheets": []}).json()["id"]
    second = _upload(client, second_id, [plain]).json()["result"]

    assert second["floors"][0]["devices"]["Smoke detector"] == 3
    assert second["floors"][0]["devices"]["Smoke detector with sounder base"] == 2
    # Recognised from the geometry (40) and the name it was taught under
    # (5), against the 50 an accepted symbol needs: enough to count, and
    # flagged because no legend confirmed it here.
    recognised = {symbol["block"]: symbol for symbol in second["symbols"]}
    assert recognised["AB01"]["state"] == "accepted_flagged" and recognised["AB01"]["confidence"] == 90.0
    assert recognised["AB01"]["method"] == "geometry + block_name"


def test_an_engineer_settles_a_symbol_the_drawing_could_not_explain(client, db_session, tmp_path):
    _login(client)
    project_id = _project(client)
    drawing = _explained(tmp_path, "GROUND FLOOR PLAN", "unclear.dxf", legend=True)
    before = _upload(client, project_id, [drawing]).json()["result"]

    # The third symbol is explained by nothing: it is counted under its own
    # block name and put up for review.
    unsure = [symbol for symbol in before["symbols"] if symbol["state"] == "unresolved"]
    assert [symbol["block"] for symbol in unsure] == ["AB03"]
    assert before["floors"][0]["devices"]["AB03"] == 1
    assert any("could not be recognised" in warning for warning in before["warnings"])

    response = client.post(f"/projects/{project_id}/floor-boq/symbols/confirm",
                           json={"block": "AB03", "device": "Manual call point"})

    assert response.status_code == 200, response.text
    after = response.json()["result"]
    settled = next(symbol for symbol in after["symbols"] if symbol["block"] == "AB03")
    assert settled["device"] == "Manual call point" and settled["confidence"] == 100.0
    assert settled["state"] == "accepted" and settled["method"] == "engineer"
    # The schedule is counted again with it, and nothing else moved.
    assert after["floors"][0]["devices"] == {"Smoke detector": 3, "Smoke detector with sounder base": 2,
                                             "Manual call point": 1}
    assert "Manual call point" in after["devices"] and "AB03" not in after["devices"]
    assert [instance["device"] for instance in after["instances"] if instance["block"] == "AB03"] == ["Manual call point"]

    # And the platform has learnt the symbol for every project after this one.
    learnt = db_session.query(DeviceSymbol).filter(DeviceSymbol.device == "Manual call point").one()
    assert learnt.source == "engineer" and learnt.block_names == ["AB03"] and learnt.fingerprints

    # It is read back from the database, not counted again.
    assert client.get(f"/projects/{project_id}/floor-boq").json()["result"] == after
    assert client.post(f"/projects/{project_id}/floor-boq/symbols/confirm",
                       json={"block": "NOT-HERE", "device": "Sounder"}).status_code == 404
