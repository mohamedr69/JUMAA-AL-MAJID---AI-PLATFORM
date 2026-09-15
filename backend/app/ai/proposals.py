"""What a model may answer, and what the platform checks before believing it.

A proposal names a target, a value, and the source region the value was
read from. Validation is Python's: schema, allowed target, that the source
reference points at evidence that was actually sent, that the value is the
kind the target takes, and -- for a cell reading -- that an independent OCR
re-read of the same crop agrees. A proposal that passes is `validated`; one
that is well-formed but unconfirmed is `needs_human_review`; anything else
is `rejected` with the reason. Schema validity is not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = "1"


class SourceRef(BaseModel):
    page: int | None = None
    region: str | None = None    # the evidence part label the value was read from


class ProposedChange(BaseModel):
    target: str
    value: str
    source: SourceRef
    reason: str = Field(default="", max_length=240)


class Proposal(BaseModel):
    task_id: str
    status: Literal["proposed", "insufficient_evidence", "needs_human_review"]
    proposed_changes: list[ProposedChange] = []
    source_references: list[SourceRef] = []
    unresolved_issues: list[str] = []


# The JSON schema the provider constrains output to. Kept explicit (rather
# than derived from the pydantic model) so the wire contract is visible and
# versioned with SCHEMA_VERSION.
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {"type": "string", "enum": ["proposed", "insufficient_evidence", "needs_human_review"]},
        "proposed_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "value": {"type": "string"},
                    "source": {
                        "type": "object",
                        "properties": {
                            "page": {"type": ["integer", "null"]},
                            "region": {"type": ["string", "null"]},
                        },
                        "required": ["page", "region"],
                        "additionalProperties": False,
                    },
                    "reason": {"type": "string"},
                },
                "required": ["target", "value", "source", "reason"],
                "additionalProperties": False,
            },
        },
        "source_references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"page": {"type": ["integer", "null"]}, "region": {"type": ["string", "null"]}},
                "required": ["page", "region"],
                "additionalProperties": False,
            },
        },
        "unresolved_issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["task_id", "status", "proposed_changes", "source_references", "unresolved_issues"],
    "additionalProperties": False,
}


@dataclass
class Validation:
    state: str                    # "validated" | "needs_human_review" | "rejected" | "insufficient_evidence"
    reason: str
    value: str | None = None      # the value as it would be applied, when not rejected
    independent_check: str | None = None
    # Instruction-like wording in the text the call carried (app/ai/guard.py).
    flags: list[str] = field(default_factory=list)


_DIGITS_RE = re.compile(r"^\d{1,6}$")


def parse(data: dict[str, Any] | None) -> Proposal | Validation:
    if data is None:
        return Validation("rejected", "no reply")
    try:
        return Proposal.model_validate(data)
    except ValidationError as exc:
        return Validation("rejected", f"reply does not match the schema: {exc.errors()[0].get('msg', 'invalid')}")


def validate(
    proposal: Proposal,
    *,
    task: str,
    allowed_target: str,
    sent_regions: set[str],
    allowed_values: set[str] | None = None,
    independent_readings: set[str] | None = None,
    word_quantities: set[str] | None = None,
    injection_flags: list[str] | None = None,
) -> Validation:
    """Judge one proposal for one issue.

    `sent_regions` are the labels of the evidence parts that were actually
    sent; `independent_readings` are what a separate OCR pass read off the
    same crop (for cell tasks); `allowed_values` bounds a classification.
    `injection_flags` name instruction-like wording found in the document
    text sent: a proposal made from such text is never `validated`.
    """
    verdict = _judge(proposal, task=task, allowed_target=allowed_target, sent_regions=sent_regions,
                     allowed_values=allowed_values, independent_readings=independent_readings,
                     word_quantities=word_quantities)
    flags = list(injection_flags or [])
    verdict.flags = flags
    if flags and verdict.state == "validated":
        verdict.state = "needs_human_review"
        verdict.reason = ("the document text contains instruction-like wording (" + ", ".join(flags)
                          + "), so the reading is not taken on its own; " + verdict.reason)
    return verdict


def _judge(
    proposal: Proposal,
    *,
    task: str,
    allowed_target: str,
    sent_regions: set[str],
    allowed_values: set[str] | None,
    independent_readings: set[str] | None,
    word_quantities: set[str] | None,
) -> Validation:
    from app.ai import guard

    if proposal.status == "insufficient_evidence":
        return Validation("insufficient_evidence", "; ".join(proposal.unresolved_issues) or "the model found the evidence insufficient")
    if not proposal.proposed_changes:
        return Validation("needs_human_review", "; ".join(proposal.unresolved_issues) or "no change proposed")
    if len(proposal.proposed_changes) > 1:
        return Validation("rejected", "more than one change for a single-target task")

    change = proposal.proposed_changes[0]
    if change.target != allowed_target:
        return Validation("rejected", f"target {change.target!r} is not the issue's target {allowed_target!r}")
    if change.source.region and change.source.region not in sent_regions:
        return Validation("rejected", f"source region {change.source.region!r} was not part of the evidence sent")

    value = change.value.strip()
    if not value:
        return Validation("rejected", "empty value")
    suspicious = guard.suspicious_value(value)
    if suspicious:
        return Validation("rejected", suspicious)

    if task == "read_cell":
        word = re.sub(r"[^A-Za-z]", "", value).lower()
        if not (_DIGITS_RE.match(value) or (word_quantities and word in word_quantities)):
            return Validation("rejected", f"{value!r} is neither a whole number nor a quantity word")
        readings = {r for r in (independent_readings or set()) if r}
        if value in readings:
            return Validation("validated", "agrees with an independent OCR reading of the same cell", value,
                              independent_check="ocr_agrees")
        return Validation("needs_human_review", "no independent reading agrees; shown with the cell image for the engineer",
                          value, independent_check="ocr_disagrees")

    if task == "classify_system":
        upper = value.upper()
        if allowed_values is not None and upper not in allowed_values:
            return Validation("rejected", f"{value!r} is not one of the systems the DRF marks: {sorted(allowed_values)}")
        # A classification is never validated on its own: it is a suggestion the reviewer picks.
        return Validation("needs_human_review", "a suggestion for the reviewer; the system is theirs to choose", upper)

    if task == "read_field":
        if len(value) > 200:
            return Validation("rejected", "value is too long for a form field")
        readings = {r for r in (independent_readings or set()) if r}
        normalised = re.sub(r"[^a-z0-9]", "", value.lower())
        if any(normalised == re.sub(r"[^a-z0-9]", "", r.lower()) for r in readings):
            return Validation("validated", "agrees with an independent OCR reading", value, independent_check="ocr_agrees")
        return Validation("needs_human_review", "no independent reading agrees", value, independent_check="ocr_disagrees")

    return Validation("rejected", f"unknown task {task!r}")
