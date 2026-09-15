"""The evaluation set, the harness that scores a model against it, and the
gates that keep a task switched off until it has passed.

**Cases** are labelled examples: a document, the page and region an issue
points at, and the value an engineer settled on (or `null` when the right
answer is "cannot be read"). They are built from reviewed issues --
`cases_from_reviews` -- so the set grows as engineers work, and are kept as
JSON Lines under `backend/evaluations/cases/<task>.jsonl`. They hold values
from real documents and stay out of git.

**A run** sends each case through the same evidence builder, validation and
independent OCR check the live pipeline uses, but bypasses the result cache
(a cached answer is not a fresh measurement) and the project budget. The
report records the prompt, schema and parser versions and the model, and
scores:

- precision: correct values among the values proposed
- recall: correct values among the cases that have a value
- abstention rate: cases with no value proposed
- false validations: cases the platform would have marked `validated` with a
  wrong value -- the number that must be zero
- tokens and estimated cost

**Gates.** A task routed as eligible but not in `issues.ENABLED_TASKS` is
switched on only by a passing report for the current prompt version: enough
cases, precision at or above the bar, no false validations. `table_layout`
is such a task; it has no evidence builder yet, so no report for it can be
produced and it stays off -- which is the intent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai.budget import Limits
from app.ai.proposals import SCHEMA_VERSION, Validation, parse, validate
from app.ai.provider import PROMPT_VERSION, AiProvider
from app.core.timeutils import utc_now

EVAL_ROOT = Path(__file__).resolve().parent.parent.parent / "evaluations"

# Tasks the harness can run today.
RUNNABLE_TASKS = frozenset({"read_cell"})


@dataclass(frozen=True)
class Gate:
    min_cases: int
    min_precision: float
    max_false_validations: int = 0


# Tasks that are off until a report passes. The bars are deliberately high:
# a layout interpretation proposes whole rows.
GATES: dict[str, Gate] = {
    "table_layout": Gate(min_cases=50, min_precision=0.98),
}


@dataclass
class EvalCase:
    id: str
    task: str
    document: str
    page: int | None
    region: list[int] | None
    target: str
    expected: str | None            # None: the right answer is "cannot be read"
    code: str = "AMBIGUOUS_OCR"
    detail: dict = field(default_factory=dict)
    document_sha256: str | None = None
    source: str = "review"


@dataclass
class CaseResult:
    id: str
    expected: str | None
    proposed: str | None
    state: str
    reason: str
    correct: bool
    false_validation: bool
    flags: list[str]
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


# --- building the set -----------------------------------------------------------


def cases_from_reviews(db: Session, *, task: str = "read_cell") -> list[EvalCase]:
    """One case per issue an engineer settled with a value, where the issue
    points at a region of a document that is still on disk."""
    from app.extraction.issues import IssueCode, llm_task_for
    from app.models import ExtractionIssue

    cases = []
    rows = (
        db.query(ExtractionIssue)
        .filter(ExtractionIssue.state == "resolved", ExtractionIssue.resolved_value.isnot(None))
        .order_by(ExtractionIssue.id)
        .all()
    )
    for row in rows:
        try:
            code = IssueCode(row.code)
        except ValueError:
            continue
        if llm_task_for(code) != task or row.page is None or not row.region:
            continue
        run = row.run
        if not run or not Path(run.document_path).is_file():
            continue
        cases.append(EvalCase(
            id=f"issue-{row.id}", task=task, document=run.document_path, page=row.page, region=list(row.region),
            target=row.target, expected=row.resolved_value, code=row.code, detail=dict(row.detail or {}),
            document_sha256=run.document_sha256,
        ))
    return cases


def cases_path(task: str, root: Path | None = None) -> Path:
    return (root or EVAL_ROOT) / "cases" / f"{task}.jsonl"


def write_cases(cases: list[EvalCase], path: Path) -> None:
    """Merge into the file by case id: a later review of the same issue
    replaces the earlier label; cases added by hand are kept."""
    existing = {c.id: c for c in load_cases(path)} if path.exists() else {}
    for case in cases:
        existing[case.id] = case
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in existing.values():
            handle.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")


def load_cases(path: Path) -> list[EvalCase]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(EvalCase(**json.loads(line)))
    return cases


# --- running ----------------------------------------------------------------------


def _default_evidence(case: EvalCase, max_output_tokens: int):
    from app.ai import evidence as evidence_builder
    from app.extraction.issues import Issue, IssueCode
    from app.services import design_sheet_extractor

    issue = Issue(IssueCode(case.code), page=case.page, region=tuple(case.region) if case.region else None,
                  target=case.target, detail=case.detail)
    return evidence_builder.for_quantity_cell(Path(case.document), issue, render_dpi=design_sheet_extractor.RENDER_DPI,
                                              max_output_tokens=max_output_tokens), issue


def _default_independent(case: EvalCase, issue) -> set[str]:
    from app.extraction import pipeline
    from app.services import design_sheet_extractor

    return pipeline.independent_readings(Path(case.document), issue, design_sheet_extractor.RENDER_DPI)


def _same(a: str | None, b: str | None) -> bool:
    norm = lambda s: "".join((s or "").split()).lower()  # noqa: E731
    return norm(a) == norm(b)


def run_case(case: EvalCase, provider: AiProvider, *, max_output_tokens: int = 400,
             evidence_for: Callable | None = None, independent_for: Callable | None = None) -> CaseResult:
    from app.ai import guard
    from app.services import design_sheet_extractor

    if case.task not in RUNNABLE_TASKS:
        raise ValueError(f"the harness cannot run {case.task!r} cases yet")
    try:
        evidence, issue = (evidence_for or _default_evidence)(case, max_output_tokens)
    except Exception as exc:  # noqa: BLE001 -- a case whose document cannot be rendered is reported, not fatal
        return CaseResult(case.id, case.expected, None, "error", "", False, False, [], error=f"evidence: {exc}")
    independent = (independent_for or _default_independent)(case, issue)
    flags = guard.scan_parts(evidence.request.parts)
    response = provider.complete(evidence.request)
    if not response.ok:
        return CaseResult(case.id, case.expected, None, "error", response.error_detail or "", False, False, flags,
                          response.usage.input_tokens, response.usage.output_tokens, error=response.error)
    parsed = parse(response.data)
    if isinstance(parsed, Validation):
        verdict = parsed
    else:
        verdict = validate(parsed, task=case.task, allowed_target=case.target, sent_regions=evidence.sent_regions,
                           independent_readings=independent, word_quantities=design_sheet_extractor.WORD_QUANTITIES,
                           injection_flags=flags)
    proposed = verdict.value if verdict.state in ("validated", "needs_human_review") else None
    if case.expected is None:
        correct = proposed is None
    else:
        correct = proposed is not None and _same(proposed, case.expected)
    false_validation = verdict.state == "validated" and not correct
    return CaseResult(case.id, case.expected, proposed, verdict.state, verdict.reason, correct, false_validation, flags,
                      response.usage.input_tokens, response.usage.output_tokens)


def score(results: list[CaseResult], limits: Limits | None = None) -> dict:
    limits = limits or Limits.from_settings()
    scored = [r for r in results if r.error is None]
    proposed = [r for r in scored if r.proposed is not None]
    with_value = [r for r in scored if r.expected is not None]
    tokens_in = sum(r.input_tokens or 0 for r in results)
    tokens_out = sum(r.output_tokens or 0 for r in results)
    rate = lambda n, d: round(n / d, 4) if d else None  # noqa: E731
    return {
        "cases": len(results),
        "scored": len(scored),
        "errors": len(results) - len(scored),
        "proposed": len(proposed),
        "correct": sum(1 for r in scored if r.correct),
        "precision": rate(sum(1 for r in proposed if r.correct), len(proposed)),
        "recall": rate(sum(1 for r in with_value if r.correct and r.proposed is not None), len(with_value)),
        "abstention_rate": rate(len(scored) - len(proposed), len(scored)),
        "validated": sum(1 for r in scored if r.state == "validated"),
        "false_validations": sum(1 for r in scored if r.false_validation),
        "flagged_injection": sum(1 for r in scored if r.flags),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "estimated_cost": round(limits.cost(tokens_in, tokens_out), 6) if limits.priced else None,
    }


def run(cases: list[EvalCase], provider: AiProvider, *, task: str, root: Path | None = None,
        model: str = "", save: bool = True, **kwargs) -> dict:
    started = time.perf_counter()
    results = [run_case(c, provider, **kwargs) for c in cases if c.task == task]
    report = {
        "task": task,
        "at": utc_now().isoformat(),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "parser_version": _parser_version(),
        "provider": getattr(provider, "name", ""),
        "model": model,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "score": score(results),
        "results": [asdict(r) for r in results],
    }
    report["gate"] = judge_gate(task, report)
    if save:
        folder = (root or EVAL_ROOT) / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{task}-{utc_now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _parser_version() -> str:
    from app.services import design_sheet_extractor

    return design_sheet_extractor.PARSER_VERSION


# --- gates ------------------------------------------------------------------------


def switched_off(task: str) -> bool:
    """Whether the server's AI_DISABLED_TASKS names the task."""
    from app.core.config import get_settings

    names = {t.strip().lower() for t in (get_settings().ai_disabled_tasks or "").split(",") if t.strip()}
    return task.lower() in names


