"""BOQ as per IFC drawings, the platform's side of it: the library starts
clean with the fire alarm devices, a drawing belongs to its project, only
an engineer answers, nothing is exported before every symbol is answered,
the drawing is filed in the project's folder, and deleting the project
takes its drawings with it. The reading and matching themselves are the
original tool's, tested in test_ifc_boq.py."""

import json
from pathlib import Path

import ezdxf

from app.core.config import get_settings
from app.ifc import storage
from app.ifc.library_file import library_path
from app.models import ProjectIfcDrawing, RoleEnum

from .conftest import login, make_user

settings = get_settings()


def _smoke(block):
    block.add_circle((0, 0), 200)
    block.add_text("S", height=150).set_placement((-50, -75))


def _dxf(path: Path, count: int = 3) -> Path:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    _smoke(doc.blocks.new("SD"))
    for i in range(count):
        doc.modelspace().add_blockref("SD", (i * 1000, 0), dxfattribs={"layer": "E-FIRE"})
    doc.saveas(path)
    return path


def _admin(client):
    assert login(client, settings.default_admin_email, settings.default_admin_password).status_code == 200


def _project(client, ep: str, folder: Path | None = None) -> int:
    body = {"ep_number": ep, "project_name": "IFC", "design_sheets": []}
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        body["source_folder_path"] = str(folder)
    return client.post("/projects", json=body).json()["id"]


def _upload(client, project_id: int, path: Path):
    return client.post(f"/projects/{project_id}/ifc-drawings",
                       files={"file": (path.name, path.read_bytes(), "application/dxf")})


def _type_id(client, code: str) -> int:
    return next(t["id"] for t in client.get("/ifc/device-types").json() if t["code"] == code)


def test_the_library_starts_clean_with_the_fire_alarm_devices(client):
    _admin(client)
    types = client.get("/ifc/device-types").json()
    codes = {t["code"] for t in types}
    # The tool's fire alarm list and the fire alarm devices its users added...
    assert {"SD", "HD", "MCP", "MCP-WP", "SPK", "FACP", "EOL", "CSF", "VE-HORN-SPKR"} <= codes
    assert len(types) == 38
    # ...and nothing else: emergency lighting comes later, and the exit signs
    # its users had filed under fire alarm wait for it.
    assert {t["category"] for t in types} == {"fire_alarm"}
    assert not codes & {"EXIT", "EL", "EMCP", "EX", "EXT-WP"}
    # No symbol is known: every symbol is asked on the first drawing.
    data = json.loads(library_path().read_text(encoding="utf-8"))
    assert data["symbols"] == [] and len(data["device_types"]) == 38
    assert library_path().parent.name == "symbols"


def test_a_drawing_belongs_to_its_project(client, tmp_path):
    _admin(client)
    mine, other = _project(client, "91001"), _project(client, "91002")
    drawing = _upload(client, mine, _dxf(tmp_path / "FA-101.dxf"))
    assert drawing.status_code == 201, drawing.text
    did = drawing.json()["id"]

    assert client.get(f"/projects/{mine}/ifc-drawings/{did}").status_code == 200
    assert client.get(f"/projects/{other}/ifc-drawings/{did}").status_code == 404
    assert client.get(f"/projects/{other}/ifc-drawings").json() == []
    listed = client.get(f"/projects/{mine}/ifc-drawings").json()
    assert [d["filename"] for d in listed] == ["FA-101.dxf"] and listed[0]["review_required"] == 1


def test_the_working_copy_is_kept_by_a_path_relative_to_the_uploads(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "91003")
    did = _upload(client, project_id, _dxf(tmp_path / "FA-102.dxf")).json()["id"]
    row = db_session.get(ProjectIfcDrawing, did)
    assert not Path(row.stored_path).is_absolute()
    assert row.stored_path.startswith("EP-91003/ifc/")
    assert storage.dxf_path(row).is_file()


