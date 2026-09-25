"""The project's state, for every page that shows it (app.services.project_state).

GET /projects/{id}/state             each system's material submittal, shop drawings and samples, and the open actions
GET /projects/{id}/actions           the project's actions (open; `resolved=true` for the resolved ones too)
GET /projects/{id}/changes?since=N   PROJECT_DATA_CHANGED after change N, and the latest change id
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import ProjectAction, User
from app.routers.projects import _get_project_or_404
from app.services import project_state

router = APIRouter(tags=["project state"])


class ActionOut(BaseModel):
    id: int
    key: str
    kind: str
    system_code: str | None
    entity_type: str | None
    entity_id: int | None
    text: str
    severity: str
    link: str | None
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None

    model_config = {"from_attributes": True}


class ChangeOut(BaseModel):
    id: int
    project_id: int
    entity_type: str
    entity_id: int | None
    system: str | None
    change_type: str
    at: datetime


class ChangesOut(BaseModel):
    latest: int
    changes: list[ChangeOut]


def _actions(db: Session, project, *, resolved: bool = False, submittals: list | None = None) -> list[ProjectAction]:
    """The actions as the records stand now. They are brought up to date by
    every writer; one that was missed -- a project last touched before
    actions were kept -- is brought up to date here, and committed only when
    something changed."""
    if project_state.reconcile_actions(db, project, submittals):
        db.commit()
    if not resolved:
        return project_state.open_actions(db, project.id)
    return (db.query(ProjectAction).filter(ProjectAction.project_id == project.id)
            .order_by(ProjectAction.resolved_at.is_not(None), ProjectAction.system_code, ProjectAction.id).all())


@router.get("/projects/{project_id}/state")
def project_state_view(project_id: int, _current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)) -> dict:
    """Project Home's summary: every system with its material submittal
    status (per brand), shop drawing and sample status, and the open
    actions -- worked out from the records, the same ones the Material
    Submittals, Drawings and Logs pages show."""
    project = _get_project_or_404(db, project_id)
    submittals = project_state.items(db, project)
    actions = _actions(db, project, submittals=submittals)
    out = project_state.summary(db, project, submittals)
    out["actions"] = [ActionOut.model_validate(a).model_dump() for a in actions]
    out["latest_change"] = project_state.changes_since(db, project.id, None)[0]
    return out


@router.get("/projects/{project_id}/actions", response_model=list[ActionOut])
def project_actions(project_id: int, resolved: bool = False, _current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> list[ProjectAction]:
    return _actions(db, _get_project_or_404(db, project_id), resolved=resolved)


@router.get("/projects/{project_id}/changes", response_model=ChangesOut)
def project_changes(project_id: int, since: int | None = None, _current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> ChangesOut:
    """What changed in the project's records after change `since`. Left out,
    only the latest id: the cursor a page starts from."""
    _get_project_or_404(db, project_id)
    latest, rows = project_state.changes_since(db, project_id, since)
    return ChangesOut(latest=latest, changes=[
        ChangeOut(id=r.id, project_id=r.project_id, entity_type=r.entity_type, entity_id=r.entity_id,
                  system=r.system_code, change_type=r.change_type, at=r.at) for r in rows])
