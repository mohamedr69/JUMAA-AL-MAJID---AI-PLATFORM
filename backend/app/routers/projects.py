import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import (
    Project,
    ProjectBoqItem,
    ProjectBoqRevision,
    ProjectDesignSheet,
    ProjectStatus,
    ProjectSystem,
    RoleEnum,
    User,
)
from app.schemas_project import (
    BoqChangeOut,
    BoqCompareOut,
    BoqEnsureResponse,
    BoqRevisionIssue,
    BoqRevisionOut,
    BoqRevisionSummaryOut,
    DetailsCheckIn,
    DetailsCheckOut,
    DocumentCandidateOut,
    ExtractedFieldOut,
    FieldComparisonOut,
    ProjectBoqItemIn,
    ProjectBoqItemOut,
    ProjectBoqItemSave,
    ProjectCreate,
    ProjectDetailsIn,
    ProjectOut,
    ProjectResolveRequest,
    ProjectResolveResponse,
    ProjectSystemIn,
    ReextractionReportOut,
    SheetComparisonOut,
    SystemComparisonOut,
)
from app.schemas_design import ProjectLogDrawingOut, ProjectLogsOut
from app.extraction import pipeline as extraction_pipeline
from app.extraction.issues import Coverage, Issue, IssueCode, PageCoverage
from app.services import activity, boq_provenance, concurrency, design_sheet_extractor
from app.services.design_sheet_extractor import (
    DesignSheetExtraction,
    DesignSheetExtractionError,
    ExtractedBoqLine,
    extract_boq_lines,
)
from app.services.boq_export import boq_workbook
from app.services.boq_revisions import compare_boq
from app.services.drf_extractor import extract_drf_fields
from app.services.ep_resolver import canonical_system_code, infer_single_system, mark_superseded, resolve_project
from app.services.reextraction import reextract_project
from app.services import system_rules
from app.services.log_scan_jobs import get_log_scan

router = APIRouter(prefix="/projects", tags=["projects"])
settings = get_settings()

CREATOR_ROLES = (RoleEnum.admin, RoleEnum.design_manager, RoleEnum.design_engineer)

# Deleting takes the project's documents, systems and BOQ with it, so it sits
# with the roles that own a project's lifecycle rather than everyone who can
# create one -- a Design Engineer can start a project but not erase one.
DELETER_ROLES = (RoleEnum.admin, RoleEnum.design_manager)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Makes the second of two overlapping BOQ opens -- React StrictMode fires
# every effect twice in dev -- wait for the first and return its result
# instead of OCR-ing the same sheets again. It only saves the wasted read; the
# atomic claim in ensure_project_boq is what prevents duplicate lines, and
# that holds across processes where this lock does not.
_boq_extraction_lock = threading.Lock()
# Checking for a running AI read and starting one happen under one lock: a
# page that asks twice at the same moment (React runs a page's first effect
# twice in development) must start one read, not two paying for the same pages.
_boq_read_start_lock = threading.Lock()
BOQ_READ_JOB = "boq_read"


def _get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.post("/resolve", response_model=ProjectResolveResponse)
def resolve(
    payload: ProjectResolveRequest,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
) -> ProjectResolveResponse:
    if not settings.projects_root:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project archive is not configured (PROJECTS_ROOT is unset)",
        )

    root = Path(settings.projects_root)
    if not root.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project archive path is not reachable",
        )

    selected = Path(payload.selected_folder) if payload.selected_folder else None
    result = resolve_project(root, payload.ep_number, selected_folder=selected)

    extracted_fields: dict[str, ExtractedFieldOut] = {}
    extracted_scope_of_work: str | None = None
    extracted_other_information: str | None = None
    extracted_systems: list[ProjectSystemIn] = []
    extraction_warnings: list[str] = []
    extraction_issues: list = []
    if result.drf_candidates:
        # Best-effort: a broken/unreadable DRF or a missing OCR install
        # shouldn't fail the whole resolve -- the engineer can still fill
        # the review form in manually.
        try:
            extraction = extract_drf_fields(result.drf_candidates[0].path)
            extracted_fields = {
                name: ExtractedFieldOut(value=f.value, confidence=f.confidence, raw_label=f.raw_label)
                for name, f in extraction.fields.items()
            }
            extracted_scope_of_work = extraction.scope_of_work
            extracted_other_information = extraction.other_information
            extraction_issues = list(extraction.issues)
            extracted_systems = [
                ProjectSystemIn(
                    name=s.name,
                    brand=s.brand,
                    method_statement=s.method_statement,
                    drawing=s.drawing,
                )
                for s in extraction.systems
            ]
            extraction_warnings = extraction.warnings
        except Exception as exc:  # noqa: BLE001
            extraction_warnings = [f"DRF field extraction failed: {exc}"]

    # A sheet named "Design.pdf", on a DRF that marks one system: it is that
    # system's sheet, and saying so is what puts its lines under a system
    # rather than "Unassigned". A DRF marking two systems settles nothing,
    # and the sheet stays unlabelled for the engineer to place.
    inferred = infer_single_system([s.name for s in extracted_systems], systems=extracted_systems)
    if inferred:
        for candidate in result.design_sheet_candidates:
            if candidate.system_guess is None:
                candidate.system_guess = inferred
                candidate.matched_via += f"; system inferred: the DRF marks {inferred} only"
        mark_superseded(result.design_sheet_candidates)

    ai_suggestions: list[dict] = []
    if settings.ai_enabled and result.drf_candidates:
        ai_suggestions = _resolve_suggestions(db_for_ai(), result, extracted_systems, extraction_issues)

    def candidate_out(m) -> DocumentCandidateOut:
        return DocumentCandidateOut(
            path=str(m.path),
            filename=m.path.name,
            system_guess=m.system_guess,
            matched_via=m.matched_via,
            revision=m.revision,
            selected=m.selected,
        )

    return ProjectResolveResponse(
        ep_number=result.ep_number,
        folder_found=not result.folder_not_found,
        is_ambiguous=result.is_ambiguous,
        matched_folders=[str(f) for f in result.matched_folders],
        source_folder=str(result.source_folder) if result.source_folder else None,
        drf_candidates=[candidate_out(m) for m in result.drf_candidates],
        design_sheet_candidates=[candidate_out(m) for m in result.design_sheet_candidates],
        warnings=result.warnings,
        errors=result.errors,
        extracted_fields=extracted_fields,
        extracted_scope_of_work=extracted_scope_of_work,
        extracted_systems=extracted_systems,
        extracted_other_information=extracted_other_information,
        extraction_warnings=extraction_warnings,
        ai_suggestions=ai_suggestions,
    )


