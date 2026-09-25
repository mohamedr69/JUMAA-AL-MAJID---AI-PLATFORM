"""The Drawings Log: each shop drawing at every revision; IFC floors only where none is drawn yet (app/services/drawing_log.py)."""
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


def test_each_shop_drawing_is_a_row_with_its_own_reference_and_every_revision():
    """The log is the shop drawings': each row a shop drawing with its own
    reference and each revision's own status. The IFC drawings add only
    the floors no shop drawing covers yet -- by floor, never by IFC sheet
    ("FA 101") -- and change no shop drawing's reference or status."""
    records = [
        _doc("3rd Basement", "R0", "rejected", reply="Revise as per comments", ref="EP-SDW-FA-B03"),
        _doc("3rd Basement", "R1", "UR", day=2, ref="EP-SDW-FA-B03"),
        _doc("3rd Basement", "R1", "approved", day=3, ref="EP-SDW-FA-B03"),   # the reply: decided beats under review
        _doc("Ground Floor", "R0", "ANN", ref="EP-SDW-FA-GF"),
        _doc("1st Podium Floor", "R0", "UR", ref="EP-SDW-FA-P01"),            # a podium, not floor 1
        _doc("TYPICAL 3RD TO 16TH FLOOR", "R0", "approved", ref="EP-SDW-FA-TYP"),
        _doc(None, "R0", "UR", ref="EP-SDW-FA-099"),                          # no floor on it: listed last
        _doc("Level 40", "R0", "UR", ref="EP-SDW-FA-098"),                    # a floor no IFC plan has
        _doc("Ground Floor", "R0", "approved", system="ELS", ref="EP-SDW-EL-GF"),   # another system's drawing
    ]
    with_ifc, without_ifc = build(IFC, records), build([], records)
    assert with_ifc["revisions"] == ["R0", "R1", "R2"] and with_ifc["submissions"] == 8

    drawings = [r for r in with_ifc["rows"] if r["source"] == "shop_drawing"]
    # The shop drawings are the same with the IFC drawings imported as without.
    strip = lambda rows: [(r["reference"], r["floor"], {k: v["status"] for k, v in r["cells"].items()})
                          for r in rows if r["source"] == "shop_drawing"]
    assert strip(with_ifc["rows"]) == strip(without_ifc["rows"])
    assert [r["reference"] for r in drawings] == [
        "EP-SDW-FA-B03", "EP-SDW-FA-GF", "EP-SDW-FA-P01", "EP-SDW-FA-TYP", "EP-SDW-FA-098", "EP-SDW-FA-099"]

    b3 = drawings[0]
    assert [b3["cells"][r]["status"] for r in ("R0", "R1", "R2")] == ["not_approved", "approved", "not_submitted"]
    assert b3["latest_revision"] == "R1" and b3["latest_status"] == "approved"
    assert b3["cells"]["R0"]["remarks"] == "Revise as per comments"
    typical = drawings[3]
    assert typical["floors"] == 14 and typical["floor"] == "Typical 3rd to 16th Floor"

    # The IFC floors no shop drawing covers: the floor alone, not submitted.
    floors = [r for r in with_ifc["rows"] if r["source"] == "ifc_floor"]
    assert [(r["floor"], r["reference"], r["latest_status"]) for r in floors] == [
        ("1st Floor", None, "not_submitted"), ("Structural Slab", None, "not_submitted")]
    # Nothing of the IFC drawing's is shown as a shop drawing's.
    assert not any("FA 1" in str(value) for row in with_ifc["rows"] for value in row.values())
    assert with_ifc["counts"] == {"approved": 2, "approved_as_noted": 1, "under_review": 3, "not_submitted": 2}


