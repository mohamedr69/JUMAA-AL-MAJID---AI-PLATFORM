"""Document classification, alongside the index -- explainable metadata,
never a business decision (Document Classification & Routing V2, the
backward-compatible foundation of 2026-09-27; the pilot hardening of the
same day, rules `classify-2026-09-27.2`).

What a file *appears to contain* is a different question from which
reader handles it (`ProjectDocument.role`), from what each record inside
it is (`extracted["records"][*]["category"]`) and from the business state
the registers hold. This module answers the first question only, from two
kinds of evidence, and writes its answer beside the row:

  hint        from what the file sync already has in hand -- the relative
              path, the file name, the role the row carries, the intake
              association (the DRF, a Design Sheet) -- with nothing opened,
              hashed or asked. A hint is a guess and says so
              (`stage = "hint"`).
  assessment  from what document processing stored -- the records the page
              gave, the model's reading of a form, the notes -- laid against
              the hint. Content that agrees with the path is `supported`;
              content and path that disagree, or two kinds of content in
              one file, are `ambiguous`; nothing to go on is `unknown`.

Every answer says what it rests on (`basis`): the intake association
(the project's own DRF or Design Sheet: authoritative, read nowhere
here), metadata (path words, the file name, the legacy role: a hint and
never more), or content (records and readings the processing stored).
Metadata never reads as content: a file kept among specifications with
nothing read off its pages is a *possible* specification, not a supported
one. Each component of a mixed file carries its own basis
(`component_support`).

Every answer carries the evidence it rests on, in words, the sources it
came from, its strength (STRONG / MODERATE / WEAK / CONFLICTING / UNKNOWN
-- no invented percentages), the rules version, the content hash, the
row's processing state when it was made and a context fingerprint. A
file's context -- where it is filed, what it is associated with -- is
part of the answer: the same bytes under Tender and under Submitted are
two documents here, though the index rightly reuses one reading for both.
An answer whose content, processing state or context has since changed
is not current (`freshness`), and is reported as such rather than as
current: a changed file waiting to be read keeps its previous reading on
the row meanwhile, and that reading is no evidence for the new content.

Nothing here changes a role, a state, a record, a status, a revision, a
dependency or a register. Classification failing is logged and leaves
the row without an answer; it never fails the sync or the processing:
every write goes through a savepoint of its own, so a failed write leaves
the caller's session usable and the caller's own writes untouched.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.models import DocumentClassification, Project, ProjectDocument

log = logging.getLogger(__name__)

# Bumped when a rule changes; never INDEX_VERSION's job (a bump here
# makes nothing be read again; a backfill re-assesses from stored data).
#   .1  2026-09-27  the foundation
#   .2  2026-09-27  metadata is never content; the stored reading is
#                   evidence only for a fresh row; a name-only DRF /
#                   Design Sheet hint does not bypass the content; the
#                   basis and the processing state travel with the answer
#   2026-09-28.1    page evidence (app.services.content_evidence) is content;
#                   a negative form reading is negative evidence; the file
#                   name outranks the folder; a record whose reference is a
#                   date is not evidence; a project code printed on the page
#                   is compared with the project; five kinds of document the
#                   taxonomy could not name (load schedule, vendor list,
#                   scope matrix, compliance statement, a drawing of unknown
#                   provenance)
#   2026-09-28.2    page findings are graded (title / strong / weak): a weak
#                   cue names no type; reference kinds (datasheet, certificate,
#                   schedule, specification, drawing) are a controlled
#                   document's components, never a second kind of it
#   2026-09-28.3    without a title, a reference kind named on page 1 stands
#                   over one named on a later page
#   2026-09-28.4    the readers' records decide the kind and the primary
#                   type; what the pages add is a component; a kind the
#                   primary is compatible with is a component
RULES_VERSION = "classify-2026-09-28.4"

# The processing states in which the row's stored reading is the reading
# of the file as it is now. A pending or processing row is a changed file
# whose previous reading is still on the row; a failed row's reading may be
# of the previous content too (the previous result stays when a read
# fails). Their stored reading is not evidence for the current content.
VERIFIED_STATES = ("fresh",)
# Roles whose documents drive a workflow: one of these assessed from its
# path and role alone is worth a person's look.
WORKFLOW_ROLES = ("submittal_form", "transmittal", "spec")


class DocumentType(str, enum.Enum):
    """What a file appears to contain. A type says nothing about a system
    (that is `system_code`, apart) nor about a status."""

    DRF = "DRF"
    DESIGN_SHEET = "DESIGN_SHEET"
    IFC_DRAWING = "IFC_DRAWING"
    SHOP_DRAWING = "SHOP_DRAWING"
    DRAWING_SCHEDULE = "DRAWING_SCHEDULE"     # added: the code reads schedules as their own source
    MATERIAL_SUBMITTAL = "MATERIAL_SUBMITTAL"
    SAMPLE_APPROVAL = "SAMPLE_APPROVAL"
    SPECIFICATION = "SPECIFICATION"
    DATASHEET = "DATASHEET"
    TRANSMITTAL = "TRANSMITTAL"
    CERTIFICATE = "CERTIFICATE"
    COMMENT_RESPONSE = "COMMENT_RESPONSE"
    CONSULTANT_DECISION = "CONSULTANT_DECISION"
    # Added 2026-09-28: kinds the pages are read as that the types above
    # could not name faithfully. A DRAWING is a drawing whose provenance --
    # ours (SHOP_DRAWING) or given to us (IFC_DRAWING) -- the evidence does
    # not establish.
    LOAD_SCHEDULE = "LOAD_SCHEDULE"
    VENDOR_LIST = "VENDOR_LIST"
    SCOPE_MATRIX = "SCOPE_MATRIX"
    COMPLIANCE_STATEMENT = "COMPLIANCE_STATEMENT"
    DRAWING = "DRAWING"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Stage(str, enum.Enum):
    HINT = "hint"
    SUPPORTED = "supported"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class Strength(str, enum.Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class Basis(str, enum.Enum):
    """What an answer rests on. Only `content` can support a type; the
    intake association is authoritative for the DRF and the Design Sheets
    and says so by name; metadata is a hint."""

    INTAKE_ASSOCIATION = "intake_association"
    METADATA = "metadata"
    CONTENT = "content"
    NONE = "none"


# Types the code can support from content today. A type not here can be
# hinted from a path (DATASHEET, CERTIFICATE) but never rises above a hint.
CONTENT_SUPPORTED = frozenset({
    DocumentType.DRF, DocumentType.DESIGN_SHEET, DocumentType.IFC_DRAWING, DocumentType.SHOP_DRAWING,
    DocumentType.DRAWING_SCHEDULE, DocumentType.MATERIAL_SUBMITTAL, DocumentType.SAMPLE_APPROVAL,
    DocumentType.TRANSMITTAL, DocumentType.COMMENT_RESPONSE, DocumentType.CONSULTANT_DECISION,
    DocumentType.SPECIFICATION, DocumentType.DATASHEET, DocumentType.CERTIFICATE, DocumentType.LOAD_SCHEDULE,
    DocumentType.VENDOR_LIST, DocumentType.SCOPE_MATRIX, DocumentType.COMPLIANCE_STATEMENT, DocumentType.DRAWING,
})
# What a page's evidence (content_evidence.Finding.kind) says the document
# is. Kinds not here (receipt, decision_status, project_code, author_*) are
# evidence *about* a document, not of its kind.
_EVIDENCE_TYPES: dict[str, DocumentType] = {
    "shop_drawing_submittal": DocumentType.SHOP_DRAWING, "material_submittal": DocumentType.MATERIAL_SUBMITTAL,
    "sample_submittal": DocumentType.SAMPLE_APPROVAL, "transmittal": DocumentType.TRANSMITTAL,
    "consultant_comments": DocumentType.CONSULTANT_DECISION, "contractor_reply": DocumentType.COMMENT_RESPONSE,
    "datasheet": DocumentType.DATASHEET, "catalogue": DocumentType.DATASHEET, "certificate": DocumentType.CERTIFICATE,
    "load_schedule": DocumentType.LOAD_SCHEDULE, "vendor_list": DocumentType.VENDOR_LIST,
    "scope_matrix": DocumentType.SCOPE_MATRIX, "compliance_statement": DocumentType.COMPLIANCE_STATEMENT,
    "specification": DocumentType.SPECIFICATION, "single_line_diagram": DocumentType.DRAWING,
    "drawing_title_block": DocumentType.DRAWING, "design_request_form": DocumentType.DRF,
}
# The order page evidence is weighed in when several kinds are found: the
# controlled forms first, then what is attached to them, then reference material.
_EVIDENCE_ORDER = (DocumentType.SHOP_DRAWING, DocumentType.MATERIAL_SUBMITTAL, DocumentType.SAMPLE_APPROVAL,
                   DocumentType.TRANSMITTAL, DocumentType.COMMENT_RESPONSE, DocumentType.CONSULTANT_DECISION,
                   DocumentType.DRF, DocumentType.CERTIFICATE, DocumentType.DATASHEET, DocumentType.LOAD_SCHEDULE,
                   DocumentType.VENDOR_LIST, DocumentType.SCOPE_MATRIX, DocumentType.COMPLIANCE_STATEMENT,
                   DocumentType.SPECIFICATION, DocumentType.DRAWING)
# Reference material as against a controlled document: a package or a
# submission holds datasheets, certificates and schedules, and a drawing's
# title block quotes a certificate number; where a controlled kind is the
# document, these are its components, never a second kind. Only among
# themselves can two of them make a document ambiguous.
_REFERENCE = frozenset({DocumentType.DATASHEET, DocumentType.CERTIFICATE, DocumentType.LOAD_SCHEDULE, DocumentType.VENDOR_LIST,
                        DocumentType.SCOPE_MATRIX, DocumentType.COMPLIANCE_STATEMENT, DocumentType.SPECIFICATION,
                        DocumentType.DRAWING})


@dataclass
class Assessment:
    primary_type: DocumentType
    stage: Stage
    strength: Strength
    component_types: list[DocumentType] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    reason: str = ""
    # Only when the evidence names one; never encoded in the type.
    system_code: str | None = None
    discipline: str | None = None
    # What the path words alone suggested, apart from the role (not stored).
    path_types: list[DocumentType] = field(default_factory=list)
    # What the answer rests on, and what each component rests on.
    basis: Basis = Basis.NONE
    component_support: dict[str, str] = field(default_factory=dict)
    # The row's processing state when the answer was made (None: not a row).
    source_state: str | None = None
    # Source-quality findings a person should see: a printed project code
    # that differs from the indexed project, a record whose reference is a
    # date, a reading made by an earlier parser, a name the pages contradict.
    flags: list[str] = field(default_factory=list)
    # The page evidence weighed, as stored (kind, page, excerpt, method).
    evidence_pages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "primary_type": self.primary_type.value, "stage": self.stage.value, "evidence_strength": self.strength.value,
            "component_types": [t.value for t in self.component_types], "evidence": list(self.evidence),
            "evidence_sources": list(self.evidence_sources), "reason": self.reason, "system_code": self.system_code,
            "discipline": self.discipline, "rules_version": RULES_VERSION, "basis": self.basis.value,
            "component_support": dict(self.component_support), "source_state": self.source_state,
            "flags": list(self.flags), "evidence_pages": list(self.evidence_pages)[:12],
        }


def _label(kind: DocumentType) -> str:
    return kind.value.lower().replace("_", " ")


# --- the fast metadata hint ---------------------------------------------------------------------
#
# Words in folder and file names, as the readers already use them
# (app.services.document_processing.priority, submittal_scanner,
# spec_finder, transmittals, document_control.GIVEN_TO_US). Path words
# are a hint, never more.

_PATH_RULES: tuple[tuple[DocumentType, re.Pattern], ...] = (
    (DocumentType.TRANSMITTAL, re.compile(r"transmittal", re.I)),
    (DocumentType.DRAWING_SCHEDULE, re.compile(r"drawing\s*(?:schedule|list|register)|schedule\s*of\s*drawings", re.I)),
    (DocumentType.SHOP_DRAWING, re.compile(r"shop\s*drawing|\bsdw\b|\bsd\b(?![a-z])|\bdrawings?\b|layout|\bdwg\b", re.I)),
    (DocumentType.MATERIAL_SUBMITTAL, re.compile(r"material\s+submittal|\bmas\b|\bmar\b|\bms\b(?!\s*office)", re.I)),
    (DocumentType.SAMPLE_APPROVAL, re.compile(r"\bsar\b|sample", re.I)),
    (DocumentType.SPECIFICATION, re.compile(r"specification|\bspecs?\b|\bdivision\s*\d|\b\d{2}\s?\d{2}\s?\d{2}\b", re.I)),
    (DocumentType.DATASHEET, re.compile(r"data\s*sheet|datasheet|catalog|catalogue|brochure|manual", re.I)),
    (DocumentType.CERTIFICATE, re.compile(r"certificate|\bcert\b|approval\s+certificate|ul\s+listing|\blpcb\b|\bfm\s+approv", re.I)),
    (DocumentType.COMMENT_RESPONSE, re.compile(r"repl(?:y|ies)|comment|response", re.I)),
    (DocumentType.COMPLIANCE_STATEMENT, re.compile(r"compliance\s+statement", re.I)),
    (DocumentType.LOAD_SCHEDULE, re.compile(r"load\s+schedule", re.I)),
    (DocumentType.VENDOR_LIST, re.compile(r"vendor\s+list|list\s+of\s+makes", re.I)),
    (DocumentType.SCOPE_MATRIX, re.compile(r"scope\s+(?:of\s+)?matrix|responsibility\s+matrix", re.I)),
    (DocumentType.DRAWING, re.compile(r"single\s+line|schematic|diagram", re.I)),
)
# Folder names, whole, as document_control.is_shop_drawing reads them: "IFC"
# is a folder ("00- IFC" too); a project filed under "Pacific" is not.
_IFC_WORDS = ("ifc", "issued for construction")
_TENDER_WORDS = ("enquiry", "enquiries", "tender")


def _folder_words(folders: str, words: tuple[str, ...]) -> bool:
    parts = [part.strip().casefold() for part in folders.replace("\\", "/").split("/") if part.strip()]
    return any(word in part for part in parts for word in words)
_RECEIVED_RE = re.compile(r"approval|approved|received|recieved|returned", re.I)
_DRF_NAME_RE = re.compile(r"\bdrf\b|design\s+request", re.I)
_DESIGN_SHEET_NAME_RE = re.compile(r"design\s+sheet", re.I)
_SYSTEM_FOLDER_RE: tuple[tuple[str, re.Pattern], ...] = (
    ("FRC", re.compile(r"\bFRC\b", re.I)),
    ("ELS", re.compile(r"\bEML\b|\bELS\b|\bCBS\b|\bELM\b|emergency\s*light|central\s*batter", re.I)),
    ("PAVA", re.compile(r"\bPAVA\b|\bPA\s*/?\s*VA\b|public\s*address|voice\s*alarm", re.I)),
    ("FAS", re.compile(r"\bFA\b|\bFAS\b|fire\s*alarm", re.I)),
)
# The reference infix a file name carries (shop_drawings.reference_system).
_REF_SYSTEM_RE: tuple[tuple[str, re.Pattern], ...] = (
    ("FAS", re.compile(r"-(?:FA|FAS|VE|FT)-", re.I)),
    ("ELS", re.compile(r"-(?:LI|ELM|EML|ELS|CBS)-", re.I)),
    ("FRC", re.compile(r"-FRC-", re.I)),
)
# A path suggestion that is not a conflict with what the content reads as:
# a reply or a decision is filed with the submission it answers.
_COMPATIBLE = {
    (DocumentType.MATERIAL_SUBMITTAL, DocumentType.COMMENT_RESPONSE), (DocumentType.MATERIAL_SUBMITTAL, DocumentType.CONSULTANT_DECISION),
    (DocumentType.MATERIAL_SUBMITTAL, DocumentType.SAMPLE_APPROVAL), (DocumentType.SAMPLE_APPROVAL, DocumentType.COMMENT_RESPONSE),
    (DocumentType.SAMPLE_APPROVAL, DocumentType.CONSULTANT_DECISION), (DocumentType.SHOP_DRAWING, DocumentType.COMMENT_RESPONSE),
    (DocumentType.SHOP_DRAWING, DocumentType.CONSULTANT_DECISION), (DocumentType.SHOP_DRAWING, DocumentType.DRAWING_SCHEDULE),
    (DocumentType.SHOP_DRAWING, DocumentType.IFC_DRAWING), (DocumentType.IFC_DRAWING, DocumentType.SHOP_DRAWING),
    (DocumentType.TRANSMITTAL, DocumentType.SAMPLE_APPROVAL), (DocumentType.MATERIAL_SUBMITTAL, DocumentType.TRANSMITTAL),
    # The word "comments" in a name fits the consultant's sheet as well as the contractor's answer.
    (DocumentType.COMMENT_RESPONSE, DocumentType.CONSULTANT_DECISION),
    # A submittal package holds its datasheets and certificates; a drawing may be a shop drawing.
    (DocumentType.MATERIAL_SUBMITTAL, DocumentType.DATASHEET), (DocumentType.MATERIAL_SUBMITTAL, DocumentType.CERTIFICATE),
    (DocumentType.SHOP_DRAWING, DocumentType.DRAWING), (DocumentType.IFC_DRAWING, DocumentType.DRAWING),
    (DocumentType.DRAWING, DocumentType.SHOP_DRAWING), (DocumentType.DRAWING, DocumentType.IFC_DRAWING),
    (DocumentType.SAMPLE_APPROVAL, DocumentType.TRANSMITTAL),
}


def _folders_and_name(relative: str | None, filename: str | None) -> tuple[str, str]:
    path = (relative or "").replace("\\", "/")
    folders, _slash, name = path.rpartition("/")
    return folders, name or (filename or "")


def _system_from_path(relative: str | None, filename: str | None = None) -> str | None:
    folders, name = _folders_and_name(relative, filename)
    for code, pattern in _REF_SYSTEM_RE:
        if pattern.search(name):
            return code
    for code, pattern in _SYSTEM_FOLDER_RE:
        if pattern.search(folders):
            return code
    return None


def hint(relative_path: str | None, filename: str | None, role: str | None, *, intake_role: str | None = None,
         intake_system: str | None = None) -> Assessment:
    """The fast hint: from the relative path, the file name, the role the
    row carries and the intake association -- nothing opened, hashed or
    asked. `intake_role` is "drf" / "design_sheet" for the intake gate's
    rows (that association is authoritative; the folder is not, and a file
    merely *named* Design Sheet is a metadata hint like any other)."""
    folders, name = _folders_and_name(relative_path, filename)
    evidence: list[str] = []
    sources: list[str] = []
    components: list[DocumentType] = []
    system = intake_system or _system_from_path(relative_path, filename)
    if intake_role == "drf" or role == "drf":
        return Assessment(DocumentType.DRF, Stage.SUPPORTED, Strength.STRONG, [], ["the project's DRF (intake association)"],
                          [Basis.INTAKE_ASSOCIATION.value], "attached to the project as its Design Request Form",
                          system_code=None, basis=Basis.INTAKE_ASSOCIATION)
    if intake_role == "design_sheet" or role == "design_sheet":
        return Assessment(DocumentType.DESIGN_SHEET, Stage.SUPPORTED, Strength.STRONG, [],
                          [f"attached to the project as a Design Sheet{f' for {system}' if system else ''} (intake association)"],
                          [Basis.INTAKE_ASSOCIATION.value], "attached to the project as a Design Sheet", system_code=system,
                          basis=Basis.INTAKE_ASSOCIATION)
    if role == "transmittal":
        evidence.append("a Word document in a Transmittal folder (the sync's own rule)")
        sources.append("role")
        return Assessment(DocumentType.TRANSMITTAL, Stage.HINT, Strength.WEAK, [], evidence, sources,
                          "filed as a transmittal; its records say what it sent", system_code=system, basis=Basis.METADATA)
    if role == "submittal_form":
        evidence.append("filed with role submittal_form (the sync's rule: a MAS reference on the first page, or a scan "
                        "under a submittal folder or name); the role is not itself proof of a form")
        sources.append("role")
        components.append(DocumentType.MATERIAL_SUBMITTAL)
    if role == "spec":
        evidence.append("kept among specifications or named as a section (role spec)")
        sources.append("role")
        components.append(DocumentType.SPECIFICATION)
    # The file's own name before the folders above it: "Data Sheet.pdf"
    # under an MS folder is a datasheet in a submittal package, not a
    # material submittal because of the folder.
    by_name = [kind for kind, pattern in _PATH_RULES if pattern.search(name)]
    by_folder = [kind for kind, pattern in _PATH_RULES if pattern.search(folders)]
    matches: list[DocumentType] = list(dict.fromkeys(by_name + by_folder))
    for kind in matches:
        where = "file name" if kind in by_name else "folder"
        evidence.append(f"path words say {_label(kind)} ({where})")
    if matches:
        sources.append("path")
    if _folder_words(folders, _IFC_WORDS):
        evidence.append("under an IFC / issued-for-construction folder: a drawing given to us, not ours")
        matches = [DocumentType.IFC_DRAWING] + [m for m in matches if m not in (DocumentType.SHOP_DRAWING, DocumentType.IFC_DRAWING)]
    elif _folder_words(folders, _TENDER_WORDS) and DocumentType.SHOP_DRAWING in matches:
        evidence.append("under an enquiry / tender folder: a drawing given to us, not a shop drawing")
        matches = [m for m in matches if m != DocumentType.SHOP_DRAWING] or [DocumentType.OTHER]
    path_types = list(matches)
    if _RECEIVED_RE.search(folders):
        evidence.append("under a received / approved folder: may carry the consultant's decision")
        components.append(DocumentType.CONSULTANT_DECISION)
    if _DRF_NAME_RE.search(name):
        matches.insert(0, DocumentType.DRF)
        evidence.append("named as a DRF (the name only; not the project's DRF)")
    elif _DESIGN_SHEET_NAME_RE.search(name):
        matches.insert(0, DocumentType.DESIGN_SHEET)
        evidence.append("named as a Design Sheet (the name only; not one of the project's Design Sheets)")
    for kind in matches:
        if kind not in components:
            components.append(kind)
    # A name that says what the file is outranks the role it was filed
    # under: the datasheet, certificate or catalogue in a submittal package
    # keeps its own identity, the package being what it belongs to.
    attachment = next((k for k in by_name if k in (DocumentType.DATASHEET, DocumentType.CERTIFICATE,
                                                    DocumentType.COMMENT_RESPONSE, DocumentType.SPECIFICATION,
                                                    DocumentType.COMPLIANCE_STATEMENT, DocumentType.LOAD_SCHEDULE,
                                                    DocumentType.VENDOR_LIST, DocumentType.SCOPE_MATRIX)), None)
    if role == "submittal_form" and attachment is None:
        primary = DocumentType.MATERIAL_SUBMITTAL
    elif role == "submittal_form":
        primary = attachment
        evidence.append("named for what it is; the submittal package it is filed with is its context, not its kind")
    elif role == "spec" and not matches:
        primary = DocumentType.SPECIFICATION
    elif matches:
        primary = matches[0]
    else:
        primary = DocumentType.UNKNOWN
    strength = Strength.WEAK if primary != DocumentType.UNKNOWN else Strength.UNKNOWN
    stage = Stage.HINT if primary != DocumentType.UNKNOWN else Stage.UNKNOWN
    reason = ("possible " + _label(primary) + " from the path and role alone; not confirmed"
              if primary != DocumentType.UNKNOWN else "nothing in the path, name or role says what this is")
    others = [c for c in components if c != primary]
    return Assessment(primary, stage, strength, others, evidence, sources or ["path"], reason, system_code=system,
                      path_types=path_types, basis=Basis.METADATA if primary != DocumentType.UNKNOWN else Basis.NONE,
                      component_support={c.value: Basis.METADATA.value for c in others})


# --- the assessment from stored data -----------------------------------------------------------


def _records_of(row: ProjectDocument) -> list[dict]:
    return [r for r in ((row.extracted or {}).get("records") or []) if isinstance(r, dict)]


def _form_of(row: ProjectDocument) -> dict | None:
    form = (row.extracted or {}).get("form")
    return form if isinstance(form, dict) else None


def _as_is(base: Assessment, state: str | None, *, evidence: list[str] | None = None, reason: str | None = None,
           system: str | None = None, sources: list[str] | None = None) -> Assessment:
    """The hint, as the answer for a row: nothing on the pages changes it."""
    return Assessment(base.primary_type, base.stage, base.strength, list(base.component_types),
                      list(evidence if evidence is not None else base.evidence),
                      list(sources if sources is not None else base.evidence_sources), reason or base.reason,
                      system_code=system if system is not None else base.system_code, path_types=list(base.path_types),
                      basis=base.basis, component_support=dict(base.component_support), source_state=state)


def _valid_records(row: ProjectDocument) -> tuple[list[dict], list[str]]:
    """The row's records that can stand as evidence, and the flags for the
    ones that cannot: a record whose reference is a date is the grammar's
    mistake, not a controlled document."""
    from app.services.document_control import is_date_shaped

    valid, flags = [], []
    for record in _records_of(row):
        if is_date_shaped(record.get("reference")):
            flags.append(f"a stored record's reference is a date ({record.get('reference')}); the reading needs repair")
            continue
        valid.append(record)
    return valid, flags


def _page_evidence(row: ProjectDocument) -> tuple[list[dict], dict[DocumentType, dict], list[str], list[str]]:
    """What the pages were found to hold (content_evidence): the findings,
    the document types they name (each with the best finding that named
    it: a title over a strong match; a weak cue names no type and is an
    evidence line only), the evidence lines, and the source-quality flags.
    Among reference kinds, a title beats the rest: a specification's own
    heading is its kind, the "product data" in its clauses is not."""
    from app.services import content_evidence

    stored = (row.extracted or {}).get("evidence")
    findings = [f for f in ((stored or {}).get("findings") or []) if isinstance(f, dict)]
    types: dict[DocumentType, dict] = {}
    lines: list[str] = []
    flags: list[str] = []
    rank = {"title": 0, "strong": 1, "weak": 2}
    for finding in sorted(findings, key=lambda f: (rank.get(f.get("grade") or "strong", 1), f.get("page") or 0)):
        kind = _EVIDENCE_TYPES.get(finding.get("kind"))
        grade = finding.get("grade") or "strong"
        if kind is None:
            continue
        excerpt = (finding.get("excerpt") or "")[:90]
        if grade == "weak":
            if kind not in types:
                lines.append(f"page {finding.get('page')} has a cue of {_label(kind)} ({finding.get('method')}: "
                             f"\u201c{excerpt}\u201d) -- a cue, not evidence of the kind")
            continue
        if kind not in types:
            types[kind] = finding
            lines.append(f"page {finding.get('page')} reads as {_label(kind)} ({finding.get('method')}"
                         f"{', title' if grade == 'title' else ''}: \u201c{excerpt}\u201d)")
    titled = {k for k, f in types.items() if (f.get("grade") or "strong") == "title" and k in _REFERENCE}
    if titled:
        # The reference kinds a title names stand; the other reference kinds are what it mentions.
        for kind in [k for k in types if k in _REFERENCE and k not in titled]:
            types.pop(kind)
    else:
        # No title: a reference kind named on the first page stands over one
        # named further in (a matrix's own heading over the makes it lists).
        first = {k for k, f in types.items() if k in _REFERENCE and (f.get("page") or 0) == 1}
        if first:
            for kind in [k for k in types if k in _REFERENCE and k not in first]:
                types.pop(kind)
    kinds = content_evidence.kinds_of(stored, grades=("title", "strong"))
    if "receipt" in kinds:
        lines.append("a receipt acknowledgement on the page: proof of delivery, not of approval")
    if "decision_status" in kinds and "author_consultant" in content_evidence.kinds_of(stored):
        lines.append("the page carries a review status in the consultant's words -- a decision component; the register decides what it applies to")
    return findings, types, lines, flags


def assess(row: ProjectDocument, *, intake_role: str | None = None, intake_system: str | None = None,
           project_ep: str | None = None) -> Assessment:
    """The assessment from what the row already holds: its records, the
    model's reading of a form, the evidence read off its first pages, its
    notes and its role -- laid against the hint. Reads no file, opens no
    PDF, calls no model. The stored reading is evidence only while the row
    is fresh: a changed file waiting to be read, or one whose read failed,
    keeps the previous reading on the row, and that is no evidence for the
    content as it is now.

    Precedence. The intake association is authoritative. Then the readers'
    controlled records and the model's reading of a form; then what the
    pages were found to hold (content_evidence); then the file's name;
    then its role; then its folders. Content that contradicts itself, or
    the name, is ambiguous and says so. A record the grammar got wrong (a
    date for a reference) is not content. A form the model read and found
    not to be one is negative evidence: the role does not make it one.
    `project_ep` is the project the row is indexed under: a project code
    printed on the page that differs is flagged, never acted on."""
    base = hint(row.relative_path, row.filename, row.role, intake_role=intake_role, intake_system=intake_system)
    state = getattr(row, "state", None)
    if base.basis is Basis.INTAKE_ASSOCIATION:
        return _as_is(base, state)   # the intake association is the evidence; nothing is read here
    if row.extracted is None:
        return _as_is(base, state)   # not processed yet: the hint stands as a hint
    if state not in VERIFIED_STATES:
        waiting = "waits to be read again" if state in ("pending", "processing") else f"is {state}"
        return _as_is(base, state, evidence=base.evidence + [f"the file {waiting}: its stored reading is of the previous "
                                                              "content and is not evidence for this one"],
                      reason=base.reason + f"; the file {waiting}, and its stored reading is not used")
    from app.services import content_evidence
    from app.services.document_control import PARSER_VERSION

    records, flags = _valid_records(row)
    form = _form_of(row)
    notes = [str(n) for n in ((row.extracted or {}).get("notes") or [])]
    evidence = list(base.evidence)
    sources = list(base.evidence_sources)
    content: list[DocumentType] = []   # what the content says, in order of weight
    support: dict[str, str] = {}
    folders, _name = _folders_and_name(row.relative_path, row.filename)
    in_ifc = _folder_words(folders, _IFC_WORDS)
    given = in_ifc or _folder_words(folders, _TENDER_WORDS)
    categories = [r.get("category") for r in records]
    srcs = [r.get("source") for r in records]
    system = base.system_code
    for record in records:
        if record.get("system_code") and not system:
            system = record["system_code"]
    parser = (row.extracted or {}).get("parser_version")
    if parser != PARSER_VERSION and (records or _records_of(row)):
        flags.append("the records were read by an earlier parser; they are read again on the next processing or repair")

    negative_form = form is not None and form.get("is_submittal") is False
    if form and form.get("is_submittal"):
        content.append(DocumentType.MATERIAL_SUBMITTAL)
        evidence.append(f"the model read a material submittal form ({form.get('reference') or 'no reference'}, "
                        f"R{form.get('revision')})" if form.get("revision") is not None else
                        f"the model read a material submittal form ({form.get('reference') or 'no reference'})")
        sources.append("form_reading")
        if form.get("system_code") and form["system_code"] != "OTHER":
            system = form["system_code"]
        reply = form.get("reply") or {}
        if reply.get("present") and reply.get("from_consultant") and reply.get("status") not in (None, "", "none"):
            evidence.append("the form carries a consultant's reply (a decision component; the register decides what it applies to)")
            if DocumentType.CONSULTANT_DECISION not in content:
                content.append(DocumentType.CONSULTANT_DECISION)
    elif negative_form:
        evidence.append("the model read the first page and found no material submittal form (negative evidence: the role "
                        "submittal_form does not make it one)")
        sources.append("form_reading")
    if "submittals" in categories:
        n = categories.count("submittals")
        evidence.append(f"{n} document-control record{'s' if n != 1 else ''} of category submittals (a MAS / MAR reference on the page)")
        sources.append("records")
        if DocumentType.MATERIAL_SUBMITTAL not in content:
            content.append(DocumentType.MATERIAL_SUBMITTAL)
    if "samples" in categories:
        n = categories.count("samples")
        evidence.append(f"{n} record{'s' if n != 1 else ''} of category samples (a SAR reference, or a transmittal's sample board)")
        sources.append("records")
        content.append(DocumentType.TRANSMITTAL if row.role == "transmittal" else DocumentType.SAMPLE_APPROVAL)
    if "drawings" in categories:
        schedule = any(s == "drawing schedule" for c, s in zip(categories, srcs) if c == "drawings")
        sheets = sum(1 for c, s in zip(categories, srcs) if c == "drawings" and s != "drawing schedule")
        if schedule:
            evidence.append("a drawing schedule was read off the page (required rows, not submitted drawings)")
            sources.append("records")
            content.append(DocumentType.DRAWING_SCHEDULE)
        if sheets:
            evidence.append(f"{sheets} drawing record{'s' if sheets != 1 else ''} (a submission cover, a title block or an SDW / DWG reference)")
            sources.append("records")
            if in_ifc:
                evidence.append("under an IFC / issued-for-construction folder: a drawing given to us")
                content.append(DocumentType.IFC_DRAWING)
            elif given:
                evidence.append("under an enquiry / tender folder: a drawing given to us, not a shop drawing")
                content.append(DocumentType.OTHER)
            else:
                content.append(DocumentType.SHOP_DRAWING)
    if "reply" in categories:
        evidence.append("a reply sheet was read off the page (a contractor's answer to the consultant's comments)")
        sources.append("records")
        content.append(DocumentType.COMMENT_RESPONSE)
        decided = any(r.get("category") == "reply" and (r.get("status") or "UR") != "UR" for r in records)
        if decided:
            evidence.append("the reply sheet quotes a decision -- evidence only; the register decides what it applies to")
            content.append(DocumentType.CONSULTANT_DECISION)
    decided_records = [r for r in records if r.get("category") in ("submittals", "samples", "drawings")
                       and (r.get("status") or "UR") != "UR" and r.get("source") == "document"]
    if decided_records:
        evidence.append(f"{len(decided_records)} record{'s' if len(decided_records) != 1 else ''} with a decision read off the page "
                        "(a stamp, a marked box or a framed option) -- a decision component")
        if DocumentType.CONSULTANT_DECISION not in content:
            content.append(DocumentType.CONSULTANT_DECISION)
    if row.role == "transmittal" and content and DocumentType.TRANSMITTAL not in content:
        evidence.append("filed as a transmittal, with controlled documents listed on it")
        content.insert(0, DocumentType.TRANSMITTAL)
    for kind in content:
        support.setdefault(kind.value, Basis.CONTENT.value)
    # What the readers' controlled records say the document is: where they
    # say anything, a kind the pages merely name is a component of it (a
    # drawing sheet quoting the material submittal it follows, a form
    # promising a sample), never a second kind.
    from_records = {c for c in content}

    # What the pages were found to hold, after the records: the readers'
    # controlled records outrank a page's words, and an attachment found
    # behind a form's records is a component of the package.
    findings, page_types, page_lines, page_flags = _page_evidence(row)
    flags.extend(page_flags)
    evidence.extend(page_lines)
    if page_types:
        sources.append("page_evidence")
    page_kinds = content_evidence.kinds_of((row.extracted or {}).get("evidence"), grades=("title", "strong"))
    stored_evidence = (row.extracted or {}).get("evidence") or {}
    if stored_evidence and stored_evidence.get("page_count", 0) > stored_evidence.get("pages_read", 0):
        evidence.append(f"{stored_evidence.get('pages_read')} of {stored_evidence.get('page_count')} pages were read for evidence")
    for kind in _EVIDENCE_ORDER:
        if kind not in page_types:
            continue
        if kind is DocumentType.SHOP_DRAWING and in_ifc:
            kind = DocumentType.IFC_DRAWING
        elif kind is DocumentType.SHOP_DRAWING and given:
            kind = DocumentType.OTHER
        if kind is DocumentType.MATERIAL_SUBMITTAL and negative_form and DocumentType.MATERIAL_SUBMITTAL not in content:
            continue   # the heading names a package the model found no form of: the name of the folder, not the file
        if kind is DocumentType.CONSULTANT_DECISION and "author_contractor" in page_kinds and "contractor_reply" in page_kinds:
            continue   # a reply sheet quotes the comments it answers
        if kind not in content:
            content.append(kind)
            support.setdefault(kind.value, Basis.CONTENT.value)
    for code in content_evidence.printed_projects(stored_evidence):
        if project_ep and code.upper() != f"EP-{project_ep}".upper():
            flags.append(f"the page prints project code {code}; the file is indexed under EP-{project_ep} (a reference copy, or filed "
                         "under the wrong project -- for a person; nothing is moved)")
    if any("not downloaded" in n for n in notes):
        evidence.append("the file is online-only in OneDrive: nothing was read")
        sources.append("notes")
        return _as_is(base, state, evidence=evidence, sources=sources, reason="the file could not be read; the hint stands",
                      system=system)

    hinted = base.primary_type
    if not content:
        # Read, and nothing on it: not one of the controlled documents.
        # The hint stands as a hint; the path and the role are metadata.
        out_flags = flags
        if base.primary_type == DocumentType.UNKNOWN:
            answer = _as_is(base, state, evidence=evidence, sources=sources, system=system,
                            reason="read, with no controlled-document record on it, nothing found on its pages and nothing in the path to go on")
        elif negative_form and hinted == DocumentType.MATERIAL_SUBMITTAL:
            evidence.append("the folder and the role say material submittal; the model found no form: a package attachment of a kind the pages did not name")
            answer = Assessment(DocumentType.UNKNOWN, Stage.UNKNOWN, Strength.UNKNOWN, [DocumentType.MATERIAL_SUBMITTAL], evidence,
                                sources, "filed in a submittal package, but not a form and of no kind the pages name; for a person",
                                system_code=system, basis=Basis.METADATA, source_state=state,
                                component_support={DocumentType.MATERIAL_SUBMITTAL.value: Basis.METADATA.value})
        elif base.primary_type in (DocumentType.DATASHEET, DocumentType.CERTIFICATE, DocumentType.SPECIFICATION,
                                   DocumentType.OTHER, DocumentType.COMPLIANCE_STATEMENT, DocumentType.LOAD_SCHEDULE,
                                   DocumentType.VENDOR_LIST, DocumentType.SCOPE_MATRIX, DocumentType.DRAWING):
            answer = _as_is(base, state, evidence=evidence, sources=sources, system=system,
                            reason=f"possible {_label(base.primary_type)} from the path and role; the content does not "
                                   "say otherwise, and cannot confirm it")
        else:
            evidence.append("the content gave no record to support the path's suggestion")
            answer = _as_is(base, state, evidence=evidence, sources=sources, system=system,
                            reason=f"possible {_label(base.primary_type)} from the path alone; not confirmed by the content")
        answer.flags.extend(out_flags)
        answer.evidence_pages.extend(findings)
        return answer

    components = list(dict.fromkeys(base.component_types + content))
    for c in base.component_types:
        support.setdefault(c.value, Basis.METADATA.value)
    # The primary type is chosen among what the readers' records say first
    # (a drawing sheet is a drawing, whatever its notes quote), and among
    # the pages' findings only where the records say nothing.
    decisive = [c for c in from_records if c not in (DocumentType.CONSULTANT_DECISION, DocumentType.COMMENT_RESPONSE,
                                                     DocumentType.DRAWING_SCHEDULE)]
    pool = [c for c in content if c in from_records] if decisive else content
    primary = pool[0]
    # A transmittal is what it is, whatever it lists. A submittal package
    # with a reply sheet, a stamp or a datasheet inside: the submission is
    # what it is; the reply, the decision and the attachments are components.
    if DocumentType.TRANSMITTAL in pool:
        primary = DocumentType.TRANSMITTAL
    elif DocumentType.MATERIAL_SUBMITTAL in pool or DocumentType.SAMPLE_APPROVAL in pool:
        primary = DocumentType.MATERIAL_SUBMITTAL if DocumentType.MATERIAL_SUBMITTAL in pool else DocumentType.SAMPLE_APPROVAL
    elif DocumentType.SHOP_DRAWING in pool or DocumentType.IFC_DRAWING in pool:
        primary = DocumentType.SHOP_DRAWING if DocumentType.SHOP_DRAWING in pool else DocumentType.IFC_DRAWING
    elif DocumentType.COMMENT_RESPONSE in pool:
        primary = DocumentType.COMMENT_RESPONSE
    elif DocumentType.CONSULTANT_DECISION in pool:
        primary = DocumentType.CONSULTANT_DECISION
    else:
        primary = next((k for k in _EVIDENCE_ORDER if k in pool), pool[0])
    # The kinds of document the content reads as, attachments of a package
    # and the decision components aside.
    kinds = {c for c in content if c not in (DocumentType.CONSULTANT_DECISION, DocumentType.COMMENT_RESPONSE,
                                             DocumentType.DRAWING_SCHEDULE)}
    if primary not in _REFERENCE:
        kinds -= _REFERENCE   # a controlled document's attachments and quotations are its components
    if from_records - {DocumentType.CONSULTANT_DECISION, DocumentType.COMMENT_RESPONSE, DocumentType.DRAWING_SCHEDULE}:
        kinds &= from_records   # the records decide; what the pages add is a component
    # A kind the primary is compatible with (a sample a submittal promises,
    # a drawing a submission covers) is a component, not a disagreement.
    kinds = {k for k in kinds if k == primary or (primary, k) not in _COMPATIBLE}
    if row.role == "transmittal" and DocumentType.TRANSMITTAL in kinds:
        kinds = {DocumentType.TRANSMITTAL}   # what a transmittal lists is not a second kind of document
    if DocumentType.TRANSMITTAL in kinds and DocumentType.SAMPLE_APPROVAL in kinds:
        kinds.discard(DocumentType.SAMPLE_APPROVAL)   # a transmittal of a sample board
    named = hinted if hinted in (DocumentType.DRF, DocumentType.DESIGN_SHEET) else None
    suggested = named or next((t for t in base.path_types if t in CONTENT_SUPPORTED), None)
    conflict = (suggested is not None and suggested != primary and suggested not in content
                and (suggested, primary) not in _COMPATIBLE)
    agrees = hinted == primary or (suggested is not None and suggested == primary)
    others = [c for c in components if c != primary]
    if row.role in ("spec", "submittal_form", "transmittal") and {"spec": DocumentType.SPECIFICATION,
                                                                  "submittal_form": DocumentType.MATERIAL_SUBMITTAL,
                                                                  "transmittal": DocumentType.TRANSMITTAL}[row.role] != primary:
        flags.append(f"the legacy role is {row.role}; the content reads as {_label(primary)} (the role stands until its consumers are migrated)")
    if len(kinds) > 1:
        return Assessment(primary, Stage.AMBIGUOUS, Strength.CONFLICTING, others,
                          evidence + ["more than one kind of document was read off the pages: " + ", ".join(_label(k) for k in sorted(kinds, key=lambda k: k.value))],
                          sources, "the content reads as more than one kind of document; the records stand, the type is for a person",
                          system_code=system, basis=Basis.CONTENT, component_support=support, source_state=state,
                          flags=flags, evidence_pages=findings)
    if conflict:
        support.setdefault(suggested.value, Basis.METADATA.value)
        flags.append(f"the {'name' if named else 'path'} says {_label(suggested)}; the pages read as {_label(primary)}")
        return Assessment(primary, Stage.AMBIGUOUS, Strength.CONFLICTING, list(dict.fromkeys(others + [suggested])),
                          evidence + [f"the {'name' if named else 'path'} suggested {_label(suggested)}; the content reads as "
                                      f"{_label(primary)}"], sources,
                          "the path and the content disagree; the content is reported, the disagreement with it",
                          system_code=system, basis=Basis.CONTENT, component_support=support, source_state=state,
                          flags=flags, evidence_pages=findings)
    from_records = bool(records) or bool(form and form.get("is_submittal"))
    strong = (primary == DocumentType.MATERIAL_SUBMITTAL and form is not None and form.get("is_submittal")
              and "submittals" in categories) or (primary == DocumentType.TRANSMITTAL and row.role == "transmittal") \
        or (primary in (DocumentType.SHOP_DRAWING, DocumentType.IFC_DRAWING) and hinted == primary and from_records)
    strength = Strength.STRONG if strong else Strength.MODERATE
    if not from_records and primary in page_types:
        reason = f"{_label(primary)}: the pages read as one" + (" and the path agrees" if agrees else "")
    else:
        reason = f"{_label(primary)}: the content supports it" + (" and the path agrees" if agrees else "")
    return Assessment(primary, Stage.SUPPORTED, strength, others, evidence, sources, reason,
                      system_code=system, basis=Basis.CONTENT, component_support=support, source_state=state,
                      flags=flags, evidence_pages=findings)


# --- context, freshness and persistence -----------------------------------------------------------


def context_fingerprint(row: ProjectDocument, project: Project | None, *, intake_role: str | None = None,
                        intake_system: str | None = None) -> str:
    """What the assessment was made in: the normalised relative path, the
    role, the intake association and the project's identity. Not the
    absolute path, not the content (that is `content_sha256`)."""
    relative = PurePosixPath((row.relative_path or row.filename or "").replace("\\", "/")).as_posix().casefold()
    material = json.dumps({"relative": relative, "role": row.role, "intake": intake_role, "intake_system": intake_system,
                           "project": (project.ep_number if project is not None else None) or (project.id if project else None),
                           "rules": RULES_VERSION}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def intake_of(project: Project, row: ProjectDocument) -> tuple[str | None, str | None]:
    """(intake role, system) for the intake gate's rows; (None, None) otherwise."""
    if row.role in ("drf", "design_sheet"):
        return row.role, row.system_code
    return None, None


