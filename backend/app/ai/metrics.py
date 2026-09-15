"""How good the model's answers have been, measured against what engineers decided.

Every proposal carries an `outcome` once its issue is closed
(`pipeline.record_outcomes`). From those:

- **precision** -- of the values the model proposed and an engineer then
  decided on, the share taken exactly as proposed.
- **validated precision** -- the same, for proposals the platform marked
  `validated`. This is the number that must stay at 100%: a validated
  proposal that an engineer corrected is a false validation, and each one is
  listed.
- **recall** -- of the issues an engineer settled with a value, the share the
  model had proposed that exact value for.
- **abstention rate** -- the share of proposals where the model (or the
  validator) gave no value.
- **correction rate** -- of the decided proposals with a value, the share an
  engineer changed.

Usage (calls, cache hits, tokens, cost, errors) comes from `AiUsage`. All of
it is grouped by task, prompt version and model, so a prompt change shows up
as a new row instead of blurring the old one.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.ai.budget import Limits, calls_today
from app.core.timeutils import utc_now
from app.models import AiProposal, AiUsage, ExtractionIssue, ExtractionRun


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def proposal_metrics(db: Session, *, since: datetime | None = None, project_id: int | None = None) -> list[dict]:
    query = db.query(AiProposal).join(ExtractionIssue, AiProposal.issue_id == ExtractionIssue.id)
    if project_id is not None:
        query = query.join(ExtractionRun, ExtractionIssue.run_id == ExtractionRun.id).filter(ExtractionRun.project_id == project_id)
    if since is not None:
        query = query.filter(AiProposal.created_at >= since)

    groups: dict[tuple[str, str, str], dict] = defaultdict(lambda: {
        "proposals": 0, "from_cache": 0, "validated": 0, "needs_human_review": 0, "rejected_by_validator": 0,
        "insufficient_evidence": 0, "flagged_injection": 0, "decided": 0, "accepted": 0, "corrected": 0,
        "rejected": 0, "abstained": 0, "superseded": 0, "validated_decided": 0, "validated_accepted": 0,
        "false_validations": [], "_issues_with_value": set(), "_issues_recalled": set(),
    })
    for proposal in query.all():
        g = groups[(proposal.task, proposal.prompt_version, proposal.model)]
        g["proposals"] += 1
        g["from_cache"] += int(bool(proposal.from_cache))
        if proposal.injection_flags:
            g["flagged_injection"] += 1
        state_key = "rejected_by_validator" if proposal.state == "rejected" else proposal.state
        if state_key in g:
            g[state_key] += 1
        outcome = proposal.outcome
        if outcome is None:
            continue
        g[outcome] = g.get(outcome, 0) + 1
        if outcome in ("accepted", "corrected", "rejected"):
            g["decided"] += 1
            if proposal.state == "validated":
                g["validated_decided"] += 1
                if outcome == "accepted":
                    g["validated_accepted"] += 1
                else:
                    g["false_validations"].append({"proposal_id": proposal.id, "issue_id": proposal.issue_id,
                                                   "proposed": proposal.value, "decided": proposal.outcome_value,
                                                   "outcome": outcome})
        if proposal.outcome_value:
            g["_issues_with_value"].add(proposal.issue_id)
            if outcome == "accepted":
                g["_issues_recalled"].add(proposal.issue_id)

    rows = []
    for (task, prompt_version, model), g in sorted(groups.items()):
        with_value = len(g.pop("_issues_with_value"))
        recalled = len(g.pop("_issues_recalled"))
        rows.append({
            "task": task, "prompt_version": prompt_version, "model": model, **g,
            "precision": _rate(g["accepted"], g["decided"]),
            "validated_precision": _rate(g["validated_accepted"], g["validated_decided"]),
            "recall": _rate(recalled, with_value),
            "abstention_rate": _rate(g["insufficient_evidence"] + g["rejected_by_validator"], g["proposals"]),
            "correction_rate": _rate(g["corrected"], g["accepted"] + g["corrected"]),
        })
    return rows


def usage_metrics(db: Session, *, since: datetime | None = None, project_id: int | None = None) -> list[dict]:
    query = db.query(
        AiUsage.task, AiUsage.model,
        func.count(AiUsage.id), func.sum(case((AiUsage.cache_hit.is_(True), 1), else_=0)),
        func.coalesce(func.sum(AiUsage.input_tokens), 0), func.coalesce(func.sum(AiUsage.output_tokens), 0),
        func.coalesce(func.sum(AiUsage.estimated_cost), 0), func.coalesce(func.avg(AiUsage.latency_ms), 0),
    )
    if since is not None:
        query = query.filter(AiUsage.at >= since)
    if project_id is not None:
        query = query.filter(AiUsage.project_id == project_id)
    rows = []
    for task, model, count, hits, tokens_in, tokens_out, cost, latency in query.group_by(AiUsage.task, AiUsage.model).all():
        errors_query = db.query(AiUsage.outcome, func.count(AiUsage.id)).filter(
            AiUsage.task == task, AiUsage.model == model, AiUsage.outcome != "ok")
        if since is not None:
            errors_query = errors_query.filter(AiUsage.at >= since)
        if project_id is not None:
            errors_query = errors_query.filter(AiUsage.project_id == project_id)
        errors = dict(errors_query.group_by(AiUsage.outcome).all())
        rows.append({
            "task": task, "model": model, "calls": int(count), "cache_hits": int(hits or 0),
            "input_tokens": int(tokens_in), "output_tokens": int(tokens_out), "estimated_cost": round(float(cost), 6),
            "average_latency_ms": int(latency or 0), "errors": {k: int(v) for k, v in errors.items()},
        })
    return sorted(rows, key=lambda r: (r["task"], r["model"]))


def budget_status(db: Session, project_id: int | None) -> dict:
    """What a project may still spend today, and the per-job limits."""
    limits = Limits.from_settings()
    used = calls_today(db, project_id)
    since = utc_now() - timedelta(days=1)
    cost_query = db.query(func.coalesce(func.sum(AiUsage.estimated_cost), 0)).filter(AiUsage.at >= since)
    if project_id is not None:
        cost_query = cost_query.filter(AiUsage.project_id == project_id)
    return {
        "calls_last_24h": used,
        "calls_per_day_limit": limits.max_calls_per_project_per_day,
        "calls_remaining": max(0, limits.max_calls_per_project_per_day - used),
        "cost_last_24h": round(float(cost_query.scalar() or 0), 6),
        "priced": limits.priced,
        "cost_per_job_limit": limits.max_cost_per_job if limits.priced else None,
        "calls_per_document_limit": limits.max_calls_per_document,
        "elapsed_s_per_job_limit": limits.max_elapsed_s_per_job,
    }


def summary(db: Session, *, days: int | None = 30, project_id: int | None = None) -> dict:
    since = utc_now() - timedelta(days=days) if days else None
    from app.ai import evaluation

    return {
        "since": since,
        "proposals": proposal_metrics(db, since=since, project_id=project_id),
        "usage": usage_metrics(db, since=since, project_id=project_id),
        "budget": budget_status(db, project_id),
        "evaluations": evaluation.latest_reports(),
        "gates": evaluation.gate_status(),
        "disabled_tasks": evaluation.disabled_tasks(),
    }
