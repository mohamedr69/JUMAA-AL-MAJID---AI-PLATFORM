"""The model reads the Design Sheets on a project's first open, a second
reading settles each row, and the reading is stored so no document is read
twice. Nothing else reads a sheet: where the model cannot, the sheet is
recorded as not read, with the reason."""

from __future__ import annotations

import pytest
from PIL import Image

import app.routers.jobs as jobs_router
from app.ai import provider as provider_module
from app.ai import sheet_reader
from app.ai.provider import RecordingProvider
from app.core.config import get_settings
from app.models import DocumentReading, ExtractionIssue, ExtractionRun, Project, ProjectDesignSheet, RoleEnum, User

from .conftest import login, make_user

settings = get_settings()

# One page, 1000 x 600: under the sent width, so one band and thousandths
# map straight onto pixels (x / 1000 * 1000, y / 1000 * 600).
PAGE = Image.new("L", (1000, 600), 255)


def _row(kind, qty, catalog, description, y, readable=True):
    # Each row's box is a different width: on this blank test page a crop of
    # one size is the same image as another of that size, and the same image
    # is (rightly) answered from the cache rather than asked again.
    return {"kind": kind, "quantity": qty, "catalog_no": catalog, "description": description, "readable": readable,
            "box": [50, y, 700 + y, y + 30]}


PAGE_ANSWER = {
    "rows": [
        _row("section", "", "", "B1 BUILDING", 50),
        _row("item", "1", "4-CPU", "Central Processor Module", 100),
        _row("item", "120", "SIGA-PS", "Photoelectric smoke detector", 140),
        _row("item", "14", "SIGA-CT1", "Single input module", 180),
        _row("item", "", "SIGA-HFS", "Heat detector", 220, readable=False),
    ],
    "has_line_items": True,
}


def _close_up(qty, catalog, description):
    return {"rows": [{"label": "R1", "quantity": qty, "catalog_no": catalog, "description": description, "readable": True}]}


def _page_with(quantities: dict) -> dict:
    """PAGE_ANSWER with some rows' quantities changed: a second reading of
    the page that disputes those rows."""
    rows = [dict(row, quantity=quantities.get(row["catalog_no"], row["quantity"])) for row in PAGE_ANSWER["rows"]]
    return {"rows": rows, "has_line_items": True}


# The second reading of the page, agreeing with the first on every row.
SECOND = _page_with({})


@pytest.fixture()
def ai(monkeypatch):
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    monkeypatch.setattr(sheet_reader, "page_images", lambda path: iter([(1, 1, PAGE)]))
    monkeypatch.setattr(sheet_reader, "render_page", lambda path, number: PAGE)
    yield
    monkeypatch.setattr(settings, "ai_enabled", False)


@pytest.fixture()
def recording():
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    yield provider
    provider_module.set_provider(None)


def _project(db, sheet_path, ep="70001"):
    user = db.query(User).filter(User.role == RoleEnum.fire_alarm_design_engineer).first() or make_user(db, "e@x.com", RoleEnum.fire_alarm_design_engineer)
    project = Project(ep_number=ep, project_name="Test", created_by_id=user.id)
    project.design_sheets = [ProjectDesignSheet(system_code="FAS", document_path=str(sheet_path))]
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _login(client):
    login(client, settings.default_admin_email, settings.default_admin_password)


def test_bands_partition_a_page_and_overlap():
    page_bands = sheet_reader.bands(2240)
    assert len(page_bands) == 3
    assert page_bands[0][2] == 0 and page_bands[-1][3] == 2240
    for (_t, bottom, _ot, own_bottom), (top, _b, own_top, _ob) in zip(page_bands, page_bands[1:]):
        assert bottom - top >= sheet_reader.BAND_OVERLAP, "a row cut by one band is whole in the next"
        assert own_bottom == own_top, "every row centre belongs to exactly one band"
    assert sheet_reader.bands(600) == [(0, 600, 0, 600)]


def test_the_model_reads_the_sheet_and_a_second_reading_settles_it(client, db_session, tmp_path, ai, recording):
    sheet = tmp_path / "EP-70001 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 one sheet")
    # The page read twice, then a close-up of the row neither reading could read.
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet)
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] and body["reading"] is None
    by_catalog = {item["catalog_no"]: item for item in body["items"]}
    assert set(by_catalog) == {"4-CPU", "SIGA-PS", "SIGA-CT1"}
    assert by_catalog["SIGA-PS"]["quantity"] == "120", "two readings agree"
    assert by_catalog["4-CPU"]["group_heading"] == "B1 BUILDING"
    assert recording.calls == 3
    assert [r.task for r in recording.requests] == ["read_sheet_page", "read_sheet_page_second", "read_sheet_row_close_up"]

    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ai" and run.reading_id is not None and run.failure is None
    reading = db_session.get(DocumentReading, run.reading_id)
    assert reading.status == "completed" and reading.pages == 1 and reading.kind == "design_sheet"
    assert [r["catalog_no"] for r in reading.reading["pages"][0]["rows"] if r["kind"] == "item"] == \
        ["4-CPU", "SIGA-PS", "SIGA-CT1", "SIGA-HFS"]
    # The unreadable row is a row to review, with the close-up's reading beside it.
    issues = db_session.query(ExtractionIssue).filter(ExtractionIssue.run_id == run.id).all()
    assert len(issues) == 1 and issues[0].detail["catalog_no"] == "SIGA-HFS"
    assert issues[0].detail["ai_reading"]["quantity"] == "30" and issues[0].region is not None

    state = client.get(f"/projects/{project.id}/extraction").json()
    assert state["runs"][0]["reader"] == "ai"
    assert [r["status"] for r in state["readings"]] == ["completed"]


