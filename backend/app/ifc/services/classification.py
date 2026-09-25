"""Identify a freshly read drawing's symbols, cheapest first, before it is saved.

    exact signature in the library   -> known          0 tokens
    library / Revit family resemblance -> counted, the engineer confirms   0 tokens
    letters and block name agree     -> deterministic  0 tokens
    AI cache                         -> as before      0 tokens
    AI: metadata, then the picture   -> AI verified, if it passes the gate
    anything else                    -> the engineer's review queue, with the reason

Only symbols the engineer would otherwise be asked about go past the
library: those on the floor plans that look like devices. A legend symbol
or a door on the architecture costs nothing. Everything here is about a
symbol's *signature* -- the extractor already grouped the occurrences --
so a building of 18,000 devices drawn with 120 symbols is 120 questions
at most, and far fewer once the library knows its consultant's symbols.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ifc.dxf import matcher
from app.ifc.resolve import resolved_drawing
from app.ifc.services import ai_symbol_review, library, symbol_matching
from app.models import IfcDeviceType, ProjectIfcDrawing

log = logging.getLogger(__name__)

# The engineer's queue: why a symbol is in it.
NO_CANDIDATES = "no_candidates"
ALIAS_AMBIGUOUS = "alias_ambiguous"
DETERMINISTIC_CONFLICT = "deterministic_conflict"
RESEMBLANCE = "resemblance_match"
QUEUE_REASONS = {
    ai_symbol_review.AI_UNCERTAIN: "The AI was not sure",
    ai_symbol_review.AI_REJECTED: "The AI's answer did not pass the checks",
    ai_symbol_review.AI_UNAVAILABLE: "The AI could not be reached",
    ai_symbol_review.AI_DISABLED: "AI review is off",
    ai_symbol_review.AI_BUDGET: "The AI call limit for this read was reached",
    NO_CANDIDATES: "Nothing suggests what it is",
    ALIAS_AMBIGUOUS: "Its block name means different devices on different drawings",
    DETERMINISTIC_CONFLICT: "Its words and the library disagree",
    RESEMBLANCE: "Counted by resemblance to a library symbol: confirm it",
}
# Symbols the engineer is asked about (app.ifc.resolve.review_kind) that the pipeline may answer.
PIPELINE_KINDS = ("answer", "suggested", "architecture")


@dataclass
class Summary:
    total_occurrences: int = 0
    unique_symbols: int = 0
    known: int = 0              # exact signature already in the library
    resemblance: int = 0        # counted by resemblance; the engineer confirms
    deterministic: int = 0
    ai_verified: int = 0
    engineer_review: int = 0
    not_asked: int = 0          # off the floor plans, or unlikely to be a device
    ai_reviewed: int = 0        # unique signatures that entered the AI stage
    ai_cache_hits: int = 0
    ai_metadata_calls: int = 0
    ai_visual_calls: int = 0
    ai_input_tokens: int = 0
    ai_output_tokens: int = 0
    ai_errors: int = 0
    ai_note: str | None = None
    seconds: float = 0.0
    by_reason: dict[str, int] = field(default_factory=dict)
    # {signature: "deterministic" | "ai"}: what this read answered itself.
    classified: dict[str, str] = field(default_factory=dict)


def classify(db: Session, *, groups: list[dict], meta: dict, project_id: int | None, drawing_name: str,
             job_id: int | None = None, use_ai: bool = True, check=None, progress=None) -> tuple[dict, Summary]:
    """Answer what can be answered of a drawing's symbol groups, writing the
    answers to the library (committed). Returns ({signature: why it is left
    for the engineer, and the AI's answer}, the summary)."""
    import time

    started = time.monotonic()
    settings = get_settings()
    summary = Summary()
    if progress is not None:
        progress("matching_symbols", 0.0)
    # The drawing as it will be resolved, without saving it: which symbols
    # the engineer would be asked about, and what the library says of each.
    transient = ProjectIfcDrawing(meta=meta, groups=groups, filename=drawing_name)
    resolved = resolved_drawing(db, transient, with_occurrences=False)["groups"]
    summary.unique_symbols = len(resolved)
    summary.total_occurrences = sum(g.get("count", 0) for g in resolved)

    pending = []
    for g in resolved:
        kind = (g.get("match") or {}).get("kind")
        if g["status"] in ("verified", "ignored") and kind == "exact":
            summary.known += 1
        elif g["status"] == "verified" and kind in ("library", "family"):
            summary.resemblance += 1
        if g.get("review") in PIPELINE_KINDS:
            pending.append(g)
        elif g.get("review") in (None, "optional", "skipped") and not (g["status"] in ("verified", "ignored")):
            summary.not_asked += 1

    queue: dict[str, dict] = {}
    types = [matcher.device_type_out(t) for t in db.query(IfcDeviceType).filter(IfcDeviceType.is_active.is_(True))
             .order_by(IfcDeviceType.sort_order, IfcDeviceType.id).all()]
    codes = {t["code"].upper(): t for t in types}
    ambiguous = library.ambiguous_names(db)
    symbols = None
    items: list[ai_symbol_review.Item] = []
    if progress is not None:
        progress("deterministic_review", 0.0)
    for g in pending:
        if check is not None:
            check()
        sig = g["signature"]
        names = {n.upper() for n in g.get("block_names") or {}}
        if names & ambiguous:
            queue[sig] = {"reason": ALIAS_AMBIGUOUS}
            continue
        if settings.ifc_deterministic_auto_verify:
            dt = symbol_matching.strong_rule(g, codes)
            if dt is not None:
                if library.remember(db, g, dt["id"], source=library.DETERMINISTIC, drawing_name=drawing_name,
                                    notes="Letters and block name both name this device") is not None:
                    summary.deterministic += 1
                    summary.classified[sig] = library.DETERMINISTIC
                    continue
        if not use_ai:
            queue[sig] = {"reason": ai_symbol_review.AI_DISABLED}
            continue
        if symbols is None:
            from app.ifc.resolve import library as load_library

            symbols, _ = load_library(db)
        cands = symbol_matching.candidates(g, types, symbols, settings.ifc_ai_max_candidates)
        if not cands:
            queue[sig] = {"reason": NO_CANDIDATES}
            continue
        items.append(ai_symbol_review.Item(key=f"s{len(items) + 1}", group=g, candidates=cands))
    db.commit()

    if items:
        report = ai_symbol_review.review(db, items, project_id=project_id, drawing_name=drawing_name, job_id=job_id,
                                         check=check, progress=progress)
        summary.ai_reviewed = report.reviewed
        summary.ai_cache_hits = report.cache_hits
        summary.ai_metadata_calls = report.metadata_calls
        summary.ai_visual_calls = report.visual_calls
        summary.ai_input_tokens = report.input_tokens
        summary.ai_output_tokens = report.output_tokens
        summary.ai_errors = report.errors
        summary.ai_note = report.note
        for sig, verdict in report.verdicts.items():
            if verdict.outcome == "accepted":
                summary.ai_verified += 1
                summary.classified[sig] = library.AI
            else:
                queue[sig] = {"reason": verdict.reason, "ai": verdict.ai}
    summary.engineer_review = len(queue)
    for entry in queue.values():
        summary.by_reason[entry["reason"]] = summary.by_reason.get(entry["reason"], 0) + 1
    summary.seconds = round(time.monotonic() - started, 2)
    log.info("ifc.classify drawing=%r job=%s unique=%d occurrences=%d known=%d resemblance=%d deterministic=%d "
             "ai_reviewed=%d ai_cache_hits=%d ai_calls=%d/%d ai_verified=%d engineer=%d tokens_in=%d tokens_out=%d",
             drawing_name, job_id, summary.unique_symbols, summary.total_occurrences, summary.known,
             summary.resemblance, summary.deterministic, summary.ai_reviewed, summary.ai_cache_hits,
             summary.ai_metadata_calls, summary.ai_visual_calls, summary.ai_verified, summary.engineer_review,
             summary.ai_input_tokens, summary.ai_output_tokens)
    return queue, summary


def summary_dict(summary: Summary) -> dict:
    """The summary as kept on the drawing (`meta["processing"]`); what it
    classified is kept beside it (`meta["classified"]`)."""
    out = asdict(summary)
    out.pop("classified", None)
    return out
