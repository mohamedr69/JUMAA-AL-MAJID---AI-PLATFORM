"""The intake gate and readiness: a document that is not the project's,
not whole or not readable blocks issuing the BOQ."""

from pathlib import Path

import pymupdf
import pytest

from app.core.config import get_settings
from app.models import ExtractionIssue, ExtractionRun, Project, RoleEnum
from app.services import document_intake

from .conftest import login, make_user
from .test_projects import _login_admin

settings = get_settings()


def _pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 80), "DESIGN SHEET", fontsize=12)
        page.insert_text((250, 820), text, fontsize=9)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize(("text", "expected"), [
    ("EP-30088 Design Sheet ELS.pdf", {"30088"}),
    ("EP30784 EML Design.pdf", {"30784"}),
    ("ep_31112 DRF", {"31112"}),
    ("REP-30000 report", set()),
    ("Design Sheet.pdf", set()),
])
def test_ep_numbers_in_names(text, expected):
    assert document_intake.ep_numbers(text) == expected


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    return root


def _create(client, archive, *, ep, folder, drf, sheets):
    """Created with no archive configured, the way the legacy rows were made
    before creation checked paths -- then the archive is switched on."""
    _login_admin(client)
    body = {"ep_number": ep, "project_name": "Tower", "source_folder_path": str(folder), "drf_document_path": str(drf) if drf else None,
            "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}],
            "design_sheets": [{"system_code": code, "document_path": str(path)} for code, path in sheets]}
    resp = client.post("/projects", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _findings(rows, filename):
    row = next(r for r in rows if r["filename"] == filename)
    return {f["code"]: f["severity"] for f in row["findings"]}, row


def test_the_gate_catches_other_projects_documents_and_missing_pages(client, db_session, archive, monkeypatch):
    folder = archive / "AG Engineering" / "EP-40001 Tower"
    drf = _pdf(archive / "Al Ghurair MEP" / "EP-40001 Tower" / "Scan" / "EP-40001 DRF.pdf", ["Page 1 of 1"])  # EP-31112's case
    misnamed = _pdf(folder / "Commercial" / "EP-40088 Design Sheet ELS.pdf", ["Page 1 of 1"])              # EP-30058's case
    partial = _pdf(folder / "Commercial" / "EP-40001 FAS Design.pdf", ["Page 1 of 2"])                     # page 2 missing
    pid = _create(client, archive, ep="40001", folder=folder, drf=drf, sheets=[("ELS", misnamed), ("FAS", partial)])
    monkeypatch.setattr(settings, "projects_root", str(archive))

    rows = client.post(f"/projects/{pid}/documents/intake").json()

    drf_codes, drf_row = _findings(rows, "EP-40001 DRF.pdf")
    assert drf_codes == {"OUTSIDE_SOURCE_FOLDER": "blocked"} and drf_row["intake_status"] == "blocked"
    assert "AG Engineering" in drf_row["findings"][0]["message"] and "Al Ghurair MEP" in drf_row["findings"][0]["message"]
    misnamed_codes, _ = _findings(rows, "EP-40088 Design Sheet ELS.pdf")
    assert misnamed_codes == {"EP_MISMATCH_FILENAME": "blocked"}
    partial_codes, partial_row = _findings(rows, "EP-40001 FAS Design.pdf")
    assert partial_codes == {"INCOMPLETE_SOURCE": "blocked"}
    assert partial_row["page_count"] == 1 and partial_row["printed_pages"]["declared_totals"] == [2]
    assert len(partial_row["sha256"]) == 64 and partial_row["relative_path"].startswith("Commercial")

    # Readiness names all three, and the BOQ cannot be issued.
    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "description": "CPU", "quantity": "1"}])
    ready = client.get(f"/projects/{pid}/readiness").json()
    documents = next(c for c in ready["checks"] if c["key"] == "documents")
    assert documents["status"] == "blocked" and documents["count"] == 3
    assert ready["boq_ready_for_issue"] is False
    refused = client.post(f"/projects/{pid}/boq/revisions", json={})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_ready"