def db_for_ai():
    """A session for the resolve-time suggestions, which log usage and
    read the cache but touch no project."""
    from app.database import SessionLocal

    return SessionLocal()


def _resolve_suggestions(db, result, extracted_systems, extraction_issues) -> list[dict]:
    """What the model suggests about a resolution, for the review form to
    show beside the fields -- never applied.

    Two cases: a design sheet with no code on a DRF that marks more than
    one system (the model may propose one of the marked codes), and a DRF
    field read at low confidence (the model reads the cell image again).
    Each is budgeted like a run; a budget that trips leaves the rest
    unsuggested and says so.
    """
    from app.ai.budget import BudgetExceeded, open_budget

    suggestions: list[dict] = []
    try:
        budget = open_budget(db, None)
        marked = [s.name for s in extracted_systems]
        candidates = system_rules.codes_for_rows(extracted_systems)
        if len(candidates) > 1:
            for candidate in result.design_sheet_candidates:
                if candidate.system_guess is not None:
                    continue
                try:
                    verdict = extraction_pipeline.suggest_sheet_system(
                        db, candidate.path, candidates=candidates, marked_rows=marked, budget=budget
                    )
                except BudgetExceeded as exc:
                    suggestions.append({"kind": "sheet_system", "target": str(candidate.path), "state": "starved",
                                        "value": None, "reason": f"budget: {exc.limit}"})
                    continue
                if verdict is not None:
                    suggestions.append({"kind": "sheet_system", "target": str(candidate.path), "state": verdict.state,
                                        "value": verdict.value, "reason": verdict.reason})
        drf = result.drf_candidates[0].path
        for issue in extraction_issues:
            if issue.code.value != "AMBIGUOUS_OCR":
                continue
            try:
                verdict = extraction_pipeline.suggest_drf_field(db, drf, issue, budget=budget)
            except BudgetExceeded as exc:
                suggestions.append({"kind": "drf_field", "target": issue.target, "state": "starved",
                                    "value": None, "reason": f"budget: {exc.limit}"})
                continue
            if verdict is not None:
                suggestions.append({"kind": "drf_field", "target": issue.target, "state": verdict.state,
                                    "value": verdict.value, "reason": verdict.reason})
    except Exception as exc:  # noqa: BLE001 -- suggestions are optional; resolution is not
        suggestions.append({"kind": "error", "target": "", "state": "failed", "value": None, "reason": str(exc)})
    finally:
        db.close()
    return suggestions


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _validate_source_paths(payload: ProjectCreate) -> None:
    """A project's documents belong to its folder.

    The paths come from the client, which is what let EP-31112 be saved with
    a source folder under one contractor and a DRF under another; and a
    source folder outside the archive would let `/logs/file` serve files
    from anywhere on the machine. So, where an archive is configured, the
    source folder must be inside it (or the uploads folder), and every
    document inside the source folder (or the uploads folder). A machine
    with no archive configured -- the test suite -- has nothing to check
    against.
    """
    if not settings.projects_root:
        return
    archive_roots = [Path(settings.projects_root), Path(settings.uploads_root)]
    source = Path(payload.source_folder_path) if payload.source_folder_path else None
    if source is not None and not any(_is_within(source, r) for r in archive_roots):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The source folder is outside the project archive",
        )
    allowed = [r for r in ([source] if source else []) + [Path(settings.uploads_root)]]
    documents = [("The DRF", payload.drf_document_path)] + [
        ("A Design Sheet", sheet.document_path) for sheet in payload.design_sheets
    ]
    for label, document in documents:
        if document and not any(_is_within(Path(document), r) for r in allowed):
            where = f" {source}" if source else " -- the project has no source folder"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{label} ({Path(document).name}) is outside the project folder{where}",
            )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"A project for EP number '{payload.ep_number}' already exists",
    )
    existing = db.query(Project).filter(Project.ep_number == payload.ep_number).first()
    if existing:
        raise conflict
    _validate_source_paths(payload)

    project = Project(
        ep_number=payload.ep_number,
        status=ProjectStatus.active,
        source_folder_path=payload.source_folder_path,
        drf_document_path=payload.drf_document_path,
        design_engineer_id=current_user.id,
        created_by_id=current_user.id,
    )
    _apply_details(project, payload)
    project.design_sheets = [
        ProjectDesignSheet(system_code=system_rules.effective_code(ds.system_code, project), document_path=ds.document_path)
        for ds in payload.design_sheets
    ]

    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        # A double-submitted form gets past the check above on both requests;
        # the unique constraint catches the second, which is still a conflict.
        db.rollback()
        raise conflict
    db.refresh(project)
    activity.record(db, current_user, "project.created", f"Created {activity.project_label(project)}",
                    project=project, entity_type="project", entity_id=project.id,
                    detail={"design_sheets": len(project.design_sheets)})
    db.refresh(project)
    return project