def current(db: Session, row: ProjectDocument) -> DocumentClassification | None:
    return (db.query(DocumentClassification)
            .filter(DocumentClassification.document_id == row.id, DocumentClassification.superseded_at.is_(None))
            .order_by(DocumentClassification.id.desc()).first())


# What an assessment's freshness can be. `current` is the only one shown as the answer for the row as it is.
CURRENT, RULES_CHANGED, CONTEXT_CHANGED, SOURCE_CHANGED = "current", "rules_changed", "context_changed", "source_changed"


def freshness(entry: DocumentClassification | None, row: ProjectDocument, fingerprint: str) -> str | None:
    """Whether a stored assessment still applies to the row as it is: the
    same rules, the same context, the same content *and* the same
    processing state -- a changed file waiting to be read keeps its old
    hash on the row until it is read, and an assessment of the old content
    is not the answer for the new. None for no assessment."""
    if entry is None:
        return None
    if entry.rules_version != RULES_VERSION:
        return RULES_CHANGED
    if entry.context_fingerprint != fingerprint:
        return CONTEXT_CHANGED
    if (entry.content_sha256 or None) != (row.sha256 or None):
        return SOURCE_CHANGED
    if (entry.assessment or {}).get("source_state") != getattr(row, "state", None):
        return SOURCE_CHANGED
    return CURRENT


