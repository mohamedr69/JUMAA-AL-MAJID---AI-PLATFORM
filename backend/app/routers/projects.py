import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
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
    ProjectDesignSheet,
    ProjectStatus,
    ProjectSystem,
    RoleEnum,
    User,
)
from app.schemas_project import (
    BoqEnsureResponse,
    DocumentCandidateOut,
    ExtractedFieldOut,
    ProjectBoqItemIn,
    ProjectBoqItemOut,
    ProjectCreate,
    ProjectOut,
    ProjectResolveRequest,
    ProjectResolveResponse,
    ProjectSystemIn,
)
from app.services.design_sheet_extractor import (
    DesignSheetExtractionError,
    ExtractedBoqLine,
    extract_boq_lines,
)
from app.services.drf_extractor import extract_drf_fields
from app.services.ep_resolver import resolve_project

router = APIRouter(prefix="/projects", tags=["projects"])
settings = get_settings()

CREATOR_ROLES = (RoleEnum.admin, RoleEnum.design_manager, RoleEnum.design_engineer)

# Deleting takes the project's documents, systems and BOQ with it, so it sits
# with the roles that own a project's lifecycle rather than everyone who can
# create one -- a Design Engineer can start a project but not erase one.
DELETER_ROLES = (RoleEnum.admin, RoleEnum.design_manager)

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
        project_name=payload.project_name,
        plot_number=payload.plot_number,
        location=payload.location,
        client=payload.client,
        consultant=payload.consultant,
        contractor=payload.contractor,
        contact_person=payload.contact_person,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        scope_of_work=payload.scope_of_work,
        other_information=payload.other_information,
        source_folder_path=payload.source_folder_path,
        drf_document_path=payload.drf_document_path,
        design_engineer_id=current_user.id,
        created_by_id=current_user.id,
    )
    project.design_sheets = [
        ProjectDesignSheet(system_code=ds.system_code, document_path=ds.document_path)
        for ds in payload.design_sheets
    ]
    project.systems = [
        ProjectSystem(
            name=s.name,
            brand=s.brand,
            method_statement=s.method_statement,
            drawing=s.drawing,
        )
        for s in payload.systems
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


@router.get("/{project_id}/boq", response_model=list[ProjectBoqItemOut])
def get_project_boq(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectBoqItem]:
    return _get_project_or_404(db, project_id).boq_items


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
        ProjectBoqItem(
            system_code=item.system_code,
            position=index,
            group_heading=item.group_heading,
            catalog_no=item.catalog_no,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total_price=item.total_price,
        )
        for index, item in enumerate(items)
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
    layout is reported once instead of retried on every visit; the returned
    warnings say which sheet, and its lines can be typed in by hand.
    """
    project = _get_project_or_404(db, project_id)

    if project.boq_extracted_at is not None:
        return BoqEnsureResponse(items=project.boq_items, extracted=False, warnings=[])

    with _boq_extraction_lock:
        # Another open may have finished the read while this one waited.
        db.expire(project)
        if project.boq_extracted_at is not None:
            return BoqEnsureResponse(items=project.boq_items, extracted=False, warnings=[])

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
            .values(boq_extracted_at=utc_now())
        ).rowcount
        if not claimed:
            # Beaten by a request in another process; return what it stored.
            # The rollback ends the transaction the claim opened and expires
            # `project`, so nothing loaded before that request committed is
            # returned in place of its lines.
            db.rollback()
            return BoqEnsureResponse(items=project.boq_items, extracted=False, warnings=[])

        position = len(project.boq_items)
        for system_code, line in extracted:
            # Prices are left for the engineer. The sheets carry Unit/Total
            # Price columns but they are blank on every sheet in the archive,
            # so there is nothing to read and nothing to check a read against.
            project.boq_items.append(
                ProjectBoqItem(
                    system_code=system_code,
                    position=position,
                    group_heading=line.group_heading,
                    catalog_no=line.catalog_no,
                    description=line.description,
                    quantity=line.quantity,
                )
            )
            position += 1

        db.commit()
        db.refresh(project)
        return BoqEnsureResponse(items=project.boq_items, extracted=True, warnings=warnings)


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
