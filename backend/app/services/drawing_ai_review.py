"""The AI's look at what the drawing rules could not settle.

The rules run first and settle nearly everything at no cost: a reply that
quotes its drawing and revision is that revision's answer; a file at a
revision nothing proves was submitted is a candidate. What is left is
small and specific, and only that is asked:

  * a consultant reply whose reference matches no drawing the log knows
    (a typo in the stamp, a renumbered sheet): which drawing and revision
    does it answer, among a few the rules picked as likely?
  * two references for one floor of one system: a renumbering, another
    floor's drawing filed here, or a title block read wrong?

One compact question each, answered in a fixed shape, cached by the file's
content so an unchanged file never costs a second call. An answer is
taken only when it passes the checks (`validate`) -- the reference is one
the rules offered, the revision and status are real, the confidence is
over the line -- and never over an engineer's decision. A call that fails
leaves the finding for the engineer; the sync is not failed.

The model sees the reply's reference and words and the candidates'
references and floors: no project name, client, consultant, path or
drawing content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai import cache, guard
from app.ai.provider import AiRequest, TextPart, get_provider
from app.core.config import get_settings

log = logging.getLogger(__name__)

TASK_REPLY = "drawings_reply_match"
TASK_REFERENCE = "drawings_reference_conflict"
TASK_FLOOR = "drawings_floor_alias"
PROMPT_VERSION = "drawings-review-2026-09-26.1"

SYSTEM_PROMPT = (
    "You review shop drawing document control records.\n"
    "Match only to the candidates given; choose null when none fits.\n"
    "Return structured output only.\n"
    "If the evidence is insufficient or conflicting, set requires_engineer true.\n"
    "Never invent a drawing reference, revision or status."
)

STATUSES = ("approved", "approved_as_noted", "not_approved", "under_review", "unknown")
REASONS = ("REFERENCE_MATCH", "REFERENCE_PARTIAL_MATCH", "FLOOR_MATCH", "REVISION_MATCH", "RENUMBERED",
           "OTHER_FLOOR", "EXTRACTION_ERROR", "INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE")

REPLY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["drawing_reference", "revision", "status", "confidence", "reason_code", "requires_engineer"],
    "properties": {
        "drawing_reference": {"type": ["string", "null"]},
        "revision": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": list(STATUSES)},
        "confidence": {"type": "number"},
        "reason_code": {"type": "string", "enum": list(REASONS)},
        "requires_engineer": {"type": "boolean"},
    },
}
FLOOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["possible_same_floor", "confidence", "evidence", "requires_engineer"],
    "properties": {
        "possible_same_floor": {"type": "boolean"},
        "confidence": {"type": "number"},
        "evidence": {"type": "string"},
        "requires_engineer": {"type": "boolean"},
    },
}

REFERENCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["assessment", "keep_reference", "confidence", "reason_code", "requires_engineer"],
    "properties": {
        "assessment": {"type": "string", "enum": ["renumbered", "other_floor", "extraction_error", "uncertain"]},
        "keep_reference": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason_code": {"type": "string", "enum": list(REASONS)},
        "requires_engineer": {"type": "boolean"},
    },
}


@dataclass
class Finding:
    """One thing the AI said, validated: `accepted` when the backend may
    act on it, else what to show the engineer."""
    kind: str
    key: str
    accepted: bool
    text: str
    ai: dict
    system: str
    shop_drawing_id: int | None = None
    floor_key: str | None = None
    apply: dict = field(default_factory=dict)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    skipped: int = 0


def enabled() -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.drawings_ai_review_enabled:
        return False, "the drawings AI review is switched off (DRAWINGS_AI_REVIEW_ENABLED)"
    if not settings.ai_enabled:
        return False, "AI assistance is switched off on this server (AI_ENABLED)"
    provider = get_provider()
    if not provider.ready:
        return False, provider.status
    return True, None


def _revision_ok(value) -> str | None:
    m = re.fullmatch(r"R0*(\d{1,2})", str(value or "").strip().upper())
    return f"R{int(m.group(1))}" if m else None


def _ask(task: str, schema: dict, payload: dict, *, sha: str, project_id: int, db: Session, report: Report):
    """One question, through the cache. Returns (data, error)."""
    settings = get_settings()
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    model = settings.drawings_ai_model or settings.ai_model_small
    key = cache.cache_key(scope="drawings", document_sha256=sha, evidence_fingerprint=fingerprint, task=task, context={},
                          parser_version="1", prompt_version=PROMPT_VERSION, schema_version="1", model=model)
    hit = cache.get(db, key, project_id=project_id, ttl_days=settings.ai_cache_ttl_days, document_sha256=sha)
    if hit is not None:
        report.cache_hits += 1
        return hit, None
    if report.calls >= settings.drawings_ai_max_calls_per_sync:
        report.skipped += 1
        return None, "the AI call limit for this sync was reached"
    report.calls += 1
    text = json.dumps(payload, separators=(",", ":"))
    if guard.instruction_flags(text):
        return None, "the document's text reads like an instruction"
    request = AiRequest(task=task, system=SYSTEM_PROMPT, parts=[TextPart("records", text)], schema=schema,
                        max_output_tokens=200, tier="small", timeout_s=settings.drawings_ai_timeout_s,
                        model=settings.drawings_ai_model, idempotency_key=key)
    response = get_provider().complete(request)
    if not response.ok or not isinstance(response.data, dict):
        report.errors += 1
        return None, (response.error_detail or response.error or "no answer")[:300]
    cache.put(db, key, response.data, project_id=project_id, document_sha256=sha, task=task)
    return response.data, None


def validate_reply(data: dict, candidates: list[dict], *, threshold: float) -> tuple[str, str | None]:
    """("accepted", None) | ("uncertain", why) | ("rejected", why)."""
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        return "rejected", "the confidence is not a number between 0 and 1"
    if data.get("status") not in STATUSES or data.get("reason_code") not in REASONS:
        return "rejected", "the answer is not in the expected shape"
    if data.get("requires_engineer") or data.get("drawing_reference") is None:
        return "uncertain", "the AI asked for an engineer" if data.get("requires_engineer") else "no candidate fits"
    if data["drawing_reference"] not in {c["reference"] for c in candidates}:
        return "rejected", "the drawing reference was not one of the candidates supplied"
    if _revision_ok(data.get("revision")) is None or data.get("status") == "unknown":
        return "uncertain", "the revision or status is not known"
    if confidence < threshold:
        return "uncertain", f"confidence {confidence:.0%} is below {threshold:.0%}"
    return "accepted", None


def review_replies(db: Session, project_id: int, system: str, unmatched: list[dict], drawings: list[dict],
                   report: Report) -> list[Finding]:
    """Each reply whose reference matches no drawing: which drawing among
    the likely ones (same system; a reference that shares its numbers or
    its floor) does it answer?"""
    settings = get_settings()
    findings = []
    for reply in unmatched:
        likely = _likely_drawings(reply, drawings)[:5]
        key = f"{system}:reply_unmatched:{reply['sha256'][:16]}"
        if not likely:
            report.skipped += 1
            continue
        payload = {
            "reply": {"reference": reply["reference"][:80], "revision": reply["revision"], "status": reply["status"],
                      "words": (reply.get("words") or "")[:300]},
            "candidates": [{"reference": d["reference"], "floor": d["floor"], "revisions": d["revisions"]} for d in likely],
        }
        data, error = _ask(TASK_REPLY, REPLY_SCHEMA, payload, sha=reply["sha256"], project_id=project_id, db=db, report=report)
        if data is None:
            findings.append(Finding("ai_review_required", key, False, system=system,
                                    text=f"The reply {reply['name']} names {reply['reference']} {reply['revision']}, which is "
                                         f"no drawing of the log; the AI could not look at it ({error}).",
                                    ai={"error": error}))
            continue
        verdict, why = validate_reply(data, likely, threshold=settings.drawings_ai_auto_accept_threshold)
        chosen = next((d for d in likely if d["reference"] == data.get("drawing_reference")), None)
        ai = {"task": TASK_REPLY, "drawing_reference": data.get("drawing_reference"), "revision": data.get("revision"),
              "status": data.get("status"), "confidence": data.get("confidence"), "reason_code": data.get("reason_code"),
              "requires_engineer": data.get("requires_engineer"), "validation": verdict, "validation_reason": why,
              "prompt_version": PROMPT_VERSION}
        if verdict == "accepted" and chosen is not None:
            findings.append(Finding(
                "ai_suggestion", key, True, system=system, shop_drawing_id=chosen["id"], floor_key=chosen.get("floor_key"),
                text=f"The reply {reply['name']} ({reply['reference']} {reply['revision']}) answers "
                     f"{chosen['reference']} {_revision_ok(data['revision'])}: {data['status'].replace('_', ' ')}.",
                ai=ai, apply={"shop_drawing_id": chosen["id"], "revision": _revision_ok(data["revision"]),
                              "status": data["status"], "reply_path": reply.get("path"), "reply_text": reply.get("words")}))
        else:
            findings.append(Finding(
                "reply_unmatched", key, False, system=system,
                shop_drawing_id=chosen["id"] if chosen else None, floor_key=chosen.get("floor_key") if chosen else None,
                text=f"The reply {reply['name']} names {reply['reference']} {reply['revision']}, which is no drawing of "
                     f"the log" + (f"; the AI thinks it may be {chosen['reference']} ({data.get('confidence', 0):.0%})."
                                   if chosen else "; the AI could not tell which drawing it answers."),
                ai=ai))
    return findings


def review_reference_conflicts(db: Session, project_id: int, system: str, conflicts: list[dict],
                               report: Report) -> list[Finding]:
    """Two references for one floor: what the AI makes of it -- a
    suggestion for the engineer, never a change to the references."""
    findings = []
    for conflict in conflicts:
        key = f"{system}:reference_conflict_ai:{conflict['floor_key']}"
        sha = hashlib.sha256("|".join(sorted(r["reference"] for r in conflict["references"])).encode()).hexdigest()
        payload = {"floor": conflict["floor_key"],
                   "references": [{"reference": r["reference"], "revisions": r["revisions"], "title": (r.get("title") or "")[:80]}
                                  for r in conflict["references"][:4]]}
        data, error = _ask(TASK_REFERENCE, REFERENCE_SCHEMA, payload, sha=sha, project_id=project_id, db=db, report=report)
        if data is None:
            continue
        assessment = data.get("assessment") if data.get("assessment") in ("renumbered", "other_floor", "extraction_error", "uncertain") else "uncertain"
        text = {"renumbered": "the drawing was renumbered", "other_floor": "one of them belongs to another floor",
                "extraction_error": "one reference was read wrong off its title block",
                "uncertain": "it could not tell why"}[assessment]
        findings.append(Finding(
            "ai_suggestion", key, False, system=system, floor_key=conflict["floor_key"],
            text=f"{conflict['floor']}: two references ({', '.join(r['reference'] for r in conflict['references'][:4])}). "
                 f"The AI thinks {text} ({float(data.get('confidence') or 0):.0%}). Choose the reference that stands.",
            ai={"task": TASK_REFERENCE, **{k: data.get(k) for k in ("assessment", "keep_reference", "confidence", "reason_code",
                                                                     "requires_engineer")}, "prompt_version": PROMPT_VERSION}))
    return findings


def review_floor_duplicates(db: Session, project_id: int, issues: list, report: Report) -> list[tuple]:
    """Two names the rules suspect are one floor ("1st Mechanical Floor"
    and L02): what the AI makes of it, beside the suspicion -- possible
    same floor, how sure, and why. An opinion for the engineer's Merge or
    Keep Separate; it merges nothing, whatever the confidence. Returns
    (issue, verdict) pairs. The model sees the two names and the sheets
    around them: no project, client, path or drawing content."""
    out = []
    for issue in issues:
        detail = issue.detail or {}
        if not detail.get("alias_key") or not detail.get("canonical_key"):
            continue
        payload = {"named_floor": (detail.get("alias_label") or detail["alias_key"])[:80],
                   "level": (detail.get("canonical_label") or detail["canonical_key"])[:80],
                   "ifc_sheet_title": (detail.get("ifc_title") or "")[:120],
                   "neighbouring_sheets": [str(n)[:80] for n in (detail.get("neighbours") or [])[:4]],
                   "question": "Could the named floor and the level be the same physical floor of this building?"}
        sha = hashlib.sha256(f"{detail['alias_key']}|{detail['canonical_key']}".encode()).hexdigest()
        data, error = _ask(TASK_FLOOR, FLOOR_SCHEMA, payload, sha=sha, project_id=project_id, db=db, report=report)
        if data is None:
            continue
        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            confidence = None
        same = bool(data.get("possible_same_floor"))
        out.append((issue, {"task": TASK_FLOOR, "possible_same_floor": same, "confidence": confidence,
                            "evidence": str(data.get("evidence") or "")[:300],
                            "reason_code": "POSSIBLE_SAME_FLOOR" if same else "DIFFERENT_FLOORS",
                            # The AI never merges a floor: the engineer's confirmation is required either way.
                            "requires_engineer": True, "prompt_version": PROMPT_VERSION}))
    return out


def _tokens(reference: str) -> set[str]:
    return {t for t in re.split(r"[^A-Z0-9]+", reference.upper()) if t}


def _likely_drawings(reply: dict, drawings: list[dict]) -> list[dict]:
    """The drawings a stray reply may answer, likeliest first: the more of
    its reference's parts they share, the likelier; a shared floor counts."""
    tokens = _tokens(reply["reference"])
    scored = []
    for d in drawings:
        shared = len(tokens & _tokens(d["reference"]))
        digits = {t for t in tokens if t.isdigit()} & {t for t in _tokens(d["reference"]) if t.isdigit()}
        score = shared + 2 * len(digits)
        if reply.get("floor_key") and reply["floor_key"] in (d.get("floor_keys") or []):
            score += 3
        if score >= 2:
            scored.append((score, d))
    return [d for _, d in sorted(scored, key=lambda s: -s[0])]