def test_an_earlier_revision_is_never_not_submitted_once_a_later_one_exists():
    """R1 on file proves R0 was submitted and answered; R2 proves R0 and R1
    were. An earlier revision whose reply is not in the folder says so --
    it is never "not submitted" or "under review", and never borrows the
    latest revision's status."""
    out = build([], [
        _doc("Basement 1", "R0", "UR", ref="EP-SDW-FA-B01"),                 # filed, reply not read
        _doc("Basement 1", "R1", "UR", day=2, ref="EP-SDW-FA-B01"),
        _doc("Basement 2", "R2", "approved", day=3, ref="EP-SDW-FA-B02"),   # only R2 in the folder
    ])
    b1, b2 = (next(r for r in out["rows"] if r["reference"] == ref) for ref in ("EP-SDW-FA-B01", "EP-SDW-FA-B02"))
    assert [b1["cells"][r]["status"] for r in ("R0", "R1", "R2")] == ["reply_not_found", "under_review", "not_submitted"]
    assert b1["cells"]["R0"]["path"] == "03- Drawings/SD/FA/R0/EP-SDW-FA-B01.pdf"   # its own file, still linked
    assert [b2["cells"][r]["status"] for r in ("R0", "R1", "R2")] == ["reply_not_found", "reply_not_found", "approved"]
    assert b2["cells"]["R0"]["path"] is None and "R2 was submitted" in b2["cells"]["R0"]["note"]


def test_no_earlier_revision_is_ever_open_or_unsubmitted_once_a_later_one_exists():
    """The rule, over every shape of history: for a shop drawing standing at
    Rn, no revision before it reads "Not Submitted" or "Under Review" --
    with or without the IFC drawings, whatever the folder holds of it."""
    import itertools

    statuses = ("UR", "rejected", "ANN", "approved", None)          # None: that revision is not in the folder
    histories = [h for h in itertools.product(statuses, repeat=3) if h[-1] is not None]
    for n, history in enumerate(histories):
        records = [_doc("Ground Floor", f"R{i}", status, day=1 + i, ref=f"EP-SDW-FA-{n:03d}")
                   for i, status in enumerate(history) if status is not None]
        for out in (build([], records), build(IFC, records)):
            for row in (r for r in out["rows"] if r["source"] == "shop_drawing"):
                top = int(row["latest_revision"][1:])
                earlier = {rev: row["cells"][rev]["status"] for rev in out["revisions"] if int(rev[1:]) < top}
                assert not set(earlier.values()) & {"not_submitted", "under_review"}, (history, earlier)
                for i, status in enumerate(history[:-1]):
                    if status in ("rejected", "ANN", "approved"):    # an answer on file is shown as filed
                        assert row["cells"][f"R{i}"]["status"] != "reply_not_found", (history, i)


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
    # The IFC floor with no shop drawing yet: the floor, no reference.
    (row,) = log["rows"]
    assert (row["source"], row["reference"], row["latest_status"]) == ("ifc_floor", None, "not_submitted")
    assert log["ifc"][0]["revision"] == "R0"
    export = client.get(f"/projects/{project_id}/drawings/log/export.xlsx")
    assert export.status_code == 200 and "Drawings Log FAS.xlsx" in export.headers["content-disposition"]
    # Folders open only on the PC the platform runs on; the test client is not it.
    assert client.post(f"/projects/{project_id}/drawings/open-folder", json={}).status_code == 403


