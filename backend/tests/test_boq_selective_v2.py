"""BOQ Extraction V2, phase 3: no second full-page read by default; a row
the page geometry witnesses is accepted on the first reading; the rest are
verified as row strips in small batches by the fast model, escalated to
the standard model's close-up only on disagreement; a row verified before
is not asked about again; a call that fails leaves the row pending."""

from __future__ import annotations

import pytest

from app.ai import sheet_reader
from app.core.config import get_settings
from app.extraction.issues import ReviewReason
from app.models import DocumentReading, ExtractionIssue, ExtractionRun

from .test_ai_sheet_reader import PAGE_ANSWER, _close_up, _login, _project, _row, recording  # noqa: F401
from .test_boq_extraction_v2 import _Failure, _issues, _run, _sheet

settings = get_settings()


@pytest.fixture()
def selective(monkeypatch):
    """The reader as configured since 2026-09-27: one page reading, the
    geometry, then targeted verification."""
    import app.routers.jobs as jobs_router
    from PIL import Image

    page = Image.new("L", (1000, 600), 255)
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_read_full_second_pass", False)
    monkeypatch.setattr(settings, "ai_verify_rows_per_call", 8)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    monkeypatch.setattr(sheet_reader, "page_images", lambda path: iter([(1, 1, page)]))
    monkeypatch.setattr(sheet_reader, "render_page", lambda path, number: page)
    yield
    monkeypatch.setattr(settings, "ai_enabled", False)


def _evidence(levels: dict[str, str]):
    """A page-evidence seam rating rows by catalog number; "high" for the rest."""
    def fake(rows, image):
        return {i: {"score": 0.95 if levels.get(r["catalog_no"], "high") == "high" else 0.3,
                    "level": levels.get(r["catalog_no"], "high"), "components": {}, "inline": ""}
                for i, r in enumerate(rows) if r["kind"] == "item"}
    return fake


def _verified(*rows) -> dict:
    return {"rows": [{"row_id": row_id, "quantity": qty, "part_number": part, "readable": True} for row_id, qty, part in rows]}


# --- TEST 17: a high-confidence row makes no second AI call -----------------------------------------


def test_rows_the_geometry_witnesses_are_accepted_on_one_reading(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({}))
    sheet = _sheet(tmp_path, "EP-73001 FAS Design.pdf")
    # One page reading; the unreadable SIGA-HFS row alone gets a close-up.
    recording.answers = [PAGE_ANSWER, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="73001")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}
    assert [r.task for r in recording.requests] == ["read_sheet_page", "read_sheet_row_close_up"]
    line = next(i for i in body["items"] if i["catalog_no"] == "SIGA-PS")
    assert float(line["ocr_confidence"]) == 95.0 and line["raw_values"]["evidence"]["level"] == "high"
    run = _run(db_session, project)
    assert run.state == "completed"
    reading = db_session.get(DocumentReading, run.reading_id)
    assert reading.status == "completed" and reading.reading.get("second") == [], "no second full reading was made"
    assert reading.reading["settled"]["p1r2"]["by"] == "geometry"


# --- TEST 18: a low-confidence row gets targeted verification only ----------------------------------


def test_only_the_uncertain_rows_are_verified_as_row_strips(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({"SIGA-PS": "low", "SIGA-CT1": "medium"}))
    sheet = _sheet(tmp_path, "EP-73002 FAS Design.pdf")
    # The page; the unreadable SIGA-HFS row's close-up (made as the rows are
    # walked); then the one strip call for the two uncertain rows.
    recording.answers = [PAGE_ANSWER, _close_up("30", "SIGA-HFS", "Heat detector"),
                         _verified(("p1r3", "14", "SIGA-CT1"), ("p1r2", "120", "SIGA-PS"))]
    project = _project(db_session, sheet, ep="73002")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}
    assert [r.task for r in recording.requests] == ["read_sheet_page", "read_sheet_row_close_up", "verify_boq_row_crops"]
    strips = recording.requests[2]
    task = next(p.text for p in strips.parts if hasattr(p, "text"))
    assert "p1r2" in task and "p1r3" in task and "p1r1" not in task, "the witnessed row was not sent"
    assert strips.tier == "small"
    reading = db_session.get(DocumentReading, _run(db_session, project).reading_id)
    assert reading.reading["settled"]["p1r2"]["by"] == "tier1" and reading.reading["settled"]["p1r1"]["by"] == "geometry"


def test_batches_are_bounded_and_answers_are_matched_by_row_id_not_order(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(settings, "ai_verify_rows_per_call", 4)
    rows = [_row("item", str(n), f"P-{n}", f"Part number {n}", 60 + 40 * n) for n in range(1, 11)]
    page = {"rows": rows, "has_line_items": True}
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({f"P-{n}": "low" for n in range(1, 11)}))
    sheet = _sheet(tmp_path, "EP-73003 FAS Design.pdf")
    # Three calls of 4, 4 and 2; each answered out of order.
    recording.answers = [
        page,
        _verified(("p1r4", "4", "P-4"), ("p1r1", "1", "P-1"), ("p1r3", "3", "P-3"), ("p1r2", "2", "P-2")),
        _verified(("p1r8", "8", "P-8"), ("p1r5", "5", "P-5"), ("p1r7", "7", "P-7"), ("p1r6", "6", "P-6")),
        _verified(("p1r10", "10", "P-10"), ("p1r9", "9", "P-9")),
    ]
    project = _project(db_session, sheet, ep="73003")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {f"P-{n}": str(n) for n in range(1, 11)}
    assert [r.task for r in recording.requests] == ["read_sheet_page"] + ["verify_boq_row_crops"] * 3


