"""The compliance knowledge base: importing the source collection, filling a
statement from it deterministically, reviewing one clause with the model on
request, and the guards around all of it.

The synthetic source folder copies the real one's shape: the workbook of
record with its seven tables, the same records exported again as Markdown
under knowledge/, an index, and the extraction's working files. The
specification is test_compliance_statements' EP-30784 lookalike with a few
more clauses.
"""

import io
import json
from pathlib import Path

import openpyxl
import pytest

import app.ai.provider as provider_module
from app.ai.provider import RecordingProvider
from app.compliance.spec_text import read_bytes
from app.core.config import get_settings
from app.knowledge import autofill, importer, policy
from app.knowledge.normalize import normalize_requirement, requirement_hash
from app.models import KnowledgeEquivalence, KnowledgeResponse, Project, ProjectBoqItem, RoleEnum

from .conftest import login, make_user
from .test_compliance import _clear_cache, _project
from .test_compliance_statements import BODY, approve_all, spec_pdf

settings = get_settings()

# The specification: BODY's clauses plus a few for the cases below.
SPEC_BODY = BODY[:-1] + [BODY[-1][:-1] + [
    "E.", "Provide training to the Client's personnel on the operation of the system.",
    "F.", "Batteries shall give 24 hours' standby followed by 30 minutes' alarm.",
    "G.", "Sounders shall be installed in bedrooms.",
    "H.", "Short.",
    "END OF SECTION 283111",
]]