def test_quantities_and_export_wait_for_every_answer(client, tmp_path):
    _admin(client)
    project_id = _project(client, "91004")
    drawing = _upload(client, project_id, _dxf(tmp_path / "FA-103.dxf", count=4)).json()
    base = f"/projects/{project_id}/ifc-drawings/{drawing['id']}"
    assert drawing["review"]["ready"] is False and drawing["review"]["required"] == 1
    locked = client.get(f"{base}/export")
    assert locked.status_code == 409 and "Verify the drawing's symbols first" in locked.json()["detail"]

    signature = drawing["groups"][0]["signature"]
    answered = client.post(f"{base}/review", json={"answers": [{"signature": signature,
                                                                  "device_type_id": _type_id(client, "SD")}]})
    assert answered.status_code == 200, answered.text
    assert answered.json()["review"]["ready"] is True and answered.json()["totals"]["fire_alarm"] == 4

    export = client.get(f"{base}/export")
    assert export.status_code == 200
    assert "EP-91004 FA-103 - BOQ as per IFC.xlsx" in export.headers["content-disposition"]

    # The answer is in the library: the next drawing, on another project, is counted at once.
    again = _upload(client, _project(client, "91005"), _dxf(tmp_path / "FA-201.dxf", count=2)).json()
    assert again["review"]["ready"] is True and again["totals"]["fire_alarm"] == 2


def test_a_viewer_reads_but_does_not_upload_or_answer(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "91006")
    drawing = _upload(client, project_id, _dxf(tmp_path / "FA-104.dxf")).json()
    make_user(db_session, "viewer@example.com", RoleEnum.viewer)
    assert login(client, "viewer@example.com").status_code == 200

    base = f"/projects/{project_id}/ifc-drawings"
    assert client.get(f"{base}/{drawing['id']}").status_code == 200
    assert _upload(client, project_id, _dxf(tmp_path / "FA-105.dxf")).status_code == 403
    signature = drawing["groups"][0]["signature"]
    assert client.post(f"{base}/{drawing['id']}/review",
                       json={"answers": [{"signature": signature, "ignore": True}]}).status_code == 403
    assert client.post("/ifc/device-types", json={"code": "X1", "name": "X", "category": "fire_alarm"}).status_code == 403


def test_the_drawing_is_filed_in_the_projects_folder_and_never_overwrites(client, tmp_path):
    _admin(client)
    folder = tmp_path / "EP-91007"
    project_id = _project(client, "91007", folder)
    path = _dxf(tmp_path / "FA-106.dxf")
    original = path.read_bytes()

    first = _upload(client, project_id, path).json()
    assert first["archive_path"] == "03- Drawings/IFC/Electrical/FA/FA-106.dxf"
    filed = folder / first["archive_path"]
    assert filed.read_bytes() == original

    # The same drawing again is the same file; a different one of the same name is kept beside it.
    assert _upload(client, project_id, path).json()["archive_path"] == first["archive_path"]
    changed = _upload(client, project_id, _dxf(tmp_path / "FA-106.dxf", count=5)).json()
    assert changed["archive_path"].startswith("03- Drawings/IFC/Electrical/FA/FA-106 (uploaded ")
    assert filed.read_bytes() == original    # the first one is untouched


def test_a_project_folder_not_on_this_pc_still_reads_the_drawing(client, tmp_path):
    _admin(client)
    project_id = _project(client, "91008")
    drawing = _upload(client, project_id, _dxf(tmp_path / "FA-107.dxf"))
    assert drawing.status_code == 201
    assert drawing.json()["archive_path"] is None


def test_deleting_a_drawing_or_the_project_removes_its_working_copies(client, db_session, tmp_path):
    _admin(client)
    project_id = _project(client, "91009")
    one = _upload(client, project_id, _dxf(tmp_path / "FA-108.dxf")).json()["id"]
    two = _upload(client, project_id, _dxf(tmp_path / "FA-109.dxf")).json()["id"]
    first = storage.dxf_path(db_session.get(ProjectIfcDrawing, one))
    assert client.delete(f"/projects/{project_id}/ifc-drawings/{one}").status_code == 204
    assert not first.exists()

    assert storage.dxf_path(db_session.get(ProjectIfcDrawing, two)).exists()
    folder = storage.uploads_root() / "EP-91009" / "ifc"
    assert client.delete(f"/projects/{project_id}").status_code == 204
    db_session.expire_all()
    assert db_session.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.project_id == project_id).count() == 0
    assert not folder.exists()