def test_a_disputed_row_escalates_to_a_close_up_and_a_conflict_is_reviewed(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({"SIGA-PS": "low", "SIGA-CT1": "low"}))
    sheet = _sheet(tmp_path, "EP-73004 FAS Design.pdf")
    recording.answers = [
        PAGE_ANSWER,
        _close_up("30", "SIGA-HFS", "Heat detector"),
        # Tier 1 disputes both rows.
        _verified(("p1r2", "126", "SIGA-PS"), ("p1r3", "41", "SIGA-CT1")),
        # Tier 2: SIGA-PS's close-up sides with the first reading; SIGA-CT1's sides with neither.
        _close_up("120", "SIGA-PS", "Photoelectric smoke detector"),
        _close_up("47", "SIGA-CT1", "Single input module"),
    ]
    project = _project(db_session, sheet, ep="73004")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120"}
    assert [r.tier for r in recording.requests] == ["small", "standard", "small", "standard", "standard"]
    issues = _issues(db_session, _run(db_session, project))
    conflict = issues["SIGA-CT1"].detail
    assert conflict["reason_code"] == ReviewReason.QUANTITY_CONFLICT.value and conflict["pending"] is False
    assert conflict["primary"]["quantity"] == "14"
    assert conflict["verification"]["tier1"]["quantity"] == "41" and conflict["verification"]["close_up"]["quantity"] == "47"


def test_a_row_verified_before_is_taken_from_the_cache(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({"SIGA-PS": "low"}))
    sheet = _sheet(tmp_path, "EP-73005 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _close_up("30", "SIGA-HFS", "Heat detector"), _verified(("p1r2", "120", "SIGA-PS"))]
    project = _project(db_session, sheet, ep="73005")
    _login(client)
    client.post(f"/projects/{project.id}/boq/ensure").json()
    assert recording.calls == 3

    # The row checkpoint forgotten: the verification itself is remembered.
    reading = db_session.query(DocumentReading).one()
    reading.reading = {**reading.reading, "settled": {}}
    db_session.commit()
    result = sheet_reader.read_design_sheet(db_session, project, project.design_sheets[0])
    assert recording.calls == 3, "no new call: the strip's reading and the close-up were cached"
    assert {l.catalog_no: l.quantity for l in result.lines} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}


def test_a_strip_call_that_fails_leaves_its_rows_pending(client, db_session, tmp_path, selective, recording, monkeypatch):
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({"SIGA-PS": "low", "SIGA-CT1": "low"}))
    sheet = _sheet(tmp_path, "EP-73006 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER, _close_up("30", "SIGA-HFS", "Heat detector"), _Failure("timeout")]
    project = _project(db_session, sheet, ep="73006")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert {i["catalog_no"] for i in body["items"]} == {"4-CPU"}
    run = _run(db_session, project)
    issues = _issues(db_session, run)
    for catalog in ("SIGA-PS", "SIGA-CT1"):
        assert issues[catalog].detail["reason_code"] == ReviewReason.AI_TIMEOUT.value and issues[catalog].detail["pending"] is True
    assert issues["SIGA-PS"].detail["primary"]["quantity"] == "120"
    assert run.state == "partial"
    # Resumed: only the strip call is made again.
    recording.answers = [_verified(("p1r2", "120", "SIGA-PS"), ("p1r3", "14", "SIGA-CT1"))]
    result = sheet_reader.read_design_sheet(db_session, project, project.design_sheets[0])
    assert recording.requests[-1].task == "verify_boq_row_crops" and result.state == "completed" and len(result.lines) == 3


def test_no_batch_is_started_without_time_for_it(client, db_session, tmp_path, selective, recording, monkeypatch):
    from .test_boq_extraction_v2 import _tight_budget

    monkeypatch.setattr(settings, "ai_read_band_reserve_s", 50.0)
    monkeypatch.setattr(settings, "ai_read_close_up_reserve_s", 100.0)   # a batch keeps 200 s in hand
    monkeypatch.setattr(sheet_reader, "_budget", _tight_budget(95.0))
    monkeypatch.setattr(sheet_reader, "_page_evidence", _evidence({"SIGA-PS": "low"}))
    sheet = _sheet(tmp_path, "EP-73007 FAS Design.pdf")
    recording.answers = [PAGE_ANSWER]
    project = _project(db_session, sheet, ep="73007")
    _login(client)

    client.post(f"/projects/{project.id}/boq/ensure").json()

    assert recording.calls == 1
    run = _run(db_session, project)
    issues = _issues(db_session, run)
    assert issues["SIGA-PS"].detail["reason_code"] == ReviewReason.TIME_BUDGET_EXHAUSTED.value
    assert run.state == "timed_out" and run.budget_exhausted == "elapsed_time"
