"""The AI check of a project against its sources (app.ai.verification).

  GET  /projects/{id}/ai-verification               the latest BOQ and Project Info checks, and whether one can run
  POST /projects/{id}/ai-verification/ensure        start a check for a project never checked (AI_VERIFY_AUTO)
  POST /projects/{id}/jobs/ai-verify?scope=all      check now: "all", "boq" or "details"
  POST /projects/{id}/ai-verification/{vid}/undo    put back what a check changed
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai import verification
from app.core.config import get_settings
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import AiVerification, Project, User
from app.routers.jobs import JobOut, _out, _refuse_duplicate
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, jobs

router = APIRouter(tags=["ai verification"])

KIND = "ai_verify"
SCOPES = ("all", "boq", "details")


class VerificationOut(BaseModel):
    id: int
    scope: str
    status: str
    summary: dict
    items: list[dict]
    notes: list[str]
    error: str | None
    stale: bool
    can_undo: bool
    models: list[str]
    calls: int
    started_at: datetime
    finished_at: datetime | None


class VerificationStateOut(BaseModel):
    available: bool
    reason: str | None
    auto: bool
    job: JobOut | None
    boq: VerificationOut | None
    details: VerificationOut | None


def _verification_out(project: Project, record: AiVerification | None) -> VerificationOut | None:
    if record is None:
        return None
    current = project.boq_version if record.scope == "boq" else project.details_version
    stale = record.status == "completed" and record.version_after is not None and record.version_after != current
    return VerificationOut(
        id=record.id, scope=record.scope, status=record.status, summary=record.summary or {}, items=record.items or [],
        notes=record.notes or [], error=record.error, stale=stale,
        can_undo=record.status == "completed" and bool(record.undo) and not stale,
        models=record.models or [], calls=record.calls or 0, started_at=record.started_at, finished_at=record.finished_at,
    )


def _state(db: Session, project: Project) -> VerificationStateOut:
    reason = verification.available(project)
    running = jobs.active_job(db, project.id, KIND)
    return VerificationStateOut(
        available=reason is None, reason=reason, auto=get_settings().ai_verify_auto,
        job=_out(running) if running else None,
        boq=_verification_out(project, verification.latest(db, project.id, "boq")),
        details=_verification_out(project, verification.latest(db, project.id, "details")),
    )


def _start(db: Session, project: Project, user: User, scope: str, *, trigger: str):
    project_id, user_id = project.id, user.id

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        target = session.get(Project, project_id)
        actor = session.get(User, user_id)
        result: dict = {"trigger": trigger}
        scopes = ["details", "boq"] if scope == "all" else [scope]
        for part in scopes:
            if part == "details" and not target.drf_document_path:
                result["details"] = {"skipped": "no DRF"}
                continue
            if part == "boq" and not target.design_sheets:
                result["boq"] = {"skipped": "no Design Sheets"}
                continue
            try:
                record = (verification.verify_details if part == "details" else verification.verify_boq)(session, target, actor, ctx=ctx)
            except jobs.Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 -- one scope failing does not stop the other
                session.rollback()
                result[part] = {"error": str(exc)[:500]}
                continue
            s = record.summary or {}
            result[part] = {"verification_id": record.id, **{k: s.get(k, 0) for k in
                                                              ("confirmed", "corrected", "added", "removed", "unresolved", "not_checked")}}
            what = "the BOQ against the Design Sheets" if part == "boq" else "Project Info against the DRF"
            activity.record(session, actor, f"ai.verified_{part}",
                            f"AI checked {what}: {s.get('confirmed', 0)} confirmed, {s.get('corrected', 0)} corrected, "
                            f"{s.get('added', 0)} added, {s.get('removed', 0)} removed, {s.get('unresolved', 0)} unresolved",
                            project=target, entity_type="ai_verification", entity_id=record.id,
                            detail={"trigger": trigger, **{k: v for k, v in s.items() if isinstance(v, (int, bool))}})
            session.refresh(target)
        return result

    from app.routers import jobs as jobs_router

    return jobs.start(db, kind=KIND, project_id=project.id, user_id=user.id, work=work, run_inline=jobs_router.RUN_INLINE)


@router.get("/projects/{project_id}/ai-verification", response_model=VerificationStateOut)
def get_verification(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VerificationStateOut:
    return _state(db, _get_project_or_404(db, project_id))


@router.post("/projects/{project_id}/ai-verification/ensure", response_model=VerificationStateOut)
def ensure_verification(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VerificationStateOut:
    """Opening the BOQ or Project Info of a project the AI has never checked
    starts the check, once: a failed or undone check is not retried here."""
    project = _get_project_or_404(db, project_id)
    eligible = (get_settings().ai_verify_auto and current_user.role in CREATOR_ROLES
                and verification.available(project) is None
                and jobs.active_job(db, project.id, KIND) is None
                and db.query(AiVerification).filter(AiVerification.project_id == project.id).first() is None
                and (project.design_sheets or project.drf_document_path))
    if eligible:
        _start(db, project, current_user, "all", trigger="auto")
    return _state(db, project)


@router.post("/projects/{project_id}/jobs/ai-verify", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_verification(
    project_id: int,
    scope: str = "all",
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    project = _get_project_or_404(db, project_id)
    if scope not in SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"scope must be one of {', '.join(SCOPES)}")
    reason = verification.available(project)
    if reason:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=reason)
    _refuse_duplicate(db, project, KIND)
    return _out(_start(db, project, current_user, scope, trigger="manual"))


@router.post("/projects/{project_id}/ai-verification/{verification_id}/undo", response_model=VerificationStateOut)
def undo_verification(
    project_id: int,
    verification_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> VerificationStateOut:
    project = _get_project_or_404(db, project_id)
    record = db.get(AiVerification, verification_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such verification")
    try:
        verification.undo(db, project, record, current_user)
    except verification.UndoRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    activity.record(db, current_user, "ai.verification_undone", f"Undid the AI check #{record.id} ({record.scope})",
                    project=project, entity_type="ai_verification", entity_id=record.id)
    db.refresh(project)
    return _state(db, project)

