"""What the AI can say about a symbol that nothing else could identify.

AI identifies the symbol; Python counts it; the engineer is the final
authority. The model is asked only about symbols that are on the floor
plans, that no engineer, library entry or deterministic rule has
answered, and that Python found a short list of candidate device types
for. It is asked about each *signature* once -- 1,400 detectors drawn with
one symbol are one question -- and never about quantities.

Cheapest first, for each symbol:

  1. the AI cache: the same signature asked with the same candidates, the
     same prompt and the same model before (`IfcSymbolReview`) -> no call;
  2. metadata: its letters, block names and shape counts, and the
     candidates, many symbols to a call;
  3. visual: only for what the words left uncertain, its 32x32 picture
     (never the floor plan), a few to a call.

Every answer passes a gate before it is taken (`validate`): the schema,
a device type from the candidates Python supplied, a confidence at or
above IFC_AI_AUTO_VERIFY_THRESHOLD, no disagreement with the symbol's own
words or with the library, no instruction-like wording in what was sent.
What passes goes into the library as source "ai" -- below an engineer's
answer, which it never overwrites. What does not is left for the
engineer, with the AI's answer shown. A call that fails (timeout, rate
limit, a garbled reply) is retried once, then left for the engineer: the
drawing is read either way.

The model sees no project: no name, client, consultant, EP number, path,
drawing or BOQ -- only the symbol.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from app.ai import guard
from app.ai.provider import AiRequest, ImagePart, TextPart, get_provider
from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.ifc import hints
from app.ifc.dxf import geometry as G
from app.ifc.dxf import matcher
from app.ifc.services import library
from app.ifc.services.symbol_matching import family_of, on_device_layer
from app.models import AiUsage, IfcSymbol, IfcSymbolReview

log = logging.getLogger(__name__)

TASK_METADATA = "ifc_symbol_metadata"
TASK_VISUAL = "ifc_symbol_visual"
PROMPT_VERSION = "ifc-symbols-2026-09-25.1"

# The runtime prompt: short and the same on every call, so the provider can cache it.
SYSTEM_PROMPT = (
    "You classify CAD fire alarm / ELV symbols.\n"
    "Choose only from candidate_types.\n"
    "Return structured output only.\n"
    "If the evidence is insufficient, conflicting, or no candidate matches, return uncertain.\n"
    "Never invent a device type."
)

DECISIONS = ("device", "not_device", "uncertain")
REASON_CODES = ("LABEL_MATCH", "BLOCK_NAME_MATCH", "LABEL_GEOMETRY_MATCH", "GEOMETRY_MATCH", "ARCHITECTURAL",
                "ANNOTATION", "INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE", "NO_CANDIDATE_MATCHES")

RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "decision", "device_type_id", "confidence", "reason_code", "requires_engineer"],
                "properties": {
                    "key": {"type": "string"},
                    "decision": {"type": "string", "enum": list(DECISIONS)},
                    "device_type_id": {"type": ["integer", "null"]},
                    "confidence": {"type": "number"},
                    "reason_code": {"type": "string", "enum": list(REASON_CODES)},
                    "requires_engineer": {"type": "boolean"},
                },
            },
        },
    },
}

# Why a symbol is in the engineer's queue (see also app.ifc.services.classification).
AI_UNCERTAIN, AI_REJECTED, AI_UNAVAILABLE, AI_DISABLED, AI_BUDGET = (
    "ai_uncertain", "ai_rejected", "ai_unavailable", "ai_disabled", "ai_budget")


@dataclass
class Item:
    """One unique signature to classify, with the candidates Python chose."""
    key: str
    group: dict
    candidates: list[dict]

    @property
    def signature(self) -> str:
        return self.group["signature"]


@dataclass
class Verdict:
    """What became of one item: "accepted" (in the library as AI) or the
    reason it is left for the engineer, and the AI's answer if it gave one."""
    signature: str
    outcome: str
    reason: str | None = None
    ai: dict | None = None


@dataclass
class Report:
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    cache_hits: int = 0
    metadata_calls: int = 0
    visual_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: int = 0
    reviewed: int = 0          # unique signatures that entered the AI stage
    enabled: bool = True
    note: str | None = None    # for the engineer, when the AI could not help


def enabled() -> tuple[bool, str | None]:
    """Whether the AI review runs on this server, and if not, why not."""
    settings = get_settings()
    if not settings.ifc_ai_symbol_review_enabled:
        return False, "the AI symbol review is switched off (IFC_AI_SYMBOL_REVIEW_ENABLED)"
    if not settings.ai_enabled:
        return False, "AI assistance is switched off on this server (AI_ENABLED)"
    try:
        from app.ai import evaluation

        if TASK_METADATA in evaluation.disabled_tasks():
            return False, f"the task {TASK_METADATA} is disabled on this server"
    except Exception:  # noqa: BLE001 -- the evaluation gates are optional here
        pass
    provider = get_provider()
    if not provider.ready:
        return False, provider.status
    return True, None


