"""The model reads the Design Sheets on a project's first open, the OCR read
is the witness, and the reading is stored so no document is read twice."""

from __future__ import annotations

import pytest
from PIL import Image

import app.routers.jobs as jobs_router
from app.ai import provider as provider_module
from app.ai import sheet_reader
from app.ai.provider import RecordingProvider
from app.core.config import get_settings
from app.models import DocumentReading, ExtractionIssue, ExtractionRun, Project, ProjectDesignSheet, RoleEnum, User
from app.services.design_sheet_extractor import DesignSheetExtraction, ExtractedBoqLine

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


def _ocr_line(catalog, description, quantity, y):
    return ExtractedBoqLine(catalog_no=catalog, description=description, quantity=quantity, group_heading=None,
                            confidence=80.0, page=1, y_px=y, table_span=(50, 950), row_bounds=(y - 9, y + 11),
                            quantity_span=(50, 150))


def _witness(lines, failure=None):
    def read(path, on_page=None):
        result = DesignSheetExtraction(lines=list(lines), failure=failure)
        return result
    return read


# The OCR read of the same page: agrees on 4-CPU and SIGA-CT1, reads SIGA-PS
# as 126 where the model read 120, misses SIGA-HFS, and finds SIGA-CC1 alone.
OCR_LINES = [
    _ocr_line("4-CPU", "Central Processor Module", "1", 69),
    _ocr_line("SIGA-PS", "Photoelectric smoke detector", "126", 93),
    _ocr_line("SIGA-CT1", "Single input module", "14", 117),
    _ocr_line("SIGA-CC1", "Synchronised output module", "6", 400),
]


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
    user = db.query(User).filter(User.role == RoleEnum.design_engineer).first() or make_user(db, "e@x.com", RoleEnum.design_engineer)
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


def test_the_model_reads_the_sheet_and_the_ocr_read_witnesses_it(client, db_session, tmp_path, ai, recording, monkeypatch):
    sheet = tmp_path / "EP-70001 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 one sheet")
    monkeypatch.setattr(sheet_reader, "ocr_read", _witness(OCR_LINES))
    # The page, then the close-ups: the disputed SIGA-PS, the unreadable
    # SIGA-HFS, and the row only the OCR found.
    recording.answers = [PAGE_ANSWER,
                         _close_up("120", "SIGA-PS", "Photoelectric smoke detector"),
                         _close_up("30", "SIGA-HFS", "Heat detector"),
                         _close_up("6", "SIGA-CC1", "Synchronised output module")]
    project = _project(db_session, sheet)
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] and body["reading"] is None
    by_catalog = {item["catalog_no"]: item for item in body["items"]}
    assert set(by_catalog) == {"4-CPU", "SIGA-PS", "SIGA-CT1", "SIGA-CC1"}
    assert by_catalog["SIGA-PS"]["quantity"] == "120", "the close-up sided with the model over the OCR read"
    assert by_catalog["4-CPU"]["group_heading"] == "B1 BUILDING"
    assert recording.calls == 4
    assert recording.requests[0].task == "read_sheet_page"
    assert [r.task for r in recording.requests[1:]] == ["read_sheet_row_close_up"] * 3

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
    monkeypatch.setattr(sheet_reader, "ocr_read", _witness(OCR_LINES))
    recording.answers = [PAGE_ANSWER,
                         _close_up("120", "SIGA-PS", "Photoelectric smoke detector"),
                         _close_up("30", "SIGA-HFS", "Heat detector"),
                         _close_up("6", "SIGA-CC1", "Synchronised output module")]
    first = _project(db_session, sheet, ep="70002")
    _login(client)
    assert len(client.post(f"/projects/{first.id}/boq/ensure").json()["items"]) == 4
    assert recording.calls == 4

    # Another project filed with the same sheet: the same content, the same
    # reading, read out of the database in the request itself.
    second = _project(db_session, sheet, ep="70003")
    body = client.post(f"/projects/{second.id}/boq/ensure").json()

    assert body["extracted"] and body["reading"] is None and len(body["items"]) == 4
    assert recording.calls == 4, "no model call for a document already read"
    assert db_session.query(DocumentReading).count() == 1
    runs = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == second.id).all()
    assert runs[0].reader == "ai" and runs[0].reading_id == db_session.query(DocumentReading).one().id


