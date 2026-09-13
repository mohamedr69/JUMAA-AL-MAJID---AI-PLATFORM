"""The run: one document's read, recorded with its issues; the assistance
stage that asks a model about the issues the router allows, within budget;
and the two things an engineer can do with a proposal.

Order of a run (the letters are the brief's):
  A identify   -- document path, content hash, parser version
  C parse      -- the deterministic extractor (done by the caller)
  D/E validate -- the extractor's coverage and issues, routed by code
  F repair     -- the extractor's own fallbacks have already run
  G ask        -- `assist_run`: evidence for each eligible issue, cache,
                  budget, provider call
  H validate   -- `proposals.validate` against the evidence and an
                  independent OCR re-read
  I preserve   -- issues stay open / proposed / starved; nothing is applied
  J save       -- run, issues, proposals and usage rows

`accept_issue` is the only path by which a proposal reaches the BOQ, and
it goes through the same rows the BOQ editor writes, as the accepting
engineer, refusing to touch a line that has since been entered by hand.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai import cache as result_cache
from app.ai import evidence as evidence_builder
from app.ai.budget import BudgetExceeded, JobBudget, open_budget
from app.ai.proposals import SCHEMA_VERSION, Validation, parse, validate
from app.ai.provider import PROMPT_VERSION, AiProvider, AiRequest, estimate_input_tokens, get_provider
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.extraction.issues import Issue, IssueCode, Outcome, llm_task_for, route
from app.models import (
    AiProposal,
    AiUsage,
    ExtractionIssue,
    ExtractionRun,
    Project,
    ProjectBoqItem,
    ProjectDesignSheet,
    User,
)
from app.services import design_sheet_extractor
from app.services.design_sheet_extractor import DesignSheetExtraction

SCOPE = "default"   # single tenant today; part of every cache key so a later split changes keys


# --- A: identity ---------------------------------------------------------------


def sha256_of(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# --- J: saving a run -----------------------------------------------------------


def record_design_sheet_run(
    db: Session,
    project: Project,
    sheet: ProjectDesignSheet,
    result: DesignSheetExtraction,
    *,
    trigger: str = "auto",
) -> ExtractionRun:
    run = ExtractionRun(
        project_id=project.id,
        kind="design_sheet",
        document_path=sheet.document_path,
        document_sha256=sha256_of(Path(sheet.document_path)),
        system_code=sheet.system_code,
        parser_version=design_sheet_extractor.PARSER_VERSION,
        outcome=result.outcome.value,
        lines_accepted=len(result.lines),
        coverage=result.coverage.to_dict(),
        failure=result.failure,
        trigger=trigger,
        finished_at=utc_now(),
    )
    for issue in result.issues:
        run.issues.append(_issue_row(issue))
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _issue_row(issue: Issue) -> ExtractionIssue:
    return ExtractionIssue(
        code=issue.code.value,
        severity=route(issue.code).severity,
        page=issue.page,
        region=list(issue.region) if issue.region else None,
        target=issue.target,
        detail=issue.detail,
        state="open",
    )


def _as_issue(row: ExtractionIssue) -> Issue:
    return Issue(
        code=IssueCode(row.code),
        page=row.page,
        region=tuple(row.region) if row.region else None,
        target=row.target,
        detail=dict(row.detail or {}),
    )


# --- H: the independent check --------------------------------------------------


def independent_readings(pdf_path: Path, issue: Issue, render_dpi: int) -> set[str]:
    """What Tesseract reads off the same crop in its other modes. A
    proposal that agrees with one of these is `validated`; one that agrees
    with none is shown to the engineer as unconfirmed."""
    if issue.page is None or issue.region is None:
        return set()
    try:
        import io

        import pytesseract
        from PIL import Image

        png = evidence_builder.render_region(pdf_path, issue.page, issue.region, render_dpi)
        image = Image.open(io.BytesIO(png))
        readings: set[str] = set()
        for psm in (7, 11, 6):
            text = pytesseract.image_to_string(image, config=f"--psm {psm}")
            for token in re.findall(r"[A-Za-z0-9]+", text):
                readings.add(token)
                cleaned = design_sheet_extractor._clean_quantity(token)
                if cleaned:
                    readings.add(cleaned)
        raw = (issue.detail.get("raw_quantity") or "").strip()
        if raw:
            cleaned = design_sheet_extractor._clean_quantity(raw)
            if cleaned:
                readings.add(cleaned)
        return readings
    except Exception:  # noqa: BLE001 -- no Tesseract, or an unreadable crop: nothing independent to say
        return set()


# --- G: one call, cached, budgeted, de-duplicated -------------------------------


def _log_usage(db: Session, *, project_id: int | None, run_id: int | None, task: str, model: str,
               usage, cost: float, latency_ms: int, cache_hit: bool, escalated: bool, outcome: str) -> None:
    db.add(AiUsage(
        project_id=project_id, run_id=run_id, task=task, model=model,
        input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
        cached_input_tokens=getattr(usage, "cached_input_tokens", None),
        reasoning_tokens=getattr(usage, "reasoning_tokens", None),
        estimated_cost=cost, latency_ms=latency_ms, cache_hit=cache_hit, escalated=escalated, outcome=outcome,
    ))
    db.commit()


def ask(
    db: Session,
    *,
    project_id: int | None,
    run_id: int | None,
    document_sha256: str,
    evidence: evidence_builder.Evidence,
    context: dict[str, Any],
    budget: JobBudget,
    provider: AiProvider | None = None,
    allowed_target: str,
    allowed_values: set[str] | None = None,
    independent: set[str] | None = None,
) -> tuple[Validation, dict | None, bool, str, str]:
    """Ask once about one issue. Returns (validation, proposal data,
    from_cache, model, cache_key). Raises `BudgetExceeded`."""
    settings = get_settings()
    provider = provider or get_provider()
    request: AiRequest = evidence.request
    model = settings.ai_model_small if request.tier == "small" else settings.ai_model_standard
    key = result_cache.cache_key(
        scope=SCOPE, document_sha256=document_sha256, evidence_fingerprint=evidence.fingerprint,
        task=request.task, context=context, parser_version=design_sheet_extractor.PARSER_VERSION,
        prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, model=model,
    )
    request.idempotency_key = key

    def judge(data: dict | None) -> Validation:
        parsed = parse(data)
        if isinstance(parsed, Validation):
            return parsed
        return validate(
            parsed, task=request.task, allowed_target=allowed_target, sent_regions=evidence.sent_regions,
            allowed_values=allowed_values, independent_readings=independent,
            word_quantities=design_sheet_extractor.WORD_QUANTITIES,
        )

    with result_cache.InFlight(key) as first:
        cached = result_cache.get(db, key, project_id=project_id, ttl_days=settings.ai_cache_ttl_days,
                                  document_sha256=document_sha256)
        if cached is not None:
            _log_usage(db, project_id=project_id, run_id=run_id, task=request.task, model=cached.get("model", model),
                       usage=None, cost=0.0, latency_ms=0, cache_hit=True, escalated=False, outcome="ok")
            return judge(cached.get("data")), cached.get("data"), True, cached.get("model", model), key
        if not first:
            # The request that made the call has finished without caching
            # (an error): nothing to reuse, and nothing to repeat either.
            return Validation("insufficient_evidence", "an identical request just failed"), None, False, model, key

        reservation = budget.reserve(estimate_input_tokens(request), request.max_output_tokens)
        response = provider.complete(request)
        cost = budget.reconcile(reservation, response.usage.input_tokens, response.usage.output_tokens,
                                response.usage.cached_input_tokens)
        escalated = False
        if response.error == "invalid_response":
            # One escalation to the standard tier, if the budget allows it.
            try:
                request.tier = "standard"
                reservation = budget.reserve(estimate_input_tokens(request), request.max_output_tokens, escalation=True)
                _log_usage(db, project_id=project_id, run_id=run_id, task=request.task, model=response.model or model,
                           usage=response.usage, cost=cost, latency_ms=response.latency_ms, cache_hit=False,
                           escalated=False, outcome=response.error)
                response = provider.complete(request)
                cost = budget.reconcile(reservation, response.usage.input_tokens, response.usage.output_tokens,
                                        response.usage.cached_input_tokens)
                escalated = True
            except BudgetExceeded:
                pass
        _log_usage(db, project_id=project_id, run_id=run_id, task=request.task, model=response.model or model,
                   usage=response.usage, cost=cost, latency_ms=response.latency_ms, cache_hit=False,
                   escalated=escalated, outcome=response.error or "ok")
        if not response.ok:
            reason = f"{response.error}: {response.error_detail}" if response.error_detail else (response.error or "no reply")
            return Validation("insufficient_evidence", reason), None, False, response.model or model, key
        verdict = judge(response.data)
        if verdict.state != "rejected":
            result_cache.put(db, key, {"data": response.data, "model": response.model or model},
                             project_id=project_id, document_sha256=document_sha256, task=request.task)
        return verdict, response.data, False, response.model or model, key


# --- the assistance stage for a stored run --------------------------------------


def assist_run(db: Session, project: Project, run: ExtractionRun, *, budget: JobBudget,
               provider: AiProvider | None = None) -> None:
    settings = get_settings()
    pdf_path = Path(run.document_path)
    document_sha = run.document_sha256 or sha256_of(pdf_path) or ""
    dpi = design_sheet_extractor.RENDER_DPI
    for row in run.issues:
        if row.state != "open":
            continue
        issue = _as_issue(row)
        task = llm_task_for(issue.code)
        if task is None:
            continue
        if task != "read_cell" or issue.region is None or issue.page is None:
            continue
        try:
            evidence = evidence_builder.for_quantity_cell(pdf_path, issue, render_dpi=dpi,
                                                          max_output_tokens=settings.ai_max_output_tokens_per_task)
        except Exception as exc:  # noqa: BLE001 -- the page could not be rendered: nothing to send
            row.state_reason = f"evidence could not be built: {exc}"
            db.commit()
            continue
        if evidence.byte_size > evidence_builder.MAX_EVIDENCE_BYTES:
            row.state_reason = "evidence larger than the limit"
            db.commit()
            continue
        independent = independent_readings(pdf_path, issue, dpi)
        try:
            verdict, data, from_cache, model, key = ask(
                db, project_id=project.id, run_id=run.id, document_sha256=document_sha, evidence=evidence,
                context={"target": issue.target}, budget=budget, provider=provider,
                allowed_target=issue.target, independent=independent,
            )
        except BudgetExceeded as exc:
            row.state = "starved"
            row.state_reason = f"budget: {exc.limit}"
            run.budget_exhausted = exc.limit
            db.commit()
            continue
        if not from_cache:
            run.ai_calls += 1
        run.ai_cost = float(run.ai_cost or 0) + float(budget.spent_cost)
        proposal = AiProposal(
            issue=row, task=task, cache_key=key, model=model, prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION, proposal=data or {}, state=verdict.state, state_reason=verdict.reason,
            value=verdict.value, from_cache=from_cache,
        )
        db.add(proposal)
        row.state = "proposed" if verdict.state in ("validated", "needs_human_review") else "open"
        row.state_reason = verdict.reason
        db.commit()
    if run.budget_exhausted:
        run.outcome = Outcome.BUDGET_EXHAUSTED.value
    db.commit()


def assist_project(db: Session, project: Project, *, trigger: str = "auto",
                   provider: AiProvider | None = None) -> list[ExtractionRun]:
    """Ask about every open eligible issue on the project's latest runs.
    Does nothing -- and calls nothing -- when assistance is off."""
    settings = get_settings()
    if provider is None:
        if not settings.ai_enabled:
            return []
        provider = get_provider()
        if not getattr(provider, "ready", False):
            # Enabled but not credentialed: the issues stay open and the
            # page says why. Asking would only produce auth errors.
            return []
    runs = (
        db.query(ExtractionRun)
        .filter(ExtractionRun.project_id == project.id, ExtractionRun.kind == "design_sheet")
        .order_by(ExtractionRun.id.desc())
        .all()
    )
    latest: dict[str, ExtractionRun] = {}
    for run in runs:
        latest.setdefault(run.document_path, run)
    budget = open_budget(db, project.id)
    touched = []
    for run in latest.values():
        if any(i.state == "open" and llm_task_for(IssueCode(i.code)) for i in run.issues):
            run.trigger = trigger
            assist_run(db, project, run, budget=budget, provider=provider)
            touched.append(run)
    return touched


# --- classification at resolve time --------------------------------------------


def sheet_first_page_text(pdf_path: Path) -> str:
    """The sheet's own words: the text layer, or an OCR of the top of the
    first page where there is none."""
    try:
        import pymupdf

        with pymupdf.open(str(pdf_path)) as doc:
            if doc.page_count == 0:
                return ""
            text = doc[0].get_text().strip()
            if text:
                return text[:evidence_builder.MAX_TEXT_PART_CHARS]
            image = design_sheet_extractor._render_page(doc[0])
    except Exception:  # noqa: BLE001
        return ""
    try:
        import pytesseract

        top = image.crop((0, 0, image.width, int(image.height * 0.4)))
        return pytesseract.image_to_string(top, config="--psm 6").strip()[:evidence_builder.MAX_TEXT_PART_CHARS]
    except Exception:  # noqa: BLE001
        return ""


def suggest_sheet_system(
    db: Session,
    pdf_path: Path,
    *,
    candidates: list[str],
    marked_rows: list[str],
    budget: JobBudget,
    provider: AiProvider | None = None,
) -> Validation | None:
    """A suggestion for a sheet with no code on a DRF marking several
    systems. Never applied; the reviewer picks."""
    settings = get_settings()
    if not candidates:
        return None
    text = sheet_first_page_text(pdf_path)
    if len(text) < 20:
        return Validation("insufficient_evidence", "the sheet's first page has no readable words")
    issue = Issue(IssueCode.AMBIGUOUS_SYSTEM, page=1, target=f"sheet_system:{pdf_path.name}",
                  detail={"candidates": candidates, "marked_rows": marked_rows})
    evidence = evidence_builder.for_sheet_system(text, issue, max_output_tokens=settings.ai_max_output_tokens_per_task)
    verdict, _data, _cached, _model, _key = ask(
        db, project_id=None, run_id=None, document_sha256=sha256_of(pdf_path) or "", evidence=evidence,
        context={"candidates": sorted(candidates)}, budget=budget, provider=provider,
        allowed_target=issue.target, allowed_values={c.upper() for c in candidates},
    )
    return verdict


def suggest_drf_field(
    db: Session,
    drf_path: Path,
    issue: Issue,
    *,
    budget: JobBudget,
    provider: AiProvider | None = None,
) -> Validation | None:
    """A second reading of a low-confidence DRF field, from its cell image."""
    from app.services import drf_extractor

    settings = get_settings()
    if issue.region is None:
        return None
    dpi = drf_extractor.RENDER_DPI
    evidence = evidence_builder.for_drf_field(drf_path, issue, render_dpi=dpi,
                                              max_output_tokens=settings.ai_max_output_tokens_per_task)
    independent = independent_readings(drf_path, issue, dpi) | {issue.detail.get("value", "")}
    verdict, _data, _cached, _model, _key = ask(
        db, project_id=None, run_id=None, document_sha256=sha256_of(drf_path) or "", evidence=evidence,
        context={"field": issue.detail.get("field")}, budget=budget, provider=provider,
        allowed_target=issue.target, independent=independent,
    )
    return verdict


# --- what an engineer does with an issue ----------------------------------------


class AcceptRefused(Exception):
    pass


def _line_key(system_code: str | None, group: str | None, catalog: str | None, description: str) -> tuple:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
    return (norm(system_code), norm(group), norm(catalog), norm(description))


def accept_issue(db: Session, project: Project, row: ExtractionIssue, user: User, value: str | None) -> ProjectBoqItem:
    """Add the row the read dropped, with the quantity the engineer accepts.

    The value may be the proposal's or one the engineer typed; either way
    it is the engineer's decision and is recorded as such. If the BOQ
    already holds a line for this item -- the engineer typed it in after the
    read -- nothing is added and the issue is closed as superseded.
    """
    if row.state in ("resolved", "rejected"):
        raise AcceptRefused("this issue is already closed")
    if not row.target.startswith("boq_line:"):
        raise AcceptRefused("only a dropped BOQ row can be accepted into the BOQ")
    value = (value or "").strip()
    if not value:
        proposal = next((p for p in reversed(row.proposals) if p.value), None)
        value = proposal.value if proposal else ""
    if not value:
        raise AcceptRefused("no quantity to accept: type one")
    if not (value.isdigit() or re.sub(r"[^A-Za-z]", "", value).lower() in design_sheet_extractor.WORD_QUANTITIES):
        raise AcceptRefused(f"{value!r} is not a quantity")

    from app.routers.projects import _brand_for

    run = row.run
    detail = row.detail or {}
    key = _line_key(run.system_code, detail.get("group_heading"), detail.get("catalog_no"), detail.get("description", ""))
    for existing in project.boq_items:
        if _line_key(existing.system_code, existing.group_heading, existing.catalog_no, existing.description) == key:
            row.state = "resolved"
            row.state_reason = "superseded by review: the BOQ already holds this line"
            row.resolved_by_id = user.id
            row.resolved_at = utc_now()
            db.commit()
            raise AcceptRefused("the BOQ already holds this line; nothing was added")

    line = ProjectBoqItem(
        system_code=run.system_code,
        position=len(project.boq_items),
        group_heading=detail.get("group_heading"),
        manufacturer=_brand_for(run.system_code, project.systems),
        catalog_no=detail.get("catalog_no"),
        description=detail.get("description", ""),
        quantity=value,
    )
    project.boq_items.append(line)
    row.state = "resolved"
    row.state_reason = "accepted by the engineer"
    row.resolved_by_id = user.id
    row.resolved_at = utc_now()
    row.resolved_value = value
    db.commit()
    db.refresh(line)
    return line


def reject_issue(db: Session, row: ExtractionIssue, user: User, reason: str | None) -> None:
    if row.state in ("resolved", "rejected"):
        raise AcceptRefused("this issue is already closed")
    row.state = "rejected"
    row.state_reason = reason or "rejected by the engineer"
    row.resolved_by_id = user.id
    row.resolved_at = utc_now()
    db.commit()