def test_a_symbol_drawn_far_from_its_base_point_counts_where_it_is_seen(tmp_path):
    """EP-30880's sounder strobe block is drawn about 1 km from its own base
    point and inserted mirrored: the symbol shows on the basement plan while
    its insertion point is off every sheet. The tool placed devices by the
    insertion point and counted 1 of the 14 on each basement; a device is
    on the sheet, and on the architecture, where it is seen."""
    from app.ifc.dxf.extract import extract

    doc = ezdxf.new("R2018")
    strobe = doc.blocks.new("faw")
    strobe.add_circle((92_000, -47_000), 200)             # the drawing, far from the base point (0, 0)
    strobe.add_text("S", height=150).set_placement((91_950, -47_075))
    plan = doc.blocks.new("GROUND PLAN")                  # the architecture: a floor-plan container
    for i in range(12):
        plan.add_blockref("WALL", (i * 1000, 0))
    wall = doc.blocks.new("WALL")                         # a room 900 wide and 9 m deep
    wall.add_lwpolyline([(0, 0), (900, 0), (900, 9000), (0, 9000)], close=True)
    msp = doc.modelspace()
    msp.add_blockref("GROUND PLAN", (0, 0))
    # Mirrored like the real ones (x scale -1): the symbol lands at insert + (-92 000, -47 000).
    for i in range(3):
        msp.add_blockref("faw", (92_000 + i * 2000, 52_000), dxfattribs={"xscale": -1, "layer": "FIRE ALARM SYMBOL"})
    lay = doc.layouts.new("FA-01")
    lay.add_viewport(center=(200, 150), size=(400, 300), view_center_point=(5000, 5000), view_height=30_000)
    lay.add_text("GROUND FLOOR PLAN", height=5).set_placement((10, 10))
    doc.saveas(tmp_path / "offset.dxf")

    group = next(g for g in extract(str(tmp_path / "offset.dxf")).groups if "faw" in g.block_names)
    assert [(o.sheet, o.on_arch) for o in group.occurrences] == [("FA-01", True)] * 3
    # The insertion point is kept as it is, for the export and for re-reading.
    assert all(o.x >= 92_000 and o.y == 52_000 for o in group.occurrences)
    assert {round(o.cx) for o in group.occurrences} == {0, 2000, 4000}


# --- symbols drawn without a block ---------------------------------------------------------------


def _speaker_lines(msp, x: float, y: float, turn: int = 0, layer: str = "SYMBOL FA"):
    """The EP-30880 sounder as bad drafting leaves it: exploded into a
    triangle and a rectangle, no block. `turn` quarter-turns it."""
    def at(px, py):
        for _ in range(turn):
            px, py = -py, px
        return (x + px, y + py)
    msp.add_lwpolyline([at(-100, -80), at(-100, 80), at(20, 0)], close=True, dxfattribs={"layer": layer})
    msp.add_lwpolyline([at(20, -40), at(80, -40), at(80, 40), at(20, 40)], close=True, dxfattribs={"layer": layer})


def _drawing_with_loose_symbols(path: Path) -> Path:
    doc = ezdxf.new("R2018")
    _smoke(doc.blocks.new("SD"))
    msp = doc.modelspace()
    for i in range(4):                                     # the drawing's own symbol blocks set the scale
        msp.add_blockref("SD", (i * 3000, 20_000), dxfattribs={"layer": "E-FIRE"})
    for i, turn in enumerate((0, 1, 2, 3, 0)):            # five exploded speakers, drawn four ways
        _speaker_lines(msp, i * 3000, 0, turn)
    msp.add_circle((0, 10_000), 200, dxfattribs={"layer": "E-FIRE"})       # an exploded smoke detector...
    msp.add_text("OS", height=150, dxfattribs={"layer": "E-FIRE"}).set_placement((-80, 9_925))  # ...and its letters
    msp.add_line((0, 5000), (40_000, 5000), dxfattribs={"layer": "E-FIRE"})  # a cable: no symbol
    doc.saveas(path)
    return path


def test_symbols_drawn_without_a_block_are_read_by_what_they_look_like(tmp_path):
    from app.ifc.dxf.extract import extract
    from app.ifc.dxf.loose import LOOSE_NAME

    result = extract(str(_drawing_with_loose_symbols(tmp_path / "loose.dxf")))
    loose = [g for g in result.groups if LOOSE_NAME in g.block_names]
    speakers = [g for g in loose if g.entity_counts == {"LWPOLYLINE": 2}]
    # Five exploded speakers, whichever way each was turned, are one symbol.
    assert len(speakers) == 1 and speakers[0].count == 5
    # The loose circle takes the loose "OS" beside it as its letters.
    assert [g.label for g in loose if g.entity_counts == {"CIRCLE": 1}] == ["OS"]
    # The cable is nobody's symbol, and the smoke detector blocks are read as blocks.
    assert result.loose_symbols == 6
    assert next(g for g in result.groups if "SD" in g.block_names).count == 4