def is_current(entry: DocumentClassification | None, row: ProjectDocument, fingerprint: str) -> bool:
    """Whether a stored assessment still applies (`freshness` == current).
    A stale one is never presented as current."""
    return freshness(entry, row, fingerprint) == CURRENT


def record(db: Session, project: Project, row: ProjectDocument, assessment: Assessment, *, source: str) -> DocumentClassification | None:
    """Keep the assessment beside the row: the earlier current one is
    superseded (kept as history), unless an engineer confirmed it -- then
    the automatic one is stored already superseded, and the engineer's
    stays current. Returns the new row; None when the same assessment is
    already current (nothing written). Never commits: the caller's
    transaction decides. Writes inside a savepoint of its own, so a write
    that fails leaves the caller's session usable and its writes untouched
    (the database also refuses a second current row per document)."""
    intake_role, intake_system = intake_of(project, row)
    fingerprint = context_fingerprint(row, project, intake_role=intake_role, intake_system=intake_system)
    existing = current(db, row)
    data = assessment.to_dict()
    if is_current(existing, row, fingerprint) and existing.assessment == data and existing.stage == assessment.stage.value:
        return None
    now = utc_now()
    entry = DocumentClassification(
        project_id=project.id, document_id=row.id, content_sha256=row.sha256, context_fingerprint=fingerprint,
        rules_version=RULES_VERSION, stage=assessment.stage.value, primary_type=assessment.primary_type.value,
        component_types=[t.value for t in assessment.component_types], evidence_strength=assessment.strength.value,
        evidence=list(assessment.evidence)[:20], evidence_sources=list(dict.fromkeys(assessment.evidence_sources)),
        reason=assessment.reason[:500], system_code=assessment.system_code, discipline=assessment.discipline,
        source=source, engineer_confirmed=False, assessment=data, created_at=now,
    )
    with db.begin_nested():
        if existing is not None and existing.engineer_confirmed:
            entry.superseded_at = now    # history only: the engineer's word stands
        elif existing is not None:
            existing.superseded_at = now
            db.flush()                   # the old one closed before the new one opens: one current row per document
        db.add(entry)
        db.flush()
    return entry