SOURCES = [
    # source_id, filename, project, job, system, manufacturer, brand, family, revision, date, review status
    ("SRC-aaa00001", "FA Compliance R1.pdf", "EP-100 Tower", "EP-100", "FA", "EDWARDS", "EDWARDS", "Emaar", "R1", "file-modified 2024-05-01", "stamp/status text in PDF: Status/Code B"),
    ("SRC-bbb00002", "FA Compliance R0.pdf", "EP-100 Tower", "EP-100", "FA", "EDWARDS", "EDWARDS", "Emaar", "R0", "file-modified 2023-01-01", "Unknown"),
    ("SRC-ccc00003", "Compliance.pdf", "EP-300 Mall", "EP-300", "FA", "HONEYWELL", "NOTIFIER", "Unknown", "R0", "file-modified 2022-06-01", "Unknown"),
]
REQUIREMENTS = {
    "REQ-0000000001": "Operating Voltage: 24-V dc, nominal.",
    "REQ-0000000002": "Graphic annunciator mounted at the fire command centre.",
    "REQ-0000000003": "A strip printer mounted in the main FACP enclosure.",
    "REQ-0000000004": "Self-Restoring: Detectors do not require resetting.",
    "REQ-0000000005": "This Section includes fire alarm systems with manual stations and detectors.",
    "REQ-0000000006": "Firefighters' telephone handsets connected to the FACP.",
    "REQ-0000000007": "Provide training to the Client's personnel on the operation of the system.",
    "REQ-0000000008": "Batteries shall give 48 hours' standby followed by 30 minutes' alarm.",
    "REQ-0000000009": "Sounders shall not be installed in bedrooms.",
    "REQ-0000000010": "Short.",
}
# response_id, requirement, manufacturer, brand, models, response text, status, review_flag, scope, party
RESPONSES = [
    ("RSP-000000001", "REQ-0000000001", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000002", "REQ-0000000002", "EDWARDS", "EDWARDS", None, "Not applicable as per design", "Not Applicable", None, None, None),
    ("RSP-000000003", "REQ-0000000003", "EDWARDS", "EDWARDS", None, "Comply", "Comply", "OCR-derived text — verify against PDF", None, None),
    ("RSP-000000004", "REQ-0000000004", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000005", "REQ-0000000005", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000055", "REQ-0000000005", "EDWARDS", "EDWARDS", None, "Noted", "Noted", None, None, None),
    ("RSP-000000006", "REQ-0000000006", "EDWARDS", "EDWARDS", "SIGA-TEL", "Comply with SIGA-TEL handsets", "Comply", None, None, None),
    ("RSP-000000007", "REQ-0000000007", "EDWARDS", "EDWARDS", None, "Comply. Training shall be provided by Al Arabia.", "Comply", None, None, "by Al Arabia"),
    ("RSP-000000008", "REQ-0000000008", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000009", "REQ-0000000009", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000010", "REQ-0000000010", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None),
    ("RSP-000000011", "REQ-0000000001", "HONEYWELL", "NOTIFIER", None, "Comply", "Comply", None, None, None),
]
# mapping_id -> (requirement, source, clause, method, confidence)
MAPPINGS = {
    "MAP-000000001": ("REQ-0000000001", "SRC-aaa00001", "2.1.A.1", "A_table", "high"),
    "MAP-000000002": ("REQ-0000000002", "SRC-aaa00001", "2.1.B", "A_table", "high"),
    "MAP-000000003": ("REQ-0000000003", "SRC-aaa00001", "2.1.C", "D_annot_ocr", "medium"),
    "MAP-000000004": ("REQ-0000000004", "SRC-aaa00001", "2.1.A.2", "B2_margin", "low"),
    "MAP-000000005": ("REQ-0000000005", "SRC-aaa00001", "1.2.A", "A_table", "high"),
    "MAP-000000055": ("REQ-0000000005", "SRC-bbb00002", "1.2.A", "A_table", "high"),
    "MAP-000000006": ("REQ-0000000006", "SRC-aaa00001", "2.1.D", "A_table", "high"),
    "MAP-000000007": ("REQ-0000000007", "SRC-aaa00001", "2.1.E", "A_table", "high"),
    "MAP-000000008": ("REQ-0000000008", "SRC-aaa00001", "2.1.F", "A_table", "high"),
    "MAP-000000009": ("REQ-0000000009", "SRC-aaa00001", "2.1.G", "A_table", "high"),
    "MAP-000000010": ("REQ-0000000010", "SRC-aaa00001", "2.1.H", "A_table", "high"),
    "MAP-000000011": ("REQ-0000000001", "SRC-ccc00003", "2.1", "A_table", "high"),
}
LINKS = [
    ("RSP-000000001", "MAP-000000001", "SRC-aaa00001", None), ("RSP-000000002", "MAP-000000002", "SRC-aaa00001", None),
    ("RSP-000000003", "MAP-000000003", "SRC-aaa00001", None), ("RSP-000000004", "MAP-000000004", "SRC-aaa00001", None),
    ("RSP-000000005", "MAP-000000005", "SRC-aaa00001", None),
    ("RSP-000000055", "MAP-000000055", "SRC-bbb00002", None),
    ("RSP-000000006", "MAP-000000006", "SRC-aaa00001", None), ("RSP-000000007", "MAP-000000007", "SRC-aaa00001", None),
    ("RSP-000000008", "MAP-000000008", "SRC-aaa00001", None), ("RSP-000000009", "MAP-000000009", "SRC-aaa00001", None),
    ("RSP-000000010", "MAP-000000010", "SRC-aaa00001", None), ("RSP-000000011", "MAP-000000011", "SRC-ccc00003", None),
]


def workbook_bytes(responses=RESPONSES, links=LINKS) -> bytes:
    wb = openpyxl.Workbook()
    readme = wb.active
    readme.title = "README"
    readme.append(["Compliance Response Database", None])
    readme.append(["Built 2026-09-14 11:38", None])
    ws = wb.create_sheet("SOURCES")
    ws.append(["source_id", "filename", "onedrive_link_or_id", "duplicate_copies", "project", "job_number", "client", "consultant",
               "contractor", "submittal_title", "system", "system_note", "manufacturer", "brand", "manufacturer_wording",
               "manufacturer_note", "specification_family", "family_note", "section_numbers", "document_revision", "document_date",
               "document_review_status", "extraction_status", "pages", "scanned_pages", "ocr_pages", "pages_processed", "methods",
               "n_pairs", "n_unanswered", "n_empty_requirement", "extracted_at", "sha1"])
    for sid, name, project, job, system, maker, brand, family, rev, date, review in SOURCES:
        ws.append([sid, name, f"SharePoint\\{project}\\{name}", None, project, job, None, None, None, None, system, None, maker, brand,
                   None, None, family, None, "283111", rev, date, review, "extracted", 10, 0, 0, "all", "A_table:9", 9, 0, 0,
                   "2026-09-14 10:31", "0" * 40])
    ws = wb.create_sheet("REQUIREMENTS")
    ws.append(["requirement_id", "system", "systems_seen", "subsystem", "topic", "canonical_requirement_text", "technical_variant",
               "merge_review_status", "numbers", "n_sources"])
    for rid, text in REQUIREMENTS.items():
        ws.append([rid, "FA", "FA", "General", None, text, None, "Exact-duplicate grouping only", "", 1])
    ws = wb.create_sheet("SPECIFICATION_MAPPINGS")
    ws.append(["mapping_id", "requirement_id", "specification_family", "specification_title", "specification_edition", "section_number",
               "clause_number", "clause_label_as_printed", "heading_context", "original_requirement_text", "source_id", "pdf_page",
               "printed_page", "extraction_method", "pairing_confidence"])
    for mid, (rid, sid, clause, method, conf) in MAPPINGS.items():
        ws.append([mid, rid, "Emaar", "Fire alarm", "Unknown", "283111", clause, clause, None, REQUIREMENTS[rid], sid, "7", "3", method, conf])
    ws = wb.create_sheet("RESPONSES")
    ws.append(["response_id", "requirement_id", "system", "manufacturer", "brand", "model_configuration", "exact_historical_response",
               "remarks_column", "original_compliance_status", "scope_conditions", "responsible_party", "cited_supporting_references",
               "review_flag", "specification_families", "n_sources"])
    for rsp, rid, maker, brand, models, text, status, flag, scope, party in responses:
        ws.append([rsp, rid, "FA", maker, brand, models, text, None, status, scope, party, None, flag, "Emaar", 1])
    ws = wb.create_sheet("RESPONSE_SOURCES")
    ws.append(["response_id", "mapping_id", "source_id", "pdf_page", "historical_review_status", "consultant_comment", "superseded_status"])
    for rsp, mid, sid, superseded in links:
        ws.append([rsp, mid, sid, "7", "stamp/status text in PDF: Status/Code B", None, superseded])
    ws = wb.create_sheet("MERGE_REVIEW")
    ws.append(["requirement_id_a", "requirement_id_b", "similarity", "proposal", "text_a", "text_b"])
    ws.append(["REQ-0000000001", "REQ-0000000008", 61.0, "Similar wording, technical values differ — keep separate", "", ""])
    ws.append(["REQ-0000000002", "REQ-0000000003", 90.0, "Proposed equivalent-wording merge — review", "", ""])
    ws = wb.create_sheet("REVIEW_ISSUES")
    ws.append(["issue_type", "record_id", "detail", "extra"])
    ws.append(["Specification family unknown", "SRC-ccc00003", "Unknown", "{}"])
    ws.append(["Manufacturer uncertain", "SRC-ccc00003", "inferred", "{}"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def source_folder(root: Path, **kwargs) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / importer.CANONICAL_WORKBOOK).write_bytes(workbook_bytes(**kwargs))
    (root / "index.json").write_text(json.dumps({"built": "2026-09-14 11:38", "files": []}))
    (root / "INDEX.md").write_text("# Index\n")
    (root / "CLAUDE.md").write_text("# notes\n")
    (root / "knowledge" / "FA" / "EDWARDS" / "Emaar").mkdir(parents=True, exist_ok=True)
    (root / "knowledge" / "FA" / "EDWARDS" / "Emaar" / "Section_283111.md").write_text("## Clause 2.1.A.1 — REQ-0000000001\n- RSP-000000001 Comply\n")
    (root / "agent_bundle" / "knowledge" / "FA").mkdir(parents=True, exist_ok=True)
    (root / "agent_bundle" / "knowledge" / "FA" / "Section_283111.md").write_text("same again\n")
    (root / "agent_bundle" / "01_CATALOG.md").write_text("| FA |\n")
    (root / "_work" / "extract").mkdir(parents=True, exist_ok=True)
    (root / "_work" / "extract" / "abc.json").write_text("{}")
    return root


@pytest.fixture()
def knowledge(db_session, tmp_path, monkeypatch):
    root = source_folder(tmp_path / "Compliance_Response_Database")
    monkeypatch.setattr(settings, "compliance_knowledge_source", str(root))
    autofill.invalidate()
    record = importer.run_import(db_session)
    assert record.status == "succeeded", record.error
    yield root
    autofill.invalidate()


@pytest.fixture()
def no_ai():
    """A provider that records every call, so a test can assert none was made."""
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    yield provider
    provider_module.set_provider(None)


def _project_with_boq(client, db_session, folder: Path, *, scope="Supply, Testing & Commissioning") -> int:
    project_id = _project(client, folder)
    project = db_session.get(Project, project_id)
    project.scope_of_work = scope
    for system in project.systems:
        if system.name == "Fire Alarm":
            system.brand = "Edwards"
    db_session.add(ProjectBoqItem(project_id=project_id, position=1, description="Photoelectric smoke detector",
                                  manufacturer="Edwards", catalog_no="SIGA-PS", quantity="120", unit="no", system_code="FAS"))
    db_session.add(ProjectBoqItem(project_id=project_id, position=2, description="Fire alarm control panel",
                                  manufacturer="Edwards", catalog_no="EST4", quantity="1", unit="no", system_code="FAS"))
    db_session.commit()
    return project_id


def _prepared(client, project_id: int, folder: Path) -> dict:
    spec_pdf(folder / "Specification" / "283111 - FIRE DETECTION.pdf", body=SPEC_BODY)
    spec = client.get(f"/projects/{project_id}/compliance").json()["systems"][0]["specs"][0]
    resp = client.post(f"/projects/{project_id}/compliance/prepare",
                       json={"system_code": "FAS", "path": spec["path"], "member": spec["member"],
                             "first_page": spec["first_page"], "last_page": spec["last_page"]})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- normalisation ------------------------------------------------------------------------


def test_normalisation_is_conservative():
    same = "A.  Operating Voltage:\n24-V dc, nominal."
    assert requirement_hash(same) == requirement_hash("Operating Voltage: 24-V dc, nominal.")
    assert normalize_requirement("1.5 mm cable") == "1.5 mm cable"          # a number is not a label
    assert requirement_hash("not less than 24 hours") != requirement_hash("not less than 48 hours")
    assert requirement_hash("Sounders shall be installed") != requirement_hash("Sounders shall not be installed")
    assert requirement_hash("SIGA-PS") != requirement_hash("SIGA-PD")
    assert requirement_hash("≥ 65 dB(A)") != requirement_hash("> 65 dB(A)")
    assert requirement_hash("BS 5839-1:2017") != requirement_hash("BS 5839-1:2013")


# --- import --------------------------------------------------------------------------------


def test_the_source_is_inspected_and_only_the_workbook_is_imported(db_session, tmp_path, monkeypatch):
    root = source_folder(tmp_path / "Compliance_Response_Database")
    monkeypatch.setattr(settings, "compliance_knowledge_source", str(root))
    inspection = importer.inspect_source()
    roles = inspection.counts()
    assert roles["canonical"] == 1 and roles["duplicate_export"] == 2 and roles["index"] == 3
    assert roles["intermediate"] == 1 and roles["documentation"] == 1

    record = importer.run_import(db_session)
    assert record.status == "succeeded", record.error
    assert (record.files_discovered, record.files_imported, record.files_skipped) == (8, 1, 7)
    assert record.records_added == len(SOURCES) + len(REQUIREMENTS) + len(MAPPINGS) + len(RESPONSES)
    status = importer.status(db_session)
    assert status["records"]["responses"] == len(RESPONSES) and status["records"]["sources"] == 3
    # The policy: what stands and why the rest does not.
    reasons = {r.response_id: (r.autofill_eligibility, r.eligibility_reasons)
               for r in db_session.query(KnowledgeResponse)}
    assert reasons["RSP-000000001"] == ("eligible", None)
    assert reasons["RSP-000000002"][0] == "blocked" and "depends on the project" in reasons["RSP-000000002"][1]
    assert "review flag" in reasons["RSP-000000003"][1]
    assert "high-confidence" in reasons["RSP-000000004"][1]
    assert "too short" in reasons["RSP-000000010"][1]
    assert "review issue: Manufacturer uncertain" in reasons["RSP-000000011"][1]
    assert status["records"]["eligible_responses"] == 7
    # The duplicate exports were seen and skipped, not imported twice.
    report = importer.import_report(db_session, record.id)
    assert {f["role"]: f["count"] for f in report["files"]}["duplicate_export"] == 2
    assert report["canonical"]["action"] == "imported"

    # Unchanged workbook: nothing read again.
    again = importer.run_import(db_session)
    assert again.status == "unchanged" and again.files_unchanged == 1 and again.records_added == 0

    # An engineer validates an equivalence; a changed workbook re-imports
    # around it: one response gone (inactive), one new (added).
    equivalence = db_session.query(KnowledgeEquivalence).filter_by(source_requirement_id="REQ-0000000002").one()
    equivalence.engineer_validation_status = "validated"
    db_session.commit()
    changed = [r for r in RESPONSES if r[0] != "RSP-000000009"] + [
        ("RSP-000000012", "REQ-0000000009", "EDWARDS", "EDWARDS", None, "Comply", "Comply", None, None, None)]
    (root / importer.CANONICAL_WORKBOOK).write_bytes(workbook_bytes(responses=changed, links=LINKS + [("RSP-000000012", "MAP-000000009", "SRC-aaa00001", None)]))
    third = importer.run_import(db_session)
    assert third.status == "succeeded", third.error
    assert third.records_added == 1 and third.records_inactive == 1
    assert db_session.get(KnowledgeResponse, "RSP-000000009").active is False
    assert db_session.query(KnowledgeEquivalence).filter_by(source_requirement_id="REQ-0000000002").one().engineer_validation_status == "validated"

    # A broken workbook fails the import and leaves the knowledge standing.
    (root / importer.CANONICAL_WORKBOOK).write_bytes(b"not a workbook")
    failed = importer.run_import(db_session)
    assert failed.status == "failed" and failed.error
    assert importer.status(db_session)["records"]["responses"] == len(RESPONSES)
    assert importer.status(db_session)["last_successful"]["id"] == third.id

    # An unreachable folder is a failed import with a plain message, and
    # nothing is treated as deleted.
    monkeypatch.setattr(settings, "compliance_knowledge_source", str(tmp_path / "gone"))
    missing = importer.run_import(db_session)
    assert missing.status == "failed" and "not reachable" in missing.error
    assert db_session.query(KnowledgeResponse).filter_by(active=True).count() == len(RESPONSES)
    monkeypatch.setattr(settings, "compliance_knowledge_source", None)
    assert "not set" in importer.run_import(db_session).error


# --- autofill --------------------------------------------------------------------------------


def test_autofill_fills_eligible_exact_matches_and_only_those(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "ai_enabled", True)
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    statement = _prepared(client, project_id, folder)
    rows = {r["ref"]: r for r in statement["rows"]}
    assert rows["2.1.A.1"]["response"] == "" and rows["2.1.A.1"]["workflow"] == "unfilled"
    assert rows["1.3.A"]["workflow"] == "autofilled" and rows["1.3.A"]["origin"] == "rule"

    filled = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill")
    assert filled.status_code == 200, filled.text
    after = {r["ref"]: r for r in filled.json()["rows"]}
    # Exact wording, eligible record, the project's manufacturer: a draft.
    row = after["2.1.A.1"]
    assert (row["response"], row["workflow"], row["origin"]) == ("Comply", "autofilled", "database")
    assert row["technical"] == {"status": "complies", "origin": "historical", "verified": False}
    assert row["match"]["response_id"] == "RSP-000000001" and row["match"]["sources"][0]["source_id"] == "SRC-aaa00001"
    assert row["match"]["boq_item"]["manufacturer"] == "Edwards" and "Same wording" in row["match"]["explanation"]
    # Project-dependent status, a review flag, a low-confidence pairing:
    # candidates, nothing written.
    # A project-dependent status: a candidate, nothing written.
    assert after["2.1.B"]["response"] == "" and after["2.1.B"]["workflow"] == "candidate"
    assert after["2.1.B"]["match"]["result"] == "candidate" and after["2.1.B"]["match"]["same_wording"] is True
    assert any("depends on the project" in u for u in after["2.1.B"]["match"]["unresolved"])
    # Same wording, the project's manufacturer, one answer, but the record is
    # flagged for how it was extracted (OCR; a low-confidence pairing): the
    # answer is written in and the row stays a candidate to verify.
    for ref, reason in (("2.1.C", "review flag"), ("2.1.A.2", "high-confidence")):
        row = after[ref]
        assert (row["response"], row["workflow"], row["origin"]) == ("Comply", "candidate", "database"), ref
        assert row["match"]["result"] == "flagged" and any(reason in u for u in row["match"]["unresolved"]), ref
        assert row["technical"] == {"status": "complies", "origin": "historical", "verified": False}
        assert "check its PDF page" in row["note"]
    # Eligible answers that disagree are not chosen.
    assert after["1.2.A"]["response"] == "" and after["1.2.A"]["match"]["result"] == "conflict"
    # A model the BOQ does not propose; a commitment the scope does not name.
    assert after["2.1.D"]["match"]["result"] == "missing_model" and after["2.1.D"]["response"] == ""
    assert after["2.1.E"]["match"]["result"] == "scope" and "training" in after["2.1.E"]["match"]["unresolved"][0]
    # A changed rating and a negation are similar, never the same.
    assert after["2.1.F"]["match"]["result"] == "candidate" and after["2.1.F"]["match"]["candidates"][0]["requirement_id"] == "REQ-0000000008"
    assert after["2.1.G"]["response"] == "" and after["2.1.G"]["match"]["result"] in ("candidate", "none")
    # Nothing near: no match.
    assert after["2.1.H"]["match"]["result"] == "none"
    counts = filled.json()["summary"]["autofill"]
    assert counts == {"filled": 1, "flagged": 2, "candidates": 3, "unmatched": 1, "blocked": 3, "learned": 0}
    assert no_ai.calls == 0

    # The engineer picks a past answer shown for a candidate; nothing else
    # can be picked, and no model is called.
    offered = after["2.1.B"]["match"]["candidates"][0]["response_id"]
    url = f"/projects/{project_id}/compliance/statements/{statement['id']}/rows/{rows['2.1.B']['id']}/use-answer"
    assert client.post(url, json={"response_id": "RSP-000000001"}).status_code == 400
    used = client.post(url, json={"response_id": offered})
    assert used.status_code == 200, used.text
    row = next(r for r in used.json()["rows"] if r["ref"] == "2.1.B")
    assert (row["response"], row["remark"], row["origin"], row["workflow"]) == ("Not applicable", "Not applicable as per design", "database", "autofilled")
    client.patch(f"/projects/{project_id}/compliance/statements/{statement['id']}", json={"rows": [{"id": rows["2.1.B"]["id"], "response": "", "remark": ""}]})
    assert no_ai.calls == 0

    # A manual answer and a reviewed row survive the next auto-fill.
    edited = client.patch(f"/projects/{project_id}/compliance/statements/{statement['id']}",
                          json={"rows": [{"id": rows["2.1.B"]["id"], "response": "Not applicable", "remark": "No annunciator in BOQ"}]})
    assert edited.status_code == 200
    reviewed = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/rows/{rows['2.1.A.1']['id']}/reviewed",
                           json={"reviewed": True})
    assert reviewed.status_code == 200
    reviewed_row = next(r for r in reviewed.json()["rows"] if r["ref"] == "2.1.A.1")
    assert reviewed_row["workflow"] == "reviewed" and reviewed_row["technical"]["verified"] is True
    again = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill").json()
    again_rows = {r["ref"]: r for r in again["rows"]}
    assert again_rows["2.1.B"]["response"] == "Not applicable" and again_rows["2.1.B"]["origin"] == "manual"
    assert again_rows["2.1.A.1"]["workflow"] == "reviewed"
    assert no_ai.calls == 0

    # Opening, saving, approving, exporting: no model.
    assert client.get(f"/projects/{project_id}/compliance/statements/{statement['id']}").status_code == 200
    approve_all(client, project_id, statement["id"])
    assert client.get(f"/projects/{project_id}/compliance/statements/{statement['id']}/export").status_code == 200
    assert client.get(f"/projects/{project_id}/compliance/statements/{statement['id']}/export.pdf").status_code == 200
    assert no_ai.calls == 0

    # Clear all: every answer, remark, match and review goes; the engineer's
    # own remark stays when asked, and the approval is withdrawn.
    cleared = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/clear", json={"keep_manual_remarks": True})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    cleared_rows = {r["ref"]: r for r in body["rows"]}
    assert all(r["response"] == "" and r["workflow"] == "unfilled" and r["match"] is None
               for r in body["rows"] if not r["heading"] and r["source"] != "lead_in")
    assert cleared_rows["2.1.B"]["remark"] == "No annunciator in BOQ" and cleared_rows["2.1.A.1"]["remark"] == ""
    assert body["approved"] is False and "autofill" not in body["summary"]
    wiped = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/clear", json={}).json()
    assert next(r for r in wiped["rows"] if r["ref"] == "2.1.B")["remark"] == ""
    assert no_ai.calls == 0


def test_an_engineer_approved_answer_is_reused_on_the_next_statement(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    first = _prepared(client, project_id, folder)
    base = f"/projects/{project_id}/compliance/statements/{first['id']}"
    rows = {r["ref"]: r for r in first["rows"]}
    # The engineer answers a clause in their own words and signs it off.
    clause = rows["2.1.E"]
    client.patch(base, json={"rows": [{"id": clause["id"], "response": "Comply",
                                       "remark": "Comply with training by Al Arabia on the proposed EST4 system."}]})
    assert client.post(f"{base}/rows/{clause['id']}/reviewed", json={"reviewed": True}).status_code == 200

    second = client.post(f"/projects/{project_id}/compliance/prepare", json={
        "system_code": "FAS", "path": first["spec"]["path"], "member": first["spec"].get("member"),
        "first_page": first["spec"].get("first_page"), "last_page": first["spec"].get("last_page")}).json()
    filled = client.post(f"/projects/{project_id}/compliance/statements/{second['id']}/autofill").json()
    row = next(r for r in filled["rows"] if r["ref"] == "2.1.E")
    assert (row["response"], row["remark"]) == ("Comply", "Comply with training by Al Arabia on the proposed EST4 system.")
    assert (row["source"], row["origin"], row["workflow"]) == ("learned", "database", "autofilled")
    assert row["match"]["learned"] is True and "approved by" in row["match"]["explanation"]
    assert filled["summary"]["autofill"]["learned"] == 1
    # A draft still: the new statement's engineer reviews it.
    assert row["technical"]["verified"] is False

    # Taking the review back retires the learned answer.
    assert client.post(f"{base}/rows/{clause['id']}/reviewed", json={"reviewed": False}).status_code == 200
    third = client.post(f"/projects/{project_id}/compliance/prepare", json={
        "system_code": "FAS", "path": first["spec"]["path"], "member": first["spec"].get("member"),
        "first_page": first["spec"].get("first_page"), "last_page": first["spec"].get("last_page")}).json()
    refilled = client.post(f"/projects/{project_id}/compliance/statements/{third['id']}/autofill").json()
    assert next(r for r in refilled["rows"] if r["ref"] == "2.1.E")["source"] != "learned"
    assert no_ai.calls == 0


def test_a_generic_boq_line_or_another_manufacturer_does_not_fill(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project(client, folder)
    project = db_session.get(Project, project_id)
    # A BOQ that names no manufacturer and no model.
    db_session.add(ProjectBoqItem(project_id=project_id, position=1, description="Addressable smoke detector", system_code="FAS"))
    db_session.commit()
    statement = _prepared(client, project_id, folder)
    filled = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill").json()
    rows = {r["ref"]: r for r in filled["rows"]}
    assert rows["2.1.A.1"]["response"] == "" and rows["2.1.A.1"]["match"]["result"] == "candidate"
    assert "no manufacturer recorded" in rows["2.1.A.1"]["match"]["unresolved"][0]
    assert no_ai.calls == 0
    # The project offers another manufacturer: still a candidate.
    for system in project.systems:
        system.brand = "Siemens"
    db_session.commit()
    filled = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill").json()
    assert next(r for r in filled["rows"] if r["ref"] == "2.1.A.1")["response"] == ""


def test_a_statement_saved_in_the_old_shape_is_upgraded_and_filled(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):
    """Statements prepared before rows carried a workflow status (EP-30058's)
    were skipped whole by auto-fill: 0 looked up, 0 filled."""
    from app.models import ComplianceStatement

    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    statement = _prepared(client, project_id, folder)
    # Put it back in the old shape: no workflow/origin/technical/match, an
    # earlier matcher's "reference" answer, a rule row, an empty row.
    record = db_session.get(ComplianceStatement, statement["id"])
    old = []
    for row in record.rows:
        row = {k: v for k, v in row.items() if k not in ("workflow", "origin", "technical", "match", "ai_review")}
        if row["ref"] == "2.1.C":
            row.update(response="Comply", source="reference", reference={"similarity": 0.9, "response": "Comply"})
        elif row["ref"] == "2.1.A.1":
            row.update(response="", source="none", state="review", reference={"similarity": 0.7})
        old.append(row)
    record.rows = old
    db_session.commit()

    opened = client.get(f"/projects/{project_id}/compliance/statements/{statement['id']}").json()
    rows = {r["ref"]: r for r in opened["rows"]}
    assert rows["2.1.C"]["response"] == "Comply" and rows["2.1.C"]["workflow"] == "candidate" and rows["2.1.C"]["origin"] == "legacy"
    assert rows["1.3.A"]["workflow"] == "autofilled" and rows["1.3.A"]["origin"] == "rule"
    assert rows["2.1.A.1"]["workflow"] == "unfilled" and "reference" not in rows["2.1.A.1"]
    assert any("earlier past-statement matcher" in n for n in opened["summary"]["notes"])

    filled = client.post(f"/projects/{project_id}/compliance/statements/{statement['id']}/autofill").json()
    row = next(r for r in filled["rows"] if r["ref"] == "2.1.A.1")
    assert (row["response"], row["origin"]) == ("Comply", "database")
    assert filled["summary"]["autofill"]["filled"] == 1 and "looked up" in filled["summary"]["notes"][0]
    # The legacy answer is kept, not overwritten.
    assert next(r for r in filled["rows"] if r["ref"] == "2.1.C")["response"] == "Comply"
    assert no_ai.calls == 0


# --- the single-clause review ------------------------------------------------------------


def _review(clause_id: str, **overrides) -> dict:
    return {"clause_id": clause_id, "suggested_response": "Not applicable", "suggested_remark": "No graphic annunciator in the BOQ.",
            "proposed_compliance_status": "not_applicable", "evidence_references": ["BOQ-1", "RSP-000000002"],
            "missing_information": ["Design drawing showing the fire command centre"], "deviations": [],
            "review_notes": "The BOQ proposes detectors and a panel only.", **overrides}


def test_one_clause_is_reviewed_only_on_request(client, db_session, tmp_path, monkeypatch, knowledge):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "ai_enabled", True)
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    statement = _prepared(client, project_id, folder)
    sid = statement["id"]
    client.post(f"/projects/{project_id}/compliance/statements/{sid}/autofill")
    rows = {r["ref"]: r for r in client.get(f"/projects/{project_id}/compliance/statements/{sid}").json()["rows"]}
    clause = rows["2.1.B"]["id"]
    boq_id = db_session.query(ProjectBoqItem).filter_by(project_id=project_id).first().id

    provider = RecordingProvider([_review(clause, evidence_references=[f"BOQ-{boq_id}", "RSP-000000002", "RSP-999999999"])])
    provider_module.set_provider(provider)
    try:
        url = f"/projects/{project_id}/compliance/statements/{sid}/rows/{clause}/review"
        first = client.post(url, json={"instruction": "Is Not applicable right here?", "request_id": "req-1"})
        assert first.status_code == 200, first.text
        review = first.json()["ai_review"]
        assert review["status"] == "done" and review["suggestion"]["suggested_response"] == "Not applicable"
        # References the model made up are dropped and said so.
        assert review["suggestion"]["evidence_references"] == [f"BOQ-{boq_id}", "RSP-000000002"]
        assert "RSP-999999999" in review["suggestion"]["review_notes"]
        assert first.json()["workflow"] == "ai_pending" and first.json()["response"] == ""
        # The payload: this clause, its BOQ, its past answer, the scope -- not
        # the rest of the specification or the database.
        sent = "\n".join(p.text for p in provider.requests[0].parts)
        assert "Graphic annunciator" in sent and f"BOQ-{boq_id}" in sent and "RSP-000000002" in sent
        assert "Supply, Testing & Commissioning" in sent and "Is Not applicable right here?" in sent
        assert "strip printer" not in sent and "RSP-000000001" not in sent
        assert provider.calls == 1

        # A double-click is one request; reopening the statement calls nothing.
        assert client.post(url, json={"request_id": "req-1"}).json()["ai_review"]["request_id"] == "req-1"
        assert client.get(f"/projects/{project_id}/compliance/statements/{sid}").status_code == 200
        assert provider.calls == 1

        # Accept: the draft, not a reviewed row. Reject: nothing overwritten.
        accepted = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{clause}/suggestion", json={"action": "accept"})
        assert accepted.status_code == 200, accepted.text
        row = next(r for r in accepted.json()["rows"] if r["id"] == clause)
        assert (row["response"], row["remark"], row["workflow"], row["origin"]) == ("Not applicable", "No graphic annunciator in the BOQ.", "ai_pending", "ai")
        assert row["technical"] == {"status": "not_applicable", "origin": "ai", "verified": False}
        marked = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{clause}/reviewed", json={"reviewed": True}).json()
        assert next(r for r in marked["rows"] if r["id"] == clause)["workflow"] == "reviewed"

        other = rows["2.1.C"]["id"]
        provider.answers = [RuntimeError("boom")]
        failed = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{other}/review", json={"request_id": "req-2"})
        assert failed.status_code == 200 and failed.json()["ai_review"]["status"] == "failed"
        assert provider.calls == 2                       # no automatic retry
        rejected = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{other}/suggestion", json={"action": "reject"})
        assert rejected.status_code == 400               # nothing to decide on
        provider.answers = [_review(other, suggested_response="Comply", suggested_remark="", proposed_compliance_status="complies",
                                    evidence_references=[])]
        retried = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{other}/review", json={"request_id": "req-3"})
        assert retried.json()["ai_review"]["status"] == "done" and provider.calls == 3
        rejected = client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{other}/suggestion", json={"action": "reject"}).json()
        row = next(r for r in rejected["rows"] if r["id"] == other)
        # Rejected: the draft from the flagged record stays exactly as it was.
        assert row["response"] == "Comply" and row["ai_review"]["decision"] == "rejected" and row["workflow"] == "candidate"
    finally:
        provider_module.set_provider(None)


# --- security and versions ---------------------------------------------------------------------


def test_cross_project_requests_and_changed_inputs(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "ai_enabled", True)
    _clear_cache()
    login(client, settings.default_admin_email, settings.default_admin_password)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    statement = _prepared(client, project_id, folder)
    sid = statement["id"]
    client.post(f"/projects/{project_id}/compliance/statements/{sid}/autofill")
    clause = next(r["id"] for r in statement["rows"] if r["ref"] == "2.1.A.1")

    # Another project cannot reach this statement by id.
    other = client.post("/projects", json={"ep_number": "11111", "project_name": "Other", "source_folder_path": str(tmp_path / "other"),
                                           "systems": [{"name": "Fire Alarm", "method_statement": True, "drawing": True}], "design_sheets": []})
    assert other.status_code == 201
    other_id = other.json()["id"]
    assert client.get(f"/projects/{other_id}/compliance/statements/{sid}").status_code == 404
    assert client.post(f"/projects/{other_id}/compliance/statements/{sid}/rows/{clause}/review", json={}).status_code == 404
    assert client.post(f"/projects/{other_id}/compliance/statements/{sid}/autofill").status_code == 404
    # A viewer reads, never fills or reviews; the update action is the admin's.
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")
    assert client.get(f"/projects/{project_id}/compliance/statements/{sid}").status_code == 200
    assert client.post(f"/projects/{project_id}/compliance/statements/{sid}/autofill").status_code == 403
    assert client.post(f"/projects/{project_id}/compliance/statements/{sid}/rows/{clause}/review", json={}).status_code == 403
    assert client.post("/admin/knowledge/import").status_code == 403
    assert client.get("/projects/compliance/knowledge").status_code == 200
    login(client, settings.default_admin_email, settings.default_admin_password)

    # The BOQ changes: the drafts that came from the knowledge base are
    # flagged, their text kept, and no model is called.
    db_session.add(ProjectBoqItem(project_id=project_id, position=3, description="Heat detector", manufacturer="Edwards",
                                  catalog_no="SIGA-HFS", quantity="10", unit="no", system_code="FAS"))
    db_session.commit()
    reopened = client.get(f"/projects/{project_id}/compliance/statements/{sid}").json()
    row = next(r for r in reopened["rows"] if r["id"] == clause)
    assert row["workflow"] == "recheck" and row["response"] == "Comply" and "the BOQ" in row["note"]
    assert any("recheck" in n for n in reopened["summary"]["notes"])
    # And a rule row, which came from no input, is not.
    assert next(r for r in reopened["rows"] if r["ref"] == "1.3.A")["workflow"] == "autofilled"
    assert no_ai.calls == 0
    # The admin status page names no filesystem path.
    status = client.get("/admin/knowledge").json()
    assert status["source_configured"] is True and str(tmp_path) not in json.dumps(status)
    assert status["last_successful"]["records_added"] > 0