def test_importing_the_ifc_drawing_again_leaves_every_shop_drawing_as_it_was(client, db_session, tmp_path):
    """The bug: after BOQ > As per IFC, the log showed the IFC sheets' names
    as the shop drawings' references and an R0 the consultant had rejected
    as "Not Submitted". The shop drawings are their own records -- the
    document index of the project folder -- and importing the IFC drawing,
    or importing it again, neither rewrites nor removes one of them, nor
    changes how the log shows it."""
    from dataclasses import asdict

    from app.core.timeutils import utc_now
    from app.models import Project, ProjectDocument

    assert login(client, settings.default_admin_email, settings.default_admin_password).status_code == 200
    folder = tmp_path / "EP-91043 Tower"
    folder.mkdir()
    pid = client.post("/projects", json={"ep_number": "91043", "project_name": "Tower", "design_sheets": [],
                                         "source_folder_path": str(folder)}).json()["id"]
    reference = "EP-91043-SDW-FA-GF-010002"
    for revision, status, reply in (("R0", "rejected", "Revise as per comments"), ("R1", "UR", None)):
        relative = f"04- Drawings/SD/FA/{revision}/{reference}.pdf"
        record = ControlledDocument("FAS", f"{reference}.pdf", relative, datetime(2026, 9, 1 + int(revision[1])),
                                    reference, revision, status, floor="GROUND FLOOR", reply_text=reply,
                                    category="drawings")
        data = {**asdict(record), "modified": record.modified.isoformat()}
        db_session.add(ProjectDocument(project_id=pid, role="document", path=str(folder / relative),
                                       relative_path=relative, filename=f"{reference}.pdf", state="fresh",
                                       findings=[], acknowledged=[], extracted={"records": [data], "notes": []}))
    db_session.get(Project, pid).documents_synced_at = utc_now()
    db_session.commit()

    def shop_drawings():
        rows = client.get(f"/projects/{pid}/drawings/log").json()["rows"]
        return [(r["reference"], r["floor"], {k: v["status"] for k, v in r["cells"].items()})
                for r in rows if r["source"] == "shop_drawing"], rows

    before, _ = shop_drawings()
    assert before == [(reference, "Ground Floor", {"R0": "not_approved", "R1": "under_review", "R2": "not_submitted"})]
    stored = sorted((d.id, d.relative_path, str(d.extracted)) for d in
                    db_session.query(ProjectDocument).filter(ProjectDocument.project_id == pid))

    doc = ezdxf.new("R2018")
    doc.modelspace().add_circle((0, 0), 200)
    path = tmp_path / "GROUND FLOOR FIRE ALARM LAYOUT.dxf"
    doc.saveas(path)
    for _ in range(2):                                   # imported, then imported again
        assert client.post(f"/projects/{pid}/ifc-drawings",
                           files={"file": (path.name, path.read_bytes(), "application/dxf")}).status_code in (200, 201)
    after, rows = shop_drawings()
    assert after == before
    assert not any(path.stem in str(value) for row in rows for value in row.values())
    db_session.expire_all()
    assert sorted((d.id, d.relative_path, str(d.extracted)) for d in
                  db_session.query(ProjectDocument).filter(ProjectDocument.project_id == pid)) == stored


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
    # The material approval first -- from the register, not a folder -- then the contractor's files.
    assert [g["name"] for g in out["groups"]] == ["Approvals", "Electrical IFC Drawings", "Mechanical IFC Drawings", "Others"]
    assert out["total"] == 7 and out["received"] == 2 and out["not_received"] == 5
    assert not items["material_approval"]["received"] and items["material_approval"]["kind"] == "approval"
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
    assert els["system"] == "ELS" and [g["name"] for g in els["groups"]] == ["Approvals", "Electrical Drawings", "Others"]
    items = {i["key"]: i for g in els["groups"] for i in g["items"]}
    assert set(items) == {"material_approval", "els_lighting_ifc", "els_fa_ifc", "els_load_schedule"} and els["received"] == 1
    assert items["els_load_schedule"]["format"] == "PDF / XLS" and items["els_load_schedule"]["received"]
    assert items["els_lighting_ifc"]["remarks"] == "Required for ELS design coordination"

    fas = client.get(f"/projects/{project_id}/drawings/required").json()
    assert fas["system"] == "FAS" and fas["total"] == 7                     # the fire alarm list is its own
    sent = client.post(f"/projects/{project_id}/drawings/required/request", json={"keys": ["els_lighting_ifc"]}).json()
    assert "emergency lighting shop drawings" in sent["body"] and "(IFC / DWG)" in sent["body"]
    items = {i["key"]: i for g in client.get(f"/projects/{project_id}/drawings/required?system=ELS").json()["groups"] for i in g["items"]}
    assert items["els_lighting_ifc"]["requested_at"]
    xlsx = client.get(f"/projects/{project_id}/drawings/required/export.xlsx?system=ELS")
    assert xlsx.status_code == 200 and "Actions Required ELS.xlsx" in xlsx.headers["content-disposition"]
    assert client.get(f"/projects/{project_id}/drawings/required?system=XYZ").status_code == 404