def enabled() -> bool:
    return bool(get_settings().document_classification_v2)


def hint_rows(db: Session, project: Project, rows: list[ProjectDocument]) -> int:
    """The fast hint for rows the sync just recorded, written beside them.
    Metadata only; nothing opened. Returns how many were written. Never
    raises: a row whose hint cannot be written is logged and skipped, the
    session stays usable (each write is a savepoint of its own), and the
    sync goes on. Called after the sync's own writes are committed."""
    if not enabled() or not rows:
        return 0
    written = failed = 0
    project_id = project.id
    for row in rows:
        document_id, relative = row.id, row.relative_path or row.filename   # before anything that could expire the row
        try:
            intake_role, intake_system = intake_of(project, row)
            assessment = hint(row.relative_path, row.filename, row.role, intake_role=intake_role, intake_system=intake_system)
            assessment.source_state = row.state   # the hint is the answer for the row as it is now (pending, mostly)
            if record(db, project, row, assessment, source="hint") is not None:
                written += 1
        except Exception:  # noqa: BLE001 -- classification never fails the sync
            failed += 1
            if failed == 1:
                log.exception("Document classification hint could not be written for document %s (%s) of project %s",
                              document_id, relative, project_id)
            else:
                log.warning("Document classification hint could not be written for document %s (%s) of project %s",
                            document_id, relative, project_id)
    return written


