"""BOQ Extraction V2, phase 1: a read that stops keeps its state and resumes;
no detected row is dropped; a call that failed never removes an item; the
part number is the item's identity; the BOQ is written in one transaction;
a temporary folder that will not delete does not fail a call."""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

from app.ai import provider as provider_module
from app.ai import sheet_reader
from app.ai.budget import JobBudget, Limits
from app.ai.provider import ClaudeCodeProvider, RecordingProvider
from app.core.config import get_settings
from app.extraction.issues import ReviewReason
from app.models import BackgroundJob, DocumentReading, ExtractionIssue, ExtractionRun, Project
from app.services import boq_provenance

from .test_ai_sheet_reader import (  # noqa: F401 -- the fixtures are registered by the import
    PAGE_ANSWER, SECOND, _close_up, _login, _page_with, _project, _row, ai, recording,
)

settings = get_settings()


class _Failure(Exception):
    """A scripted provider failure of a kind: "timeout", "transport", ..."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _sheet(tmp_path, name: str):
    path = tmp_path / name
    path.write_bytes(f"%PDF-1.4 {name}".encode())
    return path


def _ensure(client, project) -> dict:
    return client.post(f"/projects/{project.id}/boq/ensure").json()


def _run(db, project) -> ExtractionRun:
    return db.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).order_by(ExtractionRun.id.desc()).first()


def _issues(db, run) -> dict[str, ExtractionIssue]:
    rows = db.query(ExtractionIssue).filter(ExtractionIssue.run_id == run.id, ExtractionIssue.target.like("boq_line:%")).all()
    return {row.detail.get("catalog_no") or row.detail.get("description"): row for row in rows}


def _reading(db, run) -> DocumentReading:
    return db.get(DocumentReading, run.reading_id)


# --- TEST 1 / TEST 2: a call that failed leaves the row, for review -------------------------------


@pytest.mark.parametrize("kind, code", [("timeout", ReviewReason.AI_TIMEOUT), ("transport", ReviewReason.AI_PROVIDER_ERROR)])
def test_a_close_up_that_fails_leaves_the_row_for_review_not_removed(client, db_session, tmp_path, ai, recording, kind, code):
    """The two readings dispute SIGA-CT1; its close-up times out (or the
    provider fails). The row is not a line, but it is not gone either: a
    review row carrying the first reading's quantity, the reason being the
    failure, and pending -- a resumed read asks about it again."""
    sheet = _sheet(tmp_path, "EP-71001 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _page_with({"SIGA-CT1": "41"}), _Failure(kind), _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="71001")
    _login(client)

    body = _ensure(client, project)

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120"}
    run = _run(db_session, project)
    assert run.state == "partial", run.state
    issues = _issues(db_session, run)
    assert set(issues) == {"SIGA-CT1", "SIGA-HFS"}
    disputed = issues["SIGA-CT1"].detail
    assert disputed["reason_code"] == code.value and disputed["pending"] is True
    assert disputed["primary"]["quantity"] == "14", "the first reading stands, unverified"
    assert disputed["verification"]["status"] == "not_completed"
    assert disputed["row_id"] == "p1r3" and disputed["source_stage"] == "primary"
    assert issues["SIGA-HFS"].detail["reason_code"] == ReviewReason.UNREADABLE.value
    settled = _reading(db_session, run).reading["settled"]
    assert set(settled) == {"p1r1", "p1r2", "p1r4"}, "the failed row is not checkpointed as settled"
    assert client.get(f"/projects/{project.id}/extraction").json()["runs"][0]["state"] == "partial"


# --- TEST 3: a row only the second reading found is never dropped ---------------------------------


def _second_with_extra_row(quantity: str = "6") -> dict:
    rows = list(SECOND["rows"]) + [_row("item", quantity, "SIGA-CC1", "Synchronised output module", 260)]
    return {"rows": rows, "has_line_items": True}


def test_a_row_only_the_second_reading_found_is_reviewed_when_its_close_up_disagrees(client, db_session, tmp_path, ai, recording):
    sheet = _sheet(tmp_path, "EP-71002 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _second_with_extra_row("6"),
                         _close_up("30", "SIGA-HFS", "Heat detector"),               # the row neither reading could read
                         _close_up("5", "SIGA-CC1", "Synchronised output module")]  # disagrees with the second reading
    project = _project(db_session, sheet, ep="71002")
    _login(client)

    body = _ensure(client, project)

    assert "SIGA-CC1" not in {i["catalog_no"] for i in body["items"]}
    run = _run(db_session, project)
    issue = _issues(db_session, run)["SIGA-CC1"].detail
    assert issue["reason_code"] == ReviewReason.QUANTITY_CONFLICT.value and issue["source_stage"] == "second"
    assert issue["row_id"] == "p1s5" and issue["primary"]["quantity"] == "6" and issue["pending"] is False
    assert run.state == "completed", "settled: the engineer decides, nothing is pending"


def test_a_row_only_the_second_reading_found_is_reviewed_when_its_close_up_fails(client, db_session, tmp_path, ai, recording):
    sheet = _sheet(tmp_path, "EP-71003 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _second_with_extra_row("6"), _close_up("30", "SIGA-HFS", "Heat detector"), _Failure("timeout")]
    project = _project(db_session, sheet, ep="71003")
    _login(client)

    _ensure(client, project)

    run = _run(db_session, project)
    issue = _issues(db_session, run)["SIGA-CC1"].detail
    assert issue["reason_code"] == ReviewReason.AI_TIMEOUT.value and issue["pending"] is True
    assert run.state == "partial"


# --- TEST 4 / TEST 5 / TEST 6: the part number is the identity ------------------------------------


def test_the_same_quantity_under_a_different_part_number_does_not_verify(client, db_session, tmp_path, ai, recording):
    """The first reading has SIGA-CT1 = 14; the second reads the row as
    SIGA-CT2 = 14 with the same description. Same quantity, different
    item: not a line, and the close-up that also reads SIGA-CT2 makes it a
    part-number conflict for the engineer, with both values shown."""
    sheet = _sheet(tmp_path, "EP-71004 FAS Design.pdf")
    second = {"rows": [dict(row, catalog_no="SIGA-CT2") if row["catalog_no"] == "SIGA-CT1" else row for row in SECOND["rows"]],
              "has_line_items": True}
    recording.answers = [PAGE_ANSWER, second,
                         _close_up("14", "SIGA-CT2", "Single input module"),        # the SIGA-CT1 row's close-up
                         _close_up("30", "SIGA-HFS", "Heat detector"),
                         {"rows": [{"label": "R1", "quantity": "", "catalog_no": "", "description": "", "readable": False}]}]
    project = _project(db_session, sheet, ep="71004")
    _login(client)

    body = _ensure(client, project)

    assert {i["catalog_no"] for i in body["items"]} == {"4-CPU", "SIGA-PS"}
    issues = _issues(db_session, _run(db_session, project))
    conflict = issues["SIGA-CT1"].detail
    assert conflict["reason_code"] == ReviewReason.PART_NUMBER_CONFLICT.value
    assert conflict["primary"]["catalog_no"] == "SIGA-CT1" and conflict["verification"]["close_up"]["catalog_no"] == "SIGA-CT2"
    assert issues["SIGA-CT2"].detail["source_stage"] == "second"


def test_exact_part_number_and_quantity_verify_and_similar_descriptions_do_not_merge(client, db_session, tmp_path, ai, recording):
    """Two adjacent rows with the same wording and different part numbers
    are two items: each is paired with its own second reading and each is
    a line with its own quantity."""
    sheet = _sheet(tmp_path, "EP-71005 FAS Design.pdf")
    page = {"rows": list(PAGE_ANSWER["rows"]) + [_row("item", "7", "SIGA-CT2", "Single input module", 200)], "has_line_items": True}
    recording.answers = [page, page, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="71005")
    _login(client)

    body = _ensure(client, project)

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14", "SIGA-CT2": "7"}
    assert recording.calls == 3, "no close-up: every row was paired with its own second reading"


def test_the_part_number_is_normalised_and_a_prefix_is_not_the_same_part():
    same = sheet_reader.same_item
    a = {"catalog_no": "SIGA-OSD-FCN", "description": "Intelligent Photoelectric Smoke Detector"}
    assert same(a, {"catalog_no": " siga-osd-fcn ", "description": "Photoelectric smoke detector"})
    assert same(a, {"catalog_no": "SIGA OSD FCN", "description": ""})
    assert not same(a, {"catalog_no": "SIGA-OSD", "description": "Intelligent Photoelectric Smoke Detector"})
    assert not same(a, {"catalog_no": "SIGA-HRD-FCN", "description": "Intelligent Photoelectric Smoke Detector"})
    # Only a reading with no part number falls back to the wording.
    assert same(a, {"catalog_no": "", "description": "Intelligent Photoelectric Smoke Detector"})
    assert not same(a, {"catalog_no": "", "description": "Heat detector"})


# --- TEST 12 / TEST 13 / TEST 14: the time budget, the checkpoint, the resume ---------------------


def _tight_budget(remaining_s: float):
    """A read budget with `remaining_s` of its time left."""
    import time

    def make(db, project_id):
        import dataclasses

        limits = dataclasses.replace(Limits.from_settings(), max_input_tokens_per_task=60_000, max_output_tokens_per_task=12_000,
                                     max_calls_per_document=60, max_calls_per_project_per_day=600, max_elapsed_s_per_job=1000.0)
        return JobBudget(limits=limits, calls_today_before=0, started=time.monotonic() - (1000.0 - remaining_s))

    return make


def test_the_time_budget_stops_the_read_between_calls_and_the_state_is_kept(client, db_session, tmp_path, ai, recording, monkeypatch):
    """95 s left: enough for the two page readings (90 s in hand each), not
    for a close-up (200 s in hand). The close-ups are not started; the rows
    that needed one keep their first reading, pending; the run is timed out."""
    monkeypatch.setattr(settings, "ai_read_band_reserve_s", 50.0)
    monkeypatch.setattr(settings, "ai_read_close_up_reserve_s", 200.0)
    monkeypatch.setattr(sheet_reader, "_budget", _tight_budget(95.0))
    sheet = _sheet(tmp_path, "EP-71006 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _page_with({"SIGA-CT1": "41"})]
    project = _project(db_session, sheet, ep="71006")
    _login(client)

    body = _ensure(client, project)

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120"}
    assert recording.calls == 2, "no close-up was started with the time gone"
    run = _run(db_session, project)
    assert run.state == "timed_out" and run.budget_exhausted == "elapsed_time"
    issues = _issues(db_session, run)
    assert issues["SIGA-CT1"].detail["reason_code"] == ReviewReason.TIME_BUDGET_EXHAUSTED.value
    assert issues["SIGA-CT1"].detail["primary"]["quantity"] == "14" and issues["SIGA-CT1"].detail["pending"] is True
    reading = _reading(db_session, run)
    assert reading.status == "completed", "both page readings were made"
    assert set(reading.reading["settled"]) == {"p1r1", "p1r2"}
    job = db_session.query(BackgroundJob).filter(BackgroundJob.project_id == project.id, BackgroundJob.kind == "boq_read").one()
    assert job.result["state"] == "timed_out"


def test_a_resumed_read_asks_only_about_the_pending_rows_and_then_nothing(client, db_session, tmp_path, ai, recording, monkeypatch):
    monkeypatch.setattr(settings, "ai_read_band_reserve_s", 50.0)
    monkeypatch.setattr(settings, "ai_read_close_up_reserve_s", 200.0)
    monkeypatch.setattr(sheet_reader, "_budget", _tight_budget(95.0))
    sheet = _sheet(tmp_path, "EP-71007 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _page_with({"SIGA-CT1": "41"})]
    project = _project(db_session, sheet, ep="71007")
    _login(client)
    _ensure(client, project)
    assert recording.calls == 2 and _run(db_session, project).state == "timed_out"

    # Time again: the resume makes the two close-ups and nothing else.
    monkeypatch.setattr(settings, "ai_read_close_up_reserve_s", 20.0)
    monkeypatch.setattr(sheet_reader, "_budget", _tight_budget(900.0))
    recording.answers = [_close_up("14", "SIGA-CT1", "Single input module"), _close_up("30", "SIGA-HFS", "Heat detector")]
    result = sheet_reader.read_design_sheet(db_session, project, project.design_sheets[0])

    assert recording.calls == 4, "no page was read again"
    assert [r.task for r in recording.requests[2:]] == ["read_sheet_row_close_up", "read_sheet_row_close_up"]
    assert result.state == "completed" and {l.catalog_no: l.quantity for l in result.lines} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}
    reading = db_session.query(DocumentReading).one()
    assert set(reading.reading["settled"]) == {"p1r1", "p1r2", "p1r3", "p1r4"}

    # Read once more: every row is taken from the checkpoint, no call is made.
    again = sheet_reader.read_design_sheet(db_session, project, project.design_sheets[0])
    assert recording.calls == 4
    assert again.state == "completed" and len(again.lines) == 3 and len(again.issues) == 1


def test_a_reading_cut_short_mid_page_is_resumed_from_the_page_it_stopped_at(client, db_session, tmp_path, ai, recording, monkeypatch):
    """The second reading of the page fails (the provider): the reading is
    stored partial, the page's rows keep their first reading for review,
    and the next read makes only the second reading."""
    sheet = _sheet(tmp_path, "EP-71008 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _Failure("transport")]
    project = _project(db_session, sheet, ep="71008")
    _login(client)

    body = _ensure(client, project)

    assert body["items"] == [], "nothing is a line on one reading"
    run = _run(db_session, project)
    reading = _reading(db_session, run)
    assert reading.status == "partial" and sheet_reader.pending_stages(reading.reading, reading.pages) == [(1, "second")]
    assert run.state == "partial"
    issues = _issues(db_session, run)
    assert set(issues) == {"4-CPU", "SIGA-PS", "SIGA-CT1", "SIGA-HFS"}
    assert issues["SIGA-PS"].detail["reason_code"] == ReviewReason.AI_PROVIDER_ERROR.value
    assert issues["SIGA-PS"].detail["primary"]["quantity"] == "120" and issues["SIGA-PS"].detail["pending"] is True

    recording.answers = [SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    result = sheet_reader.read_design_sheet(db_session, project, project.design_sheets[0])
    assert [r.task for r in recording.requests[2:]] == ["read_sheet_page_second", "read_sheet_row_close_up"]
    assert result.state == "completed" and len(result.lines) == 3
    assert db_session.query(DocumentReading).one().status == "completed"


# --- TEST 15: the BOQ and its completion are one transaction -------------------------------------


def test_a_failure_while_saving_leaves_the_project_unextracted(client, db_session, tmp_path, ai, recording, monkeypatch):
    sheet = _sheet(tmp_path, "EP-71009 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="71009")
    _login(client)

    def broken(**kwargs):
        raise RuntimeError("the line could not be built")

    monkeypatch.setattr(boq_provenance, "extracted_item", broken)
    body = _ensure(client, project)

    assert body["extracted"] is False and body["items"] == []
    assert body["reading"]["status"] == "failed" and "could not be built" in body["reading"]["error"]
    db_session.expire_all()
    fresh = db_session.get(Project, project.id)
    assert fresh.boq_extracted_at is None and fresh.boq_items == []
    assert db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).count() == 0
    assert db_session.query(DocumentReading).count() == 1, "the reading itself is a checkpoint and is kept"


# --- TEST 16: a temporary folder that will not delete does not fail the call ----------------------


def test_a_temporary_folder_that_cannot_be_removed_does_not_fail_the_call(monkeypatch, tmp_path):
    provider = ClaudeCodeProvider.__new__(ClaudeCodeProvider)
    provider._cli = "claude-fake"
    provider._models = {"small": "sonnet", "standard": "opus"}
    provider._timeout = 5.0
    provider._semaphore = threading.BoundedSemaphore(1)
    reply = json.dumps({"subtype": "success", "structured_output": {"ok": 1}, "usage": {"input_tokens": 1, "output_tokens": 1}})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=reply, stderr=""))
    monkeypatch.setattr(provider_module.time, "sleep", lambda s: None)
    import shutil

    real_rmtree = shutil.rmtree
    # Five refusals: the call's own removal gives up (deferred); the next
    # call's sweep then succeeds.
    refusals = {"left": 5}

    def in_use(path, *args, **kwargs):
        if refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", in_use)
    provider_module._deferred_folders.clear()
    request = provider_module.AiRequest(task="t", system="s", parts=[provider_module.ImagePart("img", b"png")],
                                        schema={"type": "object"}, max_output_tokens=10)

    first = provider.complete(request)

    assert first.ok and first.data == {"ok": 1}, first
    assert len(provider_module._deferred_folders) == 1, "deferred, not raised"
    second = provider.complete(request)
    assert second.ok and provider_module._deferred_folders == [], "a later call removed it"


def test_a_cli_timeout_is_a_timeout_not_an_answer(monkeypatch):
    provider = ClaudeCodeProvider.__new__(ClaudeCodeProvider)
    provider._cli = "claude-fake"
    provider._models = {"small": "sonnet", "standard": "opus"}
    provider._timeout = 5.0
    provider._semaphore = threading.BoundedSemaphore(1)

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(subprocess, "run", slow)
    response = provider.complete(provider_module.AiRequest(task="t", system="s", parts=[], schema={}, max_output_tokens=10))
    assert response.error == "timeout" and response.retryable
    assert sheet_reader.error_reason("timeout: Claude Code did not answer within 5 s") == ReviewReason.AI_TIMEOUT
    assert sheet_reader.error_reason("budget: elapsed_time") == ReviewReason.TIME_BUDGET_EXHAUSTED
    assert sheet_reader.error_reason("budget: calls_per_document") == ReviewReason.AI_BUDGET_EXHAUSTED
    assert sheet_reader.error_reason("transport: gone") == ReviewReason.AI_PROVIDER_ERROR
