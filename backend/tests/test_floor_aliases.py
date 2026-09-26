"""One physical floor, however the documents name it.

The Building Floor Registry keeps a floor's canonical identity apart from
the names the IFC and the shop drawings give it, and from the project's
own aliases between them. "L2", "L02", "LEVEL 2" and "2ND FLOOR" are one
floor everywhere; "1st Mechanical Floor" is L02 only where this
project's drawings say so or an engineer confirms it -- and then it is one
row, "L02" with "1st Mechanical Floor" under it, on every later IFC
revision and sync. A suspicion is asked, never merged; a merge overwrites
nothing of any shop drawing.
"""
from app.models import DrawingIssue, Project, ProjectBuildingFloor, ProjectFloorAlias, ProjectShopDrawing, ShopDrawingEvent
from app.services import shop_drawings

from .test_drawings_module import FA, _admin, _cells, _doc, _dxf, _import_ifc, _log, _project, _reconcile, _row, _seed, ai  # noqa: F401


def _floors(db, pid: int, *, active_only: bool = True) -> list[tuple]:
    query = db.query(ProjectBuildingFloor).filter(ProjectBuildingFloor.project_id == pid)
    if active_only:
        query = query.filter(ProjectBuildingFloor.active.is_(True))
    return [(f.floor_key, f.display_name, f.secondary_name) for f in query.order_by(ProjectBuildingFloor.sort_order)]


def _duplicates(client, pid: int) -> list[dict]:
    issues = client.get(f"/projects/{pid}/drawings/issues?system=FAS").json()
    return [i for i in issues["system_checks"] if i["kind"] == "possible_duplicate_floor"]


def _supersede(client, pid: int, path):
    first = client.get(f"/projects/{pid}/ifc-drawings").json()[0]
    response = client.post(f"/projects/{pid}/ifc-drawings", data={"supersedes_id": str(first["id"])},
                           files={"file": (path.name, path.read_bytes(), "application/dxf")})
    assert response.status_code == 201, response.text


def test_the_regression_an_alias_makes_one_floor_of_two_names(client, db_session, tmp_path):
    """IFC floor names L01, 1st Mechanical Floor, L02, and the project's
    alias 1st Mechanical Floor -> L02: the registry is L01, L02; the log
    is L01, L02; L02 shows "1st Mechanical Floor" under it; there is no
    separate 1st Mechanical Floor row. And the alias holds through the next
    IFC revision."""
    _admin(client)
    pid, _folder = _project(client, tmp_path, "94001")
    _import_ifc(client, pid, _dxf(tmp_path / "ifc.dxf", ["L01", "1ST MECHANICAL FLOOR", "L02"]))
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "MECHANICAL#1", "L2"]

    merged = client.post(f"/projects/{pid}/drawings/floors/merge", json={"alias_key": "MECHANICAL#1", "canonical_key": "L2"})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged"] is True and merged.json()["conflicts"] == []
    db_session.expire_all()
    assert _floors(db_session, pid) == [("L1", "L01", None), ("L2", "L02", "1st Mechanical Floor")]
    gone = db_session.query(ProjectBuildingFloor).filter_by(project_id=pid, floor_key="MECHANICAL#1").one()
    assert gone.active is False and gone.merged_into == "L2"
    (alias,) = db_session.query(ProjectFloorAlias).filter_by(project_id=pid).all()
    assert (alias.alias_key, alias.canonical_key, alias.decision, alias.source) == ("MECHANICAL#1", "L2", "merge", "engineer")

    log = _log(client, pid, "FAS")
    assert [(r["floor"], r["floor_secondary"]) for r in log["rows"]] == [("L01", None), ("L02", "1st Mechanical Floor")]
    assert _duplicates(client, pid) == []

    # The next IFC revision names the floors the same way: still one floor, no question asked.
    _supersede(client, pid, _dxf(tmp_path / "ifc2.dxf", ["L01", "1ST MECHANICAL FLOOR", "L02"]))
    _reconcile(client, pid)
    db_session.expire_all()
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "L2"]
    assert [(r["floor"], r["floor_secondary"]) for r in _log(client, pid, "FAS")["rows"]] == [("L01", None), ("L02", "1st Mechanical Floor")]
    assert _duplicates(client, pid) == []
    registry = client.get(f"/projects/{pid}/drawings/floors").json()
    assert [a["alias_key"] for a in registry["aliases"]] == ["MECHANICAL#1"]