def test_a_sheet_the_ocr_cannot_read_is_read_by_two_ai_readings(client, db_session, tmp_path, ai, recording, monkeypatch):
    """The ELS case: no rule layout the extractor knows, so the OCR read has
    no lines to witness with. Every readable row gets a second, independent
    AI reading; the two agreeing make the line."""
    sheet = tmp_path / "EP-70004 ELS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 unruled sheet")
    monkeypatch.setattr(sheet_reader, "ocr_read", _witness([], failure="Could not find a line-item table"))
    second = {"rows": [
        {"label": "R1", "quantity": "1", "catalog_no": "4-CPU", "description": "Central Processor Module", "readable": True},
        {"label": "R2", "quantity": "120", "catalog_no": "SIGA-PS", "description": "Photoelectric smoke detector", "readable": True},
        {"label": "R3", "quantity": "41", "catalog_no": "SIGA-CT1", "description": "Single input module", "readable": True},
    ]}
    recording.answers = [PAGE_ANSWER, second,
                         _close_up("14", "SIGA-CT1", "Single input module"),   # the disputed row: sides with the page
                         _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="70004")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["warnings"] == [], "the sheet the OCR could not read is read"
    assert {i["catalog_no"]: i["quantity"] for i in body["items"]} == {"4-CPU": "1", "SIGA-PS": "120", "SIGA-CT1": "14"}
    assert [r.task for r in recording.requests] == ["read_sheet_page", "read_sheet_rows_second",
                                                    "read_sheet_row_close_up", "read_sheet_row_close_up"]
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ai" and run.failure is None
    assert any("could not witness" in n for n in run.coverage["notes"])


def test_without_the_model_the_ocr_read_stands_as_before(client, db_session, tmp_path, monkeypatch):
    sheet = tmp_path / "EP-70005 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    monkeypatch.setattr(sheet_reader, "ocr_read", _witness(OCR_LINES))
    project = _project(db_session, sheet, ep="70005")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] and len(body["items"]) == 4 and body["reading"] is None
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    assert run.reader == "ocr" and run.reading_id is None
    assert db_session.query(DocumentReading).count() == 0


def test_the_first_read_runs_as_a_job_the_page_follows(client, db_session, tmp_path, ai, recording, monkeypatch):
    """Off the test harness the read takes minutes: the open starts a job
    and answers with it; the BOQ is read out of the stored reading after."""
    monkeypatch.setattr(jobs_router, "RUN_INLINE", False)
    sheet = tmp_path / "EP-70006 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    monkeypatch.setattr(sheet_reader, "ocr_read", _witness(OCR_LINES))
    recording.answers = [PAGE_ANSWER,
                         _close_up("120", "SIGA-PS", "Photoelectric smoke detector"),
                         _close_up("30", "SIGA-HFS", "Heat detector"),
                         _close_up("6", "SIGA-CC1", "Synchronised output module")]
    project = _project(db_session, sheet, ep="70006")
    _login(client)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert body["extracted"] is False and body["items"] == []
    assert body["reading"] and body["reading"]["kind"] == "boq_read"
    # Asked again while it runs: the same job, not a second one.
    again = client.post(f"/projects/{project.id}/boq/ensure").json()
    assert again["reading"] is None or again["reading"]["id"] == body["reading"]["id"]
    import time

    for _ in range(100):
        job = client.get(f"/jobs/{body['reading']['id']}").json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    assert job["result"]["lines"] == 4
    done = client.post(f"/projects/{project.id}/boq/ensure").json()
    assert done["reading"] is None and len(done["items"]) == 4
