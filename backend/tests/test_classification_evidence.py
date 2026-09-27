"""Classification rules classify-2026-09-28.1 on the shapes of the
confirmed audit cases, as synthetic rows: page evidence is content, a
negative form reading is negative evidence, the file name outranks the
folder, a date record is not evidence, a printed project code that differs
is flagged, a consultant's sheet is not a contractor's reply, a receipt is
not an approval, and the new kinds are named. No project file is read."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import document_classification as dc
from app.services.content_evidence import EVIDENCE_VERSION
from app.services.document_control import PARSER_VERSION

T = dc.DocumentType


def _finding(kind: str, page: int = 1, excerpt: str = "…", method: str = "text", rule: str | None = None,
             grade: str = "strong") -> dict:
    return {"kind": kind, "page": page, "excerpt": excerpt, "method": method, "rule": rule or kind, "grade": grade,
            "version": EVIDENCE_VERSION, "content_sha256": "sha"}


def _row(relative: str, role: str = "document", *, records=None, form=None, findings=None, pages=1, parser=PARSER_VERSION,
         state="fresh") -> SimpleNamespace:
    extracted = {"records": records or [], "notes": [], "form": form, "parser_version": parser,
                 "evidence": {"version": EVIDENCE_VERSION, "pages_read": min(pages, 3), "page_count": pages,
                              "scanned_pages": [], "unread_pages": [], "findings": findings or []}}
    return SimpleNamespace(id=1, relative_path=relative, filename=relative.rsplit("/", 1)[-1], role=role, extracted=extracted,
                           sha256="sha", system_code=None, state=state)


# --- F3: attachments in a submittal package keep their own identity --------------------------------


def test_a_datasheet_in_a_submittal_package_is_a_datasheet_not_the_package():
    row = _row("06. MS/EML/Revised/PDF/Data Sheet.pdf", role="submittal_form", form={"is_submittal": False},
               findings=[_finding("datasheet", page=2, excerpt="EATON CGLine+ Web Compact Controller Datasheet")], pages=16)
    a = dc.assess(row, project_ep="30088")
    assert a.primary_type == T.DATASHEET and a.stage == dc.Stage.SUPPORTED and a.basis == dc.Basis.CONTENT
    assert T.MATERIAL_SUBMITTAL in a.component_types and a.component_support["MATERIAL_SUBMITTAL"] == "metadata"
    assert any("found no material submittal form" in e for e in a.evidence)
    assert not any("reader found a material submittal form" in e for e in a.evidence)
    assert any(p["kind"] == "datasheet" and p["page"] == 2 for p in a.evidence_pages)
    # The hint alone, before the pages were read: the name outranks the folder and the role.
    h = dc.hint("06. MS/EML/Revised/PDF/Data Sheet.pdf", "Data Sheet.pdf", "submittal_form")
    assert h.primary_type == T.DATASHEET and h.stage == dc.Stage.HINT and T.MATERIAL_SUBMITTAL in h.component_types


def test_a_certificate_and_a_catalogue_under_ms_are_what_their_pages_say():
    cert = dc.assess(_row("06. MS/EML/Revised/PDF/Test Certificate.pdf",
                          findings=[_finding("certificate", excerpt="DEKRA hereby declares ... ENEC certification")], pages=42))
    assert cert.primary_type == T.CERTIFICATE and cert.stage == dc.Stage.SUPPORTED and T.MATERIAL_SUBMITTAL in cert.component_types
    catalogue = dc.assess(_row("06. MS/FA/Revised/PDF/Product Catalog.pdf", role="submittal_form",
                               findings=[_finding("catalogue", page=2, method="ocr")], pages=8))
    assert catalogue.primary_type == T.DATASHEET and catalogue.stage == dc.Stage.SUPPORTED
    # 190 pages, two read: the answer says how much was looked at.
    big = dc.assess(_row("06. MS/FA/SOM/DATA SHEET/data sheet.pdf", role="submittal_form", findings=[], pages=190))
    assert big.primary_type == T.DATASHEET and big.stage == dc.Stage.HINT and any("of 190 pages" in e for e in big.evidence)


def test_a_negative_form_reading_with_nothing_else_is_unknown_not_a_submittal():
    row = _row("06. MS/EML/COO.pdf", role="submittal_form", form={"is_submittal": False}, findings=[_finding("project_code", rule="EP-30058")])
    a = dc.assess(row, project_ep="30088")
    assert a.primary_type == T.UNKNOWN and a.stage == dc.Stage.UNKNOWN and T.MATERIAL_SUBMITTAL in a.component_types
    assert any("EP-30058" in f and "EP-30088" in f for f in a.flags)


def test_a_real_submittal_package_stays_a_package_with_its_attachments_as_components():
    row = _row("06. MS/EML/R0/EP-1 - Material Submittal - EML - R0.pdf", role="submittal_form",
               records=[{"category": "submittals", "source": "document", "status": "UR", "reference": "X-MAS-1"}],
               form={"is_submittal": True, "reference": "X-MAS-1", "revision": 0, "system_code": "ELS", "reply": {"present": False}},
               findings=[_finding("material_submittal"), _finding("datasheet", page=3), _finding("certificate", page=3)])
    a = dc.assess(row)
    assert a.primary_type == T.MATERIAL_SUBMITTAL and a.stage == dc.Stage.SUPPORTED and a.strength == dc.Strength.STRONG
    assert {T.DATASHEET, T.CERTIFICATE} <= set(a.component_types)
    assert a.component_support["DATASHEET"] == "content" and a.component_support["CERTIFICATE"] == "content"


# --- F4: general documents are named from their pages -------------------------------------------------


def test_general_documents_are_named_from_their_pages():
    iso = dc.assess(_row("07. PQ/PQ -R1/6. ISO DXB-.pdf", findings=[_finding("certificate", excerpt="Certificate of Approval ISO 9001:2015")]))
    assert iso.primary_type == T.CERTIFICATE and iso.stage == dc.Stage.SUPPORTED and iso.strength == dc.Strength.MODERATE
    schedule = dc.assess(_row("08. RECEIVED/ELECTRICAL/LOAD SCHEDULE.pdf", role="spec", findings=[_finding("load_schedule")]))
    assert schedule.primary_type == T.LOAD_SCHEDULE and schedule.stage == dc.Stage.SUPPORTED
    assert any("legacy role is spec" in f for f in schedule.flags), "the role stands; the disagreement is said"
    vendors = dc.assess(_row("08. RECEIVED/Vendor List - Project.pdf", role="spec", findings=[_finding("vendor_list")]))
    assert vendors.primary_type == T.VENDOR_LIST and vendors.stage == dc.Stage.SUPPORTED
    matrix = dc.assess(_row("08. RECEIVED/Fire & Life Safety Scope of Matrix - Rev 2.pdf", role="spec", findings=[_finding("scope_matrix")]))
    assert matrix.primary_type == T.SCOPE_MATRIX and matrix.stage == dc.Stage.SUPPORTED
    diagram = dc.assess(_row("08. RECEIVED/ELECTRICAL/SCHEMATIC DIAGRAM/PDF/SINGLE LINE DIAGRAM.pdf", findings=[_finding("single_line_diagram")]))
    assert diagram.primary_type == T.DRAWING and diagram.stage == dc.Stage.SUPPORTED
    assert T.SHOP_DRAWING not in a_types(diagram) and T.IFC_DRAWING not in a_types(diagram), "provenance is not established by a drawing alone"
    specs = dc.assess(_row("08. RECEIVED/01. HYDRAULICS SPECS.pdf", role="spec", findings=[_finding("specification")]))
    assert specs.primary_type == T.SPECIFICATION and specs.stage == dc.Stage.SUPPORTED and not specs.flags
    nothing = dc.assess(_row("09. Scan/unnamed.pdf", findings=[]))
    assert nothing.primary_type == T.UNKNOWN and nothing.stage == dc.Stage.UNKNOWN


def a_types(assessment: dc.Assessment) -> set:
    return {assessment.primary_type, *assessment.component_types}


def test_a_scanned_transmittal_with_a_receipt_is_a_transmittal_not_an_approval():
    row = _row("09. Scan Document/EP-30088 CBS Sam B ack 13.08.26.pdf",
               findings=[_finding("transmittal", method="ocr", excerpt="DOCUMENT TRANSMITAL"), _finding("sample_submittal", method="ocr"),
                         _finding("receipt", method="ocr"), _finding("project_code", method="ocr", rule="EP-30058")])
    a = dc.assess(row, project_ep="30088")
    assert a.primary_type == T.TRANSMITTAL and a.stage == dc.Stage.SUPPORTED and a.basis == dc.Basis.CONTENT
    assert T.SAMPLE_APPROVAL in a.component_types and T.CONSULTANT_DECISION not in a_types(a)
    assert any("proof of delivery, not of approval" in e for e in a.evidence)
    assert any("prints project code EP-30058" in f for f in a.flags)
    assert "page_evidence" in a.evidence_sources


# --- F5 / F7: decisions, comments and replies stay apart ---------------------------------------------------


def test_a_consultants_comments_sheet_is_a_decision_document_not_a_contractor_reply():
    row = _row("07. PQ/PQ -R1/Consults Comments.pdf",
               findings=[_finding("consultant_comments", excerpt="ADDITIONAL COMMENTS SHEET"), _finding("decision_status", excerpt="Status-C"),
                         _finding("author_consultant")])
    a = dc.assess(row, project_ep="30088")
    assert a.primary_type == T.CONSULTANT_DECISION and a.stage == dc.Stage.SUPPORTED
    assert T.COMMENT_RESPONSE not in a_types(a) or a.primary_type != T.COMMENT_RESPONSE
    entry = SimpleNamespace(stage="supported", evidence_strength="moderate", primary_type="CONSULTANT_DECISION",
                            assessment={"basis": "content", "flags": []})
    assert any("for a person; nothing is applied" in r for r in dc.review_reasons(entry, row, dc.CURRENT))


def test_a_contractors_reply_stays_a_reply_and_a_framed_decision_is_a_component():
    reply = _row("06. MS/EML/R1/Reply to MS Consultant Comments.pdf", role="spec",
                 records=[{"category": "reply", "source": "reply", "status": "UR", "reference": "X-MAR-MEP-0109"}],
                 findings=[_finding("contractor_reply"), _finding("author_contractor"), _finding("consultant_comments")])
    a = dc.assess(reply)
    assert a.primary_type == T.COMMENT_RESPONSE and a.stage == dc.Stage.SUPPORTED
    assert T.CONSULTANT_DECISION not in a_types(a), "the comments a reply quotes are not a second decision document"
    cover = _row("3. SHOP DRAWING/FIRE ALARM/X-SD-MEP-FA-0054-01-COMMENTED-C.pdf",
                 records=[{"category": "drawings", "source": "document", "status": "rejected", "reference": "X-SD-MEP-FA-0054",
                           "revision": "R1", "system_code": "FAS", "listed": ["X-SD-MEP/FA-104 A~104 M"]}],
                 findings=[_finding("shop_drawing_submittal"), _finding("author_consultant")])
    a = dc.assess(cover)
    assert a.primary_type == T.SHOP_DRAWING and a.stage == dc.Stage.SUPPORTED and a.strength == dc.Strength.STRONG
    assert T.CONSULTANT_DECISION in a.component_types and T.MATERIAL_SUBMITTAL not in a_types(a)


# --- F1 / F2 as the classification sees them ----------------------------------------------------------------


def test_a_record_whose_reference_is_a_date_is_not_evidence_and_is_flagged():
    row = _row("3. SHOP DRAWING/FIRE ALARM/X-SD-MEP-FA-0054-01-COMMENTED-C.pdf",
               records=[{"category": "submittals", "source": "document", "status": "UR", "reference": "6-Mar-2026", "revision": "R1"}],
               findings=[_finding("shop_drawing_submittal")], parser="parse-earlier")
    a = dc.assess(row)
    assert a.primary_type == T.SHOP_DRAWING and T.MATERIAL_SUBMITTAL not in a_types(a)
    assert any("reference is a date" in f for f in a.flags) and any("earlier parser" in f for f in a.flags)
    assert "records" not in a.evidence_sources


def test_a_name_the_pages_contradict_is_ambiguous_with_a_source_flag():
    row = _row("06. MS/EML/COMPLIANCE STATEMENT.pdf",
               findings=[_finding("specification", excerpt="265200- EMERGENCY LIGHTING PART 1 GENERAL")], pages=33)
    a = dc.assess(row)
    assert a.stage == dc.Stage.AMBIGUOUS and a.strength == dc.Strength.CONFLICTING
    assert a.primary_type == T.SPECIFICATION and T.COMPLIANCE_STATEMENT in a.component_types
    assert any("compliance statement" in f and "specification" in f for f in a.flags)


def test_two_kinds_of_page_content_are_ambiguous_unless_one_is_the_others_attachment():
    mixed = dc.assess(_row("x/pack.pdf", findings=[_finding("load_schedule"), _finding("vendor_list")]))
    assert mixed.stage == dc.Stage.AMBIGUOUS, "two reference kinds named on the same first page"
    later = dc.assess(_row("x/pack.pdf", findings=[_finding("load_schedule"), _finding("vendor_list", page=2)]))
    assert later.primary_type == T.LOAD_SCHEDULE and later.stage == dc.Stage.SUPPORTED, "the first page's kind stands over a later page's"
    titled = dc.assess(_row("x/spec.pdf", findings=[_finding("specification", grade="title"), _finding("datasheet", page=3)]))
    assert titled.primary_type == T.SPECIFICATION and titled.stage == dc.Stage.SUPPORTED
    sheet = dc.assess(_row("05- Drawings/L01.pdf", records=[{"category": "drawings", "source": "document", "status": "UR"}],
                           findings=[_finding("material_submittal"), _finding("certificate")]))
    assert sheet.primary_type == T.SHOP_DRAWING and sheet.stage == dc.Stage.SUPPORTED, "the records decide; the pages' words are components"
    assert {T.MATERIAL_SUBMITTAL, T.CERTIFICATE} <= set(sheet.component_types)
    package = dc.assess(_row("06. MS/pack.pdf", findings=[_finding("material_submittal"), _finding("datasheet", page=2)]))
    assert package.primary_type == T.MATERIAL_SUBMITTAL and package.stage == dc.Stage.SUPPORTED


def test_metadata_only_types_and_the_intake_association_are_unchanged():
    assert dc.assess(_row("x/Design Sheet.pdf", records=[])).stage == dc.Stage.HINT
    drf = dc.assess(_row("Scan/DRF.pdf", role="drf"), intake_role="drf")
    assert drf.basis == dc.Basis.INTAKE_ASSOCIATION and drf.stage == dc.Stage.SUPPORTED
    pending = dc.assess(_row("05- Drawings/L01.pdf", records=[{"category": "drawings", "source": "document", "status": "UR"}], state="pending"))
    assert pending.stage == dc.Stage.HINT and pending.basis in (dc.Basis.METADATA, dc.Basis.NONE)
    assert all(t.value in {m.value for m in T} for t in (T.LOAD_SCHEDULE, T.VENDOR_LIST, T.SCOPE_MATRIX, T.COMPLIANCE_STATEMENT, T.DRAWING))
    assert dc.RULES_VERSION == "classify-2026-09-28.4"