def assess_row(db: Session, project: Project, row: ProjectDocument, *, source: str = "assessment") -> DocumentClassification | None:
    """Assess one processed row from what it holds and keep the answer.
    Never raises: a failure is logged, the row is left without an answer,
    and whatever the caller wrote stands (the write is a savepoint of its
    own). Flushes only."""
    if not enabled():
        return None
    document_id, project_id = row.id, project.id
    try:
        intake_role, intake_system = intake_of(project, row)
        assessment = assess(row, intake_role=intake_role, intake_system=intake_system, project_ep=project.ep_number)
        return record(db, project, row, assessment, source=source)
    except Exception:  # noqa: BLE001 -- classification never fails the processing
        log.exception("Document classification failed for document %s of project %s", document_id, project_id)
        return None


def review_reasons(entry: DocumentClassification, row: ProjectDocument | None, fresh: str | None) -> list[str]:
    """Why a person should look at this one: conflicting or no evidence, an
    answer no longer current, or a workflow document assessed from its
    path and role alone. A flag for a reviewer; never a status."""
    reasons: list[str] = []
    if fresh is not None and fresh != CURRENT:
        reasons.append({RULES_CHANGED: "assessed under earlier rules", CONTEXT_CHANGED: "the file's context changed since",
                        SOURCE_CHANGED: "the file changed since, or waits to be read"}[fresh])
    if entry.stage == Stage.AMBIGUOUS.value or entry.evidence_strength == Strength.CONFLICTING.value:
        reasons.append("the evidence conflicts")
    if entry.stage == Stage.UNKNOWN.value:
        reasons.append("nothing says what the file is")
    basis = (entry.assessment or {}).get("basis")
    if row is not None and row.role in WORKFLOW_ROLES and basis in (Basis.METADATA.value, Basis.NONE.value):
        reasons.append("a workflow document assessed from its path and role only")
    if entry.primary_type == DocumentType.CONSULTANT_DECISION.value:
        reasons.append("a consultant's decision document: which submission it applies to is for a person; nothing is applied")
    reasons.extend((entry.assessment or {}).get("flags") or [])
    return reasons


