"""The AI checks the BOQ against the Design Sheets and Project Info against the
DRF, and settles each value by agreement between independent readings."""

from __future__ import annotations

import pymupdf
import pytest
from PIL import Image

import app.routers.jobs as jobs_router
import app.routers.projects as projects_router
from app.ai import provider as provider_module
from app.ai import verification
from app.ai.provider import RecordingProvider
from app.models import AiVerification, BoqSnapshot, Project
from app.services import drf_extractor
from app.services.design_sheet_extractor import ExtractedBoqLine
from app.services.drf_extractor import DrfExtractionResult, ExtractedField, ExtractedSystem

from .test_projects import _login_admin, _valid_project_payload


def _line(catalog, description, quantity, y, group="Main Panel"):
    return ExtractedBoqLine(catalog_no=catalog, description=description, quantity=quantity, group_heading=group,
                            confidence=90.0, page=1, y_px=y, table_span=(100, 1900), row_bounds=(y - 20, y + 20))


FIRST_READ = [
    _line("4-CPU", "Central Processor Module", "1", 200),
    _line("SIGA-PS", "Photoelectric smoke detector", "120", 300),
    _line("SIGA-CT1", "Single input module", "14", 400),
    _line("SIGA-HFS", "Heat detector", "30", 500),
]
FRESH_READ = [
    _line("4-CPU", "Central Processor Module", "1", 200),
    _line("SIGA-PS", "Photoelectric smoke detector", "126", 300),
    _line("SIGA-CT1", "Single input module", "14", 400),
    _line("SIGA-CC1", "Synchronised output module", "6", 600),
]


def _rows(*readings):
    return {"rows": [{"label": f"R{i}", "quantity": q, "catalog_no": c, "description": d, "readable": True}
                     for i, (q, c, d) in enumerate(readings, start=1)]}


@pytest.fixture()
def sheets(monkeypatch):
    state = {"lines": FIRST_READ}
    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: list(state["lines"]))
    monkeypatch.setattr(verification._Pages, "get", lambda self, path, page, run: Image.new("L", (2000, 2800), 255))
    monkeypatch.setattr(verification, "page_count", lambda path: 1)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    return state


@pytest.fixture()
def recording():
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    yield provider
    provider_module.set_provider(None)


def _project(client, sheets, ep="62001"):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload(ep)).json()["id"]
    assert len(client.post(f"/projects/{pid}/boq/ensure").json()["items"]) == 4
    return pid


# --- settling one value ---------------------------------------------------------------------


def test_agreement_between_two_sources_settles_a_value():
    q = verification.agree_quantity
    assert verification.settle(q, "5", "7", "5")[0] == "confirmed"
    assert verification.settle(q, "5", "7", "7")[:2] == ("corrected", "7")
    assert verification.settle(q, "5", "7", "9")[0] == "second"
    assert verification.settle(q, "5", "7", "9", "9")[:2] == ("corrected", "9")
    assert verification.settle(q, "5", "7", "9", "5")[0] == "confirmed"
    assert verification.settle(q, "5", "7", None, None)[0] == "unresolved"
    assert verification.settle(q, None, None, None, "1", "1")[:2] == ("corrected", "1")
    # A part number the OCR and the AI agree on is written as the AI spelled it.
    assert verification.settle(verification.agree_catalog, "£232 301H", "E232 301H", "E-232 301H",
                               spell_as_ai=True)[1] == "E-232 301H"


def test_descriptions_with_different_numbers_do_not_agree():
    assert verification.agree_text("Digital Power Amplifier Module 300W", "Digital Power Amplifier Module 300 W")
    assert not verification.agree_text("Digital Power Amplifier Module 300W", "Digital Power Amplifier Module 500W")


# --- the BOQ ------------------------------------------------------------------------------------