def disabled_tasks() -> list[str]:
    from app.core.config import get_settings

    return sorted({t.strip() for t in (get_settings().ai_disabled_tasks or "").split(",") if t.strip()})


def judge_gate(task: str, report: dict) -> dict:
    gate = GATES.get(task)
    if gate is None:
        return {"gated": False, "passed": None, "reasons": []}
    s = report.get("score") or {}
    reasons = []
    if (s.get("scored") or 0) < gate.min_cases:
        reasons.append(f"{s.get('scored') or 0} scored cases; {gate.min_cases} needed")
    if s.get("precision") is None or s["precision"] < gate.min_precision:
        reasons.append(f"precision {s.get('precision')}; {gate.min_precision} needed")
    if (s.get("false_validations") or 0) > gate.max_false_validations:
        reasons.append(f"{s.get('false_validations')} false validations; at most {gate.max_false_validations}")
    if report.get("prompt_version") != PROMPT_VERSION:
        reasons.append("the report is for an earlier prompt version")
    return {"gated": True, "passed": not reasons, "reasons": reasons}


_reports_cache: dict[Path, tuple[tuple, list]] = {}


def _reports(root: Path | None = None) -> list[tuple[Path, dict]]:
    folder = (root or EVAL_ROOT) / "reports"
    if not folder.is_dir():
        return []
    # Asked on every issue listing: read the folder again only when it changed.
    stamp = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in folder.glob("*.json")))
    cached = _reports_cache.get(folder)
    if cached and cached[0] == stamp:
        return cached[1]
    found = []
    for path in sorted(folder.glob("*.json")):
        try:
            found.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    _reports_cache[folder] = (stamp, found)
    return found


def latest_reports(root: Path | None = None) -> list[dict]:
    """The newest report per task, without its per-case results."""
    latest: dict[str, dict] = {}
    for path, report in _reports(root):
        task = report.get("task")
        if task:
            latest[task] = {"file": path.name, **{k: v for k, v in report.items() if k != "results"},
                            "gate": judge_gate(task, report)}
    return [latest[t] for t in sorted(latest)]


def gate_open(task: str, root: Path | None = None) -> bool:
    """Whether a gated task has a passing report for the current prompt."""
    if task not in GATES:
        return False
    return any(r.get("task") == task and judge_gate(task, r)["passed"] for _p, r in _reports(root))


def gate_status(root: Path | None = None) -> list[dict]:
    rows = []
    for task, gate in sorted(GATES.items()):
        rows.append({"task": task, "open": gate_open(task, root), "runnable": task in RUNNABLE_TASKS,
                     "min_cases": gate.min_cases, "min_precision": gate.min_precision,
                     "max_false_validations": gate.max_false_validations})
    return rows