def _masked(value: str | None, keep: int) -> str | None:
    if not value:
        return value
    return value[:keep] + "•" * max(3, len(value) - keep)


def _for(user: User, project: Project) -> ProjectOut:
    """The project as this user may see it. The DRF's contact person's email
    and phone are a named individual's details: viewers, who only read the
    project, see them masked."""
    out = ProjectOut.model_validate(project, from_attributes=True)
    if user.role == RoleEnum.viewer:
        email = out.contact_email or ""
        at = email.find("@")
        out = out.model_copy(update={
            "contact_email": (email[:1] + "•••" + email[at:]) if at > 0 else _masked(out.contact_email, 1),
            "contact_phone": _masked(out.contact_phone, 4),
        })
    return out


@router.get("", response_model=list[ProjectOut])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    return [_for(current_user, p) for p in db.query(Project).order_by(Project.created_at.desc()).all()]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = _get_project_or_404(db, project_id)
    activity.record_open(db, current_user, project)
    return _for(current_user, project)


@router.put("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectDetailsIn,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Correct the project information after creation -- the DRF read is a
    suggestion, and a wrong value found later needs somewhere to be fixed.
    The EP number and document paths are not editable: they tie the project
    to its archive folder. Refused with 409 when `If-Match` names a
    `details_version` someone else's save has since moved on from."""
    project = _get_project_or_404(db, project_id)
    concurrency.require_current(if_match, project.details_version, "The project information")
    project.details_version += 1
    before = _details_snapshot(project)
    _apply_details(project, payload)
    propagated = _propagate_details(db, project, before)
    changed = {
        name: f"{_short(before['fields'].get(name))} -> {_short(getattr(project, name))}"
        for name in _DETAIL_FIELDS
        if before["fields"].get(name) != getattr(project, name)
    }
    old_systems = sorted(f"{s.name} ({s.brand})" if s.brand else s.name for s in before["systems"])
    new_systems = sorted(f"{s.name} ({s.brand})" if s.brand else s.name for s in project.systems)
    if old_systems != new_systems:
        changed["systems"] = f"{', '.join(old_systems) or '-'} -> {', '.join(new_systems) or '-'}"
    if any(db.is_modified(item) for item in project.boq_items):
        # A brand or system change carried into the BOQ lines is a BOQ write:
        # a table open elsewhere must not save over it.
        project.boq_version += 1
    db.commit()
    if changed:
        activity.record(db, current_user, "project.updated",
                        f"Changed project information: {', '.join(name.replace('_', ' ') for name in changed)}",
                        project=project, entity_type="project", entity_id=project.id, detail=changed)
    db.refresh(project)
    project.propagated = propagated
    return project


def _short(value, limit: int = 80) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _details_snapshot(project: Project) -> dict:
    return {"fields": {name: getattr(project, name) for name in _DETAIL_FIELDS},
            "systems": [ProjectSystem(name=s.name, brand=s.brand, method_statement=s.method_statement, drawing=s.drawing)
                        for s in project.systems]}


def _propagate_details(db: Session, project: Project, before: dict) -> list[str]:
    """Carry a Project Info change into what was derived from it.

    Most of the platform reads the project live -- the pages, the exports, the
    submittal package, the compliance facts -- so an edit is already there.
    What holds a copy is brought in line here:

    - A BOQ line carries the manufacturer its system's DRF brand gave it when
      the line was read. When that brand changes, lines still carrying the old
      brand take the new one; a line whose manufacturer an engineer typed in
      is theirs and is left alone.
    - The compliance page's specification search remembers which specs fit
      the project (its name, client, systems): it is searched again.
    - Compliance drafts depend on the scope of work and the BOQ: a statement
      whose inputs moved flags its drafts for recheck when it is next opened
      (app.compliance.service.recheck), which this edit now triggers.
    """
    notes: list[str] = []
    old_systems = before["systems"]
    recoded = system_rules.normalize_project(project)
    if recoded:
        notes.append(f"{recoded} design sheet, BOQ line or submittal code{'s' if recoded != 1 else ''} moved to "
                     + ("FAS: the Edwards fire alarm carries voice evacuation and fire telephone"
                        if project.voice_evacuation_integrated else "their system"))
    if before["fields"].get("separate_ve_panel") is False and project.separate_ve_panel:
        notes.append("voice evacuation is now a system of its own; lines already under FAS stay there until moved in the BOQ")
    moved = 0
    for item in project.boq_items:
        old_brand = _brand_for(item.system_code, old_systems)
        new_brand = _brand_for(item.system_code, project.systems)
        if not old_brand or not new_brand or old_brand == new_brand:
            continue
        if (item.manufacturer or "").strip().upper() == old_brand.strip().upper():
            item.manufacturer = new_brand
            moved += 1
    if moved:
        notes.append(f"{moved} BOQ line{'s' if moved != 1 else ''} now carry the new brand")

    changed = [name for name in _DETAIL_FIELDS if before["fields"].get(name) != getattr(project, name)]
    systems_changed = sorted((s.name, s.brand or "") for s in old_systems) != sorted((s.name, s.brand or "") for s in project.systems)
    if changed or systems_changed:
        from app.routers import compliance as compliance_router  # imported here: it imports this module

        with compliance_router._cache_lock:
            compliance_router._cache.pop(project.id, None)
        notes.append("the compliance specification search will run again")
    if "scope_of_work" in changed or "other_information" in changed or moved:
        notes.append("compliance drafts that depend on the scope or BOQ are flagged for recheck when opened")
    return notes


def _check_details(db: Session, drf: Path, payload: DetailsCheckIn, project_id: int | None) -> DetailsCheckOut:
    from app.services import details_check

    details = payload.details.model_dump()
    systems = details.pop("systems")
    try:
        result = details_check.check(db, drf, details, systems, project_id=project_id)
    except details_check.DetailsCheckError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DetailsCheckOut(**vars(result))


@router.post("/details-check", response_model=DetailsCheckOut)
def check_new_project_details(
    payload: DetailsCheckIn,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DetailsCheckOut:
    """Check the review form's values against the DRF the resolver found,
    before the project is created. Suggestions only."""
    if not payload.drf_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No DRF to check against")
    drf = Path(payload.drf_path)
    roots = [Path(r) for r in (settings.projects_root, settings.uploads_root) if r]
    if settings.projects_root and not any(_is_within(drf, r) for r in roots):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The DRF is outside the project archive")
    return _check_details(db, drf, payload, None)


@router.post("/{project_id}/details-check", response_model=DetailsCheckOut)
def check_project_details(
    project_id: int,
    payload: DetailsCheckIn,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DetailsCheckOut:
    """Check Project Info's values (as the form holds them now) against the
    project's own DRF. Suggestions only: apply and save to keep any."""
    project = _get_project_or_404(db, project_id)
    from app.ai import project_policy

    if not project_policy.allowed(project):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=project_policy.BLOCKED_MESSAGE)
    if not project.drf_document_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This project has no DRF to check against: attach one under Documents")
    return _check_details(db, Path(project.drf_document_path), payload, project.id)


@router.get("/{project_id}/boq", response_model=list[ProjectBoqItemOut])
def get_project_boq(
    project_id: int,
    response: Response,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectBoqItem]:
    project = _get_project_or_404(db, project_id)
    concurrency.set_version_header(response, project.boq_version)
    return project.boq_items


@router.get("/{project_id}/boq/export.xlsx")
def export_project_boq(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The saved BOQ as an Excel workbook: a Summary sheet, then one sheet per
    system. Anyone who can see the BOQ can export it."""
    project = _get_project_or_404(db, project_id)
    activity.record(db, current_user, "boq.exported", "Exported the BOQ", project=project, entity_type="boq",
                    entity_id=project.id, detail={"lines": len(project.boq_items), "version": project.boq_version})
    content = boq_workbook(
        project,
        project.boq_items,
        [sheet.system_code for sheet in project.design_sheets],
        exported_by=current_user.full_name,
        exported_at=utc_now(),
    )
    return _xlsx_response(content, f"EP-{project.ep_number} BOQ.xlsx")


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.put("/{project_id}/boq", response_model=list[ProjectBoqItemOut])
def replace_project_boq(
    project_id: int,
    items: list[ProjectBoqItemSave],
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> list[ProjectBoqItem]:
    """Replace the whole BOQ in one call.

    The client edits the table as a whole -- rows get reordered, inserted and
    removed together -- so it sends the finished list. `position` comes from
    the list order rather than the client. Each line carries the id it was
    loaded with, which is how its provenance (the sheet, page and raw read it
    came from) survives the save; a changed sheet value is recorded as an
    engineer's correction with the machine's value kept beside it.

    Refused with 409 when `If-Match` names a version that is no longer
    current: someone else saved the BOQ after this table was loaded.
    """
    project = _get_project_or_404(db, project_id)
    concurrency.require_current(if_match, project.boq_version, "The BOQ")
    before = len(project.boq_items)
    incoming = [
        {**item.model_dump(), "system_code": system_rules.effective_code(item.system_code, project)} for item in items
    ]
    project.boq_items = boq_provenance.rebuild_items(project, incoming, current_user)
    project.boq_version += 1
    db.commit()
    concurrency.set_version_header(response, project.boq_version)
    systems = sorted({item.system_code for item in project.boq_items if item.system_code})
    activity.record(db, current_user, "boq.saved", f"Saved the BOQ: {len(items)} line{'s' if len(items) != 1 else ''}",
                    project=project, entity_type="boq", entity_id=project.id,
                    detail={"lines": len(items), "lines_before": before, "systems": ", ".join(systems)})
    db.refresh(project)
    return project.boq_items


@router.post("/{project_id}/boq/ensure", response_model=BoqEnsureResponse)
def ensure_project_boq(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BoqEnsureResponse:
    """Return the BOQ, reading it out of the Design Sheets the first time.

    Idempotent by design: `boq_extracted_at` records that the read has been
    attempted, so opening the BOQ again returns what is stored rather than
    running OCR over the sheets a second time. Re-reading would either
    duplicate lines or discard the engineer's corrections, and OCR of a
    multi-page sheet is slow enough that doing it per page view would be felt.

    The stamp is set even when a sheet cannot be read, so an unreadable
    layout is not retried on every visit. Which sheets failed is stored with
    it and returned on every call, and their lines can be typed in by hand.
    """
    project = _get_project_or_404(db, project_id)

    if project.boq_extracted_at is not None:
        return _stored_boq(project)

    # The model reads the sheets the first time (app.ai.sheet_reader), and
    # that takes minutes for a scanned multi-page sheet: it runs as a job the
    # page follows, and the BOQ is read out of the stored reading when it is
    # done. A sheet read before -- the same content, on any project -- has
    # its reading in the database already and is read out of it here and now.
    from app.ai import sheet_reader
    from app.services import jobs

    if project.design_sheets and sheet_reader.available(project) is None and _needs_ai_read(db, project):
        with _boq_read_start_lock:
            db.expire_all()
            if project.boq_extracted_at is not None:
                return _stored_boq(project)
            running = jobs.active_job(db, project.id, BOQ_READ_JOB)
            if running is None:
                running = _start_boq_read(db, project, current_user)
        db.expire_all()
        if running.status == "succeeded" and project.boq_extracted_at is not None:
            # The job ran inline (tests) and is already done: this call read the sheets.
            return BoqEnsureResponse(items=project.boq_items, extracted=True,
                                     warnings=project.boq_extraction_warnings or [], version=project.boq_version)
        if project.boq_extracted_at is not None:
            return _stored_boq(project)
        from app.routers.jobs import _out as job_out

        return BoqEnsureResponse(items=[], extracted=False, warnings=[], version=project.boq_version,
                                 reading=job_out(running).model_dump(mode="json"))

    return _extract_boq(db, project, user_id=current_user.id)


def _needs_ai_read(db: Session, project: Project) -> bool:
    """Whether any of the project's sheets has no stored AI reading yet."""
    from app.ai import sheet_reader

    for sheet in project.design_sheets:
        path = Path(sheet.document_path)
        if not path.is_file():
            continue
        if sheet_reader.stored(db, extraction_pipeline.sha256_of(path) or "") is None:
            return True
    return False


def _start_boq_read(db: Session, project: Project, user: User):
    """The first read of the BOQ as a job: the model reads the sheets, the
    reading is stored, and the BOQ is written out of it -- the same read
    `ensure_project_boq` makes inline when no model call is needed."""
    project_id, user_id = project.id, user.id

    def work(session: Session, ctx) -> dict:
        target = session.get(Project, project_id)
        response = _extract_boq(session, target, user_id=user_id, ctx=ctx)
        actor = session.get(User, user_id)
        if response.extracted:
            lines = len(response.items)
            unread = len(response.warnings)
            activity.record(session, actor, "boq.read_by_ai",
                            f"AI read the Design Sheets into the BOQ: {lines} line{'s' if lines != 1 else ''}"
                            + (f"; {unread} sheet{'s' if unread != 1 else ''} not read" if unread else ""),
                            project=target, entity_type="boq", entity_id=project_id,
                            detail={"lines": lines, "warnings": response.warnings})
        return {"lines": len(response.items), "warnings": response.warnings, "extracted": response.extracted}

    from app.routers import jobs as jobs_router
    from app.services import jobs

    return jobs.start(db, kind=BOQ_READ_JOB, project_id=project.id, user_id=user.id, work=work,
                      run_inline=jobs_router.RUN_INLINE)


def _extract_boq(db: Session, project: Project, *, user_id: int | None, ctx=None) -> BoqEnsureResponse:
    """Read the sheets into the BOQ, once. Idempotent under the lock and the
    `boq_extracted_at` stamp; a second caller gets what the first stored."""
    with _boq_extraction_lock:
        # Another open may have finished the read while this one waited.
        db.expire(project)
        if project.boq_extracted_at is not None:
            return _stored_boq(project)

        # Read every sheet before writing anything: OCR takes seconds, and a
        # write held open that long would block every other write in the app.
        warnings: list[str] = []
        extracted: list[tuple[str | None, ExtractedBoqLine]] = []
        # Each sheet's read is kept with what it covered and could not
        # settle, so a dropped row is a row to review rather than a row gone.
        reads: list[tuple[ProjectDesignSheet, DesignSheetExtraction]] = []
        total_sheets = len(project.design_sheets)
        for index, sheet in enumerate(project.design_sheets):
            name = Path(sheet.document_path).name

            def on_page(page: int, pages: int, index=index, name=name) -> None:
                if ctx is not None:
                    ctx.progress(index * 100 + round(100 * (page - 1) / max(pages, 1)), total_sheets * 100,
                                 f"Reading {name}: page {page} of {pages} (sheet {index + 1} of {total_sheets})")

            result = _read_design_sheet(db, project, sheet, on_page=on_page, ctx=ctx, user_id=user_id)
            reads.append((sheet, result))
            if result.failure:
                warnings.append(f"{Path(sheet.document_path).name}: {result.failure}")
                continue
            extracted.extend((system_rules.effective_code(sheet.system_code, project), line) for line in result.lines)

        # Test and set the stamp in one statement. Checking it up front and
        # setting it after the OCR is what let two overlapping opens both
        # extract, storing every line twice.
        claimed = db.execute(
            update(Project)
            .where(Project.id == project.id, Project.boq_extracted_at.is_(None))
            .values(boq_extracted_at=utc_now(), boq_extraction_warnings=warnings or None)
        ).rowcount
        if not claimed:
            # Beaten by a request in another process; return what it stored.
            # The rollback ends the transaction the claim opened and expires
            # `project`, so nothing loaded before that request committed is
            # returned in place of its lines.
            db.rollback()
            return _stored_boq(project)

        db.commit()
        # The runs are recorded first, so every line can name the run -- and
        # through it the document, its hash and the parser -- it was read by.
        runs = {id(sheet): extraction_pipeline.record_design_sheet_run(db, project, sheet, result)
                for sheet, result in reads}
        position = len(project.boq_items)
        library = boq_provenance.part_library(db)
        for sheet, result in reads:
            if result.failure:
                continue
            system_code = system_rules.effective_code(sheet.system_code, project)
            for line in result.lines:
                # Prices, unit and remarks are left for the engineer. The
                # sheets carry Unit/Total Price columns but they are blank on
                # every sheet in the archive, so there is nothing to read and
                # nothing to check a read against.
                item = boq_provenance.extracted_item(
                    system_code=system_code, line=line, run=runs[id(sheet)], position=position,
                    manufacturer=_brand_for(system_code, project.systems, project.separate_ve_panel),
                )
                boq_provenance.check_catalog(item, library)
                project.boq_items.append(item)
                position += 1
        project.boq_version += 1
        db.commit()

    # The assistance stage runs after the lock is released: it may call a
    # model, and it must never make the read itself fail. With AI_ENABLED
    # false it calls nothing.
    if settings.ai_enabled:
        try:
            extraction_pipeline.assist_project(db, project)
        except Exception as exc:  # noqa: BLE001 -- assistance is optional; the BOQ is not
            warnings.append(f"AI assistance did not complete: {exc}")
    db.refresh(project)
    return BoqEnsureResponse(items=project.boq_items, extracted=True, warnings=warnings, version=project.boq_version)


def _read_design_sheet(db: Session, project: Project, sheet: ProjectDesignSheet, on_page=None, ctx=None,
                       user_id: int | None = None) -> DesignSheetExtraction:
    """One sheet's read, with its coverage and issues: the model's reading
    witnessed by the OCR read (app.ai.sheet_reader), or the OCR read alone
    where the model cannot be used.

    `extract_boq_lines` is the seam the test suite stubs -- a fake that
    returns lines, or raises -- and it is honoured: when the name bound in
    this module is the real function the detailed read is used, otherwise
    the stub's lines (or its error) are wrapped as a read that covered one
    page and settled everything else, and no model is asked.
    """
    if extract_boq_lines is design_sheet_extractor.extract_boq_lines:
        from app.ai import sheet_reader

        return sheet_reader.read_design_sheet(db, project, sheet, user_id=user_id, ctx=ctx, on_page=on_page)
    path = Path(sheet.document_path)
    try:
        lines = extract_boq_lines(path)
    except DesignSheetExtractionError as exc:
        return DesignSheetExtraction(failure=str(exc), issues=[Issue(IssueCode.UNRECOGNIZED_TABLE_LAYOUT)])
    return DesignSheetExtraction(lines=lines, coverage=Coverage(pages=[PageCoverage(page=1, processed=True)]))


def _stored_boq(project: Project) -> BoqEnsureResponse:
    return BoqEnsureResponse(
        items=project.boq_items,
        extracted=False,
        warnings=project.boq_extraction_warnings or [],
        version=project.boq_version,
    )


# The DRF Systems rows a Design Sheet's system code can stand for, before the
# project's own rules (app.services.system_rules): ELS is CBS, and an Edwards
# FAS carries Voice Evacuation too.
SYSTEM_CODE_DRF_ROWS: dict[str, tuple[str, ...]] = system_rules.BASE_ROWS


def _brand_for(system_code: str | None, systems: list[ProjectSystem], separate_ve_panel: bool = False) -> str | None:
    """The brand the DRF gives the system a Design Sheet is for. Blank rather
    than a guess when the code could be more than one row and they disagree."""
    integrated = system_rules.voice_evacuation_integrated(systems, separate_panel=separate_ve_panel)
    rows = system_rules.drf_rows(system_code, integrated=integrated)
    brands = {s.brand.strip() for s in systems if s.name in rows and s.brand and s.brand.strip()}
    return brands.pop() if len(brands) == 1 else None


# Everything ProjectDetailsIn carries except systems, which is a relationship.
_DETAIL_FIELDS = tuple(name for name in ProjectDetailsIn.model_fields if name != "systems")


def _apply_details(project: Project, details: ProjectDetailsIn) -> None:
    """Copy the reviewable project information onto `project`, replacing its
    systems wholesale -- the form edits them as one table."""
    for name in _DETAIL_FIELDS:
        setattr(project, name, getattr(details, name))
    project.systems = [ProjectSystem(**system.model_dump()) for system in details.systems]


# --- BOQ revisions ---


def _current_lines(project: Project) -> list[ProjectBoqItemIn]:
    return [ProjectBoqItemIn.model_validate(item, from_attributes=True) for item in project.boq_items]


def _revision_lines(revision: ProjectBoqRevision) -> list[ProjectBoqItemIn]:
    return [ProjectBoqItemIn.model_validate(item) for item in revision.items]


def _revision_summary(revision: ProjectBoqRevision) -> BoqRevisionSummaryOut:
    return BoqRevisionSummaryOut(
        number=revision.number,
        label=revision.label,
        note=revision.note,
        issued_at=revision.issued_at,
        issued_by_name=revision.issued_by.full_name,
        line_count=len(revision.items),
    )


def _get_revision_or_404(project: Project, number: int) -> ProjectBoqRevision:
    for revision in project.boq_revisions:
        if revision.number == number:
            return revision
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found")


@router.get("/{project_id}/boq/revisions", response_model=list[BoqRevisionSummaryOut])
def list_boq_revisions(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BoqRevisionSummaryOut]:
    """Newest first."""
    project = _get_project_or_404(db, project_id)
    return [_revision_summary(revision) for revision in reversed(project.boq_revisions)]


@router.post(
    "/{project_id}/boq/revisions",
    response_model=BoqRevisionSummaryOut,
    status_code=status.HTTP_201_CREATED,
)
def issue_boq_revision(
    project_id: int,
    payload: BoqRevisionIssue,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BoqRevisionSummaryOut:
    """Issue the saved BOQ as the next revision: Rev 00 first, then Rev 01...

    Refused when nothing has changed since the last one, which also stops a
    double-click from issuing the same BOQ twice under two numbers. "Changed"
    is decided by the same comparison the Revisions page shows, so the page
    and this check cannot disagree; a pure reordering of lines is not a change.
    """
    project = _get_project_or_404(db, project_id)
    lines = _current_lines(project)
    if not lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The BOQ has no lines to issue.")

    # A revision is what goes out, so nothing unsettled may be in it: the
    # documents are checked again now (a file can change after it was read)
    # and every BOQ check must pass (app.services.readiness).
    from app.services import document_intake, readiness

    if document_intake.project_documents(project):
        document_intake.run(db, project)
        db.refresh(project)
    blockers = readiness.boq_blockers(db, project)
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "not_ready", "message": "The BOQ cannot be issued yet: " + " ".join(blockers),
                    "blockers": blockers},
        )

    latest = project.boq_revisions[-1] if project.boq_revisions else None
    if latest is not None and not compare_boq(_revision_lines(latest), lines):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Nothing has changed since {latest.label}."
        )

    revision = ProjectBoqRevision(
        number=latest.number + 1 if latest is not None else 0,
        note=(payload.note or "").strip() or None,
        items=[line.model_dump(mode="json") for line in lines],
        issued_by_id=current_user.id,
    )
    project.boq_revisions.append(revision)
    try:
        db.commit()
    except IntegrityError:
        # Two issues at once both took the same number; the constraint let
        # one through.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another revision was issued at the same moment. Reload to see it.",
        )
    db.refresh(revision)
    activity.record(db, current_user, "boq.revision_issued", f"Issued BOQ {revision.label}",
                    project=project, entity_type="boq_revision", entity_id=revision.id,
                    detail={"lines": len(lines), "note": revision.note})
    db.refresh(revision)
    return _revision_summary(revision)