def test_an_exploded_symbol_is_verified_and_counted_like_a_block(client, db_session, tmp_path):
    from app.models import IfcBlockAlias

    _admin(client)
    project_id = _project(client, "91010")
    drawing = _upload(client, project_id, _drawing_with_loose_symbols(tmp_path / "loose.dxf")).json()
    assert drawing["loose_symbols"] == 6
    speaker = next(g for g in drawing["groups"] if g["entity_counts"] == {"LWPOLYLINE": 2})
    assert speaker["review"] == "answer" and speaker["on_plans"] == 5   # asked, like any unknown symbol

    base = f"/projects/{project_id}/ifc-drawings/{drawing['id']}"
    answered = client.post(f"{base}/review", json={"answers": [
        {"signature": speaker["signature"], "device_type_id": _type_id(client, "SPKW")}]}).json()
    counted = next(g for g in answered["groups"] if g["signature"] == speaker["signature"])
    assert counted["status"] == "verified" and counted["boq_qty"] == 5
    # The shared name of symbols drawn without a block is never a name to know one by.
    from app.ifc.dxf.loose import LOOSE_NAME

    assert LOOSE_NAME.upper() not in {a.block_name for a in db_session.query(IfcBlockAlias)}


# --- reading as a job: a percentage and the time left ---------------------------------------------