def test_a_title_naming_both_is_evidence_kept_for_every_later_revision_and_sync(client, db_session, tmp_path):
    """The shop drawing is filed as L02 under the title "L02- 1ST MECHANICAL
    FLOOR PLAN"; the IFC sheet calls that floor "1ST MECHANICAL FLOOR".
    The title ties them: one floor, the alias kept at project level, and
    a new IFC revision and another sync change nothing."""
    _admin(client)
    pid, folder = _project(client, tmp_path, "94002")
    _import_ifc(client, pid, _dxf(tmp_path / "ifc.dxf", ["L01", "1ST MECHANICAL FLOOR", "L03"]))
    _seed(db_session, pid, folder, [_doc("L02", "R0", "ANN", ref=f"{FA}-L02", name="L02- 1ST MECHANICAL FLOOR PLAN")])
    _reconcile(client, pid)
    db_session.expire_all()
    assert _floors(db_session, pid) == [("L1", "L01", None), ("L2", "L02", "1st Mechanical Floor"), ("L3", "L03", None)]
    (alias,) = db_session.query(ProjectFloorAlias).filter_by(project_id=pid).all()
    assert (alias.alias_key, alias.canonical_key, alias.source) == ("MECHANICAL#1", "L2", "evidence")
    assert "L02- 1ST MECHANICAL FLOOR PLAN" in alias.evidence["titles"]
    drawing = db_session.query(ProjectShopDrawing).filter_by(project_id=pid).one()
    assert drawing.floor_keys == ["L2"]
    row = _row(_log(client, pid, "FAS"), f"{FA}-L02")
    assert (row["floor"], row["floor_secondary"], row["latest_status"]) == ("L02", "1st Mechanical Floor", "approved_as_noted")
    assert _duplicates(client, pid) == []

    events = db_session.query(ShopDrawingEvent).filter_by(project_id=pid).count()
    _supersede(client, pid, _dxf(tmp_path / "ifc2.dxf", ["L01", "1ST MECHANICAL FLOOR", "L03"]))
    _reconcile(client, pid)
    _reconcile(client, pid)
    db_session.expire_all()
    assert _floors(db_session, pid) == [("L1", "L01", None), ("L2", "L02", "1st Mechanical Floor"), ("L3", "L03", None)]
    assert db_session.query(ProjectFloorAlias).filter_by(project_id=pid).count() == 1
    assert db_session.query(ShopDrawingEvent).filter_by(project_id=pid).count() == events
    assert _cells(_row(_log(client, pid, "FAS"), f"{FA}-L02"))["R0"] == "approved_as_noted"


def test_a_named_sheet_where_a_level_has_no_ifc_sheet_is_asked_never_merged(client, db_session, tmp_path):
    """The IFC has L01, 1ST MECHANICAL FLOOR, L03; the shop drawings name
    L02 and nothing ties them. A possible duplicate floor is asked; Keep
    Separate is kept, and the next sync does not ask again."""
    _admin(client)
    pid, folder = _project(client, tmp_path, "94003")
    _import_ifc(client, pid, _dxf(tmp_path / "ifc.dxf", ["L01", "1ST MECHANICAL FLOOR", "L03"]))
    _seed(db_session, pid, folder, [_doc("L02", "R0", "UR", ref=f"{FA}-L02", name="L02 PLAN")])
    _reconcile(client, pid)
    db_session.expire_all()
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "MECHANICAL#1", "L2", "L3"]
    (issue,) = _duplicates(client, pid)
    assert (issue["detail"]["alias_key"], issue["detail"]["canonical_key"]) == ("MECHANICAL#1", "L2")
    assert "1st Mechanical Floor and L02 may represent the same physical level" in issue["text"]
    assert issue["source"] == "system" and issue["ai"] is None

    kept = client.post(f"/projects/{pid}/drawings/floors/separate", json={"alias_key": "MECHANICAL#1", "canonical_key": "L2"})
    assert kept.status_code == 200, kept.text
    db_session.expire_all()
    (alias,) = db_session.query(ProjectFloorAlias).filter_by(project_id=pid).all()
    assert (alias.decision, alias.source) == ("separate", "engineer")
    assert _duplicates(client, pid) == []
    _reconcile(client, pid)
    assert _duplicates(client, pid) == []
    resolved = db_session.query(DrawingIssue).filter_by(project_id=pid, kind="possible_duplicate_floor").one()
    assert resolved.resolved_at is not None and resolved.resolution == "Kept separate"
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "MECHANICAL#1", "L2", "L3"]


