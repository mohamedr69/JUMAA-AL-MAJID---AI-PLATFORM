"""Starting, following and stopping long work (app.services.jobs).

  POST /projects/{id}/jobs/boq-reread         read the Design Sheets again into a candidate
  POST /projects/{id}/jobs/documents-intake   check the project's documents
  GET  /projects/{id}/jobs                    this project's recent jobs, newest first
  GET  /jobs/{job}                            one job: status, progress, result
  POST /jobs/{job}/cancel                     ask it to stop at the next step
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import BackgroundJob, Project, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, boq_candidates, document_intake, jobs

router = APIRouter(tags=["jobs"])

# Tests run a job before the response returns, so they need not poll.
RUN_INLINE = False


class JobOut(BaseModel):
    id: int
    project_id: int | None
    kind: str
    status: str
    progress: dict
    cancel_requested: bool
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _out(job: BackgroundJob) -> JobOut:
    return JobOut(id=job.id, project_id=job.project_id, kind=job.kind, status=job.status, progress=job.progress or {},
                  cancel_requested=job.cancel_requested, result=job.result, error=job.error, created_at=job.created_at,
                  started_at=job.started_at, finished_at=job.finished_at)


def _job_or_404(db: Session, job_id: int) -> BackgroundJob:
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such job")
    return job


def _refuse_duplicate(db: Session, project: Project, kind: str) -> None:
    running = jobs.active_job(db, project.id, kind)
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"code": "job_running", "message": "This is already running for the project.",
                                    "job_id": running.id})


@router.post("/projects/{project_id}/jobs/boq-reread", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_reread(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    project = _get_project_or_404(db, project_id)
    if not project.design_sheets:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no Design Sheets to read.")
    _refuse_duplicate(db, project, "boq_reread")
    user_id = current_user.id

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        target = session.get(Project, project_id)
        user = session.get(User, user_id)
        candidate = boq_candidates.build(session, target, user, ctx=ctx)
        s = candidate.summary
        activity.record(session, user, "boq.reread", f"Re-read the Design Sheets: {s['new_lines']} lines read, "
                        f"{s['changed']} changed, {s['added']} added, {s['removed']} removed",
                        project=target, entity_type="boq_candidate", entity_id=candidate.id,
                        detail={k: s[k] for k in ("old_lines", "new_lines", "changed", "added", "removed", "probable")})
        return {"candidate_id": candidate.id, "summary": {k: v for k, v in s.items() if k != "sheets"}}

    job = jobs.start(db, kind="boq_reread", project_id=project.id, user_id=current_user.id, work=work, run_inline=RUN_INLINE)
    return _out(job)


@router.post("/projects/{project_id}/jobs/documents-intake", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_intake(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    project = _get_project_or_404(db, project_id)
    _refuse_duplicate(db, project, "documents_intake")
    user_id = current_user.id

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        target = session.get(Project, project_id)
        rows = document_intake.run(session, target, ctx=ctx)
        blocked = sum(1 for r in rows if r.intake_status == document_intake.BLOCKED)
        activity.record(session, session.get(User, user_id), "documents.checked",
                        f"Checked {len(rows)} document{'s' if len(rows) != 1 else ''}"
                        + (f": {blocked} blocked" if blocked else ": nothing blocking"),
                        project=target, entity_type="project", entity_id=project_id,
                        detail={"documents": len(rows), "blocked": blocked})
        return {"documents": len(rows), "blocked": blocked}

    job = jobs.start(db, kind="documents_intake", project_id=project.id, user_id=current_user.id, work=work,
                     run_inline=RUN_INLINE)
    return _out(job)


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    _get_project_or_404(db, project_id)
    rows = db.query(BackgroundJob).filter(BackgroundJob.project_id == project_id).order_by(BackgroundJob.id.desc()).limit(20)
    return [_out(job) for job in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    return _out(_job_or_404(db, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    job = jobs.request_cancel(db, _job_or_404(db, job_id))
    return _out(job)
