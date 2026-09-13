"""Bounded spend per job, per document and per project per day.

A `JobBudget` is opened for one pipeline run. Before each call the caller
reserves the estimated input and the output ceiling; after the call it
reconciles with what the provider reported. Reservations are counted under
a lock so concurrent issues in one run see one total. The per-project daily
count comes from the usage table, so it survives restarts.

When a limit trips, the caller is told which one; it records the issue as
starved and moves on. Nothing here retries.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.models import AiUsage


@dataclass
class Limits:
    max_input_tokens_per_task: int
    max_output_tokens_per_task: int
    max_calls_per_document: int
    max_calls_per_project_per_day: int
    max_cost_per_job: float
    max_elapsed_s_per_job: float
    max_escalations_per_document: int
    price_input: float
    price_output: float
    price_cached: float

    @classmethod
    def from_settings(cls) -> "Limits":
        s = get_settings()
        return cls(
            s.ai_max_input_tokens_per_task, s.ai_max_output_tokens_per_task, s.ai_max_calls_per_document,
            s.ai_max_calls_per_project_per_day, s.ai_max_cost_per_job, s.ai_max_elapsed_s_per_job,
            s.ai_max_escalations_per_document, s.ai_price_input_per_million, s.ai_price_output_per_million,
            s.ai_price_cached_input_per_million,
        )

    @property
    def priced(self) -> bool:
        """Whether a cost can be worked out at all. With no prices set there
        is nothing to cap and nothing to report; the call-count and time
        limits still bind."""
        return self.price_input > 0 or self.price_output > 0

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        uncached = max(0, input_tokens - cached_tokens)
        return (uncached * self.price_input + cached_tokens * self.price_cached + output_tokens * self.price_output) / 1_000_000


class BudgetExceeded(Exception):
    def __init__(self, limit: str) -> None:
        super().__init__(limit)
        self.limit = limit


@dataclass
class JobBudget:
    limits: Limits
    calls_today_before: int
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    escalations: int = 0
    reserved_cost: float = 0.0
    spent_cost: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, estimated_input: int, max_output: int, *, escalation: bool = False) -> float:
        """Take the estimated cost of one call off the job's budget, or
        raise `BudgetExceeded` naming the limit. Returns the reservation."""
        with self._lock:
            if time.monotonic() - self.started > self.limits.max_elapsed_s_per_job:
                raise BudgetExceeded("elapsed_time")
            if estimated_input > self.limits.max_input_tokens_per_task:
                raise BudgetExceeded("input_tokens_per_task")
            if max_output > self.limits.max_output_tokens_per_task:
                raise BudgetExceeded("output_tokens_per_task")
            if self.calls + 1 > self.limits.max_calls_per_document:
                raise BudgetExceeded("calls_per_document")
            if self.calls_today_before + self.calls + 1 > self.limits.max_calls_per_project_per_day:
                raise BudgetExceeded("calls_per_project_per_day")
            if escalation and self.escalations + 1 > self.limits.max_escalations_per_document:
                raise BudgetExceeded("escalations_per_document")
            estimate = self.limits.cost(estimated_input, max_output)
            if self.limits.priced and self.spent_cost + self.reserved_cost + estimate > self.limits.max_cost_per_job:
                raise BudgetExceeded("cost_per_job")
            self.reserved_cost += estimate
            self.calls += 1
            if escalation:
                self.escalations += 1
            return estimate

    def reconcile(self, reservation: float, input_tokens: int | None, output_tokens: int | None, cached: int | None) -> float:
        """Replace the reservation with the provider's reported usage."""
        with self._lock:
            self.reserved_cost -= reservation
            actual = self.limits.cost(input_tokens or 0, output_tokens or 0, cached or 0) if input_tokens is not None else reservation
            self.spent_cost += actual
            return actual


def calls_today(db: Session, project_id: int | None) -> int:
    since = utc_now() - timedelta(days=1)
    query = db.query(func.count(AiUsage.id)).filter(AiUsage.at >= since, AiUsage.cache_hit.is_(False))
    if project_id is not None:
        query = query.filter(AiUsage.project_id == project_id)
    return int(query.scalar() or 0)


def open_budget(db: Session, project_id: int | None) -> JobBudget:
    return JobBudget(limits=Limits.from_settings(), calls_today_before=calls_today(db, project_id))