def test_the_schedule_of_drawings_is_not_a_set_of_drawings():
    """A project keeps a "Shop drawings log.pdf" listing every drawing it
    plans -- FA 101 UNDER GROUND PLAN, FA 102 BASEMENT-4 -- with the
    dates they are due. Those rows carry no revision and no consultant
    reply, and read as drawings they filled the log with floors nobody
    had drawn yet. They are still read, for matching an issued drawing to
    the floor it was scheduled against; they are not drawings."""
    when = datetime(2026, 8, 6)

    def record(reference, floor, source, revision="R0", status="UR"):
        return ControlledDocument(
            system_code="FAS", name=f"{floor} PLAN", path="04- Drawings/x.pdf", modified=when,
            reference=reference, revision=revision, status=status, floor=floor,
            source=source, category="drawings")

    built = build([], [
        record("FA 101", "UNDER GROUND", "drawing schedule"),
        record("FA 102", "BASEMENT- 4", "drawing schedule"),
        record("BBY006-GME-SDW-FP-FA-BSM-B04-010025", "B04", "document", "R1", "rejected"),
    ])

    # Only the drawing that was actually submitted.
    assert built["submissions"] == 1
    assert [row["reference"] for row in built["rows"]] == [
        "BBY006-GME-SDW-FP-FA-BSM-B04-010025"]


def test_a_drawing_down_a_long_path_can_still_be_opened(client, tmp_path):
    """Windows stops at 260 characters unless a path is given in its
    extended-length form. A shop drawing inside a synced project folder
    is easily past that -- "04- Drawings/08-Shop Drawing/1.FAVE/R1/04.
    Basement-1/BBY006-GME-SDW-FP-FA-BSM-B01-010001 Shop Drawing for..." --
    and asked the ordinary way it is simply not there. The page then said
    the file had been deleted over one sitting on the drive.

    Even making the folder needs the long form, which is the whole point.
    """
    import os

    import pymupdf

    from app.core.config import get_settings
    from app.services.document_control import _os_path

    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)
    root = tmp_path / "EP-40700"
    deep = root / "04- Drawings" / "08-Shop Drawing" / "1.FAVE" / "R1"
    deep = deep / ("0" * 90) / ("1" * 90)
    os.makedirs(_os_path(deep), exist_ok=True)
    drawing = deep / ("BBY006-GME-SDW-FP-FA-BSM-B01-010001 " + "x" * 40 + ".pdf")
    assert len(str(drawing)) > 260, "the path has to be past the limit to test anything"

    with pymupdf.open() as document:
        document.new_page()
        document.save(_os_path(drawing))
    # The ordinary way it is not there at all -- which is the bug.
    assert not drawing.is_file()

    relative = drawing.relative_to(root).as_posix()
    created = client.post("/projects", json={
        "ep_number": "40700", "project_name": "Long Path", "source_folder_path": str(root),
        "design_sheets": []}).json()
    served = client.get(
        f"/projects/{created['id']}/logs/file", params={"path": relative})
    assert served.status_code == 200, served.text
    assert served.content[:5] == b"%PDF-"


def test_the_log_covers_every_system_the_project_has(client, db_session, tmp_path):
    """A project has the systems its DRF gives it, and each has shop
    drawings of its own. The log was fixed to the fire alarm, so a
    project's emergency lighting drawings were read, indexed, and then
    shown nowhere at all.

    A system a drawing names that the project does not list is still
    offered: the drawing is on the drive."""
    from app.models import Project
    from app.routers.drawings import _systems_with_drawings
    from app.services.document_control import ControlledDocument

    login(client, settings.default_admin_email, settings.default_admin_password)
    made = client.post("/projects", json={
        "ep_number": "41100", "project_name": "Two Systems", "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
                    {"name": "Monitored Emergency Lighting", "brand": "MENVIER",
                     "method_statement": True, "drawing": True}]}).json()
    project = db_session.get(Project, made["id"])

    def drawing(code, reference):
        return ControlledDocument(
            system_code=code, name="PLAN", path=f"{reference}.pdf", modified=datetime(2026, 8, 6),
            reference=reference, revision="R0", status="UR", floor="B01", category="drawings")

    records = [
        drawing("FAS", "BBY006-GME-SDW-FP-FA-BSM-B01-010001"),
        drawing("ELS", "BBY006-GME-SDW-EL-LI-BSM-B01-010027"),
        drawing("FRC", "BBY006-GME-SDW-FRC-BSM-B01-010099"),
    ]
    offered = _systems_with_drawings(project, records)
    assert "FAS" in offered and "ELS" in offered, "both of the project's systems"
    assert "FRC" in offered, "a drawing on the drive under a system the project does not list"

    # And each system shows its own drawings, not another's.
    for code, reference in [("FAS", "FP-FA"), ("ELS", "EL-LI")]:
        built = build([], records, in_system=lambda c, code=code: c == code)
        assert [r["reference"] for r in built["rows"]] == [
            next(d.reference for d in records if reference in d.reference)]