def as_dict(entry: DocumentClassification | None, row: ProjectDocument | None = None, project: Project | None = None) -> dict | None:
    """The stored assessment as the API shows it -- no absolute path; and
    whether it is still current for the row it is about."""
    if entry is None:
        return None
    fresh = None
    if row is not None:
        intake_role, intake_system = intake_of(project, row) if project is not None else (None, None)
        fresh = freshness(entry, row, context_fingerprint(row, project, intake_role=intake_role, intake_system=intake_system))
    data = entry.assessment or {}
    reasons = review_reasons(entry, row, fresh)
    return {
        "id": entry.id, "document_id": entry.document_id, "primary_type": entry.primary_type, "stage": entry.stage,
        "evidence_strength": entry.evidence_strength, "component_types": list(entry.component_types or []),
        "evidence": list(entry.evidence or []), "evidence_sources": list(entry.evidence_sources or []),
        "reason": entry.reason, "system_code": entry.system_code, "discipline": entry.discipline,
        "rules_version": entry.rules_version, "content_sha256": entry.content_sha256,
        "context_fingerprint": entry.context_fingerprint, "source": entry.source,
        "engineer_confirmed": entry.engineer_confirmed, "assessed_at": entry.created_at,
        "stale": fresh is not None and fresh != CURRENT, "freshness": fresh or CURRENT,
        "current": entry.superseded_at is None,
        "basis": data.get("basis") or Basis.NONE.value, "component_support": dict(data.get("component_support") or {}),
        "source_state": data.get("source_state"), "needs_review": bool(reasons), "review_reasons": reasons,
        "flags": list(data.get("flags") or []), "evidence_pages": list(data.get("evidence_pages") or []),
    }


