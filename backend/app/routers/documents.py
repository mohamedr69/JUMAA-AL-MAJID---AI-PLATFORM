"""The project's document index (app.services.document_sync).

  GET  /projects/{id}/documents/status        when the folder was last synced, what is stale, what failed
  POST /projects/{id}/jobs/sync-documents     sync now: stat the folder, read only new or changed files
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, User
from app.routers import jobs as jobs_router
from app.routers.jobs import JobOut, _out, _refuse_duplicate
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, document_sync, jobs

router = APIRouter(prefix="/projects", tags=["documents"])

JOB_KIND = "sync_documents"


class StaleOut(BaseModel):
    dependent_type: str
    dependent_id: str
    reason: str
    source: str
    source_role: str
    source_state: str


class FailedOut(BaseModel):
    path: str
    error: str | None


class DocumentStatusOut(BaseModel):
    synced_at: datetime | None
    documents: int
    by_state: dict[str, int]
    failed: list[FailedOut]
    stale: list[StaleOut]
    syncing: bool
    job: JobOut | None
    folder: str | None
    folder_reachable: bool


@router.get("/{project_id}/documents/status", response_model=DocumentStatusOut)
def document_status(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentStatusOut:
    """What the index holds -- from the database, the folder untouched."""
    from pathlib import Path

    project = _get_project_or_404(db, project_id)
    running = jobs.active_job(db, project.id, JOB_KIND)
    folder = Path(project.source_folder_path) if project.source_folder_path else None
    return DocumentStatusOut(**document_sync.status(db, project), syncing=running is not None,
                             job=_out(running) if running else None, folder=project.source_folder_path,
                             folder_reachable=bool(folder and folder.is_dir()))


@router.post("/{project_id}/jobs/sync-documents", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    """Sync the index with the folder, as a job: every file's size and time
    is checked, only new or changed content is read, and what depends on a
    changed document is marked stale."""
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no archive folder to sync")
    _refuse_duplicate(db, project, JOB_KIND)
    user_id = current_user.id

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        target = session.get(Project, project_id)
        actor = session.get(User, user_id)
        result = document_sync.sync(session, target, user=actor, ctx=ctx)
        activity.record(session, actor, "documents.synced",
                        f"Synced the project folder: {result['files']} file{'s' if result['files'] != 1 else ''}, "
                        f"{result['new']} new, {result['changed']} changed, {result['unchanged']} unchanged, "
                        f"{result['removed']} removed; {result['read_by_ai']} read by the AI",
                        project=target, entity_type="project", entity_id=project_id,
                        detail={k: v for k, v in result.items() if isinstance(v, (int, bool))})
        return result

    job = jobs.start(db, kind=JOB_KIND, project_id=project.id, user_id=current_user.id, work=work,
                     run_inline=jobs_router.RUN_INLINE)
    return _out(job)