def test_merging_floors_that_both_carry_drawings_needs_confirmation_and_keeps_both(client, db_session, tmp_path):
    """FAS has a drawing on 1st Mechanical Floor (R0 approved) and another
    on L02 (R0 under review). The merge stops and shows both; confirmed,
    both drawings stay with every revision and status, both on L02, and
    the floor's two references are a conflict for the engineer."""
    _admin(client)
    pid, folder = _project(client, tmp_path, "94004")
    _import_ifc(client, pid, _dxf(tmp_path / "ifc.dxf", ["L01", "1ST MECHANICAL FLOOR", "L03"]))
    _seed(db_session, pid, folder, [
        _doc("1st Mechanical Floor", "R0", "approved", ref=f"{FA}-MEC-010007", name="1ST MECHANICAL FLOOR PLAN"),
        _doc("L02", "R0", "UR", ref=f"{FA}-L02-010012", name="L02 PLAN"),
    ])
    _reconcile(client, pid)
    body = {"alias_key": "MECHANICAL#1", "canonical_key": "L2"}
    stopped = client.post(f"/projects/{pid}/drawings/floors/merge", json=body)
    assert stopped.status_code == 409, stopped.text
    detail = stopped.json()["detail"]
    assert detail["code"] == "merge_review"
    (conflict,) = detail["conflicts"]
    assert conflict["system"] == "FAS"
    assert [d["reference"] for d in conflict["alias"]] == [f"{FA}-MEC-010007"] and [d["reference"] for d in conflict["canonical"]] == [f"{FA}-L02-010012"]
    db_session.expire_all()
    assert db_session.query(ProjectFloorAlias).filter_by(project_id=pid).count() == 0     # nothing changed

    confirmed = client.post(f"/projects/{pid}/drawings/floors/merge", json={**body, "confirm": True})
    assert confirmed.status_code == 200, confirmed.text
    db_session.expire_all()
    drawings = {d.drawing_reference: d for d in db_session.query(ProjectShopDrawing).filter_by(project_id=pid)}
    assert drawings[f"{FA}-MEC-010007"].floor_keys == ["L2"] and drawings[f"{FA}-L02-010012"].floor_keys == ["L2"]
    assert [(r.revision, r.status) for r in drawings[f"{FA}-MEC-010007"].revisions] == [("R0", "approved")]
    assert [(r.revision, r.status) for r in drawings[f"{FA}-L02-010012"].revisions] == [("R0", "under_review")]
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "L2", "L3"]
    log = _log(client, pid, "FAS")
    assert {(r["floor"], r["floor_secondary"]) for r in log["rows"] if r["reference"]} == {("L02", "1st Mechanical Floor")}
    issues = client.get(f"/projects/{pid}/drawings/issues?system=FAS").json()["system_checks"]
    assert [(i["kind"], i["floor_key"]) for i in issues if i["kind"] == "reference_conflict"] == [("reference_conflict", "L2")]


def test_the_ai_gives_its_opinion_on_a_possible_duplicate_but_merges_nothing(client, db_session, tmp_path, ai):
    _admin(client)
    pid, folder = _project(client, tmp_path, "94005")
    _import_ifc(client, pid, _dxf(tmp_path / "ifc.dxf", ["L01", "1ST MECHANICAL FLOOR", "L03"]))
    _seed(db_session, pid, folder, [_doc("L02", "R0", "UR", ref=f"{FA}-L02", name="L02 PLAN")])
    fake = ai({"possible_same_floor": True, "confidence": 0.99, "evidence": "The mechanical floor sits at level 2",
               "requires_engineer": False})
    shop_drawings.reconcile(db_session, db_session.get(Project, pid), ai=True)
    (issue,) = _duplicates(client, pid)
    assert issue["ai"]["possible_same_floor"] is True and issue["ai"]["confidence"] == 0.99
    assert issue["ai"]["requires_engineer"] is True
    payload = fake.requests[-1]
    assert "94005" not in str(payload.parts[0].text) and "L02" in payload.parts[0].text
    # However sure the AI is, nothing is merged: two floors, no alias.
    db_session.expire_all()
    assert [f[0] for f in _floors(db_session, pid)] == ["L1", "MECHANICAL#1", "L2", "L3"]
    assert db_session.query(ProjectFloorAlias).filter_by(project_id=pid).count() == 0
    # A pass without the AI (the page's catch-up, a manual reconcile) keeps its opinion.
    _reconcile(client, pid)
    (issue,) = _duplicates(client, pid)
    assert issue["ai"]["possible_same_floor"] is True
    # And a floor nothing could ever match is refused as the target of a merge.
    assert client.post(f"/projects/{pid}/drawings/floors/merge",
                       json={"alias_key": "MECHANICAL#1", "canonical_key": "NOWHERE"}).status_code == 404
