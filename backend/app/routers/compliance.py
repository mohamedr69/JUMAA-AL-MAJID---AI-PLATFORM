"""Compliance statements: the specification each of the project's systems
must be answered against.

A compliance statement is written clause by clause against the consultant's
specification, so the first thing the engineer needs is that specification
-- and the honest answer is often that the project was never given one. So
this finds what the project folder holds per system (see
app.services.spec_finder), says whether each one is really this project's
(app.compliance.verify), and, where it holds nothing, offers the two ways
forward: ask the contractor for it, with a draft mail ready to send, or
upload the copy the engineer has.

With a specification in hand: prepare a statement (rules only), auto-fill
it from the compliance knowledge base (deterministic, no model), keep it
under review row by row, and -- for one clause, on the engineer's click --
ask the model (app.knowledge.review). Checking a submitted statement is
coverage and identity, no model either.
"""

import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.compliance import assist, pdf_writer, service, writer
from app.compliance.spec_text import read_bytes
from app.compliance.verify import verify
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.knowledge import importer, review
from app.models import ComplianceStatement, Project, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404, _save_upload
from app.services import activity
from app.schemas_design import (
    AiFillIn,
    ClearAnswersIn,
    ComplianceOut,
    ComplianceSystemOut,
    DraftMailOut,
    KnowledgeStatusOut,
    PrepareIn,
    ReviewDecisionIn,
    ReviewIn,
    ReviewedIn,
    SpecMatchOut,
    SpecSourceIn,
    SpecVerificationOut,
    StatementFileOut,
    StatementOut,
    StatementRowOut,
    StatementRowsIn,
    StatementSummaryOut,
    UseAnswerIn,
)
from app.services import system_rules
from app.services.spec_finder import (
    SYSTEMS,
    UPLOAD_NAME_RE,
    SpecMatch,
    find_specs,
    open_spec,
    reads_like_a_spec,
    section_numbers,
)

# Pages read to tell whose specification it is: enough for the running header
# to repeat, not the whole document.
VERIFY_PAGES = 8

router = APIRouter(prefix="/projects", tags=["compliance"])

# Reading the specifications means walking the project folder and opening
# what looks like one, which takes half a minute on a synced drive: the
# answer is held briefly so opening the page is not that wait every time.
CACHE_FOR = timedelta(minutes=15)
_cache: dict[int, tuple[datetime, list, list[str]]] = {}
_cache_lock = threading.Lock()

def _project_systems(project: Project) -> list[str]:
    """The systems this project's compliance statements cover, under their
    effective codes (app.services.system_rules): what Project Info marks and
    what its Design Sheets and BOQ deliver. An Edwards fire alarm's voice
    evacuation and fire telephone are answered in the FAS statement; ELS is CBS."""
    codes = set(system_rules.project_codes(project))
    # A specification the engineer uploaded names its system too.
    uploads = Path(get_settings().uploads_root) / f"EP-{project.ep_number}"
    if uploads.is_dir():
        codes |= {system_rules.effective_code(m.group(1), project)
                  for p in uploads.glob("*.pdf") if (m := UPLOAD_NAME_RE.match(p.name))}
    return [code for code in SYSTEMS if code in codes]


def _uploaded_specs(project: Project, wanted: set[str]) -> list[SpecMatch]:
    """The specifications the engineer uploaded, trusted for the system they
    chose: the file's name says nothing about its content, and the content
    need not carry a section number to be the specification."""
    uploads = Path(get_settings().uploads_root) / f"EP-{project.ep_number}"
    found: list[SpecMatch] = []
    if not uploads.is_dir():
        return found
    for path in sorted(uploads.glob("*.pdf")):
        match = UPLOAD_NAME_RE.match(path.name)
        # Uploads named before the system rules ("Specification CBS ...") count under their system now.
        code = system_rules.effective_code(match.group(1), project) if match else None
        if not code or code not in wanted:
            continue
        try:
            with pymupdf.open(path) as doc:
                pages = doc.page_count
                first = doc[0].get_text() if pages else ""
                spec_like = reads_like_a_spec(doc)
        except Exception:  # noqa: BLE001 -- not a PDF after all
            continue
        snippet = re.sub(r"\s+", " ", first).strip()[:400]
        section = next(iter(section_numbers(first[:2000]) & set(SYSTEMS[code].sections)), None) if code in SYSTEMS else None
        found.append(SpecMatch(
            system_code=code, path=str(path.resolve()), filename=path.name, kind="document", section_no=section,
            heading=(snippet[:80] if spec_like else "Uploaded document") or path.stem, first_page=1, last_page=pages,
            snippet=snippet, matched_on="uploaded" if spec_like else "uploaded-unread", uploaded=True,
        ))
    return found