def test_identity_findings_take_a_manager_to_accept_and_then_stop_blocking(client, db_session, archive, monkeypatch):
    folder = archive / "Client" / "EP-40002 Mall"
    sheet = _pdf(folder / "Commercial" / "EP-40099 FAS Design.pdf", ["Page 1 of 1"])
    pid = _create(client, archive, ep="40002", folder=folder, drf=None, sheets=[("FAS", sheet)])
    monkeypatch.setattr(settings, "projects_root", str(archive))
    [row] = client.post(f"/projects/{pid}/documents/intake").json()
    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "description": "CPU", "quantity": "1"}])

    make_user(db_session, "eng@ep-platform.com", RoleEnum.fire_alarm_design_engineer)
    login(client, "eng@ep-platform.com")
    base = f"/projects/{pid}/documents/intake/{row['id']}/acknowledge"
    assert client.post(base, json={"code": "EP_MISMATCH_FILENAME", "reason": "Misnamed by the estimator"}).status_code == 403

    _login_admin(client)
    assert client.post(base, json={"code": "EP_MISMATCH_FILENAME", "reason": "short"}).status_code == 422
    accepted = client.post(base, json={"code": "EP_MISMATCH_FILENAME", "reason": "The estimator misnamed it; it is this project's sheet."})
    assert accepted.status_code == 200 and accepted.json()["intake_status"] == "warning"
    assert accepted.json()["acknowledged"][0]["by_name"]
    # Re-checking keeps the acceptance, and the revision can now be issued.
    assert client.post(f"/projects/{pid}/documents/intake").json()[0]["intake_status"] == "warning"
    issued = client.post(f"/projects/{pid}/boq/revisions", json={})
    assert issued.status_code == 201, issued.text


def test_missing_unreadable_and_duplicate_documents(client, db_session, archive, monkeypatch):
    folder = archive / "Client" / "EP-40003 Villa"
    good = _pdf(folder / "Commercial" / "EP-40003 FAS Design.pdf", ["Page 1 of 1"])
    copy = folder / "Commercial" / "EP-40003 FAS Design (copy).pdf"
    copy.write_bytes(good.read_bytes())
    fake = folder / "Commercial" / "EP-40003 ELS Design.pdf"
    fake.write_text("not a pdf at all")
    gone = folder / "Commercial" / "EP-40003 PAVA Design.pdf"
    pid = _create(client, archive, ep="40003", folder=folder, drf=None,
                  sheets=[("FAS", good), ("CBS", copy), ("ELS", fake), ("PAVA", gone)])
    monkeypatch.setattr(settings, "projects_root", str(archive))

    rows = client.post(f"/projects/{pid}/documents/intake").json()
    assert _findings(rows, good.name)[0] == {"DUPLICATE_CONTENT": "warning"}
    assert _findings(rows, fake.name)[0] == {"CONTENT_TYPE_MISMATCH": "blocked"}
    gone_codes, gone_row = _findings(rows, gone.name)
    assert gone_codes == {"MISSING_FILE": "blocked"}
    refused = client.post(f"/projects/{pid}/documents/intake/{gone_row['id']}/acknowledge",
                          json={"code": "MISSING_FILE", "reason": "It will be synced later today, honestly."})
    assert refused.status_code == 400

    # A viewer sees the findings but not the absolute paths.
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    listed = client.get(f"/projects/{pid}/documents/intake").json()
    assert all(r["path"] is None for r in listed) and len(listed) == 4
    assert client.post(f"/projects/{pid}/documents/intake").status_code == 403