def test_a_sheet_read_before_is_not_read_by_the_model_again(client, db_session, tmp_path, ai, recording, monkeypatch):
    sheet = tmp_path / "EP-70002 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 shared sheet")
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    first = _project(db_session, sheet, ep="70002")
    _login(client)
    assert len(client.post(f"/projects/{first.id}/boq/ensure").json()["items"]) == 3
    assert recording.calls == 3

    # Another project filed with the same sheet: the same content, the same
    # reading, read out of the database in the request itself.
    second = _project(db_session, sheet, ep="70003")
    body = client.post(f"/projects/{second.id}/boq/ensure").json()

    assert body["extracted"] and body["reading"] is None and len(body["items"]) == 3
    assert recording.calls == 3, "no model call for a document already read"
    assert db_session.query(DocumentReading).count() == 1
    runs = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == second.id).all()
    assert runs[0].reader == "ai" and runs[0].reading_id == db_session.query(DocumentReading).one().id


def test_a_row_the_two_readings_dispute_goes_to_a_close_up(client, db_session, tmp_path, ai, recording):
    """Every readable row gets a second, independent AI reading; the two
    agreeing make the line, and a close-up settles the rows they dispute."""
    sheet = tmp_path / "EP-70004 ELS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 unruled sheet")
    second = _page_with({"SIGA-CT1": "41"})
    recording.answers = [PAGE_ANSWER, second,
                         _close_up("14", "SIGA-CT1", "Single input module"),   # the disputed row: sides with the page
                         _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="70004")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["warnings"] == []
    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}
    assert [r.task for r in recording.requests] == ["read_sheet_page", "read_sheet_page_second",
                                                    "read_sheet_row_close_up", "read_sheet_row_close_up"]
    # The close-up names the row it is after, since the model's boxes drift.
    close_up = recording.requests[2]
    assert any("SIGA-CT1" in getattr(part, "text", "") for part in close_up.parts)
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ai" and run.failure is None


def test_without_the_model_the_sheet_is_recorded_as_not_read(client, db_session, tmp_path):
    """No OCR stands in (platform owner, 2026-09-17): the sheet is not read,
    the reason is on the BOQ page, and readiness says the sheet is unread."""
    sheet = tmp_path / "EP-70005 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    project = _project(db_session, sheet, ep="70005")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] and body["items"] == [] and body["reading"] is None
    assert body["warnings"] and "Not read: AI assistance is disabled" in body["warnings"][0], body["warnings"]
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ai" and run.reading_id is None and run.failure.startswith("Not read")
    assert db_session.query(DocumentReading).count() == 0
    checks = {c["key"]: c for c in client.get(f"/projects/{project.id}/readiness").json()["checks"]}
    assert checks["coverage"]["status"] == "blocked" and checks["unresolved_rows"]["count"] == 0
    assert client.get(f"/projects/{project.id}/extraction").json()["open_issues"] == 0


def test_a_read_the_model_fails_is_recorded_as_not_read(client, db_session, tmp_path, ai, recording):
    sheet = tmp_path / "EP-70007 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    recording.answers = [{"rows": [], "has_line_items": False}, {"rows": [], "has_line_items": False}]
    project = _project(db_session, sheet, ep="70007")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["items"] == []
    assert body["warnings"] and "The AI found no table of quoted items" in body["warnings"][0], body["warnings"]
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ai" and run.reading_id is not None


def test_the_first_read_runs_as_a_job_the_page_follows(client, db_session, tmp_path, ai, recording, monkeypatch):
    """Off the test harness the read takes minutes: the open starts a job
    and answers with it; the BOQ is read out of the stored reading after."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    sheet = tmp_path / "EP-70006 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="70006")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] is False and body["items"] == []
    assert body["reading"] and body["reading"]["kind"] == "boq_read"
    # Asked again while it runs: the same job, not a second one.
    again = client.post(f"/projects/{project.id}/boq/ensure").json()
    assert again["reading"] is None or again["reading"]["id"] == body["reading"]["id"]
    import time

    # A wall-clock deadline, not a poll count: under the load of the full
    # suite the job's page rendering can take longer than the five seconds
    # a hundred polls allowed, and the test then failed for timing alone.
    deadline = time.monotonic() + 30
    while True:
        job = client.get(f"/jobs/{body['reading']['id']}").json()
        if job["status"] not in ("queued", "running") or time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    assert job["result"]["lines"] == 3
    done = client.post(f"/projects/{project.id}/boq/ensure").json()
    assert done["reading"] is None and len(done["items"]) == 3
