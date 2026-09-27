"""Document Classification & Routing V2, the foundation: explainable
metadata beside the index, with no business side effects. Off, every path
behaves as before; on, the legacy outputs are identical and each document
carries an assessment that says what it appears to be and why."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models import (
    DocumentClassification, DocumentDependency, Project, ProjectDocument, ProjectShopDrawing, ProjectSubmittal,
)
from app.services import document_classification as dc
from app.services import document_processing, document_sync

from .test_document_sync import _pdf, _project, _reading, _submittal_form, ai  # noqa: F401 -- fixture
from .test_file_sync_v2_processing import _processing_job, _rows, _states

settings = get_settings()

DRAWING = "Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout"
IFC_DRAWING = "Drawing title\nBBY006-GME-DWG-EL-FA-0009\nREV. 00\nFirst Floor Fire Alarm Layout"
REPLY = ("Reply to consultant comments\nRef No : {ref} - R.00\n"
         "Comment 1: provide datasheets\nReply: attached\n")
SCHEDULE = "DWG NO\nFIRE ALARM AND EML SUBMISSION DRAWING LIST\nFA 001\nFire Alarm Schematic\nFA 002\nEmergency Schematic\n"
SPEC = "SECTION 28 31 00\nFIRE DETECTION AND ALARM\nPART 1 GENERAL\n"


@pytest.fixture()
def classification_on(monkeypatch):
    monkeypatch.setattr(settings, "document_classification_v2", True)
    yield
    monkeypatch.setattr(settings, "document_classification_v2", False)


def _folder(tmp_path, ep: str, ref: str = "BBY006-GME-MAS-EL-FA-0001") -> Path:
    folder = tmp_path / f"EP-{ep}"
    _submittal_form(folder / "03- MS" / "01- FA" / "form.pdf", reference=ref)
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    _pdf(folder / "00- IFC" / "IFC-L01.pdf", IFC_DRAWING)
    _pdf(folder / "02- Material Submittals" / "FA" / "R0" / "Received" / "reply.pdf", REPLY.format(ref=ref))
    _pdf(folder / "06- Specifications" / "28 31 00 Fire Alarm.pdf", SPEC)
    _pdf(folder / "09- Other" / "catalogue.pdf", "A product catalogue with nothing to register")
    _pdf(folder / "05- Drawings" / "schedule.pdf", SCHEDULE)
    return folder


def _sync(client, project_id: int) -> dict:
    job = client.post(f"/projects/{project_id}/jobs/sync-documents")
    assert job.status_code == 202, job.text
    assert job.json()["status"] == "succeeded", job.json()
    return job.json()


def _snapshot(db, project_id: int, ref: str = "BBY006-GME-MAS-EL-FA-0001") -> dict:
    """Everything the legacy paths produce, timing aside; the project's own
    submittal reference as <REF>, so two projects compare."""
    db.expire_all()
    rows = _rows(db, project_id)

    def norm(value):
        return value.replace(ref, "<REF>") if isinstance(value, str) else value

    snapshot = {
        "rows": {path: {"role": r.role, "state": r.state, "reference": r.reference, "revision": r.revision,
                        "status": r.status, "system": r.system_code, "index_version": r.index_version,
                        "records": [(rec.get("category"), rec.get("reference"), rec.get("revision"), rec.get("status"),
                                     rec.get("source")) for rec in (r.extracted or {}).get("records") or []],
                        "form": bool((r.extracted or {}).get("form"))}
                 for path, r in rows.items()},
        "dependencies": sorted((d.dependent_type, d.dependent_id if d.dependent_id != str(project_id) else "<project>", d.stale)
                               for d in db.query(DocumentDependency).filter(DocumentDependency.project_id == project_id)),
        "submittals": sorted((s.reference, s.system_code, s.status.value if hasattr(s.status, "value") else str(s.status))
                             for s in db.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project_id)),
        "shop_drawings": sorted((d.drawing_reference, d.system_code) for d in db.query(ProjectShopDrawing)
                                .filter(ProjectShopDrawing.project_id == project_id)),
    }
    return {
        "rows": {path: {k: ([tuple(norm(x) for x in rec) for rec in v] if k == "records" else norm(v)) for k, v in row.items()}
                 for path, row in snapshot["rows"].items()},
        "dependencies": [tuple(norm(x) for x in d) for d in snapshot["dependencies"]],
        "submittals": [tuple(norm(x) for x in s) for s in snapshot["submittals"]],
        "shop_drawings": snapshot["shop_drawings"],
    }


def _classifications(db, project_id: int) -> dict[str, DocumentClassification]:
    db.expire_all()
    rows = {r.id: r for r in db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id)}
    return {(rows[e.document_id].relative_path or rows[e.document_id].filename): e
            for e in db.query(DocumentClassification)
            .filter(DocumentClassification.project_id == project_id, DocumentClassification.superseded_at.is_(None))}


def _by_name(db, project_id: int) -> dict[str, ProjectDocument]:
    db.expire_all()
    return {(r.relative_path or r.filename): r for r in db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id)}


# --- TEST 1 / TEST 2 / TEST 8 / TEST 15 / TEST 16: legacy outputs identical, off and on ----------------


_SNAPSHOTS: dict[bool, dict] = {}


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_outputs_are_identical_with_the_feature_off_and_on(client, db_session, tmp_path, ai, monkeypatch, enabled):
    """The same project, in a fresh database each time, with the feature
    off and then on: every legacy output is the same; the second run also
    carries an assessment per document. (Two projects in one database
    were not compared: the legacy register keeps one row per form content
    across projects, whatever the flag.)"""
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    monkeypatch.setattr(settings, "document_classification_v2", enabled)
    project_id = _project(client, _folder(tmp_path, "30901"), "30901")
    _sync(client, project_id)
    snapshot = _snapshot(db_session, project_id)
    _SNAPSHOTS[enabled] = snapshot
    assert snapshot["rows"]["03- MS/01- FA/form.pdf"]["role"] == "submittal_form"
    assert snapshot["rows"]["05- Drawings/L01.pdf"]["records"][0][0] == "drawings"
    assert snapshot["rows"]["02- Material Submittals/FA/R0/Received/reply.pdf"]["records"][0][0] == "reply"
    assert snapshot["submittals"] == [("<REF>", "FAS", "under_review")]
    if not enabled:
        assert db_session.query(DocumentClassification).count() == 0, "off: nothing is classified"
        return
    assert snapshot == _SNAPSHOTS[False], "the feature changed a legacy output"
    classified = _classifications(db_session, project_id)
    assert set(classified) == set(snapshot["rows"]), "every indexed document has an assessment"
    processed = _processing_job(db_session, project_id)
    # Every row assessed once, the form twice (after its deterministic read and after the model's).
    assert processed.result.get("classified", 0) >= len(snapshot["rows"])


def test_what_each_document_of_the_folder_is_assessed_as(client, db_session, tmp_path, ai, classification_on):
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, _folder(tmp_path, "30903"), "30903")
    _sync(client, project_id)
    c = _classifications(db_session, project_id)

    form = c["03- MS/01- FA/form.pdf"]
    assert form.primary_type == "MATERIAL_SUBMITTAL" and form.stage == "supported" and form.evidence_strength == "strong"
    assert {"role", "records", "form_reading"} <= set(form.evidence_sources) and form.system_code == "FAS"
    drawing = c["05- Drawings/L01.pdf"]
    assert drawing.primary_type == "SHOP_DRAWING" and drawing.stage == "supported"
    assert any("title block" in e or "SDW" in e for e in drawing.evidence)
    ifc = c["00- IFC/IFC-L01.pdf"]
    assert ifc.primary_type == "IFC_DRAWING" and "SHOP_DRAWING" not in ifc.component_types  # TEST 6
    reply = c["02- Material Submittals/FA/R0/Received/reply.pdf"]
    assert reply.primary_type == "COMMENT_RESPONSE" and reply.stage == "supported"           # TEST 4
    assert "CONSULTANT_DECISION" in reply.component_types or reply.primary_type == "COMMENT_RESPONSE"
    spec = c["06- Specifications/28 31 00 Fire Alarm.pdf"]
    assert spec.primary_type == "SPECIFICATION" and spec.stage == "supported", "the section heading on the page is evidence"
    assert spec.assessment["basis"] == "content" and "page_evidence" in spec.evidence_sources
    assert any(p["kind"] == "specification" and p["page"] == 1 for p in spec.assessment["evidence_pages"])
    catalogue = c["09- Other/catalogue.pdf"]
    assert catalogue.primary_type == "DATASHEET" and catalogue.stage == "supported" and catalogue.evidence_strength == "moderate"
    assert any(p["kind"] == "catalogue" for p in catalogue.assessment["evidence_pages"])
    schedule = c["05- Drawings/schedule.pdf"]
    assert "DRAWING_SCHEDULE" in ([schedule.primary_type] + list(schedule.component_types))  # TEST 7
    assert schedule.primary_type != "SHOP_DRAWING"
    for entry in c.values():
        assert entry.rules_version == dc.RULES_VERSION and entry.evidence and entry.reason and entry.context_fingerprint
        assert entry.assessment["primary_type"] == entry.primary_type
    # Nothing the classification says made a domain record: the IFC drawing
    # and the schedule are not shop drawings, the reply is not a submittal.
    drawn = {d.drawing_reference for d in db_session.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project_id)}
    assert "BBY006-GME-DWG-EL-FA-0009" not in drawn and "FA 001" not in drawn
    assert {s.reference for s in db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project_id)} \
        <= {"BBY006-GME-MAS-EL-FA-0001"}
    listing = client.get(f"/projects/{project_id}/documents/classification").json()
    by_path = {row["path"]: row for row in listing}
    assert by_path["00- IFC/IFC-L01.pdf"]["classification"]["primary_type"] == "IFC_DRAWING"
    assert by_path["00- IFC/IFC-L01.pdf"]["classification"]["stale"] is False
    files = {f["path"]: f for f in client.get(f"/projects/{project_id}/documents/sync-files").json()}
    assert files["05- Drawings/L01.pdf"]["classification"]["primary_type"] == "SHOP_DRAWING"
    metrics = client.get(f"/projects/{project_id}/documents/classification/metrics").json()
    assert metrics["documents"] == 7 and metrics["assessed"] == 7 and metrics["content_supported"] >= 4


# --- TEST 3: the sync's hint opens nothing ---------------------------------------------------------


def test_the_sync_hint_reads_no_file_hashes_nothing_and_asks_no_model(client, db_session, tmp_path, ai, classification_on, monkeypatch):
    folder = _folder(tmp_path, "30904")
    project_id = _project(client, folder, "30904")
    project = db_session.get(Project, project_id)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the file sync did heavy work for the classification hint")

    monkeypatch.setattr(document_sync.document_control, "_read_pdf", must_not_run)
    monkeypatch.setattr(document_sync.document_control, "_open_pdf", must_not_run)
    monkeypatch.setattr(document_sync, "classify", must_not_run)
    monkeypatch.setattr(document_sync, "sha256_of", must_not_run)
    result = document_sync.sync(db_session, project)

    assert result["files"] == 7 and result["classification_hints"] == 7 and ai.calls == 0
    hints = _classifications(db_session, project_id)
    assert all(e.source == "hint" and e.stage in ("hint", "unknown") for e in hints.values())
    assert hints["05- Drawings/L01.pdf"].primary_type == "SHOP_DRAWING" and hints["05- Drawings/L01.pdf"].evidence_strength == "weak"
    assert hints["00- IFC/IFC-L01.pdf"].primary_type == "IFC_DRAWING"
    assert hints["03- MS/01- FA/form.pdf"].primary_type == "MATERIAL_SUBMITTAL"
    assert "CONSULTANT_DECISION" in hints["02- Material Submittals/FA/R0/Received/reply.pdf"].component_types
    assert _states(db_session, project_id) == {"pending": 7}, "routing untouched: every file still pending"


# --- TEST 5: a previous revision's decision does not become this revision's ------------------------


def test_a_decision_printed_on_a_resubmission_is_evidence_not_this_revisions_status(client, db_session, tmp_path, ai, classification_on):
    folder = tmp_path / "EP-30905"
    _submittal_form(folder / "02- Material Submittals" / "FA" / "R1" / "Submitted" / "form-r1.pdf", revision="01")
    reading = _reading("BBY006-GME-MAS-EL-FA-0001", 1, status="resubmit", code="C")
    reading["reply"]["evidence"] = "Consultant comments on Revision 0, dated 02.09.2026: revise and resubmit"
    ai.answers = [reading]
    project_id = _project(client, folder, "30905")
    _sync(client, project_id)

    row = _rows(db_session, project_id)["02- Material Submittals/FA/R1/Submitted/form-r1.pdf"]
    assert row.status == "UR", "the legacy rule: comments on an earlier revision are not this one's answer"
    entry = _classifications(db_session, project_id)["02- Material Submittals/FA/R1/Submitted/form-r1.pdf"]
    assert entry.primary_type == "MATERIAL_SUBMITTAL"
    assert "CONSULTANT_DECISION" in entry.component_types, "the decision is a component, reported as evidence"
    assert any("register decides" in e for e in entry.evidence)


# --- TEST 9: the same content in two folders -------------------------------------------------------


def test_duplicate_content_keeps_the_legacy_reading_and_gets_its_own_context(client, db_session, tmp_path, ai, classification_on):
    # The same bytes under Submitted and under Tender. (The index's reuse of
    # a stored reading for a copy that arrives in a later sync is a legacy
    # path this task leaves as it is; its processing job currently fails on
    # a missing key -- reported apart, not fixed here -- so both copies are
    # indexed in one sync and read alike.)
    folder = tmp_path / "EP-30906"
    first = _pdf(folder / "05- Drawings" / "Submitted" / "L01.pdf", DRAWING)
    copy = folder / "01- Tender" / "L01.pdf"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(first.read_bytes())
    project_id = _project(client, folder, "30906")
    _sync(client, project_id)

    rows = _rows(db_session, project_id)
    assert rows["05- Drawings/Submitted/L01.pdf"].sha256 == rows["01- Tender/L01.pdf"].sha256
    assert (rows["05- Drawings/Submitted/L01.pdf"].extracted["records"][0]["reference"]
            == rows["01- Tender/L01.pdf"].extracted["records"][0]["reference"]), "the same content reads the same"
    c = _classifications(db_session, project_id)
    assert c["05- Drawings/Submitted/L01.pdf"].primary_type == "SHOP_DRAWING"
    assert c["01- Tender/L01.pdf"].primary_type != "SHOP_DRAWING"
    assert any("tender" in e for e in c["01- Tender/L01.pdf"].evidence)
    assert c["05- Drawings/Submitted/L01.pdf"].context_fingerprint != c["01- Tender/L01.pdf"].context_fingerprint
    assert c["05- Drawings/Submitted/L01.pdf"].content_sha256 == c["01- Tender/L01.pdf"].content_sha256


# --- TEST 10: conflicting evidence ------------------------------------------------------------------


def test_a_form_filed_among_specifications_is_ambiguous_and_its_role_unchanged(client, db_session, tmp_path, ai, classification_on):
    folder = tmp_path / "EP-30907"
    _submittal_form(folder / "06- Specifications" / "28 31 00 form.pdf")
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    project_id = _project(client, folder, "30907")
    _sync(client, project_id)

    row = _rows(db_session, project_id)["06- Specifications/28 31 00 form.pdf"]
    assert row.role == "submittal_form", "the legacy role decision stands"
    entry = _classifications(db_session, project_id)["06- Specifications/28 31 00 form.pdf"]
    assert entry.stage == "ambiguous" and entry.evidence_strength == "conflicting"
    assert entry.primary_type == "MATERIAL_SUBMITTAL" and "SPECIFICATION" in entry.component_types
    assert any("disagree" in e or "suggested" in e for e in entry.evidence)


# --- TEST 11: classification raising does not fail the processing ------------------------------------


def test_a_classification_error_leaves_the_processing_successful(client, db_session, tmp_path, ai, classification_on, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("classification exploded")

    monkeypatch.setattr(dc, "assess", broken)
    folder = tmp_path / "EP-30908"
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    project_id = _project(client, folder, "30908")
    _sync(client, project_id)
    assert _states(db_session, project_id) == {"fresh": 1}
    assert _processing_job(db_session, project_id).result["processed"] == 1
    row = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert row.extracted["records"][0]["reference"] == "BBY006-GME-SDW-EL-FA-0001"
    # The hint from the sync is there; the failed assessment left it as it was.
    assert _classifications(db_session, project_id)["05- Drawings/L01.pdf"].source == "hint"


# --- TEST 12 / TEST 18: backfill from stored data marks nothing stale ----------------------------------


def test_backfill_uses_stored_data_only_and_marks_nothing_stale(client, db_session, tmp_path, ai, monkeypatch):
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    monkeypatch.setattr(settings, "document_classification_v2", False)
    project_id = _project(client, _folder(tmp_path, "30909"), "30909")
    _sync(client, project_id)
    assert db_session.query(DocumentClassification).count() == 0
    before = _snapshot(db_session, project_id)
    stamps = {d.id: d.updated_at for d in db_session.query(DocumentDependency).filter(DocumentDependency.project_id == project_id)}

    monkeypatch.setattr(settings, "document_classification_v2", True)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the backfill touched a file or a model")

    monkeypatch.setattr(document_sync.document_control, "_open_pdf", must_not_run)
    monkeypatch.setattr(document_sync.document_control, "_read_pdf", must_not_run)
    import pymupdf

    monkeypatch.setattr(pymupdf, "open", must_not_run)
    calls_before = ai.calls
    job = client.post(f"/projects/{project_id}/jobs/classify-documents")
    assert job.status_code == 202 and job.json()["status"] == "succeeded", job.text
    assert job.json()["result"]["assessed"] == 7 and job.json()["result"]["failed"] == 0 and ai.calls == calls_before

    assert _snapshot(db_session, project_id) == before, "the backfill changed a legacy output"
    db_session.expire_all()
    assert {d.id: d.updated_at for d in db_session.query(DocumentDependency).filter(DocumentDependency.project_id == project_id)} == stamps
    assert client.get(f"/projects/{project_id}/documents/status").json()["stale"] == [], "nothing went stale"
    c = _classifications(db_session, project_id)
    assert all(e.source == "backfill" for e in c.values())
    assert c["03- MS/01- FA/form.pdf"].primary_type == "MATERIAL_SUBMITTAL" and c["03- MS/01- FA/form.pdf"].stage == "supported"
    # Run again: everything is current, nothing is written twice.
    again = client.post(f"/projects/{project_id}/jobs/classify-documents").json()
    assert again["result"]["skipped"] == 7 and again["result"]["assessed"] == 0


# --- TEST 13 / TEST 14: the DRF and the Design Sheets keep their own workflows ------------------------


def test_the_drf_and_the_design_sheets_are_classified_from_their_intake_association_only(client, db_session, tmp_path, ai,
                                                                                            classification_on, monkeypatch):
    from .conftest import login

    folder = tmp_path / "EP-30910"
    drf = _pdf(folder / "Scan" / "EP-30910 DRF.pdf", "Design Request Form")
    sheet = _pdf(folder / "Scan" / "EP-30910 FAS Design.pdf", "FAS Design Sheet")
    _pdf(folder / "05- Drawings" / "L01.pdf", DRAWING)
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={
        "ep_number": "30910", "project_name": "Skyblade", "source_folder_path": str(folder),
        "drf_document_path": str(drf), "design_sheets": [{"system_code": "FAS", "document_path": str(sheet)}],
    }).json()["id"]

    def must_not_read_a_sheet(*_args, **_kwargs):
        raise AssertionError("a Design Sheet went through general document processing")

    from app.ai import sheet_reader

    monkeypatch.setattr(sheet_reader, "read_design_sheet", must_not_read_a_sheet)
    _sync(client, project_id)

    rows = _by_name(db_session, project_id)
    sheet_key = next(k for k in rows if k.endswith("FAS Design.pdf"))
    drf_key = next(k for k in rows if k.endswith("DRF.pdf"))
    assert rows[sheet_key].role == "design_sheet" and rows[sheet_key].extracted is None
    assert rows[drf_key].role == "drf" and rows[drf_key].extracted is None
    c = _classifications(db_session, project_id)
    assert c[sheet_key].primary_type == "DESIGN_SHEET" and c[sheet_key].system_code == "FAS"
    assert c[sheet_key].evidence_sources == ["intake_association"] and c[sheet_key].evidence_strength == "strong"
    assert c[sheet_key].assessment["basis"] == "intake_association", "the association, named as such; never content"
    assert c[drf_key].primary_type == "DRF" and c[drf_key].evidence_sources == ["intake_association"]
    assert c["05- Drawings/L01.pdf"].primary_type == "SHOP_DRAWING"


# --- TEST 17: off again, with metadata stored -----------------------------------------------------------


def test_turning_the_feature_off_leaves_the_legacy_path_working_and_the_metadata_stored(client, db_session, tmp_path, ai, monkeypatch):
    ai.answers = [_reading("BBY006-GME-MAS-EL-FA-0001", 0)]
    monkeypatch.setattr(settings, "document_classification_v2", True)
    project_id = _project(client, _folder(tmp_path, "30911"), "30911")
    _sync(client, project_id)
    stored = db_session.query(DocumentClassification).filter(DocumentClassification.project_id == project_id).count()
    assert len(_classifications(db_session, project_id)) == 7, "one current assessment per document; the hints are history"
    before = _snapshot(db_session, project_id)

    monkeypatch.setattr(settings, "document_classification_v2", False)
    result = _sync(client, project_id)
    assert result["result"]["unchanged"] == 7 and "classification_hints" not in result["result"]
    assert _snapshot(db_session, project_id) == before
    files = client.get(f"/projects/{project_id}/documents/sync-files").json()
    assert all(f["classification"] is None for f in files), "off: the listing shows no classification"
    assert db_session.query(DocumentClassification).filter(DocumentClassification.project_id == project_id).count() == stored
    assert client.post(f"/projects/{project_id}/jobs/classify-documents").status_code == 409


# --- the rules on their own -------------------------------------------------------------------------------


def _row(relative: str, role: str = "document", records=None, form=None, sha="abc", notes=None) -> SimpleNamespace:
    extracted = None if records is None and form is None and notes is None else {"records": records or [], "form": form,
                                                                                    "notes": notes or []}
    return SimpleNamespace(id=1, relative_path=relative, filename=relative.rsplit("/", 1)[-1], role=role, extracted=extracted,
                           sha256=sha, system_code=None, state="fresh")


def test_the_hint_is_a_hint_and_says_where_it_came_from():
    h = dc.hint("04- Drawings/08-Shop Drawing/1.FAVE/R1/BBY006-GME-SDW-FP-FA-BSM-B01-010001.pdf", "x.pdf", "document")
    assert h.primary_type == dc.DocumentType.SHOP_DRAWING and h.stage == dc.Stage.HINT and h.strength == dc.Strength.WEAK
    assert h.system_code == "FAS" and h.evidence_sources == ["path"]
    ifc = dc.hint("04- Drawings/01- IFC/FA-L01.pdf", "FA-L01.pdf", "document")
    assert ifc.primary_type == dc.DocumentType.IFC_DRAWING
    tender = dc.hint("00- Tender/drawings/L01.pdf", "L01.pdf", "document")
    assert tender.primary_type != dc.DocumentType.SHOP_DRAWING and any("tender" in e for e in tender.evidence)
    nothing = dc.hint("misc/x.pdf", "x.pdf", "document")
    assert nothing.primary_type == dc.DocumentType.UNKNOWN and nothing.stage == dc.Stage.UNKNOWN
    sheet = dc.hint("Scan/EP-1 FAS Design.pdf", "EP-1 FAS Design.pdf", "design_sheet", intake_role="design_sheet", intake_system="FAS")
    assert sheet.primary_type == dc.DocumentType.DESIGN_SHEET and sheet.strength == dc.Strength.STRONG and sheet.system_code == "FAS"


def test_the_assessment_needs_content_to_rise_above_a_hint_and_keeps_components():
    unprocessed = dc.assess(_row("05- Drawings/L01.pdf"))
    assert unprocessed.stage == dc.Stage.HINT
    drawing = dc.assess(_row("05- Drawings/L01.pdf", records=[{"category": "drawings", "source": "document", "status": "UR",
                                                                "system_code": "FAS"}]))
    assert drawing.primary_type == dc.DocumentType.SHOP_DRAWING and drawing.stage == dc.Stage.SUPPORTED and drawing.system_code == "FAS"
    stamped = dc.assess(_row("05- Drawings/R1/Received/L01.pdf", records=[{"category": "drawings", "source": "document", "status": "ANN"}]))
    assert stamped.primary_type == dc.DocumentType.SHOP_DRAWING and dc.DocumentType.CONSULTANT_DECISION in stamped.component_types
    mixed = dc.assess(_row("03- MS/pack.pdf", role="submittal_form",
                           records=[{"category": "submittals", "source": "document", "status": "UR"},
                                    {"category": "reply", "source": "reply", "status": "ANN"}],
                           form={"is_submittal": True, "reference": "X-MAS-1", "revision": 0, "system_code": "FAS",
                                 "reply": {"present": False}}))
    assert mixed.primary_type == dc.DocumentType.MATERIAL_SUBMITTAL and mixed.strength == dc.Strength.STRONG
    assert {dc.DocumentType.COMMENT_RESPONSE, dc.DocumentType.CONSULTANT_DECISION} <= set(mixed.component_types)
    two_kinds = dc.assess(_row("x/pack.pdf", records=[{"category": "submittals", "source": "document", "status": "UR"},
                                                       {"category": "drawings", "source": "document", "status": "UR"}]))
    assert two_kinds.stage == dc.Stage.AMBIGUOUS and two_kinds.strength == dc.Strength.CONFLICTING
    unread = dc.assess(_row("x/online.pdf", notes=["online.pdf is not downloaded from OneDrive; make the project folder available offline, then refresh."]))
    assert unread.stage == dc.Stage.UNKNOWN and any("online-only" in e for e in unread.evidence)
    assert {"MATERIAL_SUBMITTAL": "content", "COMMENT_RESPONSE": "content", "CONSULTANT_DECISION": "content"} == mixed.component_support
    assert mixed.basis == dc.Basis.CONTENT


def test_an_assessment_is_stale_when_the_content_or_the_context_changed():
    row = _row("05- Drawings/L01.pdf", sha="one")
    project = SimpleNamespace(id=1, ep_number="30912")
    fingerprint = dc.context_fingerprint(row, project)
    entry = SimpleNamespace(rules_version=dc.RULES_VERSION, context_fingerprint=fingerprint, content_sha256="one",
                            assessment={"source_state": "fresh"})
    assert dc.is_current(entry, row, fingerprint)
    row.sha256 = "two"
    assert not dc.is_current(entry, row, fingerprint)
    row.sha256 = "one"
    moved = _row("01- Tender/L01.pdf", sha="one")
    assert dc.context_fingerprint(moved, project) != fingerprint
    assert not dc.is_current(entry, moved, dc.context_fingerprint(moved, project))