def test_the_boq_is_settled_against_the_sheet_and_can_be_undone(client, db_session, sheets, recording):
    pid = _project(client, sheets)
    sheets["lines"] = FRESH_READ
    recording.answers = [
        # First reading, rows in page order: 4-CPU, SIGA-PS, SIGA-CT1 (misread), SIGA-CC1.
        _rows(("1", "4-CPU", "Central Processor Module"), ("126", "SIGA-PS", "Photoelectric smoke detector"),
              ("41", "SIGA-CT1", "Single input module"), ("6", "SIGA-CC1", "Synchronised output module")),
        # Second reading, by the larger model, of the one row that did not agree.
        _rows(("14", "SIGA-CT1", "Single input module")),
        # SIGA-HFS is looked for on the pages: not found, twice.
        {"lines": [{"id": "x", "found": False, "quantity": "", "catalog_no": "", "page": 1}]},
        {"lines": []},
    ]
    job = client.post(f"/projects/{pid}/jobs/ai-verify?scope=boq")
    assert job.status_code == 202, job.text
    assert job.json()["status"] == "succeeded", job.json()
    assert recording.calls == 4
    assert [r.tier for r in recording.requests] == ["small", "standard", "small", "standard"]

    state = client.get(f"/projects/{pid}/ai-verification").json()
    record = state["boq"]
    assert record["status"] == "completed"
    assert {k: record["summary"][k] for k in ("confirmed", "corrected", "added", "removed", "unresolved")} == \
        {"confirmed": 2, "corrected": 1, "added": 1, "removed": 1, "unresolved": 0}

    lines = {i["catalog_no"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    assert set(lines) == {"4-CPU", "SIGA-PS", "SIGA-CT1", "SIGA-CC1"}
    assert lines["SIGA-PS"]["quantity"] == "126" and lines["SIGA-PS"]["ai_check"]["status"] == "corrected"
    assert lines["SIGA-CT1"]["quantity"] == "14" and lines["SIGA-CT1"]["ai_check"]["status"] == "confirmed"
    assert lines["SIGA-CC1"]["ai_check"]["status"] == "added"
    assert db_session.query(BoqSnapshot).count() == 1
    assert record["can_undo"] is True

    undone = client.post(f"/projects/{pid}/ai-verification/{record['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["boq"]["status"] == "undone"
    restored = {i["catalog_no"]: i["quantity"] for i in client.get(f"/projects/{pid}/boq").json()}
    assert restored == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14", "SIGA-HFS": "30"}


def test_an_edit_clears_the_ai_verdict_on_that_line(client, db_session, sheets, recording):
    pid = _project(client, sheets, ep="62002")
    sheets["lines"] = FIRST_READ
    recording.answers = [_rows(("1", "4-CPU", "Central Processor Module"), ("120", "SIGA-PS", "Photoelectric smoke detector"),
                               ("14", "SIGA-CT1", "Single input module"), ("30", "SIGA-HFS", "Heat detector"))]
    assert client.post(f"/projects/{pid}/jobs/ai-verify?scope=boq").json()["status"] == "succeeded"
    current = client.get(f"/projects/{pid}/boq")
    items = current.json()
    assert all(i["ai_check"]["status"] == "confirmed" for i in items)
    rows = [{k: i.get(k) for k in ("id", "system_code", "group_heading", "manufacturer", "catalog_no", "description",
                                   "quantity", "unit", "unit_price", "total_price", "remarks")} for i in items]
    rows[0]["quantity"] = "2"
    rows[1]["unit_price"] = "10.00"
    saved = client.put(f"/projects/{pid}/boq", json=rows, headers={"If-Match": current.headers["X-Resource-Version"]})
    assert saved.status_code == 200, saved.text
    after = {i["catalog_no"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    assert after["4-CPU"]["ai_check"] is None
    assert after["SIGA-PS"]["ai_check"]["status"] == "confirmed"   # a price is not a sheet value
    state = client.get(f"/projects/{pid}/ai-verification").json()
    assert state["boq"]["stale"] is True and state["boq"]["can_undo"] is False


def test_a_line_is_not_removed_on_one_reading(client, db_session, sheets, recording):
    pid = _project(client, sheets, ep="62003")
    sheets["lines"] = FIRST_READ[:3]
    recording.answers = [
        _rows(("1", "4-CPU", "Central Processor Module"), ("120", "SIGA-PS", "Photoelectric smoke detector"),
              ("14", "SIGA-CT1", "Single input module")),
        {"lines": []},                                                                        # first look: not found
        {"lines": [{"id": "c4", "found": True, "quantity": "30", "catalog_no": "SIGA-HFS", "page": 1}]},  # second: found
    ]
    assert client.post(f"/projects/{pid}/jobs/ai-verify?scope=boq").json()["status"] == "succeeded"
    lines = {i["catalog_no"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    assert "SIGA-HFS" in lines and lines["SIGA-HFS"]["quantity"] == "30"


def test_unreadable_rows_stay_as_held_and_are_listed(client, db_session, sheets, recording):
    pid = _project(client, sheets, ep="62004")
    sheets["lines"] = FIRST_READ
    unreadable = {"rows": [{"label": f"R{i}", "quantity": "", "catalog_no": "", "description": "", "readable": False}
                           for i in range(1, 5)]}
    recording.answers = [unreadable, unreadable] + [unreadable] * 4
    assert client.post(f"/projects/{pid}/jobs/ai-verify?scope=boq").json()["status"] == "succeeded"
    record = client.get(f"/projects/{pid}/ai-verification").json()["boq"]
    assert record["summary"]["unresolved"] == 4 and record["summary"]["corrected"] == 0
    assert {i["catalog_no"]: i["quantity"] for i in client.get(f"/projects/{pid}/boq").json()} == \
        {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14", "SIGA-HFS": "30"}
    # First and second readings, then a close-up of each unsettled row -- the
    # four blank test rows are the same image, so three close-ups come from the cache.
    from app.models import AiUsage

    close_ups = db_session.query(AiUsage).filter(AiUsage.task == "verify_boq_row_close_up").count()
    assert close_ups == 4 and recording.calls == 3


# --- Project Info ----------------------------------------------------------------------------------


def _drf(tmp_path):
    path = tmp_path / "EP-62010 DRF.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((60, 60), "Design Request Form", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _drf_reply(fields: dict, systems: list[dict] | None, readable: bool = True) -> dict:
    return {"fields": [{"field": name, "value": value or "", "readable": value is not None} for name, value in fields.items()],
            "systems": systems or [], "systems_readable": readable}


HELD = {"project_name": "IVY Garden 2", "plot_number": "648-8523", "location": "Wadi Al Safa 5, DLRC, Dubai",
        "client": "Samana", "consultant": "Al Hilal", "contractor": "Samana Developers",
        "contact_person": "Mahammad Naushad Bennapade", "contact_phone": "971543079068",
        "contact_email": "mahammad.bennapade@samanadevelopers.com", "scope_of_work": "Design, Supply, T&C",
        "other_information": "Quoted as per IFC drawing dated 18-06-2025 only"}


def test_project_info_is_settled_against_the_drf(client, db_session, sheets, recording, tmp_path, monkeypatch):
    pid = _project(client, sheets, ep="62010")
    project = db_session.get(Project, pid)
    project.drf_document_path = str(_drf(tmp_path))
    db_session.commit()
    ocr = DrfExtractionResult(fields={"client": ExtractedField("Samana Developers LLC", 80, "Client")},
                              systems=[ExtractedSystem("Fire Alarm", "EDWARDS", True, True)])
    monkeypatch.setattr(drf_extractor, "extract_drf_fields", lambda path: ocr)
    read = {**HELD, "client": "Samana Developers LLC", "contractor": "Samana Developer"}
    marked = [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
              {"name": "Voice Evacuation", "brand": "EDWARDS", "method_statement": False, "drawing": False}]
    recording.answers = [
        _drf_reply(read, marked),
        # Second reading: the contractor as held, the new system confirmed; Central
        # Battery System (held, MS only) is missing from both readings.
        _drf_reply({**read, "contractor": "Samana Developers"}, marked),
    ]
    assert client.post(f"/projects/{pid}/jobs/ai-verify?scope=details").json()["status"] == "succeeded"
    assert recording.calls == 2
    record = client.get(f"/projects/{pid}/ai-verification").json()["details"]
    outcomes = {i["id"]: i["outcome"] for i in record["items"]}
    assert outcomes["client"] == "corrected"
    assert outcomes["contractor"] == "confirmed"
    assert outcomes["system:Voice Evacuation"] == "added"
    # Removing a system needs every reading to agree; the OCR read a table
    # without it, but both AI readings agree too -- so here it is removed.
    assert outcomes["system:Central Battery System"] == "removed"

    db_session.expire_all()
    project = db_session.get(Project, pid)
    assert project.client == "Samana Developers LLC" and project.contractor == "Samana Developers"
    assert {s.name for s in project.systems} == {"Fire Alarm", "Voice Evacuation"}

    undone = client.post(f"/projects/{pid}/ai-verification/{record['id']}/undo")
    assert undone.status_code == 200, undone.text
    db_session.expire_all()
    project = db_session.get(Project, pid)
    assert project.client == "Samana" and {s.name for s in project.systems} == {"Fire Alarm", "Central Battery System"}


def test_what_the_drf_does_not_show_is_left_alone(client, db_session, sheets, recording, tmp_path, monkeypatch):
    pid = _project(client, sheets, ep="62011")
    project = db_session.get(Project, pid)
    project.drf_document_path = str(_drf(tmp_path))
    db_session.commit()
    # A half-page scan: no Systems table and no Other Information box.
    ocr = DrfExtractionResult(fields={}, warnings=["Could not locate the Systems table"])
    monkeypatch.setattr(drf_extractor, "extract_drf_fields", lambda path: ocr)
    read = {**HELD, "other_information": None}
    recording.answers = [_drf_reply(read, [], readable=True), _drf_reply(read, [], readable=False)]
    assert client.post(f"/projects/{pid}/jobs/ai-verify?scope=details").json()["status"] == "succeeded"
    record = client.get(f"/projects/{pid}/ai-verification").json()["details"]
    outcomes = {i["id"]: i["outcome"] for i in record["items"]}
    assert outcomes["other_information"] == "not_checked"
    assert outcomes["system:Fire Alarm"] == "not_checked"
    db_session.expire_all()
    project = db_session.get(Project, pid)
    assert {s.name for s in project.systems} == {"Fire Alarm", "Central Battery System"}
    assert project.other_information == HELD["other_information"]


# --- starting it ------------------------------------------------------------------------------------


def test_opening_a_page_starts_the_check_once(client, db_session, sheets, recording):
    pid = _project(client, sheets, ep="62020")
    sheets["lines"] = FIRST_READ
    recording.answers = [_rows(("1", "4-CPU", "Central Processor Module"), ("120", "SIGA-PS", "Photoelectric smoke detector"),
                               ("14", "SIGA-CT1", "Single input module"), ("30", "SIGA-HFS", "Heat detector"))]
    first = client.post(f"/projects/{pid}/ai-verification/ensure").json()
    assert first["boq"]["status"] == "completed"
    calls = recording.calls
    again = client.post(f"/projects/{pid}/ai-verification/ensure").json()
    assert again["boq"]["id"] == first["boq"]["id"] and recording.calls == calls
    assert db_session.query(AiVerification).filter(AiVerification.project_id == pid, AiVerification.scope == "boq").count() == 1


def test_a_project_that_refuses_ai_is_not_checked(client, db_session, sheets, recording):
    pid = _project(client, sheets, ep="62021")
    project = db_session.get(Project, pid)
    project.ai_policy = "blocked"
    db_session.commit()
    assert client.post(f"/projects/{pid}/jobs/ai-verify").status_code == 409
    state = client.post(f"/projects/{pid}/ai-verification/ensure").json()
    assert state["available"] is False and state["boq"] is None
    assert recording.calls == 0

