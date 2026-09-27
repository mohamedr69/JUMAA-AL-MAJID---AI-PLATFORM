"""The compatibility selector between what a consumer reads today and
what the classification would have it read -- nothing migrated yet.

Every consumer of the document index keeps its legacy selector (a role, a
record category, a folder): the material submittal map reads rows with
role `submittal_form`, the compliance page rows with role `spec`, the
logs the records' categories, the reply matching the received folders.
Document Classification V2 answers the same questions from the content,
and this module puts the two side by side for a row: the legacy answer,
which stands, and the classification's, as a shadow with the reasons the
two differ. A consumer is migrated only when, on the projects it is run
on, the shadow agrees with the legacy answer wherever the legacy answer
is known to be right and disagrees only where it is known to be wrong --
which scripts/consumer_shadow.py measures, and nothing here decides.

    select(consumer, row, entry) -> Selection(legacy, shadow, agree, why)

`legacy` is what the consumer uses today; `shadow` what the current
classification would give; `agree` whether they say the same. Missing,
stale, ambiguous or unknown classification never removes a document from
a consumer: with no usable shadow the selection is the legacy answer and
says so."""

from __future__ import annotations

from dataclasses import dataclass

CONSUMERS = ("submittal_map", "compliance", "logs.drawings", "logs.submittals", "replies", "intake")

# The classification types that would select a document for a consumer.
_SHADOW_TYPES = {
    "submittal_map": {"MATERIAL_SUBMITTAL"}, "compliance": {"SPECIFICATION"}, "logs.drawings": {"SHOP_DRAWING"},
    "logs.submittals": {"MATERIAL_SUBMITTAL"}, "replies": {"COMMENT_RESPONSE", "CONSULTANT_DECISION"},
    "intake": {"DRF", "DESIGN_SHEET"},
}
# Migration state per consumer: every one stays on its legacy selector
# (2026-09-28). Changed here, and only here, when a consumer is migrated.
MIGRATED: dict[str, bool] = {consumer: False for consumer in CONSUMERS}


@dataclass(frozen=True)
class Selection:
    consumer: str
    legacy: bool
    shadow: bool | None      # None: no usable classification (missing, stale, ambiguous, unknown)
    agree: bool | None
    effective: bool          # what the consumer uses: the legacy answer while not migrated
    why: str


def legacy_selects(consumer: str, row) -> bool:
    """The consumer's selector as it is today, from the row alone."""
    role = getattr(row, "role", None)
    if consumer == "submittal_map":
        return role == "submittal_form"
    if consumer == "compliance":
        return role == "spec"
    if consumer == "intake":
        return role in ("drf", "design_sheet")
    records = ((getattr(row, "extracted", None) or {}).get("records") or [])
    categories = {r.get("category") for r in records if isinstance(r, dict)}
    if consumer == "logs.submittals":
        return "submittals" in categories
    if consumer == "logs.drawings":
        from app.services import document_control

        return any(isinstance(r, dict) and r.get("category") == "drawings" and r.get("source") != "drawing schedule"
                   and document_control.is_shop_drawing(type("R", (), {"category": "drawings", "path": r.get("path", "")})())
                   for r in records)
    if consumer == "replies":
        from app.services import submittal_replies

        return bool(submittal_replies.on_file([row]))
    raise ValueError(consumer)


def shadow_selects(consumer: str, entry, freshness: str | None) -> bool | None:
    """What the classification would select, or None when it cannot be
    used: no entry, not current, ambiguous or unknown."""
    if entry is None or freshness != "current":
        return None
    if entry.stage in ("ambiguous", "unknown"):
        return None
    return entry.primary_type in _SHADOW_TYPES[consumer]


def select(consumer: str, row, entry=None, freshness: str | None = None) -> Selection:
    legacy = legacy_selects(consumer, row)
    shadow = shadow_selects(consumer, entry, freshness)
    agree = None if shadow is None else shadow == legacy
    effective = shadow if (MIGRATED.get(consumer) and shadow is not None) else legacy
    if shadow is None:
        why = "no usable classification (missing, not current, ambiguous or unknown): the legacy selector stands"
    elif agree:
        why = "the classification agrees with the legacy selector"
    else:
        why = (f"the classification reads it as {entry.primary_type.lower().replace('_', ' ')} ({entry.stage}); "
               f"the legacy selector {'includes' if legacy else 'excludes'} it -- for the shadow comparison, not applied")
    return Selection(consumer, legacy, shadow, agree, effective, why)
