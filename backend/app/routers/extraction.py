"""What a read could not settle, and what to do about it.

`GET /projects/{id}/extraction` lists the latest run per design sheet with
its coverage, open issues and any proposals; the BOQ page shows it as the
rows that need review. Accepting an issue adds the dropped row with the
quantity the engineer chose; rejecting closes it. `POST .../assist` asks the
model about the open eligible issues now (the same thing that happens on
the first open when assistance is on). `GET /admin/ai/usage` is the
diagnostics view: calls, tokens, cost, cache hits.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai import evidence as evidence_builder
from app.ai.provider import get_provider
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.extraction import pipeline
from app.extraction.issues import IssueCode, llm_task_for, route, is_row_issue
from app.models import AiUsage, ExtractionIssue, ExtractionRun, RoleEnum, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.schemas_project import ProjectBoqItemOut
from app.services import activity, design_sheet_extractor

router = APIRouter(prefix="/projects", tags=["extraction"])
admin_router = APIRouter(prefix="/admin/ai", tags=["ai diagnostics"])


class ProposalOut(BaseModel):
    id: int
    task: str
    model: str
    state: str
    state_reason: str
    value: str | None
    from_cache: bool
    injection_flags: list[str] | None = None
    outcome: str | None = None
    created_at: datetime


class IssueOut(BaseModel):
    id: int
    code: str
    severity: str
    page: int | None
    target: str
    detail: dict
    state: str
    state_reason: str | None
    llm_eligible: bool
    human_required: bool
    has_evidence_image: bool
    proposals: list[ProposalOut]


class RunOut(BaseModel):
    id: int
    kind: str
    document_name: str
    system_code: str | None
    outcome: str
    lines_accepted: int
    # Why the whole sheet could not be read, when it could not; the BOQ page
    # banner says so, and the review panel then does not say it again.
    failure: str | None = None
    unprocessed_pages: list[int]
    ai_calls: int
    ai_cost: float
    budget_exhausted: str | None
    trigger: str
    # "ocr" or "ai": who read the lines (app.ai.sheet_reader), and what the
    # reader noted about the read.
    reader: str = "ocr"
    notes: list[str] = []
    started_at: datetime
    issues: list[IssueOut]


class ReadingOut(BaseModel):
    """A stored AI reading of one of the project's documents: made once,
    kept for good, reused by every later open of a document with the same
    content (app.models.DocumentReading)."""

    id: int
    kind: str
    document_name: str
    model: str
    pages: int
    status: str
    error: str | None
    calls: int
    created_at: datetime


class ExtractionOut(BaseModel):
    ai_enabled: bool
    # Whether a call could actually be made: enabled is not the same as
    # credentialed, and the page says which.
    ai_ready: bool = False
    ai_status: str = ""
    runs: list[RunOut]
    open_issues: int
    readings: list[ReadingOut] = []


class DecisionIn(BaseModel):
    value: str | None = None
    reason: str | None = None


def _run_out(run: ExtractionRun) -> RunOut:
    coverage = run.coverage or {}
    unprocessed = [p["page"] for p in coverage.get("pages", []) if p.get("detected") and not p.get("processed")]
    return RunOut(
        id=run.id, kind=run.kind, document_name=Path(run.document_path).name, system_code=run.system_code,
        outcome=run.outcome, lines_accepted=run.lines_accepted, failure=run.failure, unprocessed_pages=unprocessed,
        ai_calls=run.ai_calls, ai_cost=float(run.ai_cost or 0), budget_exhausted=run.budget_exhausted,
        trigger=run.trigger, reader=run.reader or "ocr", notes=[str(n) for n in (coverage.get("notes") or [])],
        started_at=run.started_at,
        issues=[
            IssueOut(
                id=i.id, code=i.code, severity=i.severity, page=i.page, target=i.target, detail=i.detail or {},
                state=i.state, state_reason=i.state_reason,
                llm_eligible=llm_task_for(IssueCode(i.code)) is not None,
                human_required=route(IssueCode(i.code)).human_required,
                has_evidence_image=bool(i.region and i.page),
                proposals=[
                    ProposalOut(id=p.id, task=p.task, model=p.model, state=p.state, state_reason=p.state_reason,
                                value=p.value, from_cache=p.from_cache, injection_flags=p.injection_flags,
                                outcome=p.outcome, created_at=p.created_at)
                    for p in i.proposals
                ],
            )
            for i in run.issues
        ],
    )


def _latest_runs(db: Session, project_id: int) -> list[ExtractionRun]:
    runs = (
        db.query(ExtractionRun).filter(ExtractionRun.project_id == project_id)
        .order_by(ExtractionRun.id.desc()).all()
    )
    latest: dict[tuple[str, str], ExtractionRun] = {}
    for run in runs:
        latest.setdefault((run.kind, run.document_path), run)
    return sorted(latest.values(), key=lambda r: r.id)


@router.get("/{project_id}/extraction", response_model=ExtractionOut)
def get_extraction(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionOut:
    project = _get_project_or_404(db, project_id)
    runs = [_run_out(r) for r in _latest_runs(db, project_id)]
    provider = get_provider()
    from app.ai import project_policy, sheet_reader

    blocked = not project_policy.allowed(project)
    return ExtractionOut(
        ai_enabled=get_settings().ai_enabled and not blocked,
        ai_ready=bool(getattr(provider, "ready", False)) and not blocked,
        ai_status=project_policy.BLOCKED_MESSAGE if blocked else str(getattr(provider, "status", "")),
        runs=runs,
        open_issues=sum(1 for r in runs for i in r.issues
                        if is_row_issue(i) and i.state in ("open", "proposed", "starved")),
        readings=[
            ReadingOut(id=r.id, kind=r.kind, document_name=Path(r.document_path).name, model=r.model, pages=r.pages,
                       status=r.status, error=r.error, calls=r.calls, created_at=r.created_at)
            for r in sheet_reader.readings_for(db, project)
        ],
    )


def _issue_or_404(db: Session, project_id: int, issue_id: int) -> ExtractionIssue:
    row = db.get(ExtractionIssue, issue_id)
    if row is None or row.run.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such issue on this project")
    return row


@router.get("/{project_id}/extraction/issues/{issue_id}/evidence.png")
def issue_evidence(
    project_id: int,
    issue_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The cell the issue is about, as the engineer (and the model) sees it."""
    row = _issue_or_404(db, project_id, issue_id)
    if not row.region or not row.page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This issue has no image region")
    try:
        png = evidence_builder.render_region(Path(row.run.document_path), row.page, tuple(row.region),
                                             design_sheet_extractor.RENDER_DPI)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"The document could not be rendered: {exc}")
    return Response(png, media_type="image/png")