def test_a_page_the_read_could_not_handle_is_not_a_row_to_review(client, db_session):
    """EP-30088's ELS sheet has a layout the extractor does not recognise. The
    read records that as page- and document-level issues; the BOQ page then
    listed them under "rows need review" as blank rows asking for a quantity,
    and readiness counted them as rows to add or reject -- twice over, since
    the coverage check already reports the unread page."""
    _login_admin(client)
    pid = client.post("/projects", json={"ep_number": "40005", "project_name": "P", "systems": [], "design_sheets": []}).json()["id"]
    run = ExtractionRun(project_id=pid, kind="design_sheet", document_path="EP-30088 Design Sheet ELS.pdf", parser_version="t",
                        outcome="NEEDS_HUMAN_DECISION", failure="Could not find a line-item table in this Design Sheet",
                        coverage={"pages": [{"page": 1, "detected": True, "processed": False, "reason": "no recognised column layout"}]})
    run.issues = [ExtractionIssue(code="UNPROCESSED_PAGE_OR_REGION", severity="high", page=1, target="page:1",
                                  detail={"reason": "no recognised column layout"}, state="open"),
                  ExtractionIssue(code="UNRECOGNIZED_TABLE_LAYOUT", severity="high", page=None, target="",
                                  detail={"pages": 1}, state="open"),
                  ExtractionIssue(code="QUANTITY_OR_UNIT_PARSE_FAILURE", severity="medium", page=1, target="boq_line:1:4",
                                  detail={"description": "Emergency light"}, state="open")]
    db_session.add(run)
    db_session.commit()

    checks = {c["key"]: c for c in client.get(f"/projects/{pid}/readiness").json()["checks"]}
    # The one real row, once; the unread page is the coverage check's finding.
    assert checks["unresolved_rows"]["count"] == 1 and checks["unresolved_rows"]["items"] == ["EP-30088 Design Sheet ELS.pdf p.1: Emergency light"]

    body = client.get(f"/projects/{pid}/extraction").json()
    assert body["open_issues"] == 1
    # The page-level issues are still on the run for the sheet banner to explain.
    assert sorted(i["code"] for i in body["runs"][0]["issues"]) == [
        "QUANTITY_OR_UNIT_PARSE_FAILURE", "UNPROCESSED_PAGE_OR_REGION", "UNRECOGNIZED_TABLE_LAYOUT"]
    assert body["runs"][0]["unprocessed_pages"] == [1]


def test_readiness_blocks_on_unresolved_rows_ai_suggestions_and_unassigned_lines(client, db_session):
    _login_admin(client)
    pid = client.post("/projects", json={"ep_number": "40004", "project_name": "P", "systems": [], "design_sheets": []}).json()["id"]
    client.put(f"/projects/{pid}/boq", json=[{"system_code": None, "description": "Loose line", "quantity": "2"}])
    run = ExtractionRun(project_id=pid, kind="design_sheet", document_path="x.pdf", parser_version="t", outcome="NEEDS_INTERPRETATION",
                        coverage={"pages": [{"page": 1, "detected": True, "processed": True}, {"page": 2, "detected": True, "processed": False}]})
    run.issues = [ExtractionIssue(code="QUANTITY_OR_UNIT_PARSE_FAILURE", severity="medium", page=1, target="boq_line:1:1",
                                  detail={"description": "Speaker"}, state="open"),
                  ExtractionIssue(code="QUANTITY_OR_UNIT_PARSE_FAILURE", severity="medium", page=1, target="boq_line:1:2",
                                  detail={"description": "Horn"}, state="proposed")]
    db_session.add(run)
    db_session.commit()

    checks = {c["key"]: c for c in client.get(f"/projects/{pid}/readiness").json()["checks"]}
    assert checks["unresolved_rows"]["status"] == "blocked" and checks["unresolved_rows"]["count"] == 1
    assert checks["ai_review"]["status"] == "blocked" and checks["ai_review"]["count"] == 1
    assert checks["boq_lines"]["status"] == "blocked" and "without a system" in checks["boq_lines"]["summary"]
    refused = client.post(f"/projects/{pid}/boq/revisions", json={})
    assert refused.status_code == 409
    blockers = refused.json()["detail"]["blockers"]
    assert any("settle" in b for b in blockers) and any("AI" in b for b in blockers) and any("BOQ lines" in b for b in blockers)
