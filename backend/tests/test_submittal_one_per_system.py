"""One material submittal per system.

A system's material submittal is revised R0, R1, ... until approved. Its
revisions, the consultant's replies, the copies filed under our reference
and the main contractor's, the suppliers a fire rated cable was offered
from: none of them is another submittal. Each revision has one current
status, updated in place; what it was before is history. The page counts
submittals, by where their latest revision stands.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.ai import submittal_reader
from app.core.config import get_settings
from app.migrations import ALEMBIC_INI
from app.models import (Project, ProjectSubmittal, ProjectSubmittalEvent, ProjectSubmittalRevision,
                        ProjectSubmittalStatusChange, User)

from .conftest import login

settings = get_settings()


def _reading(reference, revision, status="none", *, system="ELS", relative, maker="EATON (MENVIER)",
             approval=False, modified="2026-09-01T10:00:00"):
    return {
        "is_submittal": True, "reference": reference, "revision": revision, "system_code": system,
        "title": f"Material Submittal for {system}", "system": system, "supplier": "AL ARABIA", "manufacturer": maker,
        "submitted": "", "relative": relative, "modified": modified, "in_approval_folder": approval,
        "reply": {"present": status != "none", "from_consultant": status != "none", "status": status, "code": "",
                  "consultant": "", "date": "", "evidence": "Approved as noted" if status != "none" else ""},
    }


# --- the map: one row per system ------------------------------------------------------------------


def test_our_copy_and_the_answered_copy_of_a_revision_are_one_submittal():
    """EP-30784's emergency lighting: "MS EML R0.pdf" (our reference, under
    review) and BBY006-GME-MAS-EL-LI-0001 (the consultant's answer, approved
    as noted) are R0 of one submittal, not two."""
    result = submittal_reader.build_map([
        _reading("EP-29941/SK/EM/201", 0, relative="03- MS/02- EML/MS EML R0.pdf"),
        _reading("BBY006-GME-MAS-EL-LI-0001", 0, "approved_as_noted", approval=True,
                 relative="08- approval/MS/EML/R0/BBY006-GME-MAS-EL-LI-0001.pdf"),
    ], systems_on_project=["ELS"])
    assert result["submittals"] == 1 and result["forms"] == 2
    (row,) = result["systems"][0]["rows"]
    assert row["reference"] == "BBY006-GME-MAS-EL-LI-0001" and row["references"][1] == "EP-29941/SK/EM/201"
    assert row["latest"] == "R0" and row["latest_status"] == "ANN"
    cell = row["cells"]["R0"]
    assert cell["copies"] == 2 and cell["also_filed_as"] == [{"reference": "EP-29941/SK/EM/201", "status": "UR"}]
    assert result["actions"] == []


def test_a_cable_resubmitted_from_another_supplier_is_a_revision_of_the_one_submittal():
    """EP-30784's fire rated cable: Frontier and Tianjie returned at R0,
    Fireguard approved as noted at R1 -- one submittal, standing at R1, with
    nothing more to file."""
    result = submittal_reader.build_map([
        _reading("BBY006-GME-MAS-EL-FA-0003", 0, "resubmit", system="FRC", maker="FRONTIER / RAMCRO", relative="frc/f.pdf"),
        _reading("BBY006-GME-MAS-EL-FA-0004", 0, "resubmit", system="FRC", maker="TIANJIE / RAMCRO", relative="frc/t.pdf",
                 modified="2026-09-02T10:00:00"),
        _reading("BBY006-GME-MAS-EL-FA-0002", 1, "approved_as_noted", system="FRC", maker="FIREGUARD / RAMCRO",
                 relative="frc/g-R1.pdf"),
    ])
    (row,) = result["systems"][0]["rows"]
    assert (row["reference"], row["latest"], row["latest_status"]) == ("BBY006-GME-MAS-EL-FA-0002", "R1", "ANN")
    assert row["manufacturer"] == "FIREGUARD / RAMCRO"
    assert row["cells"]["R0"]["status"] == "RR" and len(row["cells"]["R0"]["also_filed_as"]) == 1
    assert set(row["references"]) == {"BBY006-GME-MAS-EL-FA-0002", "BBY006-GME-MAS-EL-FA-0003", "BBY006-GME-MAS-EL-FA-0004"}
    # R0 was returned, but R1 answers it: no "R1 is not filed" for either supplier.
    assert result["actions"] == [] and result["submittals"] == 1


# --- the register: one row per system, a revision updated in place -------------------------------


def _project(client) -> Project:
    login(client, settings.default_admin_email, settings.default_admin_password)
    return client.post("/projects", json={"ep_number": "30841", "project_name": "One per system",
                                          "design_sheets": []}).json()["id"]


def test_an_answer_updates_the_revision_and_a_new_revision_joins_the_same_submittal(client, db_session):
    project = db_session.get(Project, _project(client))
    user = db_session.query(User).first()

    def sync(readings):
        submittal_reader.sync_register(db_session, project, submittal_reader.build_map(readings), user)
        db_session.expire_all()

    under_review = _reading("BBY006-GME-MAS-EL-LI-0001", 0, relative="R0/form.pdf")
    sync([under_review])
    answered = _reading("BBY006-GME-MAS-EL-LI-0001", 0, "approved_as_noted", relative="R0/answered.pdf",
                        modified="2026-09-10T10:00:00")
    sync([under_review, answered])

    (submittal,) = db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project.id).all()
    (r0,) = submittal.revisions
    assert (r0.revision, r0.status.value, r0.reply_code) == ("R00", "approved", "B")
    assert [(h.previous_status, h.new_status) for h in r0.history] == [(None, "under_review"), ("under_review", "approved")]
    assert (submittal.revision, submittal.status.value) == ("R00", "approved")
    assert len(submittal.events) == 2

    # R1 filed: the same submittal, now standing at R1 under review.
    sync([under_review, answered, _reading("BBY006-GME-MAS-EL-LI-0001", 1, relative="R1/form.pdf")])
    (submittal,) = db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project.id).all()
    assert [(r.revision, r.status.value) for r in submittal.revisions] == [("R00", "approved"), ("R01", "under_review")]
    assert (submittal.revision, submittal.status.value) == ("R01", "under_review")
    assert db_session.query(ProjectSubmittalRevision).count() == 2

    # Synced again with nothing new: nothing changes, nothing is added.
    events = len(submittal.events)
    sync([under_review, answered, _reading("BBY006-GME-MAS-EL-LI-0001", 1, relative="R1/form.pdf")])
    submittal = db_session.get(ProjectSubmittal, submittal.id)
    assert len(submittal.events) == events and db_session.query(ProjectSubmittalStatusChange).count() == 3


def test_the_page_counts_submittals_by_their_latest_revision(client, db_session):
    """ELS at R0 approved then R1 under review, FAS approved: two
    submittals, one approved and one under review -- per system as well."""
    project = db_session.get(Project, _project(client))
    readings = [
        _reading("BBY006-GME-MAS-EL-LI-0001", 0, "approved_as_noted", relative="els/R0.pdf"),
        _reading("EP-30841/SK/EM/201", 0, relative="els/our-R0.pdf"),
        _reading("BBY006-GME-MAS-EL-LI-0001", 1, relative="els/R1.pdf"),
        _reading("BBY006-GME-MAS-EL-FA-0001", 0, "approved", system="FAS", maker="EDWARDS", relative="fas/R0.pdf"),
    ]
    submittal_reader.sync_register(db_session, project, submittal_reader.build_map(readings), None)
    register = client.get(f"/projects/{project.id}/submittals").json()
    assert len(register["items"]) == 2
    assert {k: register["counts"][k] for k in ("total", "approved", "under_review", "rejected")} == \
        {"total": 2, "approved": 1, "under_review": 1, "rejected": 0}
    assert register["counts_by_system"]["ELS"]["total"] == 1 and register["counts_by_system"]["ELS"]["under_review"] == 1
    els = next(i for i in register["items"] if i["system_code"] == "ELS")
    assert [(r["revision"], r["status"]) for r in els["revisions"]] == [("R00", "approved"), ("R01", "under_review")]
    assert els["revisions"][0]["also_filed_as"] == ["EP-30841/SK/EM/201"]


def test_a_second_submittal_for_a_system_is_refused(client, db_session):
    pid = _project(client)
    first = client.post(f"/projects/{pid}/submittals", json={"title": "Fire Alarm System", "system_code": "FAS"})
    assert first.status_code == 201, first.text
    assert [(r["revision"], r["status"]) for r in first.json()["revisions"]] == [("R00", "not_submitted")]
    second = client.post(f"/projects/{pid}/submittals", json={"title": "Fire Alarm again", "system_code": "FAS"})
    assert second.status_code == 409 and second.json()["detail"]["code"] == "submittal_exists"
    # A new revision is a change to the one submittal, kept with its own status.
    changed = client.patch(f"/projects/{pid}/submittals/{first.json()['id']}",
                           json={"revision": "R01", "status": "under_review"}).json()
    assert [(r["revision"], r["status"]) for r in changed["revisions"]] == [("R00", "not_submitted"), ("R01", "under_review")]
    assert client.get(f"/projects/{pid}/submittals").json()["counts"]["total"] == 1


# --- the migration merges what was already there ---------------------------------------------------


def test_the_migration_merges_a_systems_register_rows_into_one(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'merge.db').as_posix()}")
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "b4e8f1a2c3d5")
        insert = text("INSERT INTO project_submittals (id, project_id, title, reference, system_code, manufacturer, "
                      "revision, status, reply_code, created_at, updated_at) VALUES (:id, 1, 'MS', :ref, :sys, :maker, "
                      ":rev, :status, :code, '2026-09-24', '2026-09-24')")
        for row in ((4, "BBY006-GME-MAS-EL-LI-0001", "ELS", "EATON", "R00", "approved", "B"),
                    (11, "EP-29941/SK/EM/201", "ELS", "MENVIER", "R00", "under_review", None),
                    (2, "BBY006-GME-MAS-EL-FA-0002", "FRC", "FIREGUARD", "R01", "approved", "B"),
                    (13, "BBY006-GME-MAS-EL-FA-0003", "FRC", "FRONTIER", "R00", "rejected", "C"),
                    (3, "BBY006-GME-MAS-EL-FA-0004", "FRC", "TIANJIE", "R00", "rejected", "C")):
            connection.execute(insert, dict(zip(("id", "ref", "sys", "maker", "rev", "status", "code"), row)))
        connection.execute(text("INSERT INTO project_submittal_events (submittal_id, kind, detail, at) "
                                "VALUES (11, 'ai_check', 'R0 UR', '2026-09-24')"))
        command.upgrade(config, "head")
        parents = connection.execute(text("SELECT id, system_code, revision, status FROM project_submittals ORDER BY system_code")).all()
        assert [tuple(p) for p in parents] == [(4, "ELS", "R00", "approved"), (2, "FRC", "R01", "approved")]
        revisions = connection.execute(text("SELECT submittal_id, revision, status, reference, also_filed_as "
                                            "FROM project_submittal_revisions ORDER BY submittal_id, revision")).all()
        assert [tuple(r)[:4] for r in revisions] == [
            (2, "R00", "rejected", "BBY006-GME-MAS-EL-FA-0004"), (2, "R01", "approved", "BBY006-GME-MAS-EL-FA-0002"),
            (4, "R00", "approved", "BBY006-GME-MAS-EL-LI-0001")]
        # The under-review copy's history went with it onto the one submittal.
        events = connection.execute(text("SELECT submittal_id, kind FROM project_submittal_events ORDER BY id")).all()
        assert (4, "ai_check") in [tuple(e) for e in events] and (4, "merged") in [tuple(e) for e in events]
    engine.dispose()