def _specs(project: Project, refresh: bool = False) -> tuple[list, list[str]]:
    wanted = set(_project_systems(project))
    with _cache_lock:
        cached = _cache.get(project.id)
        if cached and not refresh and utc_now() - cached[0] < CACHE_FOR:
            return cached[1], cached[2]

    folder = Path(project.source_folder_path) if project.source_folder_path else None
    if folder is None:
        matches, warnings = [], ["The project has no archive folder, so only uploaded specifications are listed."]
    elif not folder.is_dir():
        matches, warnings = [], ["The project's archive folder is not reachable; only uploaded specifications are listed."]
    else:
        # An integrated fire alarm's voice evacuation specification belongs to FAS.
        integrated = system_rules.project_integrated(project) and "FAS" in wanted
        matches, warnings = find_specs(folder, wanted | ({"VES"} if integrated else set()))
        for match in matches:
            match.system_code = system_rules.effective_code(match.system_code, project)
    # A specification kept loose and again inside the archive it arrived in
    # is one specification.
    unique: dict[tuple[str, str, str | None], object] = {}
    for match in sorted(matches, key=lambda m: (m.member is not None, m.path)):
        unique.setdefault((match.system_code, match.filename.lower(), match.section_no), match)
    found = list(unique.values())

    # Whatever the engineer uploaded for this project counts too -- first,
    # since they chose it on purpose.
    found = _uploaded_specs(project, wanted) + found

    _verify_matches(project, found)
    with _cache_lock:
        _cache[project.id] = (utc_now(), found, warnings)
    return found, warnings


def _verify_matches(project: Project, matches: list) -> None:
    """Read the first pages of each specification found and say, from its
    running header and cover, whether it is this project's. Rules only: the
    model is asked when the engineer presses Verify."""
    read: dict[tuple, object] = {}
    for match in matches:
        first = match.first_page or 1
        last = min(match.last_page or first + VERIFY_PAGES - 1, first + VERIFY_PAGES - 1)
        key = (match.path, match.member, first, last)
        if key not in read:
            spec = None
            for root in service.spec_roots(project):
                if not root.is_dir():
                    continue
                content = open_spec(root, match.path, match.member)
                if content is not None:
                    try:
                        spec = read_bytes(content, first, last)
                    except Exception:  # noqa: BLE001 -- unreadable: no verdict
                        spec = None
                    break
            read[key] = spec
        spec = read[key]
        if spec is not None:
            match.verification = verify(project, match.system_code, spec, match.member or match.filename).as_dict()


def _out(match) -> SpecMatchOut:
    return SpecMatchOut(
        system_code=match.system_code,
        path=match.path,
        filename=match.filename,
        member=match.member,
        kind=match.kind,
        section_no=match.section_no,
        heading=match.heading,
        first_page=match.first_page,
        last_page=match.last_page,
        pages=match.pages,
        snippet=match.snippet,
        matched_on=match.matched_on,
        uploaded=getattr(match, "uploaded", False),
        verification=SpecVerificationOut(**match.verification) if getattr(match, "verification", None) else None,
    )


def _knowledge_status(db: Session) -> KnowledgeStatusOut:
    return KnowledgeStatusOut(**importer.status(db))


