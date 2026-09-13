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

With a specification in hand, the two quick actions: prepare a statement
(app.compliance.service.prepare) and check a submitted one
(app.compliance.service.check).
"""

import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.compliance import assist, references, service, writer
from app.compliance.spec_text import read_bytes
from app.compliance.verify import verify
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import ComplianceStatement, Project, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404, _save_upload
from app.schemas_design import (
    AskIn,
    AskOut,
    AutofillIn,
    ComplianceOut,
    ComplianceSystemOut,
    DraftMailOut,
    PrepareIn,
    SuggestIn,
    SuggestionOut,
    ReferenceIndexOut,
    SpecMatchOut,
    SpecSourceIn,
    SpecVerificationOut,
    StatementFileOut,
    StatementOut,
    StatementRowsIn,
    StatementSummaryOut,
)
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

# The DRF rows that mean a system, so the page shows the systems the project
# actually has.
SYSTEM_DRF_ROWS = {
    "FAS": ("Fire Alarm",),
    "EML": ("Emergency Light Monitoring",),
    "VES": ("Voice Evacuation",),
    "CBS": ("Central Battery System",),
    "PAVA": ("PA/VA & BGM",),
}


def _project_systems(project: Project) -> list[str]:
    """The systems this project's compliance statements cover: what its
    Design Sheets and BOQ deliver, and what Project Info marks -- the
    engineer adds a system there (EP-29495's Central Battery System) and
    expects it on every tab. A Design Sheet's ELS is emergency lighting of
    either kind; Project Info says which.

    (Voice Evacuation marked on a DRF gets a tab of its own even where the
    fire alarm specification covers it; that tab shows the same section.)"""
    marked = {s.name for s in project.systems}
    codes = {code for code, rows in SYSTEM_DRF_ROWS.items() if marked & set(rows)}
    delivered = {(sheet.system_code or "").upper() for sheet in project.design_sheets}
    delivered |= {(item.system_code or "").upper() for item in project.boq_items}
    if "ELS" in delivered:
        delivered.add("CBS" if "Central Battery System" in marked and "Emergency Light Monitoring" not in marked else "EML")
    codes |= delivered
    # A specification the engineer uploaded names its system too.
    uploads = Path(get_settings().uploads_root) / f"EP-{project.ep_number}"
    if uploads.is_dir():
        codes |= {m.group(1).upper() for p in uploads.glob("*.pdf") if (m := UPLOAD_NAME_RE.match(p.name))}
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
        if not match or match.group(1).upper() not in wanted:
            continue
        code = match.group(1).upper()
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
        matches, warnings = find_specs(folder, wanted)
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
        references=ReferenceIndexOut(**references.status()),
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
    code = system_code.strip().upper()
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{system_code}'")
    _save_upload(project, file, f"Specification {code}")
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
    code = system_code.strip().upper()
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{system_code}'")
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
    code = code.strip().upper()
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{code}'")
    return code


def _source(body: SpecSourceIn) -> service.SpecSource:
    return service.SpecSource(path=body.path, member=body.member, first_page=body.first_page, last_page=body.last_page)


def _statement_or_404(db: Session, project: Project, statement_id: int) -> ComplianceStatement:
    statement = db.get(ComplianceStatement, statement_id)
    if statement is None or statement.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such compliance statement")
    return statement


@router.post("/{project_id}/compliance/verify", response_model=SpecVerificationOut)
def verify_specification(
    project_id: int,
    body: SpecSourceIn,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpecVerificationOut:
    """Whether a specification is this project's, for this system. The rules
    first; the model only when they cannot tell."""
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
    """Prepare a compliance statement against a specification: past answers
    and rules first, the model for what they leave."""
    project = _get_project_or_404(db, project_id)
    code = _system_code(body.system_code)
    try:
        statement = service.prepare(db, project, code, _source(body), current_user, use_ai=body.use_ai)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return StatementOut.model_validate(statement, from_attributes=True)


@router.post("/{project_id}/compliance/check", response_model=StatementOut)
def check_statement(
    project_id: int,
    system_code: str = Form(...),
    path: str = Form(...),
    member: str | None = Form(None),
    first_page: int | None = Form(None),
    last_page: int | None = Form(None),
    use_ai: bool = Form(True),
    statement_path: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Check a compliance statement -- uploaded, or one already in the project
    folder -- against the specification."""
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
        statement = service.check(db, project, code, source, content, name, current_user, use_ai=use_ai)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return StatementOut.model_validate(statement, from_attributes=True)


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
    return [StatementSummaryOut.model_validate(s, from_attributes=True)
            for s in query.order_by(ComplianceStatement.created_at.desc()).limit(30)]


@router.get("/{project_id}/compliance/statements/{statement_id}", response_model=StatementOut)
def get_statement(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatementOut:
    project = _get_project_or_404(db, project_id)
    return StatementOut.model_validate(_statement_or_404(db, project, statement_id), from_attributes=True)


@router.patch("/{project_id}/compliance/statements/{statement_id}", response_model=StatementOut)
def edit_statement(
    project_id: int,
    statement_id: int,
    body: StatementRowsIn,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """The engineer's own answers: they replace whatever proposed the row."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be edited")
    updated = service.update_rows(db, statement, [row.model_dump() for row in body.rows])
    return StatementOut.model_validate(updated, from_attributes=True)


@router.post("/{project_id}/compliance/statements/{statement_id}/autofill", response_model=StatementOut)
def autofill_statement(
    project_id: int,
    statement_id: int,
    body: AutofillIn,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> StatementOut:
    """Ask the model about the clauses in scope, many per call."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        updated = service.autofill(db, project, statement, body.scope)
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return StatementOut.model_validate(updated, from_attributes=True)


@router.post("/{project_id}/compliance/statements/{statement_id}/suggest", response_model=SuggestionOut)
def suggest_response(
    project_id: int,
    statement_id: int,
    body: SuggestIn,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuggestionOut:
    """The model's proposal for one clause, for the engineer to apply or not."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        return SuggestionOut(**service.suggest(db, project, statement, body.clause_id))
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{project_id}/compliance/statements/{statement_id}/ask", response_model=AskOut)
def ask_about_clause(
    project_id: int,
    statement_id: int,
    body: AskIn,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AskOut:
    """A question about a clause, answered from the project's facts."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    try:
        return AskOut(answer=service.ask(db, project, statement, body.clause_id, body.question))
    except service.ComplianceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{project_id}/compliance/specs", status_code=status.HTTP_204_NO_CONTENT)
def delete_uploaded_specification(
    project_id: int,
    path: str,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a specification the engineer uploaded (never one in the archive)."""
    project = _get_project_or_404(db, project_id)
    uploads = (Path(get_settings().uploads_root) / f"EP-{project.ep_number}").resolve()
    target = Path(path).resolve()
    if not (target.is_relative_to(uploads) and target.is_file() and UPLOAD_NAME_RE.match(target.name)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such uploaded specification")
    target.unlink()
    with _cache_lock:
        _cache.pop(project.id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{project_id}/compliance/statements/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_statement(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_or_404(db, project_id)
    db.delete(_statement_or_404(db, project, statement_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/compliance/statements/{statement_id}/export")
def export_statement(
    project_id: int,
    statement_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The prepared statement as the company's Excel workbook."""
    project = _get_project_or_404(db, project_id)
    statement = _statement_or_404(db, project, statement_id)
    if statement.kind != "prepare":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a prepared statement can be exported")
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


@router.get("/compliance/references", response_model=ReferenceIndexOut)
def reference_index(_current_user: User = Depends(get_current_user)) -> ReferenceIndexOut:
    """The index of the company's past statements: how many, and whether a
    walk of the archive is running."""
    return ReferenceIndexOut(**references.status())


@router.post("/compliance/references/scan", response_model=ReferenceIndexOut)
def scan_references(
    full: bool = False,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
) -> ReferenceIndexOut:
    """Walk the archive for past statements in the background."""
    references.start_scan(full=full)
    return ReferenceIndexOut(**references.status())
