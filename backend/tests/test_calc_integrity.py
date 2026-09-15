"""Calculation contracts: hashes that name inputs and results, completeness,
confirmation of automatic no-current settings, and the repair report."""

from app.core.config import get_settings
from app.models import BoqCandidate, DesignRule, Project, ProjectDocument
from app.schemas_design import Channel, SpeakerType, VoiceEvacuationDesign, Zone
from app.seed import PART_CURRENT_CATEGORY
from app.services import calc_integrity, repair_report
from app.services.ve_calculation import calculate

from .test_battery_api import _current, _login_admin, _project_with_boq

settings = get_settings()


def _ve(channels, zones=3):
    return VoiceEvacuationDesign(
        speaker_types=[SpeakerType(key="ceiling", name="Ceiling", tap_watts=0.5)],
        zones=[Zone(name=f"L{i}", counts={"ceiling": 10}) for i in range(zones)],
        channels=channels, max_load_fraction=0.8,
    )


def test_ve_result_says_when_it_is_incomplete_and_hashes_its_inputs():
    complete = calculate(_ve([Channel(first_zone=0, amplifier_watts=60)]))
    assert complete.complete and complete.incomplete_reasons == []
    assert len(complete.input_hash) == 64 and len(complete.result_hash) == 64

    unrated = calculate(_ve([Channel(first_zone=0, amplifier_watts=None)]))
    assert not unrated.complete and "no amplifier rating" in unrated.incomplete_reasons[0]

    unassigned = calculate(_ve([Channel(first_zone=1, amplifier_watts=60)]))
    assert not unassigned.complete and "on no channel" in unassigned.incomplete_reasons[0]

    # Same inputs, same hashes; a changed count changes both.
    assert calculate(_ve([Channel(first_zone=0, amplifier_watts=60)])).input_hash == complete.input_hash
    changed = _ve([Channel(first_zone=0, amplifier_watts=60)])
    changed.zones[0].counts["ceiling"] = 11
    moved = calculate(changed)
    assert moved.input_hash != complete.input_hash and moved.result_hash != complete.result_hash


def test_stable_hash_ignores_key_order():
    assert calc_integrity.stable_hash({"a": 1, "b": [1, 2]}) == calc_integrity.stable_hash({"b": [1, 2], "a": 1})


def test_battery_hashes_follow_the_catalogue_version_and_automatic_no_load_needs_confirming(client, db_session):
    _login_admin(client)
    project_id = _project_with_boq(client)
    _current(client, "4-CPU", 211, 211)
    _current(client, "3-SDDC2", 264, 336)
    first = client.get(f"/projects/{project_id}/design/battery").json()
    assert first["complete"] is False and any("lower bound" in r for r in first["incomplete_reasons"])

    # The platform sets the filler plate to no current on its own.
    db_session.add(DesignRule(category=PART_CURRENT_CATEGORY, key="4-FIL", version=1, source="No electrical load: mechanical part, set automatically",
                              data={"part_no": "4-FIL", "standby_ma": 0, "alarm_ma": 0, "description": "Filler plate", "auto": True, "no_load": True}))
    db_session.commit()
    auto = client.get(f"/projects/{project_id}/design/battery").json()
    assert [c["part_no"] for c in auto["needs_confirmation"]] == ["4-FIL"]
    assert auto["complete"] is False and auto["input_hash"] != first["input_hash"]
    readiness = {c["key"]: c for c in client.get(f"/projects/{project_id}/readiness").json()["checks"]}
    # Blocked here anyway (this BOQ quotes a 26 Ah battery for a 45 Ah load); the unconfirmed part is listed.
    assert readiness["battery"]["status"] == "blocked"
    assert any("4-FIL" in item and "not yet confirmed" in item for item in readiness["battery"]["items"])

    confirmed = client.post(f"/projects/{project_id}/design/battery/confirm-no-load", json={"part_no": "4-FIL"}).json()
    assert confirmed["needs_confirmation"] == [] and confirmed["complete"] is True
    # A new catalogue version is a new input.
    assert confirmed["input_hash"] != auto["input_hash"]
    again = client.get(f"/projects/{project_id}/design/battery").json()
    assert again["input_hash"] == confirmed["input_hash"] and again["result_hash"] == confirmed["result_hash"]


def test_rejecting_an_automatic_no_load_makes_the_part_missing_and_keeps_it_so(client, db_session):
    _login_admin(client)
    project_id = _project_with_boq(client)
    _current(client, "4-CPU", 211, 211)
    _current(client, "3-SDDC2", 264, 336)
    db_session.add(DesignRule(category=PART_CURRENT_CATEGORY, key="4-FIL", version=1, source="auto",
                              data={"part_no": "4-FIL", "standby_ma": 0, "alarm_ma": 0, "auto": True, "no_load": True}))
    db_session.commit()
    rejected = client.post(f"/projects/{project_id}/design/battery/confirm-no-load", json={"part_no": "4-FIL", "confirm": False}).json()
    assert rejected["panels"][0]["missing_parts"] == ["4-FIL"] and rejected["complete"] is False
    # The automatic fill (run on every open) does not set it back.
    client.post(f"/projects/{project_id}/design/battery/fill-currents")
    after = client.get(f"/projects/{project_id}/design/battery").json()
    assert after["panels"][0]["missing_parts"] == ["4-FIL"]


def test_the_repair_report_reads_without_writing(client, db_session, monkeypatch):
    import app.routers.projects as projects_router
    from app.services.design_sheet_extractor import ExtractedBoqLine

    _login_admin(client)
    body = {"ep_number": "80001", "project_name": "Repair", "systems": [],
            "design_sheets": [{"system_code": "FAS", "document_path": "C:/nowhere/EP-80001 FAS Design.pdf"}]}
    pid = client.post("/projects", json=body).json()["id"]
    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "catalog_no": "4-CPU", "description": "CPU", "quantity": "1"}])
    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: [
        ExtractedBoqLine(catalog_no="4-CPU", description="CPU", quantity="2", group_heading=None, confidence=90, page=1),
        ExtractedBoqLine(catalog_no="SIGA-PS", description="Smoke detector", quantity="10", group_heading=None, confidence=90, page=1),
    ])
    project = db_session.get(Project, pid)

    report = repair_report.build(db_session, project)
    assert report.boq["old_lines"] == 1 and report.boq["new_lines"] == 2
    assert report.boq["changed"] == 1 and report.boq["added"] == 1
    assert any("not there any more" in d["findings"][0]["message"] for d in report.documents)
    assert any("not there any more" in b for b in report.blocking)
    # Nothing stored: no findings, no candidate, no runs, the BOQ untouched.
    assert db_session.query(ProjectDocument).count() == 0 and db_session.query(BoqCandidate).count() == 0
    assert [i["quantity"] for i in client.get(f"/projects/{pid}/boq").json()] == ["1"]

    written = repair_report.build(db_session, project, write=True)
    assert written.candidate_id is not None and db_session.query(ProjectDocument).count() == 1
    assert [i["quantity"] for i in client.get(f"/projects/{pid}/boq").json()] == ["1"]
