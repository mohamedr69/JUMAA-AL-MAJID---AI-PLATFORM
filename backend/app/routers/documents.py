"""The project's document index (app.services.document_sync).

  GET  /projects/{id}/documents/status        when the folder was last synced, what is stale, what failed
  POST /projects/{id}/jobs/sync-documents     queue a sync: the worker process stats the folder and reads
                                              only new or changed files (app.workers.sync_worker)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import BackgroundJob, User
from app.routers import jobs as jobs_router
from app.routers.jobs import JobOut, _out
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import document_sync, jobs

router = APIRouter(prefix="/projects", tags=["documents"])

JOB_KIND = document_sync.SYNC_JOB_KIND


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
    # Whether the background worker that runs syncs is alive. A queued sync
    # with no worker waits until one starts (start.bat starts it).
    worker_running: bool = True


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
                             folder_reachable=bool(folder and folder.is_dir()),
                             worker_running=jobs.worker_running(db))


@router.post("/{project_id}/jobs/sync-documents", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    """Queue a sync of the index with the folder and answer at once. The
    worker process runs it: every file's size and time is checked, only new
    or changed content is read, and what depends on a changed document is
    marked stale. Nothing heavy happens in this request.

    Asked again while the project's sync is queued or running -- a second
    click, another tab, a colleague -- it answers with that same job,
    `already_active` set, and starts nothing. The database refuses a second
    active sync of a project (app.services.jobs.enqueue), so this holds for
    requests that arrive together too."""
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no archive folder to sync")
    job, created = jobs.enqueue(db, kind=JOB_KIND, project_id=project.id, user_id=current_user.id)
    if created and jobs_router.RUN_INLINE:
        # Tests: run it here, through the worker's own code, so they need not poll.
        from app.workers.sync_worker import Worker

        Worker().run_claimed(job.id)
        db.expire_all()
        job = db.get(BackgroundJob, job.id)
    out = _out(job)
    out.already_active = not created
    return out