# --- backfill and metrics ---------------------------------------------------------------------------


def backfill(db: Session, project: Project, *, ctx=None, batch: int = 200) -> dict:
    """Assess every indexed row of the project from stored data, in batches,
    skipping rows whose current assessment already applies. Reads no file,
    calls no model, marks nothing stale, reconciles nothing. Resumable
    (what is done is skipped next time) and cancellable between batches.
    Every eligible row ends assessed, skipped (already current) or failed
    (logged, and named in the result): eligible = assessed + skipped + failed."""
    counts: dict = {"documents": 0, "eligible": 0, "assessed": 0, "skipped": 0, "failed": 0, "failures": [],
                    "rules_version": RULES_VERSION}
    rows = (db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id, ProjectDocument.state != "removed")
            .order_by(ProjectDocument.id).all())
    counts["documents"] = counts["eligible"] = len(rows)
    for start in range(0, len(rows), batch):
        if ctx is not None:
            ctx.progress(start, len(rows), f"Classifying documents — {start} of {len(rows)}")
            ctx.check()
        for row in rows[start:start + batch]:
            document_id, relative = row.id, row.relative_path or row.filename
            intake_role, intake_system = intake_of(project, row)
            fingerprint = context_fingerprint(row, project, intake_role=intake_role, intake_system=intake_system)
            if is_current(current(db, row), row, fingerprint):
                counts["skipped"] += 1
                continue
            try:
                assessment = assess(row, intake_role=intake_role, intake_system=intake_system, project_ep=project.ep_number)
                record(db, project, row, assessment, source="backfill")
                counts["assessed"] += 1
            except Exception as exc:  # noqa: BLE001 -- named in the result; the savepoint keeps the session usable
                log.exception("Backfill classification failed for document %s (%s)", document_id, relative)
                counts["failed"] += 1
                if len(counts["failures"]) < 50:
                    counts["failures"].append({"document_id": document_id, "path": relative,
                                               "error": f"{type(exc).__name__}: {exc}"[:300]})
        db.commit()
    if ctx is not None:
        ctx.progress(len(rows), len(rows), f"Classified {counts['assessed']} documents ({counts['skipped']} already current"
                                           + (f", {counts['failed']} failed" if counts["failed"] else "") + ")")
    return counts


