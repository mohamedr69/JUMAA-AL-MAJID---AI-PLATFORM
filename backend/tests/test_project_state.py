"""One project workflow: every page a view of the same records.

The material submittal register is the record. Project Home's summary, the
Material Submittal Log, the drawings' material-approval prerequisite and the
project's actions are worked out from it (app.services.project_state), and
every change to it writes a PROJECT_DATA_CHANGED row in the same
transaction, so an open page learns of it and reloads.
"""

from app.ai import submittal_reader
from app.core.config import get_settings
from app.models import Project, ProjectAction, ProjectChange, ProjectSubmittal, User

from .conftest import login
from .test_submittal_one_per_system import _reading

settings = get_settings()


def _project(client, **extra) -> int:
    login(client, settings.default_admin_email, settings.default_admin_password)
    return client.post("/projects", json={"ep_number": "30990", "project_name": "One workflow",
                                          "design_sheets": [], **extra}).json()["id"]


def _sync(db, project, readings, user=None):
    submittal_reader.sync_register(db, project, submittal_reader.build_map(readings), user)
    db.commit()
    db.expire_all()


def test_a_returned_revision_opens_one_action_and_filing_the_next_resolves_it(client, db_session):
    """FRC from Frontier returned at R0: one action, "R1 is not filed", held
    once and shown by Home and Material Submittals alike. R1 filed: the
    action is resolved -- gone from every page -- and says why."""
    pid = _project(client)
    project = db_session.get(Project, pid)
    r0 = _reading("BBY006-GME-MAS-EL-FA-0003", 0, "resubmit", system="FRC", maker="FRONTIER", relative="frc/R0.pdf")
    _sync(db_session, project, [r0])
    # Opened by the sync itself, in its own transaction -- not by a page asking.
    assert db_session.query(ProjectAction).filter(ProjectAction.project_id == pid,
                                                  ProjectAction.resolved_at.is_(None)).count() == 1

    state = client.get(f"/projects/{pid}/state").json()
    (action,) = [a for a in state["actions"] if a["kind"] == "material_submittal_revision"]
    assert action["text"] == ("Material submittal required: BBY006-GME-MAS-EL-FA-0003 R0 was returned "
                              "revise and resubmit; R1 is not filed")
    assert client.get(f"/projects/{pid}/actions").json()[0]["id"] == action["id"]
    # The map on the Material Submittals page lists the same action, not its own.
    assert client.get(f"/projects/{pid}/submittals/map").json()["actions"] in ([], [action["text"]])

    _sync(db_session, project, [r0, _reading("BBY006-GME-MAS-EL-FA-0003", 1, system="FRC", maker="FRONTIER",
                                             relative="frc/R1.pdf")])
    assert [a for a in client.get(f"/projects/{pid}/actions").json() if a["kind"] == "material_submittal_revision"] == []
    resolved = db_session.get(ProjectAction, action["id"])
    assert resolved.resolved_at is not None and resolved.resolution == "R1 filed (BBY006-GME-MAS-EL-FA-0003)"
    assert db_session.query(ProjectAction).filter(ProjectAction.project_id == pid).count() == 1   # one record, not two

    kinds = [(c.entity_type, c.change_type) for c in
             db_session.query(ProjectChange).filter(ProjectChange.project_id == pid).order_by(ProjectChange.id)]
    assert ("submittal", "created") in kinds and ("submittal", "updated") in kinds
    assert ("action", "opened") in kinds and ("action", "resolved") in kinds


def test_a_status_changed_on_the_register_reaches_every_view(client, db_session):
    """Material Submittals marks FAS approved. In the same commit: the
    revision's status and history, a change row; and after it Home's board,
    the Material Submittal Log and the drawings' prerequisite all say
    approved -- none of them keeps a status of its own."""
    pid = _project(client)
    created = client.post(f"/projects/{pid}/submittals", json={
        "title": "Fire Alarm System", "system_code": "FAS", "manufacturer": "EDWARDS", "revision": "R0",
        "status": "under_review"}).json()
    cursor = client.get(f"/projects/{pid}/changes").json()["latest"]
    assert cursor > 0

    required = client.get(f"/projects/{pid}/drawings/required?system=FAS").json()
    approval = next(i for g in required["groups"] for i in g["items"] if i["key"] == "material_approval")
    assert not approval["received"] and approval["remarks"].startswith("Material approval missing")
    fas = next(s for s in client.get(f"/projects/{pid}/state").json()["systems"] if s["code"] == "FAS")
    assert fas["material"]["label"] == "Under Review"

    assert client.patch(f"/projects/{pid}/submittals/{created['id']}", json={"status": "approved"}).status_code == 200

    changes = client.get(f"/projects/{pid}/changes?since={cursor}").json()
    assert [(c["entity_type"], c["entity_id"], c["system"], c["change_type"]) for c in changes["changes"]] == [
        ("submittal", created["id"], "FAS", "updated")]
    assert changes["latest"] == changes["changes"][-1]["id"]
    fas = next(s for s in client.get(f"/projects/{pid}/state").json()["systems"] if s["code"] == "FAS")
    assert fas["material"]["status"] == "approved" and fas["material"]["label"] == "Approved"
    (logged,) = client.get(f"/projects/{pid}/logs").json()["material_submittals"]
    assert (logged["revision"], logged["status"], logged["source"]) == ("R0", "A", "register")
    required = client.get(f"/projects/{pid}/drawings/required?system=FAS").json()
    approval = next(i for g in required["groups"] for i in g["items"] if i["key"] == "material_approval")
    assert approval["received"] and approval["remarks"].startswith("Material approval received")