def test_a_drawing_is_read_as_a_job_with_its_progress_and_the_drawing_it_made(client, monkeypatch, tmp_path):
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    _admin(client)
    project_id = _project(client, "91011")
    path = _dxf(tmp_path / "FA-110.dxf")
    started = client.post(f"/projects/{project_id}/ifc-drawings/jobs",
                          files={"file": (path.name, path.read_bytes(), "application/dxf")})
    assert started.status_code == 202, started.text
    job = client.get(f"/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded" and job["kind"] == "ifc_read"
    assert job["progress"]["done"] == 100 and job["progress"]["stage"] == "done" and job["progress"]["eta_seconds"] == 0
    drawing = client.get(f"/projects/{project_id}/ifc-drawings/{job['result']['drawing_id']}").json()
    assert drawing["filename"] == "FA-110.dxf" and drawing["groups"][0]["count"] == 3

    # What is no drawing is refused before a job is started.
    bad = client.post(f"/projects/{project_id}/ifc-drawings/jobs", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 415


def test_a_read_that_is_stopped_leaves_nothing_behind(client, db_session, tmp_path):
    from app.models import Project, User
    from app.routers.ifc_boq import _read_drawing
    from app.services.jobs import Cancelled

    _admin(client)
    project = db_session.get(Project, _project(client, "91012"))
    user = db_session.query(User).first()
    stages = []

    def check():
        stages.append(len(stages))
        if len(stages) == 3:          # save, read -- then stopped as the symbols are walked
            raise Cancelled()

    data = _dxf(tmp_path / "FA-111.dxf").read_bytes()
    try:
        _read_drawing(db_session, project, user, "FA-111.dxf", "dxf", data, check=check)
    except Cancelled:
        pass
    else:
        raise AssertionError("the read went on after it was stopped")
    assert db_session.query(ProjectIfcDrawing).count() == 0
    assert not any(storage.project_folder(project).glob("*"))


def test_the_percentage_only_rises_and_the_time_left_comes_down():
    import time as clock

    from app.ifc.progress import ReadTimer

    seen = []
    timer = ReadTimer.for_upload(lambda p, m, s, e: seen.append((p, s, e)), is_dwg=True, size_mb=4.4,
                                 converter="AutoCAD 2027 Core Console")
    assert seen == [] and timer.order == ["save", "convert", "read", "walk", "finish", "file"]
    for stage in timer.order:
        timer.begin(stage)
        if stage == "walk":
            for fraction in (0.25, 0.5, 0.75):
                timer.at(fraction)
                clock.sleep(0.01)
                timer.tick()
    percents = [p for p, _, _ in seen]
    assert percents == sorted(percents) and percents[-1] <= 99
    assert seen[1][1] == "convert" and seen[1][2] > 3          # a DWG's conversion is estimated at a few seconds
    assert seen[-1][2] <= seen[1][2]


def test_an_architecture_that_costs_minutes_holds_the_bar_and_says_so():
    from app.ifc.progress import ReadTimer

    seen = []
    timer = ReadTimer.for_upload(lambda p, m, s, e: seen.append((p, s, e)), is_dwg=False, size_mb=28, converter=None)
    for stage in ("save", "read", "walk"):
        timer.begin(stage)
    timer.at(0.99)
    timer.tick()
    before = seen[-1][0]
    # The walk found the Revit model: 400,000 pieces to outline, not the second guessed.
    timer.plan({"finish": 400_000})
    timer.begin("finish")
    timer.at(0.1)
    timer.tick()
    percent, stage, eta = seen[-1]
    assert stage == "finish" and percent >= before and eta > 30


def test_the_architecture_outline_is_ezdxfs_own_and_says_how_far_it_is(tmp_path):
    from ezdxf import bbox as ezbbox

    from app.ifc.dxf.extract import _architecture, _Pieces

    doc = ezdxf.new("R2018")
    room = doc.blocks.new("ROOM")
    room.add_lwpolyline([(0, 0), (4000, 0), (4000, 3000), (0, 3000)], close=True)
    room.add_arc((2000, 1500), 500, 0, 90)
    plan = doc.blocks.new("PLAN")
    for i in range(30):
        plan.add_blockref("ROOM", (i * 5000, 0), dxfattribs={"rotation": 7 * i})
    ins = doc.modelspace().add_blockref("PLAN", (100, 200), dxfattribs={"rotation": 15})
    heard = []
    boxes, names = _architecture(doc, [ins], _Pieces(doc), heard.append)
    whole = ezbbox.extents([ins], fast=True)
    m = 0.02 * max(whole.extmax.x - whole.extmin.x, whole.extmax.y - whole.extmin.y)
    assert names == ["PLAN"] and boxes == [(whole.extmin.x - m, whole.extmin.y - m, whole.extmax.x + m, whole.extmax.y + m)]
    assert _Pieces(doc).insert(ins) == 60 and heard[-1] == 1.0 and heard == sorted(heard)


# --- revisions: the drawing in force, and the ones it replaced ------------------------------------


def _issue(client, project_id: int, path: Path, revision: str | None = None, supersedes: int | None = None):
    data = {}
    if revision is not None:
        data["revision"] = revision
    if supersedes is not None:
        data["supersedes_id"] = str(supersedes)
    return client.post(f"/projects/{project_id}/ifc-drawings", data=data,
                       files={"file": (path.name, path.read_bytes(), "application/dxf")})


def test_a_revised_drawing_supersedes_the_one_in_force_and_keeps_its_decisions(client, tmp_path):
    _admin(client)
    project_id = _project(client, "91020")
    first = _issue(client, project_id, _dxf(tmp_path / "FA-120.dxf", count=3), revision="R2").json()
    assert first["revision"] == "R2" and first["superseded_by"] is None
    sheet = first["sheets"][0]["name"]
    assert client.put(f"/projects/{project_id}/ifc-drawings/{first['id']}/floors",
                      json={"sheet": sheet, "multiplier": 4}).status_code == 200

    # A revision is later than the one it replaces.
    early = _issue(client, project_id, _dxf(tmp_path / "FA-120.dxf", count=5), revision="R1", supersedes=first["id"])
    assert early.status_code == 422 and "later revision" in early.json()["detail"]

    second = _issue(client, project_id, _dxf(tmp_path / "FA-120.dxf", count=5), supersedes=first["id"]).json()
    assert second["revision"] == "R3" and second["supersedes_id"] == first["id"]
    assert second["carried_over"] == {"from": "R2", "floor_overrides": 1, "review_skipped": 0}
    assert next(s for s in second["sheets"] if s["name"] == sheet)["multiplier"] == 4

    listed = {d["id"]: d for d in client.get(f"/projects/{project_id}/ifc-drawings").json()}
    assert listed[second["id"]]["current"] and not listed[first["id"]]["current"]
    assert listed[first["id"]]["superseded_by"] == second["id"]
    assert client.get(f"/projects/{project_id}/ifc-drawings/{first['id']}").json()["superseded_by"] == {"id": second["id"], "revision": "R3"}

    # What is superseded is not revised again: its revision already is.
    twice = _issue(client, project_id, _dxf(tmp_path / "FA-120.dxf"), revision="R4", supersedes=first["id"])
    assert twice.status_code == 409
    assert _issue(client, project_id, _dxf(tmp_path / "FA-120.dxf"), revision="X").status_code == 422

    # Deleting the revision in force puts the one before it back in force.
    assert client.delete(f"/projects/{project_id}/ifc-drawings/{second['id']}").status_code == 204
    assert [d["current"] for d in client.get(f"/projects/{project_id}/ifc-drawings").json()] == [True]


def test_the_comparison_puts_both_boqs_on_the_same_floors_and_devices():
    from app.ifc.comparison import compare, floor_key

    assert floor_key("3rd Basement")[0] == floor_key("3RD BASEMENT FLOOR")[0] == "B3"
    assert floor_key("Level 3")[0] == floor_key("3RD FLOOR")[0] == "L3"
    assert floor_key("1st Podium")[0] == "P1" and floor_key("Ground floor")[0] == floor_key("GROUND FLOOR")[0] == "GF"
    assert floor_key("Mech floor")[0] == floor_key("MECHANICAL FLOOR")[0] == "MECH"

    schedule = {
        "floors": ["Ground floor", "Level 3", "Level 4", "Roof Floor"],
        "items": [
            {"description": "Smoke Detector", "system": "FAS", "device": "Smoke detector", "total": 12,
             "per_floor": {"Ground floor": 4, "Level 3": 4, "Level 4": 4}},
            {"description": "Wall Speaker", "system": "FAS", "device": "Speaker", "total": 2, "per_floor": {"Roof Floor": 2}},
            {"description": "Exit Light", "system": "ELS", "device": "Exit light", "total": 9, "per_floor": {"Level 3": 9}},
        ],
    }

    def row(code, name, per_floor, mult=1):
        return {"device_type": {"code": code, "name": name}, "per_floor": per_floor, "qty": per_floor * mult}

    drawing = {"id": 1, "filename": "FA.dwg", "revision": "R1", "floor_boq": {"fire_alarm": {"floors": [
        {"sheet": "FA-01", "floor_name": "GROUND FLOOR", "floors": [], "multiplier": 1, "qty": 4,
         "rows": [row("SD", "Smoke Detector (Addressable)", 4)]},
        {"sheet": "FA-02", "floor_name": "TYPICAL 3RD TO 4TH FLOOR", "floors": [3, 4], "multiplier": 2, "qty": 10,
         "rows": [row("SD", "Smoke Detector (Addressable)", 5, 2)]},
        {"sheet": "FA-03", "floor_name": "ROOF FLOOR", "floors": [], "multiplier": 1, "qty": 2,
         "rows": [row("SPKW", "Wall Speaker", 1), row("SPK", "Ceiling Speaker", 1)]},
    ]}}}
    out = compare(schedule, [drawing])
    smoke = next(d for d in out["devices"] if d["device"] == "Smoke detector")
    assert (smoke["schedule_total"], smoke["ifc_total"], smoke["difference"], smoke["floors_differing"]) == (12, 14, 2, 2)
    assert {c["floor"]: c["difference"] for c in smoke["floors"]} == {"GF": 0, "L3": 1, "L4": 1}
    speaker = next(d for d in out["devices"] if d["device"] == "Speaker")
    assert speaker["difference"] == 0 and {t["code"] for t in speaker["ifc_types"]} == {"SPK", "SPKW"}
    assert [f["key"] for f in out["floors"]] == ["GF", "L3", "L4", "RF"]            # the schedule's order; no emergency lighting
    assert out["totals"] == {"schedule": 14, "ifc": 16, "difference": 2, "devices": 2, "devices_matching": 1,
                             "floors": 4, "floors_matching": 2}


def test_the_comparison_tab_reads_the_drawings_in_force(client, tmp_path):
    _admin(client)
    project_id = _project(client, "91021")
    first = _issue(client, project_id, _dxf(tmp_path / "FA-121.dxf", count=3)).json()
    out = client.get(f"/projects/{project_id}/ifc-comparison").json()
    assert out["schedule"] is None
    # A drawing still being answered is named, and left out (the library may already know the symbol).
    if first["review"]["required"]:
        assert out["drawings"] == [] and out["pending"][0]["id"] == first["id"]
    signature = first["groups"][0]["signature"]
    client.post(f"/projects/{project_id}/ifc-drawings/{first['id']}/review",
                json={"answers": [{"signature": signature, "device_type_id": _type_id(client, "SD")}]})
    second = _issue(client, project_id, _dxf(tmp_path / "FA-121.dxf", count=5), supersedes=first["id"]).json()
    out = client.get(f"/projects/{project_id}/ifc-comparison").json()
    assert [(d["id"], d["revision"]) for d in out["drawings"]] == [(second["id"], "R1")]
    assert out["totals"]["ifc"] == 5


def test_basements_and_podiums_are_not_numbered_floors():
    """EP-30880: '3RD BASEMENT' and '3RD/4TH PODIUM' were read as floors 3 and 4,
    so the typical 3rd-to-16th plan lost floors 3 and 4 as 'on another sheet'."""
    from app.ifc.dxf.sheets import floor_count, parse_floors, refresh_floors

    assert parse_floors("3RD BASEMENT FLOOR PLAN") == [] and parse_floors("4TH PODIUM FLOOR PLAN") == []
    assert parse_floors("2ND MEZZANINE") == [] and parse_floors("1ST FLOOR PLAN") == [1]
    assert parse_floors("TYPICAL 3RD TO 16TH FLOOR PLAN") == list(range(3, 17))
    assert parse_floors("TYP 18-20,22-30") == [18, 19, 20] + list(range(22, 31))
    assert floor_count("1ST TO 3RD BASEMENT FLOOR PLAN", parse_floors("1ST TO 3RD BASEMENT FLOOR PLAN")) == 3

    titles = ["3RD BASEMENT FLOOR PLAN", "1ST PODIUM FLOOR PLAN", "3RD PODIUM FLOOR PLAN", "4TH PODIUM FLOOR PLAN",
              "1ST FLOOR PLAN", "2ND FLOOR PLAN", "TYPICAL 3RD TO 16TH FLOOR PLAN", "TYPICAL 17TH TO 22ND FLOOR PLAN"]
    # As stored by a read before the fix: the typical plan had lost floors 3 and 4.
    stored = [{"name": f"FA {101 + i}", "title": t, "kind": "plan", "floors": [5], "multiplier": 12, "note": "stale"}
              for i, t in enumerate(titles)]
    fresh = {s["title"]: s for s in refresh_floors(stored)}
    typical = fresh["TYPICAL 3RD TO 16TH FLOOR PLAN"]
    assert typical["floors"] == list(range(3, 17)) and typical["multiplier"] == 14 and typical["note"] == ""
    assert fresh["3RD PODIUM FLOOR PLAN"]["multiplier"] == 1 and fresh["TYPICAL 17TH TO 22ND FLOOR PLAN"]["multiplier"] == 6


# --- a symbol's own words: a hint, and a flag on an answer against them ------------------------------


def test_a_symbols_own_name_is_read_but_architecture_is_not():
    from app.ifc import hints

    codes = {c: {"id": i, "code": c, "name": c, "category": "fire_alarm", "unit": "Nos"}
             for i, c in enumerate(["SD", "SDS", "HD", "MD", "MCP", "MCP-WP", "SPK", "SPKW", "STB", "CM", "FTJ", "FACP", "RP"])}

    def code(label="", *blocks):
        h = hints.hint({"label": label, "block_names": {b: 1 for b in blocks}}, codes)
        return h and h["device_type"]["code"]

    assert code("M + S", "MSS", "XREF - title block a1$0$MSS") == "MD"
    assert code("", "XREF - title block$0$BREAK GLASS WP") == "MCP-WP"
    assert code("OS", "SMOKE DETECTOR") == "SD" and code("CS", "CEILING SPEAKER") == "SPK" and code("", "VE-HORN SPKR") == "SPKW"
    assert code("RFACP", "A$C96ec361a") == "RP" and code("", "faw", "STRB") == "STB"
    # Architecture, whatever its letters: a door tag's SD, a telephone distribution point, a column's 35 CM.
    assert code("SD + W4", "X-REF_ TAG$0$DOOOR TAG") is None
    assert code("DB", "X-REF_ FILE$0$TEL DP") is None
    assert code("BATH + BED", "X-REF_ FILE$0$COL 28 TO 32 - 35 CM") is None
    assert code("ELECTRICAL ROOM + SMOKE PROOF DOOR WITH TEMPERED GLASS", "X-REF$0$PO2 SERV") is None
    assert code("", "*U441") is None and code("PARK-NO", "*U441") is None

    # Families: the same family is no conflict, another family is.
    assert hints.family_of_type("SS", "Sounder Strobe WP") == "sounder"
    assert hints.family_of_type("VE-HORN-SPKR", "WALL MOUNTED SPEAKER") == "speaker"
    assert hints.family_of_type("MD", "Multi-Sensor Detector (Smoke + Heat)") == "multi-sensor"
    assert hints.family_of_type("HDS", "Heat Detector with Sounder Base") == "heat"
    sds = {"status": "verified", "device_type": {"code": "SDS", "name": "Smoke Detector with Sounder Base"},
           "name_hint": {"device_type": codes["SD"], "family": "smoke", "reason": "letters 'OS'"}}
    assert hints.conflict(sds) is None
    mss = {"status": "verified", "device_type": {"code": "MCP", "name": "Manual Call Point (Break Glass)"},
           "name_hint": {"device_type": codes["MD"], "family": "multi-sensor", "reason": "letters 'M + S'"}}
    assert "reads as MD" in hints.conflict(mss)


def _multisensor_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    block = doc.blocks.new("MSS")
    block.add_ellipse((0, 0), major_axis=(250, 0), ratio=0.8)
    block.add_text("M", height=120).set_placement((-150, -60))
    block.add_text("S", height=120).set_placement((50, -60))
    for i in range(3):
        doc.modelspace().add_blockref("MSS", (i * 1000, 0), dxfattribs={"layer": "E-FIRE"})
    doc.saveas(path)
    return path


def test_an_answer_against_the_symbols_own_name_is_flagged_never_auto_verified(client, tmp_path):
    _admin(client)
    project_id = _project(client, "91030")
    drawing = _upload(client, project_id, _multisensor_dxf(tmp_path / "FA-130.dxf")).json()
    g = drawing["groups"][0]
    assert g["name_hint"]["device_type"]["code"] == "MD" and g["name_hint"]["family"] == "multi-sensor"
    assert g["status"] != "verified" and drawing["review"]["ready"] is False      # a hint is never an answer
    types = {t["code"]: t for t in client.get("/ifc/device-types").json()}
    assert types["MD"]["family"] == "multi-sensor" and types["MCP"]["family"] == "call point"

    base = f"/projects/{project_id}/ifc-drawings/{drawing['id']}"
    wrong = client.post(f"{base}/review", json={"answers": [{"signature": g["signature"], "device_type_id": types["MCP"]["id"]}]}).json()
    assert wrong["review"]["conflicts"] == 1 and "reads as MD" in wrong["groups"][0]["conflict"]

    client.post("/ifc/symbols/unverify", json={"signatures": [g["signature"]]})
    right = client.post(f"{base}/review", json={"answers": [{"signature": g["signature"], "device_type_id": types["MD"]["id"]}]}).json()
    assert right["review"]["conflicts"] == 0 and right["totals"]["fire_alarm"] == 3


def test_the_comparison_names_a_device_answered_as_another():
    from app.ifc.comparison import compare

    schedule = {"floors": ["Level 1", "Level 2"], "items": [
        {"description": "Multisensor", "system": "FAS", "device": "Multisensor detector", "total": 5, "per_floor": {"Level 1": 2, "Level 2": 3}},
        {"description": "Manual Pull Station", "system": "FAS", "device": "Manual call point", "total": 2, "per_floor": {"Level 1": 1, "Level 2": 1}},
    ]}
    rows = [{"device_type": {"code": "MCP", "name": "Manual Call Point (Break Glass)"}, "per_floor": n, "qty": n} for n in (3, 4)]
    drawing = {"id": 1, "filename": "FA.dwg", "revision": "R0", "floor_boq": {"fire_alarm": {"floors": [
        {"sheet": "FA-1", "floor_name": "1ST FLOOR", "floors": [1], "multiplier": 1, "qty": 3, "rows": [rows[0]]},
        {"sheet": "FA-2", "floor_name": "2ND FLOOR", "floors": [2], "multiplier": 1, "qty": 4, "rows": [rows[1]]},
    ]}}}
    out = compare(schedule, [drawing])
    assert out["swaps"] == [{"more": "Manual call point", "fewer": "Multisensor detector", "qty": 5, "floors": 2, "ifc_types": ["MCP"]}]
