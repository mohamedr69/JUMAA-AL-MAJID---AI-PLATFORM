"""Document text is data. This module keeps it that way.

Everything a model reads from a project -- a sheet's words, a specification
clause, a BOQ line, a DRF field -- was written by someone outside the
platform. Three defences, each small and tested:

1. **Fencing that cannot be closed from inside.** Each text part is sent as
   `<label> ... </label>`. A document containing `</label>` (or any tag that
   looks like one) would otherwise end the fence early and put what follows
   outside it. Tag-like sequences inside the text are neutralised before
   fencing.
2. **Instruction-like wording is flagged.** Phrases that address the model
   ("ignore the previous instructions", "you are now", "respond with") are
   looked for in every text part. A flagged call still runs -- the words may
   be innocent -- but its proposal can never be `validated`: it goes to an
   engineer with the flag shown.
3. **Answers are bounded by the task, not by the text.** Validation
   (app/ai/proposals.py) already refuses a target, region or value the task
   does not allow; `suspicious_value` adds the checks that matter when the
   text tried to steer the answer: links, markup and instruction wording in a
   value or reason.
"""

from __future__ import annotations

import re

# A tag-shaped sequence: <word>, </word>, <word attr=...>. Replaced with a
# look-alike bracket so the model still sees the characters but no fence
# boundary can be forged.
_TAG_RE = re.compile(r"<\s*(/?)\s*([A-Za-z_][\w\-.:]*)([^<>]{0,80})>")

_INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instruction|prompt|rule|above|previous|prior|system)s?\b", re.I)),
    ("role_change", re.compile(r"\byou are (now|no longer)\b|\bact as (an?|the)\b|\bpretend to be\b", re.I)),
    ("addresses_model", re.compile(
        r"\b(as an ai|language model|assistant\s*:|system\s*:|developer\s*:|the model (must|should))", re.I)),
    ("dictates_answer", re.compile(
        r"\b(respond|answer|reply|output|return)\b[^.\n]{0,30}\b(with|only|exactly|json|status|validated)\b[^.\n]{0,40}"
        r"\b(json|status|validated|proposed|insufficient_evidence|task_id)\b", re.I)),
    ("prompt_disclosure", re.compile(r"\b(reveal|print|show|repeat)\b[^.\n]{0,30}\b(system prompt|instructions|prompt)\b", re.I)),
    ("markup_injection", re.compile(r"<\s*/?\s*(system|assistant|user|instructions?|im_start|im_end)\b", re.I)),
]

_LINK_RE = re.compile(r"https?://|www\.|\]\(|<\s*a\s|javascript:", re.I)


def neutralise(text: str) -> str:
    """The text with every tag-shaped sequence made inert."""
    return _TAG_RE.sub(lambda m: f"‹{m.group(1)}{m.group(2)}{m.group(3)}›", text)


def fence(label: str, text: str) -> str:
    """One text part as the providers send it."""
    return f"<{label}>\n{neutralise(text)}\n</{label}>"


def instruction_flags(text: str) -> list[str]:
    """Which instruction-like patterns the text contains (names only)."""
    if not text:
        return []
    return [name for name, pattern in _INSTRUCTION_PATTERNS if pattern.search(text)]


def scan_parts(parts) -> list[str]:
    """Flags across a request's text parts, as "label:pattern", sorted."""
    from app.ai.provider import TextPart

    found: set[str] = set()
    for part in parts:
        if isinstance(part, TextPart):
            found.update(f"{part.label}:{name}" for name in instruction_flags(part.text))
    return sorted(found)


def suspicious_value(value: str) -> str | None:
    """Why an answer value cannot be taken, or None."""
    if _LINK_RE.search(value):
        return "the value contains a link"
    if _TAG_RE.search(value):
        return "the value contains markup"
    if instruction_flags(value):
        return "the value contains instruction-like wording"
    return None