def test_the_log_carries_each_revision_of_a_submittal_under_its_latest(client, db_session):
    """ELS R0 approved as noted, then R1 under review: one log row at R1, R0
    under it with its own status -- from the register's revisions."""
    pid = _project(client)
    project = db_session.get(Project, pid)
    _sync(db_session, project, [
        _reading("BBY006-GME-MAS-EL-LI-0001", 0, "approved_as_noted", relative="els/R0.pdf"),
        _reading("BBY006-GME-MAS-EL-LI-0001", 1, relative="els/R1.pdf"),
    ])
    (row,) = client.get(f"/projects/{pid}/logs").json()["material_submittals"]
    assert (row["reference"], row["revision"], row["status"]) == ("BBY006-GME-MAS-EL-LI-0001", "R1", "UR")
    assert [(r["revision"], r["status"]) for r in row["superseded"]] == [("R0", "ANN")]
    assert row["group_reference"] == "BBY006-GME-MAS-EL-LI-0001"


def test_the_register_and_its_actions_are_one_transaction(client, db_session):
    """sync_register does not commit on its own: a failure after it rolls
    back the register rows, the history, the actions and the change rows
    together -- never a register without its actions or the other way."""
    pid = _project(client)
    project = db_session.get(Project, pid)
    submittal_reader.sync_register(db_session, project, submittal_reader.build_map([
        _reading("CBL-0004", 0, "resubmit", system="FRC", maker="TIANJIE", relative="t.pdf")]), None)
    assert db_session.query(ProjectAction).filter(ProjectAction.project_id == pid).count() == 1
    db_session.rollback()
    assert db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == pid).count() == 0
    assert db_session.query(ProjectAction).filter(ProjectAction.project_id == pid).count() == 0
    assert db_session.query(ProjectChange).filter(ProjectChange.project_id == pid).count() == 0


def test_a_system_without_a_submittal_is_an_action_until_one_is_entered(client, db_session):
    """Once the folder has been read, a system of the project with no
    material submittal is an action; entering one resolves it."""
    pid = _project(client)
    project = db_session.get(Project, pid)
    from app.core.timeutils import utc_now
    from app.models import ProjectDesignSheet

    # The project has emergency lighting: its Design Sheet says so.
    project.design_sheets.append(ProjectDesignSheet(system_code="ELS", document_path="ELS design sheet.pdf"))
    db_session.commit()
    code = "ELS"
    # Before the folder is read, "none filed" is not known.
    assert client.get(f"/projects/{pid}/actions").json() == []
    project.documents_synced_at = utc_now()
    db_session.commit()
    texts = [a["text"] for a in client.get(f"/projects/{pid}/actions").json()]
    assert f"Material submittal required: no material submittal is filed for {code}" in texts
    client.post(f"/projects/{pid}/submittals", json={"title": code, "system_code": code})
    texts = [a["text"] for a in client.get(f"/projects/{pid}/actions").json()]
    assert f"Material submittal required: no material submittal is filed for {code}" not in texts


def test_home_counts_shop_drawings_from_their_records(client, db_session, tmp_path):
    """Home's "Shop drawings" per system is the Drawings page's own count:
    a schedule entry, a reply or a copy of a file is never a drawing."""
    from .test_drawings_module import FA, _admin, _doc, _project as _drawn_project, _reconcile, _seed

    _admin(client)
    pid, folder = _drawn_project(client, tmp_path, "95007")
    _seed(db_session, pid, folder, [
        _doc("BASEMENT- 4", "R0", "UR", ref="FA 102", name="BASEMENT- 4 FLOOR PLAN", path="04- Drawings/Shop drawings log.pdf",
             source="drawing schedule"),
        _doc("Basement 4", "R0", "approved", ref=f"{FA}-B04"),
        _doc("Basement 3", "R0", "UR", ref=f"{FA}-B03"),
    ])
    _reconcile(client, pid)
    fas = next(s for s in client.get(f"/projects/{pid}/state").json()["systems"] if s["code"] == "FAS")
    assert (fas["shop_drawings"]["total"], fas["shop_drawings"]["approved"], fas["shop_drawings"]["under_review"]) == (2, 1, 1)
    assert fas["shop_drawings"]["label"] == "Under Review"
    assert len(client.get(f"/projects/{pid}/logs").json()["drawings"]) == 2


def test_two_brands_filed_under_one_reference_are_two_log_rows(client, db_session):
    """Filed through the platform, a system's reference is the system's
    (EP-n-MAS-FRC), so Frontier and Tianjie cable can share it. They are
    two submittals, and the log lists both."""
    from app.models import SubmittalStatus

    pid = _project(client)
    for brand in ("FRONTIER", "TIANJIE"):
        db_session.add(ProjectSubmittal(project_id=pid, title="Fire Rated Cable", reference="EP-30990-MAS-FRC",
                                        system_code="FRC", brand_key=brand, manufacturer=brand, revision="R00",
                                        status=SubmittalStatus.under_review))
    db_session.commit()
    rows = client.get(f"/projects/{pid}/logs").json()["material_submittals"]
    assert sorted(r["name"] for r in rows) == ["Fire Rated Cable — FRONTIER", "Fire Rated Cable — TIANJIE"]
    assert {r["reference"] for r in rows} == {"EP-30990-MAS-FRC"}
