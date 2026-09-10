from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, ProjectDesignSheet, ProjectStatus, RoleEnum, User
from app.schemas_project import (
    DocumentCandidateOut,
    ExtractedFieldOut,
    ProjectCreate,
    ProjectOut,
    ProjectResolveRequest,
    ProjectResolveResponse,
)
from app.services.drf_extractor import extract_drf_fields
from app.services.ep_resolver import resolve_project

router = APIRouter(prefix="/projects", tags=["projects"])
settings = get_settings()

CREATOR_ROLES = (RoleEnum.admin, RoleEnum.design_manager, RoleEnum.design_engineer)


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
        extraction_warnings=extraction_warnings,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Project:
    existing = db.query(Project).filter(Project.ep_number == payload.ep_number).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A project for EP number '{payload.ep_number}' already exists",
        )

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
        systems=",".join(payload.systems) if payload.systems else None,
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

    db.add(project)
    db.commit()
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
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