def test_a_sheet_covering_a_run_of_floors_covers_all_of_them():
    """A typical-floor sheet is drawn once and issued for the whole run:
    "L23 TO L40 - RES 20 TO 37 (TYP 1A) FLOOR PLAN" is the drawing for
    every floor from 23 to 40. Keeping only the first of them left the
    other seventeen floors looking as though nobody had drawn them.

    A hyphen is not a run: "L41-3RD MECHANICAL FLOOR PLAN" is one floor."""
    from app.services.document_control import floor_name

    covering = floor_name("L23 TO L40 - RES 20 TO 37 (TYP 1A) FLOOR PLAN")
    assert covering == "L23 TO L40"
    assert floors_named(covering) == {f"L{n}" for n in range(23, 41)}

    # Two floors named together are both of them.
    pair = floor_name("L52 & 53 - RES 48 & 49 (TYP 2C) FLOOR PLAN")
    assert floors_named(pair) == {"L52", "L53"}

    # A hyphen inside a floor name is not a range: 41, not 41 down to 3.
    assert floor_name("L41-3RD MECHANICAL FLOOR PLAN") == "L41"
    assert floors_named("L41") == {"L41"}

    # And the wording that already worked still does.
    assert floors_named(floor_name("TYPICAL 23RD TO 38TH FLOOR")) == {f"L{n}" for n in range(23, 39)}
    assert floors_named(floor_name("BASEMENT-1 FLOOR PLAN")) == {"B1"}


def test_one_submission_covering_several_floors_reaches_each_of_them():
    """A basement submission is drawn and answered once for several
    floors: "Shop Drawing for Basement 4, 3, 2 Floor Plan". The floors
    are named on the wrapper, not in the drawing's own title block, so
    the record reached the log with no floor at all and three basements
    looked undrawn."""
    from app.services.document_control import floor_name

    # Named with commas, and named with an ampersand.
    listed = floor_name("BBY006-GME-SDW-EL-FA-BSM-ZZZ-010001 Shop Drawing for Basement 4, 3, 2 Floor Plan Fire Alarm Layout")
    assert listed == "Basement 4, 3, 2"
    assert floors_named(listed) == {"B4", "B3", "B2"}

    pair = floor_name("BBY006-GME-SDW-EL-FA-BSM-ZZZ-010006- Shop Drawing for Fire Alarm Basement 4 & 3")
    assert floors_named(pair) == {"B4", "B3"}

    # One floor is still one floor.
    assert floors_named(floor_name("BASEMENT-1 FLOOR PLAN")) == {"B1"}
    assert floors_named(floor_name("PODIUM-3 FLOOR PLAN")) == {"P3"}
    assert floors_named(floor_name("GROUND FLOOR PLAN")) == {"GF"}


