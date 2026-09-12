import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
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
    DocumentCandidateOut,
    ExtractedFieldOut,
    ProjectBoqItemIn,
    ProjectBoqItemOut,
    ProjectCreate,
    ProjectDetailsIn,
    ProjectOut,
    ProjectResolveRequest,
    ProjectResolveResponse,
    ProjectSystemIn,
)
from app.schemas_design import ProjectLogDrawingOut, ProjectLogsOut
from app.services.design_sheet_extractor import (
    DesignSheetExtractionError,
    ExtractedBoqLine,
    extract_boq_lines,
)
from app.services.boq_export import boq_workbook
from app.services.boq_revisions import compare_boq
from app.services.drf_extractor import extract_drf_fields
from app.services.ep_resolver import resolve_project
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
    extracted_systems: list[ProjectSystemIn] = []
    extraction_warnings: list[str] = []
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

    return ProjectResolveResponse(
        ep_number=result.ep_number,
        folder_found=not result.folder_not_found,
        is_ambiguous=result.is_ambiguous,
        matched_folders=[str(f) for f in result.matched_folders],
        drf_candidates=[
            DocumentCandidateOut(path=str(m.path), filename=m.path.name, system_guess=m.system_guess)
            for m in result.drf_candidates
        ],
        design_sheet_candidates=[
            DocumentCandidateOut(path=str(m.path), filename=m.path.name, system_guess=m.system_guess)
            for m in result.design_sheet_candidates
        ],
        warnings=result.warnings,
        errors=result.errors,
        extracted_fields=extracted_fields,
        extracted_scope_of_work=extracted_scope_of_work,
        extracted_systems=extracted_systems,
        extraction_warnings=extraction_warnings,
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
        ProjectDesignSheet(system_code=ds.system_code, document_path=ds.document_path)
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
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Project]:
    return db.query(Project).order_by(Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Project:
    return _get_project_or_404(db, project_id)


@router.put("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectDetailsIn,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Correct the project information after creation -- the DRF read is a
    suggestion, and a wrong value found later needs somewhere to be fixed.
    The EP number and document paths are not editable: they tie the project
    to its archive folder."""
    project = _get_project_or_404(db, project_id)
    _apply_details(project, payload)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}/boq", response_model=list[ProjectBoqItemOut])
def get_project_boq(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectBoqItem]:
    return _get_project_or_404(db, project_id).boq_items


@router.get("/{project_id}/boq/export.xlsx")
def export_project_boq(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The saved BOQ as an Excel workbook: a Summary sheet, then one sheet per
    system. Anyone who can see the BOQ can export it."""
    project = _get_project_or_404(db, project_id)
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
    items: list[ProjectBoqItemIn],
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> list[ProjectBoqItem]:
    """Replace the whole BOQ in one call.

    The client edits the table as a whole -- rows get reordered, inserted and
    removed together -- so sending the finished list avoids having to invent
    and reconcile per-row ids for lines the user has not saved yet. `position`
    comes from the list order rather than the client.
    """
    project = _get_project_or_404(db, project_id)
    project.boq_items = [
        ProjectBoqItem(position=index, **item.model_dump()) for index, item in enumerate(items)
    ]
    db.commit()
    db.refresh(project)
    return project.boq_items


@router.post("/{project_id}/boq/ensure", response_model=BoqEnsureResponse)
def ensure_project_boq(
    project_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
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

    with _boq_extraction_lock:
        # Another open may have finished the read while this one waited.
        db.expire(project)
        if project.boq_extracted_at is not None:
            return _stored_boq(project)

        # Read every sheet before writing anything: OCR takes seconds, and a
        # write held open that long would block every other write in the app.
        warnings: list[str] = []
        extracted: list[tuple[str | None, ExtractedBoqLine]] = []
        for sheet in project.design_sheets:
            try:
                lines = extract_boq_lines(Path(sheet.document_path))
            except DesignSheetExtractionError as exc:
                warnings.append(f"{Path(sheet.document_path).name}: {exc}")
                continue
            extracted.extend((sheet.system_code, line) for line in lines)

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

        position = len(project.boq_items)
        for system_code, line in extracted:
            # Prices, unit and remarks are left for the engineer. The sheets
            # carry Unit/Total Price columns but they are blank on every sheet
            # in the archive, so there is nothing to read and nothing to check
            # a read against.
            project.boq_items.append(
                ProjectBoqItem(
                    system_code=system_code,
                    position=position,
                    group_heading=line.group_heading,
                    manufacturer=_brand_for(system_code, project.systems),
                    catalog_no=line.catalog_no,
                    description=line.description,
                    quantity=line.quantity,
                )
            )
            position += 1

        db.commit()
        db.refresh(project)
        return BoqEnsureResponse(items=project.boq_items, extracted=True, warnings=warnings)


def _stored_boq(project: Project) -> BoqEnsureResponse:
    return BoqEnsureResponse(
        items=project.boq_items,
        extracted=False,
        warnings=project.boq_extraction_warnings or [],
    )


# The DRF Systems rows a Design Sheet's system code can stand for. ELS is
# "Emergency Lighting", which the DRF splits into two rows; on EP-29495 it is
# the central battery sheet.
SYSTEM_CODE_DRF_ROWS: dict[str, tuple[str, ...]] = {
    "FAS": ("Fire Alarm",),
    "VES": ("Voice Evacuation",),
    "PAVA": ("PA/VA & BGM",),
    "CBS": ("Central Battery System",),
    "EML": ("Emergency Light Monitoring",),
    "ELS": ("Central Battery System", "Emergency Light Monitoring"),
}


def _brand_for(system_code: str | None, systems: list[ProjectSystem]) -> str | None:
    """The brand the DRF gives the system a Design Sheet is for. Blank rather
    than a guess when the code could be more than one row and they disagree."""
    rows = SYSTEM_CODE_DRF_ROWS.get((system_code or "").upper(), ())
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


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    _current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    """Remove a project and its linked rows. The archive files it was built
    from are never touched -- only this platform's record of them."""
    project = _get_project_or_404(db, project_id)
    db.delete(project)
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
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Attach a DRF the resolver did not find. The fields it holds are not
    re-read into the project: those were reviewed when the project was
    created and are edited under Project Info."""
    project = _get_project_or_404(db, project_id)
    project.drf_document_path = _save_upload(project, file, "DRF")
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/documents/design-sheets", response_model=ProjectOut)
def upload_design_sheet(
    project_id: int,
    file: UploadFile = File(...),
    system_code: str | None = Form(None),
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Attach a Design Sheet the resolver did not find. A sheet added after
    the BOQ has been read is not read into it -- that read happens once per
    project (see the BOQ section of the README) -- so its lines are added by
    hand or the sheet is here for the record."""
    project = _get_project_or_404(db, project_id)
    code = (system_code or "").strip().upper() or None
    path = _save_upload(project, file, f"Design Sheet {code}" if code else "Design Sheet")
    project.design_sheets.append(ProjectDesignSheet(system_code=code, document_path=path))
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}/documents/design-sheets/{sheet_id}", response_model=ProjectOut)
def remove_design_sheet(
    project_id: int,
    sheet_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    """Detach a Design Sheet from the project. The file itself is left where
    it is -- in the archive it was never the platform's to delete, and an
    uploaded one stays for the record."""
    project = _get_project_or_404(db, project_id)
    sheet = next((s for s in project.design_sheets if s.id == sheet_id), None)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Design Sheet not found")
    project.design_sheets.remove(sheet)
    db.commit()
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