@router.post("/{project_id}/extraction/issues/{issue_id}/accept", response_model=ProjectBoqItemOut)
def accept_issue(
    project_id: int,
    issue_id: int,
    payload: DecisionIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    row = _issue_or_404(db, project_id, issue_id)
    try:
        item = pipeline.accept_issue(db, project, row, current_user, payload.value)
    except pipeline.AcceptRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    activity.record(db, current_user, "boq.issue_accepted",
                    f"Accepted a reading-review line into the BOQ: {item.description[:80]} (qty {item.quantity or '-'})",
                    project=project, entity_type="extraction_issue", entity_id=issue_id,
                    detail={"catalog_no": item.catalog_no, "quantity": item.quantity})
    db.refresh(item)
    return item


@router.post("/{project_id}/extraction/issues/{issue_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_issue(
    project_id: int,
    issue_id: int,
    payload: DecisionIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_or_404(db, project_id)
    row = _issue_or_404(db, project_id, issue_id)
    try:
        pipeline.reject_issue(db, row, current_user, payload.reason)
    except pipeline.AcceptRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    activity.record(db, current_user, "boq.issue_rejected", "Rejected a reading-review line",
                    project=project, entity_type="extraction_issue", entity_id=issue_id,
                    detail={"reason": payload.reason} if payload.reason else None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/extraction/assist", response_model=ExtractionOut)
def assist(
    project_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ExtractionOut:
    """Ask the model about the open eligible issues now. Idempotent: an
    issue already proposed, resolved or starved is not asked again, and an
    identical request in flight is joined rather than repeated."""
    project = _get_project_or_404(db, project_id)
    from app.ai import project_policy

    if not project_policy.allowed(project):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=project_policy.BLOCKED_MESSAGE)
    provider = get_provider()
    if not getattr(provider, "ready", False):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(getattr(provider, "status", "AI assistance is unavailable")))
    pipeline.assist_project(db, project, trigger="manual")
    return get_extraction(project_id, _current_user, db)


class UsageRowOut(BaseModel):
    at: datetime
    project_id: int | None
    task: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    estimated_cost: float
    latency_ms: int
    cache_hit: bool
    escalated: bool
    outcome: str


class UsageOut(BaseModel):
    since: datetime
    calls: int
    cache_hits: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    estimated_cost: float
    median_latency_ms: int | None
    p95_latency_ms: int | None
    recent: list[UsageRowOut]


@admin_router.get("/usage", response_model=UsageOut)
def ai_usage(
    days: int = 30,
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> UsageOut:
    since = utc_now() - timedelta(days=max(1, days))
    rows = db.query(AiUsage).filter(AiUsage.at >= since).order_by(AiUsage.at.desc()).all()
    calls = [r for r in rows if not r.cache_hit]
    latencies = sorted(r.latency_ms for r in calls)

    def percentile(p: float) -> int | None:
        if not latencies:
            return None
        return latencies[min(len(latencies) - 1, int(round((len(latencies) - 1) * p)))]

    return UsageOut(
        since=since,
        calls=len(calls),
        cache_hits=sum(1 for r in rows if r.cache_hit),
        input_tokens=sum(r.input_tokens or 0 for r in calls),
        output_tokens=sum(r.output_tokens or 0 for r in calls),
        cached_input_tokens=sum(r.cached_input_tokens or 0 for r in calls),
        estimated_cost=float(sum(float(r.estimated_cost or 0) for r in calls)),
        median_latency_ms=percentile(0.5),
        p95_latency_ms=percentile(0.95),
        recent=[
            UsageRowOut(
                at=r.at, project_id=r.project_id, task=r.task, model=r.model, input_tokens=r.input_tokens,
                output_tokens=r.output_tokens, cached_input_tokens=r.cached_input_tokens,
                reasoning_tokens=r.reasoning_tokens, estimated_cost=float(r.estimated_cost or 0),
                latency_ms=r.latency_ms, cache_hit=r.cache_hit, escalated=r.escalated, outcome=r.outcome,
            )
            for r in rows[:50]
        ],
    )


@router.get("/{project_id}/ai/budget")
def project_ai_budget(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """What the project may still ask the model today, and whether it may at all."""
    from app.ai import metrics, project_policy

    project = _get_project_or_404(db, project_id)
    provider = get_provider()
    return {
        **metrics.budget_status(db, project_id),
        "ai_enabled": get_settings().ai_enabled,
        "ai_ready": bool(getattr(provider, "ready", False)),
        "policy": project.ai_policy,
        "allowed": project_policy.allowed(project),
    }


@admin_router.get("/metrics")
def ai_metrics(
    days: int = 30,
    project_id: int | None = None,
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> dict:
    """Proposal quality against engineers' decisions, usage, budget, the
    latest evaluation reports and the task gates."""
    from app.ai import metrics

    return metrics.summary(db, days=max(1, days), project_id=project_id)


@admin_router.post("/evaluations/cases")
def build_evaluation_cases(
    task: str = "read_cell",
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> dict:
    """Add every reviewed issue for the task to the evaluation set. Calls no
    model; a run is started from `scripts/ai_eval.py`, where its cost is
    chosen deliberately."""
    from app.ai import evaluation

    if task not in evaluation.RUNNABLE_TASKS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"No evaluation harness for {task!r} yet")
    cases = evaluation.cases_from_reviews(db, task=task)
    path = evaluation.cases_path(task)
    evaluation.write_cases(cases, path)
    total = len(evaluation.load_cases(path)) if path.exists() else 0
    activity.record(db, current_user, "ai.evaluation_cases_built", f"Built {len(cases)} {task} evaluation cases",
                    detail={"task": task, "added_or_updated": len(cases), "total": total})
    return {"task": task, "added_or_updated": len(cases), "total": total}