def test_the_floor_comes_off_the_file_when_the_sheet_does_not_name_one():
    """The drawing's own title block is asked first. A submission that
    names its floors only on the wrapper would otherwise be a drawing of
    nowhere."""
    from datetime import datetime as _dt

    from app.services.document_control import parse_page

    sheet = (
        "DRAWING NO: BBY006-GME-SDW-EL-FA-BSM-ZZZ-010001" + chr(10)
        + "DRAWING TITLE: FIRE ALARM LAYOUT"
    )
    named = parse_page(sheet, "C:/p/Shop Drawing for Basement 4, 3, 2 Floor Plan.pdf", _dt(2026, 8, 6), 1)
    assert [row.floor for row in named] == ["Basement 4, 3, 2"]

    # The sheet wins where its title block names a floor: the file name is
    # only the fallback, not an override.
    titled = (
        "DRAWING NO: BBY006-GME-SDW-EL-FA-BSM-ZZZ-010001" + chr(10)
        + "DRAWING TITLE: GROUND FLOOR PLAN - FIRE ALARM LAYOUT"
    )
    on_sheet = parse_page(titled, "C:/p/Shop Drawing for Basement 4, 3, 2 Floor Plan.pdf", _dt(2026, 8, 6), 1)
    assert on_sheet[0].floor == "GROUND FLOOR"


def test_a_floor_covered_by_several_submissions_is_still_one_row():
    """A submission can cover several floors at once, so a floor can be
    covered more than once: Basement 4 is on its own sheet, and on both
    of the wrappers that carry the basements together. A row per
    submission showed B4 three times over, each with a different status
    for the same floor.

    The floors are what the rows are, and where more than one submission
    covers a floor at the same revision the one that decides it wins,
    and failing that the one filed last."""
    from app.services.document_control import ControlledDocument

    def sheet(reference, revision, status, floor, when):
        return ControlledDocument(
            system_code="FAS", name=f"{floor} PLAN", path=f"{reference}.pdf",
            modified=datetime(2026, 8, when), reference=reference, revision=revision,
            status=status, floor=floor, category="drawings")

    built = build([], [
        # B4's own sheet, answered and then resubmitted.
        sheet("BBY006-GME-SDW-FP-FA-BSM-B04-010025", "R0", "rejected", "BASEMENT-4", 1),
        sheet("BBY006-GME-SDW-FP-FA-BSM-B04-010025", "R1", "UR", "BASEMENT-4", 2),
        # Two wrappers that also cover B4.
        sheet("BBY006-GME-SDW-EL-FA-BSM-ZZZ-010001", "R0", "UR", "Basement 4, 3, 2", 3),
        sheet("BBY006-GME-SDW-EL-FA-BSM-ZZZ-010006", "R0", "rejected", "Basement 4 & 3", 4),
    ])
    floors = [row["floor_named"] for row in built["rows"]]
    assert len(floors) == len(set(floors)), f"a floor is listed twice: {floors}"

    # B4 has a sheet of its own, so that is what stands for it. Each
    # wrapper keeps the basement it is still the drawing for, and is named
    # for that rather than for everything it once covered.
    assert set(floors) == {"BASEMENT-4", "Basement 3", "Basement 2"}

    b4 = next(r for r in built["rows"] if r["floor_named"] == "BASEMENT-4")
    assert b4["revision"] == "R1"
    # R0 was answered on two of them; the one that decides it is kept.
    assert b4["revisions"]["R0"]["label"] == "Not Approved"
    assert b4["revisions"]["R1"]["label"] == "Under Review"

    # The wrapper that still stands for B2 says so, rather than claiming
    # the basements a later sheet has taken.
    wrapper = next(r for r in built["rows"] if r["floor_named"] == "Basement 2")
    assert wrapper["revisions"]["R0"]["label"] == "Under Review"