# --- what is sent ----------------------------------------------------------------------


def _names(g: dict) -> list[str]:
    """Up to three block names, as the designer chose them: no xref prefix,
    no Revit element id or view (which carries the sheet name), no
    anonymous blocks."""
    out: list[str] = []
    for name in g.get("block_names") or {}:
        words = hints._block_words(name)
        if not words:
            continue
        words = matcher.revit_family(words) or words
        if words not in out:
            out.append(words[:60])
    return out[:3]


def payload(item: Item) -> dict:
    """The symbol as the model sees it -- nothing about the project."""
    g = item.group
    hint = g.get("name_hint") or {}
    return {
        "key": item.key,
        "label": str(g.get("label") or "")[:40],
        "inner_label": str(g.get("inner_label") or "")[:40],
        "block_names": _names(g),
        "entity_counts": dict(sorted((g.get("entity_counts") or {}).items())[:12]),
        "layers": [str(layer)[:40] for layer in list(g.get("layers") or {})[:3]],
        "hint_family": hint.get("family"),
        "candidate_types": [{"id": c["id"], "code": c["code"], "name": c["name"]} for c in item.candidates],
    }


def png_of(raster_hex: str, scale: int = 4) -> bytes | None:
    """The symbol's 32x32 fingerprint as a small black-on-white PNG."""
    grid = G.grid_from_hex(raster_hex) if raster_hex else None
    if grid is None:
        return None
    from PIL import Image

    n = grid.shape[0]
    img = Image.new("L", (n + 2, n + 2), 255)
    pixels = img.load()
    for y in range(n):
        for x in range(n):
            if grid[y, x]:
                pixels[x + 1, y + 1] = 0     # row 0 is the top, as in an image (geometry.rasterise)
    img = img.resize(((n + 2) * scale, (n + 2) * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def cache_key(item: Item, stage: str, model: str) -> str:
    material = json.dumps({
        "signature": item.signature, "stage": stage, "model": model, "prompt": PROMPT_VERSION,
        "candidates": sorted((c["id"], c["code"], c["name"]) for c in item.candidates),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _model_for(stage: str) -> str:
    settings = get_settings()
    chosen = settings.ifc_ai_visual_model if stage == "visual" else settings.ifc_ai_model
    return chosen or settings.ai_model_small


# --- the gate ----------------------------------------------------------------------------


def validate(result: dict, item: Item, *, threshold: float, flagged: bool, known: bool) -> tuple[str, str | None]:
    """("accepted", None), ("uncertain", why) or ("rejected", why): whether
    the AI's answer may be taken. Confidence alone never decides."""
    decision = result.get("decision")
    confidence = result.get("confidence")
    if decision not in DECISIONS:
        return "rejected", "the answer is not one of device, not_device, uncertain"
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        return "rejected", "the confidence is not a number between 0 and 1"
    if decision == "uncertain":
        return "uncertain", "the AI was not sure"
    if result.get("requires_engineer"):
        return "uncertain", "the AI asked for an engineer"
    if flagged:
        return "rejected", "the symbol's text reads like an instruction"
    if known:
        return "rejected", "the library already has an answer for this symbol"
    g = item.group
    if decision == "device":
        type_id = result.get("device_type_id")
        chosen = next((c for c in item.candidates if c["id"] == type_id), None)
        if chosen is None:
            return "rejected", "the device type was not one of the candidates supplied"
        if confidence < threshold:
            return "uncertain", f"confidence {confidence:.0%} is below {threshold:.0%}"
        hint = g.get("name_hint") or {}
        if hint and hint.get("family") and family_of(chosen) and family_of(chosen) != hint["family"]:
            return "rejected", f"the symbol's own words read as {hint['device_type']['code']}"
        sug = g.get("suggestion") or {}
        if sug.get("is_ignored"):
            return "rejected", "the library resembles it as not a device"
        suggested = sug.get("device_type")
        if suggested and family_of(suggested) and family_of(chosen) and family_of(suggested) != family_of(chosen):
            return "rejected", f"the library resembles it as {suggested['code']}"
        return "accepted", None
    # not_device: never on a fire alarm layer, never against the symbol's own words
    if on_device_layer(g):
        return "rejected", "it is on a fire alarm or lighting layer"
    if g.get("name_hint"):
        return "rejected", "its own words name a device"
    sug = g.get("suggestion") or {}
    if sug and not sug.get("is_ignored"):
        return "rejected", "the library resembles it as a device"
    if confidence < threshold:
        return "uncertain", f"confidence {confidence:.0%} is below {threshold:.0%}"
    return "accepted", None


# --- the calls ---------------------------------------------------------------------------


def _cached(db: Session, key: str) -> IfcSymbolReview | None:
    ttl = get_settings().ai_cache_ttl_days
    row = (db.query(IfcSymbolReview)
           .filter(IfcSymbolReview.cache_key == key, IfcSymbolReview.error.is_(None),
                   IfcSymbolReview.invalidated_at.is_(None))
           .order_by(IfcSymbolReview.id.desc()).first())
    if row is None or utc_now() - row.created_at > timedelta(days=ttl):
        return None
    return row


def _call(request: AiRequest, retries: int):
    """One request, retried `retries` times on a failure on the way."""
    provider = get_provider()
    response = provider.complete(request)
    for attempt in range(retries):
        if response.ok or not response.retryable:
            break
        time.sleep(min(2.0, 0.5 * (attempt + 1)))
        response = provider.complete(request)
    return response


def _usage(db: Session, *, project_id, task: str, response, outcome: str) -> None:
    usage = response.usage
    db.add(AiUsage(project_id=project_id, run_id=None, task=task, model=(response.model or "unknown")[:64],
                   input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                   cached_input_tokens=usage.cached_input_tokens, reasoning_tokens=usage.reasoning_tokens,
                   estimated_cost=0, latency_ms=response.latency_ms or 0, cache_hit=False, escalated=False,
                   outcome=outcome))


def review(db: Session, items: list[Item], *, project_id: int | None, drawing_name: str, job_id: int | None = None,
           check=None, progress=None) -> Report:
    """Classify the items (unique signatures). Accepted answers are written
    to the library as source "ai" and committed; everything else comes back
    with the reason it is left for the engineer. Never raises for an AI
    failure: `check` (a stop asked for) is the only way out mid-way."""
    report = Report(reviewed=len(items))
    ok, why = enabled()
    if not ok:
        report.enabled, report.note = False, why
        for it in items:
            report.verdicts[it.signature] = Verdict(it.signature, "engineer", AI_DISABLED)
        return report

    settings = get_settings()
    threshold = settings.ifc_ai_auto_verify_threshold
    calls_left = [max(0, settings.ifc_ai_max_calls_per_job)]
    visual: list[Item] = []

    def settle(it: Item, stage: str, result: dict | None, row: IfcSymbolReview | None) -> str:
        """Take or refuse one answer; returns its validation."""
        known = db.query(IfcSymbol.id).filter(IfcSymbol.signature == it.signature).first() is not None
        flagged = bool(guard.instruction_flags(json.dumps(payload(it))))
        validation, reason = validate(result, it, threshold=threshold, flagged=flagged, known=known)
        ai = {"stage": stage, "decision": result.get("decision"), "device_type_id": result.get("device_type_id"),
              "confidence": result.get("confidence"), "reason_code": result.get("reason_code"),
              "model": row.model if row is not None else None, "validation": validation,
              "validation_reason": reason}
        if validation == "accepted":
            type_id = result.get("device_type_id") if result["decision"] == "device" else None
            saved = library.remember(db, it.group, type_id, source=library.AI, drawing_name=drawing_name,
                                     confidence=float(result["confidence"]),
                                     notes=f"AI ({stage}): {result.get('reason_code') or ''}".strip())
            if saved is None:
                validation, reason = "rejected", "the library already has an answer for this symbol"
                ai.update(validation=validation, validation_reason=reason)
        if validation == "accepted":
            report.verdicts[it.signature] = Verdict(it.signature, "accepted", None, ai)
        else:
            reason_code = AI_UNCERTAIN if validation == "uncertain" else AI_REJECTED
            report.verdicts[it.signature] = Verdict(it.signature, "engineer", reason_code, ai)
        return validation

    def run_stage(stage: str, todo: list[Item], batch_size: int) -> None:
        model = _model_for(stage)
        task = TASK_VISUAL if stage == "visual" else TASK_METADATA
        misses: list[tuple[Item, str]] = []
        for it in todo:
            key = cache_key(it, stage, model)
            row = _cached(db, key)
            if row is None:
                misses.append((it, key))
                continue
            report.cache_hits += 1
            result = {"decision": row.decision, "device_type_id": row.device_type_id, "confidence": row.confidence,
                      "reason_code": row.reason_code, "requires_engineer": row.requires_engineer}
            validation = settle(it, stage, result, row)
            if stage == "metadata" and validation == "uncertain":
                visual.append(it)
        db.commit()
        for start in range(0, len(misses), max(1, batch_size)):
            if check is not None:
                check()
            batch = misses[start:start + batch_size]
            if calls_left[0] <= 0:
                for it, _ in batch:
                    report.verdicts[it.signature] = Verdict(it.signature, "engineer", AI_BUDGET)
                continue
            calls_left[0] -= 1
            if progress is not None:
                progress(f"ai_review_{stage}", start / max(len(misses), 1))
            parts: list = [TextPart("symbols", json.dumps([payload(it) for it, _ in batch], separators=(",", ":")))]
            if stage == "visual":
                for it, _ in batch:
                    png = png_of(it.group.get("raster_hex", ""))
                    if png is not None:
                        parts.append(ImagePart(f"symbol {it.key}", png))
            request = AiRequest(task=task, system=SYSTEM_PROMPT, parts=parts, schema=RESULT_SCHEMA,
                                max_output_tokens=80 * len(batch) + 100, tier="small",
                                timeout_s=settings.ifc_ai_timeout_s, model=_model_for(stage),
                                idempotency_key=hashlib.sha256("|".join(k for _, k in batch).encode()).hexdigest())
            response = _call(request, settings.ifc_ai_retries)
            if stage == "visual":
                report.visual_calls += 1
            else:
                report.metadata_calls += 1
            report.input_tokens += response.usage.input_tokens or 0
            report.output_tokens += response.usage.output_tokens or 0
            results = (response.data or {}).get("results") if response.ok else None
            if not isinstance(results, list):
                report.errors += 1
                _usage(db, project_id=project_id, task=task, response=response,
                       outcome=response.error or "invalid_response")
                detail = (response.error_detail or response.error or "no results in the reply")[:300]
                log.warning("IFC AI %s review failed for %d symbol(s): %s", stage, len(batch), response.error)
                for it, key in batch:
                    db.add(IfcSymbolReview(signature=it.signature, cache_key=key, stage=stage,
                                           candidate_ids=[c["id"] for c in it.candidates], validation="error",
                                           error=detail, model=(response.model or model)[:64],
                                           prompt_version=PROMPT_VERSION, project_id=project_id,
                                           drawing_name=drawing_name[:300], job_id=job_id))
                    if it.signature not in report.verdicts or stage == "metadata":
                        report.verdicts[it.signature] = Verdict(it.signature, "engineer", AI_UNAVAILABLE)
                db.commit()
                continue
            _usage(db, project_id=project_id, task=task, response=response, outcome="ok")
            answered = {str(r.get("key")): r for r in results if isinstance(r, dict)}
            share_in = (response.usage.input_tokens or 0) // max(len(batch), 1)
            share_out = (response.usage.output_tokens or 0) // max(len(batch), 1)
            for it, key in batch:
                result = answered.get(it.key) or {"decision": "uncertain", "device_type_id": None, "confidence": 0.0,
                                                  "reason_code": "INSUFFICIENT_EVIDENCE", "requires_engineer": True}
                row = IfcSymbolReview(
                    signature=it.signature, cache_key=key, stage=stage, candidate_ids=[c["id"] for c in it.candidates],
                    decision=result.get("decision") if result.get("decision") in DECISIONS else None,
                    device_type_id=result.get("device_type_id") if isinstance(result.get("device_type_id"), int) else None,
                    confidence=float(result["confidence"]) if isinstance(result.get("confidence"), (int, float)) else None,
                    reason_code=str(result.get("reason_code") or "")[:40] or None,
                    requires_engineer=bool(result.get("requires_engineer")), validation="pending",
                    model=(response.model or model)[:64], prompt_version=PROMPT_VERSION,
                    input_tokens=share_in, output_tokens=share_out, project_id=project_id,
                    drawing_name=drawing_name[:300], job_id=job_id)
                db.add(row)
                validation = settle(it, stage, result, row)
                row.validation = validation
                row.validation_reason = (report.verdicts[it.signature].ai or {}).get("validation_reason")
                if validation == "rejected" and row.decision is None:
                    row.error = "the reply did not follow the schema"   # not reused as a cached answer
                if stage == "metadata" and validation == "uncertain":
                    visual.append(it)
            db.commit()

    run_stage("metadata", items, settings.ifc_ai_batch_size)
    if settings.ifc_ai_visual_review_enabled:
        second = [it for it in visual if it.group.get("raster_hex")
                  and report.verdicts.get(it.signature, Verdict("", "engineer")).outcome != "accepted"]
        if second:
            run_stage("visual", second, settings.ifc_ai_visual_batch_size)
    if report.errors and not any(v.outcome == "accepted" for v in report.verdicts.values()):
        report.note = "AI symbol classification is temporarily unavailable"
    return report