def metrics(db: Session, project: Project) -> dict:
    """What the assessments look like across the project, for the
    evaluation that comes later: counts only, no accuracy claim. The
    population is the current (not superseded) assessment of every indexed
    document that is not removed: history rows and removed documents are
    not counted."""
    rows = {r.id: r for r in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                                              ProjectDocument.state != "removed")}
    entries = (db.query(DocumentClassification)
               .filter(DocumentClassification.project_id == project.id, DocumentClassification.superseded_at.is_(None)).all())
    by_type: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    by_basis: dict[str, int] = {}
    by_freshness: dict[str, int] = {}
    agree = conflict = mixed = path_only = content = stale = review = flagged = 0
    seen: dict[int, int] = {}
    for entry in entries:
        row = rows.get(entry.document_id)
        if row is None:
            continue
        seen[entry.document_id] = seen.get(entry.document_id, 0) + 1
        by_type[entry.primary_type] = by_type.get(entry.primary_type, 0) + 1
        by_stage[entry.stage] = by_stage.get(entry.stage, 0) + 1
        basis = (entry.assessment or {}).get("basis") or Basis.NONE.value
        by_basis[basis] = by_basis.get(basis, 0) + 1
        if entry.component_types:
            mixed += 1
        if basis == Basis.CONTENT.value:
            content += 1
        elif basis in (Basis.METADATA.value, Basis.NONE.value):
            path_only += 1
        expected = {"drf": "DRF", "design_sheet": "DESIGN_SHEET", "submittal_form": "MATERIAL_SUBMITTAL",
                    "spec": "SPECIFICATION", "transmittal": "TRANSMITTAL"}.get(row.role)
        if expected is not None:
            if entry.primary_type == expected:
                agree += 1
            else:
                conflict += 1
        intake_role, intake_system = intake_of(project, row)
        fresh = freshness(entry, row, context_fingerprint(row, project, intake_role=intake_role, intake_system=intake_system))
        by_freshness[fresh] = by_freshness.get(fresh, 0) + 1
        if fresh != CURRENT:
            stale += 1
        if review_reasons(entry, row, fresh):
            review += 1
        if (entry.assessment or {}).get("flags"):
            flagged += 1
    assessed = len(seen)
    return {
        "documents": len(rows), "eligible": len(rows), "assessed": assessed, "unassessed": len(rows) - assessed,
        "by_type": dict(sorted(by_type.items())), "by_stage": dict(sorted(by_stage.items())),
        "by_basis": dict(sorted(by_basis.items())), "by_freshness": dict(sorted(by_freshness.items())),
        "unknown": by_type.get("UNKNOWN", 0), "ambiguous": by_stage.get("ambiguous", 0),
        "agree_with_role": agree, "conflict_with_role": conflict, "mixed_component_files": mixed,
        "path_only": path_only, "metadata_only": path_only, "content_supported": content, "stale": stale,
        "current": assessed - stale, "needs_review": review, "flagged": flagged,
        "duplicate_current": sum(1 for n in seen.values() if n > 1), "rules_version": RULES_VERSION,
    }
