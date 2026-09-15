"""The intake gate and the readiness dashboard.

  GET  /projects/{id}/readiness                               every check, with its status
  GET  /projects/{id}/documents/intake                        what the gate found, per document
  POST /projects/{id}/documents/intake                        check every document now
  POST /projects/{id}/documents/intake/{doc}/acknowledge      accept a finding, with a reason
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import ProjectDocument, RoleEnum, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, document_intake, readiness

router = APIRouter(prefix="/projects", tags=["readiness"])

MANAGER_ROLES = (RoleEnum.admin, RoleEnum.design_manager)


class DocumentIntakeOut(BaseModel):
    id: int
    role: str
    system_code: str | None
    filename: str
    relative_path: str | None
    # The absolute location, for those who can edit the project; viewers get
    # the relative path only.
    path: str | None
    sha256: str | None
    size: int | None
    mime: str | None
    page_count: int | None
    printed_pages: dict | None
    intake_status: str
    findings: list[dict]
    acknowledged: list[dict]
    checked_at: datetime | None
    intake_version: str | None


class AcknowledgeIn(BaseModel):
    code: str
    reason: str = Field(min_length=10, max_length=500)


def _out(row: ProjectDocument, user: User) -> DocumentIntakeOut:
    can_see_path = user.role in CREATOR_ROLES
    return DocumentIntakeOut(
        id=row.id, role=row.role, system_code=row.system_code, filename=row.filename, relative_path=row.relative_path,
        path=row.path if can_see_path else None, sha256=row.sha256, size=row.size, mime=row.mime, page_count=row.page_count,
        printed_pages=row.printed_pages, intake_status=row.intake_status, findings=row.findings or [],
        acknowledged=row.acknowledged or [], checked_at=row.checked_at, intake_version=row.intake_version,
    )


@router.get("/{project_id}/readiness")
def project_readiness(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return readiness.evaluate(db, _get_project_or_404(db, project_id))


@router.get("/{project_id}/documents/intake", response_model=list[DocumentIntakeOut])
def list_intake(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DocumentIntakeOut]:
    _get_project_or_404(db, project_id)
    rows = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).order_by(ProjectDocument.role, ProjectDocument.id)
    return [_out(row, current_user) for row in rows]


@router.post("/{project_id}/documents/intake", response_model=list[DocumentIntakeOut])
def run_intake(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> list[DocumentIntakeOut]:
    """Check every document on the project: present, readable, whole, this
    project's, inside its folder, not attached twice."""
    project = _get_project_or_404(db, project_id)
    rows = document_intake.run(db, project)
    blocked = sum(1 for r in rows if r.intake_status == document_intake.BLOCKED)
    activity.record(db, current_user, "documents.checked",
                    f"Checked {len(rows)} document{'s' if len(rows) != 1 else ''}"
                    + (f": {blocked} blocked" if blocked else ": nothing blocking"),
                    project=project, entity_type="project", entity_id=project.id,
                    detail={"documents": len(rows), "blocked": blocked})
    return [_out(row, current_user) for row in rows]


@router.post("/{project_id}/documents/intake/{document_id}/acknowledge", response_model=DocumentIntakeOut)
def acknowledge_finding(
    project_id: int,
    document_id: int,
    payload: AcknowledgeIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DocumentIntakeOut:
    """Accept a finding as not disqualifying, with the reason. Findings about
    whose document this is (another EP number, outside the folder, fewer pages
    than printed) take a design manager or admin."""
    project = _get_project_or_404(db, project_id)
    row = db.get(ProjectDocument, document_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such document on this project")
    if payload.code in document_intake.ACK_CODES_NEEDING_MANAGER and current_user.role not in MANAGER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="A design manager or admin must accept a finding about a document's identity")
    try:
        row = document_intake.acknowledge(db, row, payload.code, payload.reason, current_user)
    except document_intake.AcknowledgeRefused as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    activity.record(db, current_user, "documents.finding_accepted",
                    f"Accepted {payload.code.replace('_', ' ').lower()} on {row.filename}: {payload.reason[:120]}",
                    project=project, entity_type="project_document", entity_id=row.id,
                    detail={"code": payload.code, "reason": payload.reason})
    return _out(row, current_user)