@router.get("/{project_id}/compliance", response_model=ComplianceOut)
def get_compliance(
    project_id: int,
    refresh: bool = False,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComplianceOut:
    """The specification found for each of the project's systems."""
    project = _get_project_or_404(db, project_id)
    matches, warnings = _specs(project, refresh=refresh)
    return ComplianceOut(
        systems=[
            ComplianceSystemOut(
                code=code,
                name=SYSTEMS[code].name,
                specs=[_out(m) for m in matches if m.system_code == code],
            )
            for code in _project_systems(project)
        ],
        warnings=warnings,
        searched=project.source_folder_path,
        ai_available=assist.available(),
        knowledge=_knowledge_status(db),
    )


@router.get("/{project_id}/compliance/file")
def open_specification(
    project_id: int,
    path: str,
    member: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The specification itself, out of the archive it arrived in if that is
    where it lives."""
    project = _get_project_or_404(db, project_id)
    roots = [Path(project.source_folder_path)] if project.source_folder_path else []
    roots.append(Path(get_settings().uploads_root) / f"EP-{project.ep_number}")
    for root in roots:
        if not root.is_dir():
            continue
        content = open_spec(root, path, member)
        if content is not None:
            name = Path(member or path).name
            return Response(
                content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"},
            )
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such specification in this project")


@router.post("/{project_id}/compliance/specs", response_model=ComplianceOut)
def upload_specification(
    project_id: int,
    file: UploadFile = File(...),
    system_code: str = Form(...),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ComplianceOut:
    """Attach the specification the project folder does not hold."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(system_code)
    _save_upload(project, file, f"Specification {code}")
    activity.record(db, current_user, "compliance.spec_uploaded", f"Uploaded the {code} specification ({file.filename})",
                    project=project, entity_type="compliance_spec")
    with _cache_lock:
        _cache.pop(project.id, None)
    return get_compliance(project_id, refresh=True, _current_user=current_user, db=db)


@router.get("/{project_id}/compliance/draft-mail", response_model=DraftMailOut)
def draft_mail(
    project_id: int,
    system_code: str,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftMailOut:
    """A mail to the contractor asking for the specification, ready to send.
    Only what the project records is used -- nothing about the specification
    is invented, because there is none to describe."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(system_code)
    system = SYSTEMS[code]

    project_name = project.project_name or f"EP-{project.ep_number}"
    greeting = f"Dear {project.contact_person}," if project.contact_person else "Dear Sir,"
    sections = ", ".join(f"{s[:2]} {s[2:4]} {s[4:]}" for s in system.sections[:2])
    body = "\n".join(
        [
            greeting,
            "",
            f"Subject: EP-{project.ep_number} — {project_name} — {system.name} specification",
            "",
            f"We are preparing the compliance statement for the {system.name} system on the above project"
            + (f" at {project.location}" if project.location else "")
            + ".",
            "",
            f"The project documents issued to us do not include the {system.name} specification"
            f" (typically section {sections}). Kindly forward the specification so that the compliance"
            " statement can be prepared and submitted against it.",
            "",
            "Should the project have no specification for this system, kindly confirm that in writing so"
            " that we may prepare the compliance statement against the applicable UAE Fire and Life Safety"
            " Code of Practice and the approved design.",
            "",
            "Thank you,",
        ]
    )
    return DraftMailOut(
        to=project.contact_email,
        to_name=project.contact_person or project.contractor,
        subject=f"EP-{project.ep_number} — {project_name} — request for {system.name} specification",
        body=body.strip(),
    )


# --- verify, prepare, check ------------------------------------------------------------


def _system_code(code: str) -> str:
    # Any spelling of a system: EML and CBS are emergency lighting (ELS).
    code = system_rules.canonical(code) or ""
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{code}'")
    return code


def _source(body: SpecSourceIn) -> service.SpecSource:
    return service.SpecSource(path=body.path, member=body.member, first_page=body.first_page, last_page=body.last_page)


def _statement_or_404(db: Session, project: Project, statement_id: int) -> ComplianceStatement:
    """The statement, and only if it belongs to this project: a statement id
    under another project's path is not found, whatever the caller's role."""
    statement = db.get(ComplianceStatement, statement_id)
    if statement is None or statement.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such compliance statement")
    return statement


def _approval_fields(statement: ComplianceStatement) -> dict:
    approved = service.is_approved(statement)
    return {
        "approved": approved,
        "approved_at": statement.approved_at if approved else None,
        "approved_by_name": statement.approved_by_name if approved else None,
        "approval_blockers": [] if approved or statement.kind != "prepare" else service.approval_blockers(statement),
        # A fill the server lost reads as stopped, not as running forever.
        "summary": {**(statement.summary or {}), "ai_job": service.ai_job_of(statement)},
    }


def _full(statement: ComplianceStatement) -> StatementOut:
    return StatementOut.model_validate(statement, from_attributes=True).model_copy(update=_approval_fields(statement))


def _summary(statement: ComplianceStatement) -> StatementSummaryOut:
    return StatementSummaryOut.model_validate(statement, from_attributes=True).model_copy(update=_approval_fields(statement))


def _statement_out(db: Session, project: Project, statement: ComplianceStatement) -> StatementOut:
    """A statement as the page gets it, rechecked against its inputs first."""
    statement = service.recheck(db, project, statement)
    return _full(statement)


@router.post("/{project_id}/compliance/verify", response_model=SpecVerificationOut)
def verify_specification(
    project_id: int,
    body: SpecSourceIn,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpecVerificationOut:
    """Whether a specification is this project's, for this system. The rules
    first; the model only when they cannot tell -- and only on this explicit
    action."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(body.system_code)
    source = _source(body)
    first = source.first_page or 1
    source.last_page = min(source.last_page or first + VERIFY_PAGES - 1, first + VERIFY_PAGES - 1)
    try:
        spec, _content, _uploaded = service.load_spec(project, source)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    verdict = service.verify_spec(db, project, code, spec, Path(body.member or body.path).name, use_ai=True)
    return SpecVerificationOut(**verdict.as_dict())


@router.post("/{project_id}/compliance/prepare", response_model=StatementOut)
def prepare_statement(
    project_id: int,
    body: PrepareIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Prepare a compliance statement against a specification: the clauses
    and the rules. No model is called."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(body.system_code)
    try:
        statement = service.prepare(db, project, code, _source(body), current_user)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _statement_event(db, current_user, project, statement, "compliance.prepared",
                     f"Prepared a {code} compliance statement ({len(statement.rows or [])} clauses)")
    return _full(statement)


@router.post("/{project_id}/compliance/check", response_model=StatementOut)
def check_statement(
    project_id: int,
    system_code: str = Form(...),
    path: str = Form(...),
    member: str | None = Form(None),
    first_page: int | None = Form(None),
    last_page: int | None = Form(None),
    statement_path: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Check a compliance statement -- uploaded, or one already in the project
    folder -- against the specification. No model is called."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(system_code)
    source = service.SpecSource(path=path, member=member or None, first_page=first_page, last_page=last_page)
    try:
        if file is not None and file.filename:
            content, name = file.file.read(), file.filename
        elif statement_path:
            content, name = service.open_statement_file(project, statement_path)
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Upload a statement or choose one from the project")
        statement = service.check(db, project, code, source, content, name, current_user)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _statement_event(db, current_user, project, statement, "compliance.checked",
                     f"Checked the {code} compliance statement {name}")
    return _full(statement)


@router.get("/{project_id}/compliance/statements", response_model=list[StatementSummaryOut])
def list_statements(
    project_id: int,
    system_code: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StatementSummaryOut]:
    project = _get_project_or_404(db, project_id)
    query = db.query(ComplianceStatement).filter(ComplianceStatement.project_id == project.id)
    if system_code:
        query = query.filter(ComplianceStatement.system_code == system_code.strip().upper())
    return [_summary(s)
            for s in query.order_by(ComplianceStatement.created_at.desc()).limit(30)]


@router.get("/{project_id}/compliance/statements/{statement_id}", response_model=StatementOut)
def get_statement(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatementOut:
    project = _get_project_or_404(db, project_id)
    return _statement_out(db, project, _statement_or_404(db, project, statement_id))


@router.patch("/{project_id}/compliance/statements/{statement_id}", response_model=StatementOut)
def edit_statement(
    project_id: int,
    statement_id: int,
    body: StatementRowsIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """The engineer's own answers: they replace whatever proposed the row."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be edited")
    updated = service.update_rows(db, project, statement, [row.model_dump() for row in body.rows], current_user)
    _statement_event(db, current_user, project, updated, "compliance.edited",
                     f"Edited {len(body.rows)} answer{'s' if len(body.rows) != 1 else ''} in the "
                     f"{updated.system_code} compliance statement", {"rows": len(body.rows)})
    return _full(updated)


@router.post("/{project_id}/compliance/statements/{statement_id}/clear", response_model=StatementOut)
def clear_statement(
    project_id: int,
    statement_id: int,
    body: ClearAnswersIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Clear every answer, remark and review status on the statement."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be cleared")
    cleared = service.clear_answers(db, project, statement, current_user, keep_manual_remarks=body.keep_manual_remarks)
    _statement_event(db, current_user, project, cleared, "compliance.cleared",
                     f"Cleared the answers of the {cleared.system_code} compliance statement")
    return _full(cleared)


@router.post("/{project_id}/compliance/statements/{statement_id}/autofill", response_model=StatementOut)
def autofill_statement(
    project_id: int,
    statement_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Fill the empty rows from the knowledge base. Deterministic; no model."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.autofill(db, project, statement, current_user)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _statement_event(db, current_user, project, updated, "compliance.autofilled",
                     f"Auto-filled the {updated.system_code} compliance statement from the knowledge base")
    return _full(updated)


@router.post("/{project_id}/compliance/statements/{statement_id}/ai-autofill", response_model=StatementOut)
def ai_autofill_statement(
    project_id: int,
    statement_id: int,
    body: AiFillIn,
    wait: bool = False,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Fill rows with the model, from the project's facts, its scope of work,
    its BOQ, the engineer-approved answers and the knowledge base's nearest
    past answers. Separate from `/autofill`, which stays deterministic: this
    one calls the model, batched and cached, in the background -- the page
    polls the statement for `summary.ai_job` -- and marks every row it fills
    as a draft for review. `wait` runs the whole fill before answering."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.start_ai_autofill(db, project, statement, current_user, scope=body.scope, wait=wait)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _statement_event(db, current_user, project, updated, "compliance.ai_autofill",
                     f"Started AI filling of the {updated.system_code} compliance statement", {"scope": str(body.scope)})
    return _full(updated)


@router.post("/{project_id}/compliance/statements/{statement_id}/rows/{clause_id}/review", response_model=StatementRowOut)
def review_row(
    project_id: int,
    statement_id: int,
    clause_id: str,
    body: ReviewIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementRowOut:
    """Ask the model about this one clause -- the only model call in the
    workflow, and only on this action. The same request id returns the
    stored answer; a review already running is refused (409)."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be reviewed")
    before = next((r for r in statement.rows if r["id"] == clause_id), None)
    stored = (before or {}).get("ai_review") if before else None
    try:
        row = review.review_clause(db, project, statement, clause_id, instruction=body.instruction, request_id=body.request_id)
    except review.InFlight as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except review.ReviewError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if row.get("ai_review") is not stored and row.get("ai_review") != stored:
        service.record_review(db, project, statement, row, current_user)
    return StatementRowOut(**row)


@router.post("/{project_id}/compliance/statements/{statement_id}/rows/{clause_id}/suggestion", response_model=StatementOut)
def decide_suggestion(
    project_id: int,
    statement_id: int,
    clause_id: str,
    body: ReviewDecisionIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Accept, edit or reject the model's suggestion for a row."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.decide_suggestion(db, project, statement, clause_id, body.action, current_user,
                                            response=body.response, remark=body.remark)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _full(updated)


@router.post("/{project_id}/compliance/statements/{statement_id}/rows/{clause_id}/use-answer", response_model=StatementOut)
def use_past_answer(
    project_id: int,
    statement_id: int,
    clause_id: str,
    body: UseAnswerIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Take one of the past answers shown for this clause as its draft."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.use_answer(db, project, statement, clause_id, body.response_id, current_user)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _full(updated)


@router.post("/{project_id}/compliance/statements/{statement_id}/rows/{clause_id}/reviewed", response_model=StatementOut)
def mark_row_reviewed(
    project_id: int,
    statement_id: int,
    clause_id: str,
    body: ReviewedIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """The explicit review action: the row becomes the engineer's."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.mark_reviewed(db, project, statement, clause_id, current_user, reviewed=body.reviewed)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _full(updated)


@router.delete("/{project_id}/compliance/specs", status_code=status.HTTP_204_NO_CONTENT)
def delete_uploaded_specification(
    project_id: int,
    path: str,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a specification the engineer uploaded (never one in the archive)."""
    project = _get_project_or_404(db, project_id)
    uploads = (Path(get_settings().uploads_root) / f"EP-{project.ep_number}").resolve()
    target = Path(path).resolve()
    if not (target.is_relative_to(uploads) and target.is_file() and UPLOAD_NAME_RE.match(target.name)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such uploaded specification")
    target.unlink()
    activity.record(db, current_user, "compliance.spec_removed", f"Removed the uploaded specification {target.name}",
                    project=project, entity_type="compliance_spec")
    with _cache_lock:
        _cache.pop(project.id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{project_id}/compliance/statements/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_statement(
    project_id: int,
    statement_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    activity.record(db, current_user, "compliance.deleted", f"Deleted the {statement.system_code} compliance statement",
                    project=project, entity_type="compliance_statement", entity_id=statement.id, commit=False)
    db.delete(statement)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/compliance/statements/{statement_id}/approval", response_model=StatementOut)
def approve_statement(
    project_id: int,
    statement_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """The engineer approves the statement, which opens export. Refused while
    a clause is unanswered or waiting on a review."""
    project = _get_project_or_404(db, project_id)
    statement = service.recheck(db, project, _statement_or_404(db, project, statement_id))
    try:
        updated = service.approve(db, project, statement, current_user)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _statement_event(db, current_user, project, updated, "compliance.approved",
                     f"Approved the {updated.system_code} compliance statement")
    return _full(updated)


@router.delete("/{project_id}/compliance/statements/{statement_id}/approval", response_model=StatementOut)
def withdraw_statement_approval(
    project_id: int,
    statement_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Withdraw the approval, closing export until it is given again."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    withdrawn = service.withdraw_approval(db, project, statement, current_user)
    _statement_event(db, current_user, project, withdrawn, "compliance.approval_withdrawn",
                     f"Withdrew approval of the {withdrawn.system_code} compliance statement")
    return _full(withdrawn)


def _statement_event(db: Session, user: User, project: Project, statement: ComplianceStatement, action: str,
                     summary: str, detail: dict | None = None) -> None:
    """A statement-level step. Clause-by-clause changes are in compliance_audit."""
    activity.record(db, user, action, summary, project=project, entity_type="compliance_statement",
                    entity_id=statement.id, detail=detail)


def _approved_statement(db: Session, project: Project, statement_id: int) -> ComplianceStatement:
    """The statement, if it may leave the platform: prepared, and approved by
    an engineer for the answers it holds now."""
    statement = service.recheck(db, project, _statement_or_404(db, project, statement_id))
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be exported")
    if not service.is_approved(statement):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An engineer must approve the statement before it can be exported")
    return statement


@router.get("/{project_id}/compliance/statements/{statement_id}/export.pdf")
def export_statement_pdf(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The approved statement as a PDF."""
    project = _get_project_or_404(db, project_id)
    statement = _approved_statement(db, project, statement_id)
    content = pdf_writer.build_pdf(project, statement, service.project_brands(project, statement.system_code))
    name = f"EP-{project.ep_number} {statement.system_code} Compliance Statement.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.get("/{project_id}/compliance/statements/{statement_id}/export")
def export_statement(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The approved statement as the company's Excel workbook."""
    project = _get_project_or_404(db, project_id)
    statement = _approved_statement(db, project, statement_id)
    content = writer.build_workbook(project, statement, service.project_brands(project, statement.system_code))
    name = f"EP-{project.ep_number} {statement.system_code} Compliance Statement.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.get("/{project_id}/compliance/statement-files", response_model=list[StatementFileOut])
def statement_files(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StatementFileOut]:
    """Compliance statements already in the project's folders, to check."""
    project = _get_project_or_404(db, project_id)
    return [StatementFileOut(**f) for f in service.statement_files(project)]


@router.get("/compliance/knowledge", response_model=KnowledgeStatusOut)
def knowledge_status(_current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> KnowledgeStatusOut:
    """What the knowledge base holds and when it was last refreshed. Updating
    it is an administrator's action (see app.routers.knowledge)."""
    return _knowledge_status(db)
