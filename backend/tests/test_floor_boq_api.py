"""Handing drawings in: the floor-wise schedule they make is stored and
read back from the database, and a DWG says what it needs when this
server has no converter."""

from pathlib import Path

import ezdxf

from app.core.config import get_settings
from app.models import ProjectFloorBoq

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