def test_a_drawing_issued_for_a_run_of_floors_is_one_row():
    """A typical-floor sheet is drawn once for the whole run. Resolving
    the floors one at a time is what drops a superseded combined sheet,
    but the rows are gathered back under the drawing that won them: the
    log reads the way the drawing does, not nineteen rows saying Level 3,
    Level 4, Level 5."""
    from app.services.document_control import ControlledDocument

    def sheet(reference, revision, status, floor, when=1, name=None):
        return ControlledDocument(
            system_code="FAS", name=name or f"{floor} PLAN", path=f"{reference}.pdf",
            modified=datetime(2026, 8, when), reference=reference, revision=revision,
            status=status, floor=floor, category="drawings")

    # As the engineers title them: a typical-floor sheet says so on its
    # face, which is what makes it one drawing for the run rather than a
    # set of floors that happened to be sent together.
    run = "L03 TO L21 - RES 1 TO 19 (TYP 1A) FLOOR PLAN"
    built = build([], [
        sheet("BBY006-GME-SDW-FP-FA-ZZZ-ZZZ-010008", "R0", "rejected", "L03 TO L21", name=run),
        sheet("BBY006-GME-SDW-FP-FA-ZZZ-ZZZ-010008", "R1", "UR", "L03 TO L21", 2, name=run),
        sheet("BBY006-GME-SDW-FP-FA-ZZZ-L22-010009", "R0", "rejected", "L22"),
    ])
    rows = built["rows"]
    assert [r["floor_named"] for r in rows] == ["L03 TO L21", "L22"]
    assert rows[0]["revision"] == "R1"
    # It still stands for every floor of the run.
    assert floors_named(rows[0]["floor_named"]) == {f"L{n}" for n in range(3, 22)}


def test_a_file_name_is_not_a_floor_name():
    """The floor is read off the file where the sheet names none, but a
    hundred characters of drawing number and description is not a floor.
    Printed as one it filled the column and stood for nothing.

    A title block line that is a floor and nothing else still counts,
    and a title block exports its runs apart, so the gaps are closed."""
    from app.services.document_control import floor_name

    wordy = ("BBY006-GME-SDW-EL-LI-XXX-XXX-010048 Shop Drawing for Lift Machine "
             "Floor Plan Emergency Lighting Layout")
    assert floor_name(wordy, whole=False) is None
    # The floors it does name are still read out of it.
    assert floor_name("Shop Drawing for Basement 4, 3, 2 Floor Plan", whole=False) == "Basement 4, 3, 2"

    # A line that is a floor and nothing else is one.
    assert floor_name("GROUND FLOOR PLAN") == "GROUND FLOOR"
    # And the gaps a title block leaves are closed.
    assert floor_name("LIFT MACHINE ROOM  FLOOR PLAN") == "LIFT MACHINE ROOM FLOOR PLAN"


def test_a_typical_sheet_is_one_row_and_floors_drawn_apart_are_not():
    """Two kinds of drawing cover several floors, and they are not the
    same thing.

    A typical-floor sheet is drawn once for a run of identical floors
    and says so on its face -- (TYP 1A), TYPICAL 23RD TO 38TH. That is
    one drawing and one row.

    A submission that merely carries several floors together -- Basement
    4, 3, 2 -- is three floors each drawn on its own and sent in one
    envelope. The engineer tracks the basement, not the envelope, so
    those are a row each."""
    from app.services.document_control import ControlledDocument

    def sheet(reference, name, floor, revision="R0", status="UR"):
        return ControlledDocument(
            system_code="FAS", name=name, path=f"{reference}.pdf", modified=datetime(2026, 8, 6),
            reference=reference, revision=revision, status=status, floor=floor,
            category="drawings")

    built = build([], [
        sheet("BBY006-GME-SDW-FP-FA-ZZZ-ZZZ-010008",
              "L23 TO L40 - RES 20 TO 37 (TYP 1A) FLOOR PLAN", "L23 TO L40"),
        sheet("BBY006-GME-SDW-EL-FA-BSM-ZZZ-010001",
              "Shop Drawing for Basement 4, 3, 2 Floor Plan", "Basement 4, 3, 2"),
    ])
    rows = {row["floor_named"]: row for row in built["rows"]}

    # The typical run: one row, standing for all eighteen floors.
    assert "L23 TO L40" in rows and rows["L23 TO L40"]["floors"] == 18

    # The basements: a row each, and none of them claiming the others.
    assert {"Basement 4", "Basement 3", "Basement 2"} <= set(rows)
    assert all(rows[f"Basement {n}"]["floors"] == 1 for n in (4, 3, 2))

    # Every floor once, whichever kind it came from.
    covered = [f for row in built["rows"] for f in floors_named(row["floor_named"])]
    assert len(covered) == len(set(covered)), "a floor is covered twice"
