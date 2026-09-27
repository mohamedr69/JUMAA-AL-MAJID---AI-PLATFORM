"""BOQ Extraction V2, phase 4: every engineer decision on a row the read
produced is kept as evaluation data, with what the machine had read."""

from __future__ import annotations

from app.models import BoqCorrection, ExtractionIssue, ExtractionRun

from .test_ai_sheet_reader import PAGE_ANSWER, SECOND, _close_up, _login, _project, ai, recording  # noqa: F401


def test_accepting_and_rejecting_review_rows_and_editing_lines_are_recorded(client, db_session, tmp_path, ai, recording):
    sheet = tmp_path / "EP-74001 FAS Design.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    recording.answers = [PAGE_ANSWER, SECOND, _close_up("30", "SIGA-HFS", "Heat detector")]
    project = _project(db_session, sheet, ep="74001")
    _login(client)
    body = client.post(f"/projects/{project.id}/boq/ensure").json()
    run = db_session.query(ExtractionRun).filter(ExtractionRun.project_id == project.id).one()
    issue = db_session.query(ExtractionIssue).filter(ExtractionIssue.run_id == run.id).one()

    # The review row accepted with a typed quantity.
    accepted = client.post(f"/projects/{project.id}/extraction/issues/{issue.id}/accept", json={"value": "31"})
    assert accepted.status_code == 200, accepted.text
    db_session.expire_all()
    record = db_session.query(BoqCorrection).filter(BoqCorrection.project_id == project.id).one()
    assert record.kind == "review_accepted" and record.final_quantity == "31" and record.primary_part_number == "SIGA-HFS"
    assert record.reason_code == "UNREADABLE" and record.row_id == "p1r4" and record.page == 1 and record.bbox
    assert record.processor_version and record.document_sha256 == run.document_sha256

    # A machine-read line's quantity edited in the BOQ table.
    current = client.get(f"/projects/{project.id}/boq")
    rows = [{k: i.get(k) for k in ("id", "system_code", "group_heading", "manufacturer", "catalog_no", "description",
                                   "quantity", "unit", "unit_price", "total_price", "remarks")} for i in current.json()]
    target = next(r for r in rows if r["catalog_no"] == "SIGA-PS")
    target["quantity"] = "121"
    saved = client.put(f"/projects/{project.id}/boq", json=rows, headers={"If-Match": current.headers["X-Resource-Version"]})
    assert saved.status_code == 200, saved.text
    db_session.expire_all()
    edits = db_session.query(BoqCorrection).filter(BoqCorrection.kind == "line_edited").all()
    assert len(edits) == 1 and edits[0].primary_quantity == "120" and edits[0].final_quantity == "121"
    assert edits[0].primary_part_number == "SIGA-PS" and edits[0].row_id == "p1r2"
    assert len(body["items"]) == 3