@router.get("/{project_id}/boq/revisions/{number}", response_model=BoqRevisionOut)
def get_boq_revision(
    project_id: int,
    number: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoqRevisionOut:
    revision = _get_revision_or_404(_get_project_or_404(db, project_id), number)
    return BoqRevisionOut(**_revision_summary(revision).model_dump(), items=_revision_lines(revision))


@router.get("/{project_id}/boq/revisions/{number}/export.xlsx")
def export_boq_revision(
    project_id: int,
    number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_or_404(db, project_id)
    revision = _get_revision_or_404(project, number)
    activity.record(db, current_user, "boq.revision_exported", f"Exported BOQ {revision.label}", project=project,
                    entity_type="boq_revision", entity_id=revision.id)
    content = boq_workbook(
        project,
        _revision_lines(revision),
        [sheet.system_code for sheet in project.design_sheets],
        exported_by=current_user.full_name,
        exported_at=utc_now(),
        revision_label=(
            f"{revision.label}, issued {revision.issued_at:%d %b %Y} by {revision.issued_by.full_name}"
        ),
    )
    return _xlsx_response(content, f"EP-{project.ep_number} BOQ {revision.label}.xlsx")


@router.get("/{project_id}/boq/compare", response_model=BoqCompareOut)
def compare_boq_versions(
    project_id: int,
    from_rev: int,
    to_rev: int | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoqCompareOut:
    """What changed from one revision to another, or -- without `to_rev` --
    from a revision to the BOQ as currently saved."""
    project = _get_project_or_404(db, project_id)
    base = _get_revision_or_404(project, from_rev)
    if to_rev is None:
        target_label, target_lines = "Current", _current_lines(project)
    else:
        target = _get_revision_or_404(project, to_rev)
        target_label, target_lines = target.label, _revision_lines(target)

    changes = compare_boq(_revision_lines(base), target_lines)
    return BoqCompareOut(
        from_label=base.label,
        to_label=target_label,
        changes=[
            BoqChangeOut(kind=c.kind, before=c.before, after=c.after, fields=c.fields) for c in changes
        ],
    )


@router.post("/{project_id}/reextract", response_model=ReextractionReportOut)
def reextract(
    project_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ReextractionReportOut:
    """Re-read the project's DRF and Design Sheets and report what differs.

    Unlike `/boq/ensure`, this runs every time it is called -- that is the
    point: the documents in the archive change after a project is created
    (a re-scanned DRF, a Design Sheet filed later) and nothing else looks at
    them again.

    It writes nothing. Differences are reported for an engineer to apply
    through the ordinary edit paths (`PUT /projects/{id}` for the fields,
    the BOQ table for the lines), because the stored values are their
    corrections and a re-read that overwrote them would be the exact
    regression `boq_extracted_at` was introduced to stop.

    POST rather than GET because it is expensive -- OCR over every sheet --
    and should not be run by a page render, a prefetch or a refresh.
    """
    project = _get_project_or_404(db, project_id)
    root = Path(settings.projects_root) if settings.projects_root else None

    report = reextract_project(project, root)

    return ReextractionReportOut(
        ep_number=report.ep_number,
        folder_found=report.folder_found,
        folder_path=report.folder_path,
        drf_path=report.drf_path,
        fields=[FieldComparisonOut(**vars(f)) for f in report.fields],
        scope_of_work=(
            FieldComparisonOut(**vars(report.scope_of_work)) if report.scope_of_work else None
        ),
        systems=[SystemComparisonOut(**vars(s)) for s in report.systems],
        sheets=[SheetComparisonOut(**vars(s)) for s in report.sheets],
        boq_changes=[
            BoqChangeOut(kind=c.kind, before=c.before, after=c.after, fields=c.fields)
            for c in report.boq_changes
        ],
        fields_differing=report.fields_differing,
        has_differences=report.has_differences,
        warnings=report.warnings,
        errors=report.errors,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    """Remove a project and its linked rows. The archive files it was built
    from are never touched -- only this platform's record of them."""
    project = _get_project_or_404(db, project_id)
    activity.record(db, current_user, "project.deleted", f"Deleted {activity.project_label(project)}",
                    project=project, entity_type="project", entity_id=project.id,
                    detail={"boq_lines": len(project.boq_items), "submittals": len(project.submittals)}, commit=False)
    from app.services.project_deletion import delete_project as delete_with_children

    delete_with_children(db, project)
    db.commit()


# --- documents ------------------------------------------------------------------

# A document the resolver could not find is uploaded by hand instead. Only
# the formats the extractors read are accepted, so an upload cannot turn
# into a file nothing can open.
UPLOAD_SUFFIXES = {".pdf", ".xlsx", ".xlsm"}
MAX_UPLOAD_BYTES = 60 * 1024 * 1024


def _save_upload(project: Project, upload: UploadFile, label: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{suffix or 'That file'} is not a format the platform reads (PDF or Excel)",
        )
    content = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The file is larger than 60 MB")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The file is empty")
    # The name is the uploader's; the content is what gets opened. A PDF must
    # start as one, a workbook must be a zip -- anything else is refused.
    if (suffix == ".pdf" and not content.startswith(b"%PDF")) or (suffix in (".xlsx", ".xlsm") and not content.startswith(b"PK")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"The file is named {suffix} but its content is not a {'PDF' if suffix == '.pdf' else 'workbook'}")

    folder = Path(settings.uploads_root) / f"EP-{project.ep_number}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{label} {stamp}{suffix}"
    path.write_bytes(content)
    return str(path.resolve())


@router.post("/{project_id}/documents/drf", response_model=ProjectOut)
def upload_drf(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Attach a DRF the resolver did not find. The fields it holds are not
    re-read into the project: those were reviewed when the project was
    created and are edited under Project Info."""
    project = _get_project_or_404(db, project_id)
    project.drf_document_path = _save_upload(project, file, "DRF")
    db.commit()
    activity.record(db, current_user, "document.drf_uploaded", f"Uploaded the DRF ({file.filename})",
                    project=project, entity_type="project", entity_id=project.id)
    db.refresh(project)
    return project


@router.post("/{project_id}/documents/design-sheets", response_model=ProjectOut)
def upload_design_sheet(
    project_id: int,
    file: UploadFile = File(...),
    system_code: str | None = Form(None),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Attach a Design Sheet the resolver did not find. A sheet added after
    the BOQ has been read is not read into it -- that read happens once per
    project (see the BOQ section of the README) -- so its lines are added by
    hand or the sheet is here for the record."""
    project = _get_project_or_404(db, project_id)
    code = system_rules.effective_code(system_code, project)
    path = _save_upload(project, file, f"Design Sheet {code}" if code else "Design Sheet")
    project.design_sheets.append(ProjectDesignSheet(system_code=code, document_path=path))
    db.commit()
    activity.record(db, current_user, "document.design_sheet_uploaded",
                    f"Uploaded a Design Sheet{f' for {code}' if code else ''} ({file.filename})",
                    project=project, entity_type="design_sheet", entity_id=project.design_sheets[-1].id)
    db.refresh(project)
    return project


@router.delete("/{project_id}/documents/design-sheets/{sheet_id}", response_model=ProjectOut)
def remove_design_sheet(
    project_id: int,
    sheet_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Detach a Design Sheet from the project. The file itself is left where
    it is -- in the archive it was never the platform's to delete, and an
    uploaded one stays for the record."""
    project = _get_project_or_404(db, project_id)
    sheet = next((s for s in project.design_sheets if s.id == sheet_id), None)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Design Sheet not found")
    removed = f"Removed a Design Sheet{f' for {sheet.system_code}' if sheet.system_code else ''} ({Path(sheet.document_path).name})"
    project.design_sheets.remove(sheet)
    db.commit()
    activity.record(db, current_user, "document.design_sheet_removed", removed,
                    project=project, entity_type="design_sheet", entity_id=sheet_id)
    db.refresh(project)
    return project


@router.get("/{project_id}/logs", response_model=ProjectLogsOut)
def project_logs(
    project_id: int,
    refresh: bool = False,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectLogsOut:
    """Return the current drawing index from the project's archive folder."""
    project = _get_project_or_404(db, project_id)
    systems = {(system.name or "").upper() for system in project.systems}
    systems |= {(item.system_code or "").upper() for item in project.boq_items}
    systems = {code for code in systems if code}
    if not project.source_folder_path:
        return ProjectLogsOut(systems=sorted(systems), drawings=[], searched=None, warnings=["The project has no archive folder to search."])
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        return ProjectLogsOut(systems=sorted(systems), drawings=[], searched=project.source_folder_path, warnings=["The project's archive folder is not reachable."])
    scan = get_log_scan(folder, refresh=refresh)
    records, warnings = scan.records, scan.warnings
    def output(row):
        return ProjectLogDrawingOut(**{key: value for key, value in vars(row).items() if key != "category"})
    return ProjectLogsOut(
        scanning=scan.scanning, processed_files=scan.processed, total_files=scan.total,
        systems=sorted(systems | {row.system_code for row in records if row.system_code}),
        material_submittals=[output(row) for row in records if row.category == "submittals"],
        drawings=[output(row) for row in records if row.category == "drawings"],
        samples=[output(row) for row in records if row.category == "samples"],
        searched=project.source_folder_path,
        warnings=warnings,
    )




@router.get("/{project_id}/logs/file")
def project_log_file(
    project_id: int,
    path: str,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(404, detail="Project directory not configured")
    root = Path(project.source_folder_path).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(403, detail="File is outside the project directory")
    if target.suffix.lower() not in {".pdf", ".dwg", ".dxf", ".doc", ".docx", ".xls", ".xlsx", ".zip"}:
        raise HTTPException(400, detail="Unsupported log file type")
    if not target.is_file():
        raise HTTPException(404, detail="File is no longer available")
    return FileResponse(target, filename=target.name, content_disposition_type="inline")
