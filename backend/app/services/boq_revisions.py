"""Line-by-line changes between two versions of a BOQ."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.schemas_project import ProjectBoqItemIn

# A difference in any of these marks a matched line as changed. The text
# fields that pair lines up (line_key) are compared too, exactly: cleaning an
# OCR artifact out of a description ("Pictogram ;" -> "Pictogram") leaves the
# line paired with its old self, but it is still an edit, and one that should
# show -- and be issuable.
COMPARED_FIELDS = (
    "group_heading",
    "manufacturer",
    "catalog_no",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "total_price",
    "remarks",
)


def _normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def line_key(line: ProjectBoqItemIn) -> tuple[str, str, str, str]:
    """What makes a line in one version the same line in another: its system,
    group heading, part number and description, ignoring case, spacing and
    punctuation. A real edit to one of those therefore reads as the old line
    removed and a new one added -- for a part number, that is what it is.

    Same shape as possibleDuplicates in frontend/src/lib/boq.ts, which asks
    the neighbouring question (is this line entered twice?). Change one and
    look at the other, or the BOQ page could flag two lines as duplicates
    that a comparison treats as unrelated."""
    return (
        line.system_code or "",
        _normalize(line.group_heading),
        _normalize(line.catalog_no),
        _normalize(line.description),
    )


def _comparable(value):
    if isinstance(value, str):
        return value.strip() or None
    return value


@dataclass
class BoqChange:
    kind: Literal["added", "removed", "changed"]
    before: ProjectBoqItemIn | None
    after: ProjectBoqItemIn | None
    fields: list[str] = field(default_factory=list)


def compare_boq(before: Sequence[ProjectBoqItemIn], after: Sequence[ProjectBoqItemIn]) -> list[BoqChange]:
    """Added and changed lines in `after`'s order, then removed ones in
    `before`'s. Lines sharing a key (an item listed twice) pair off in order,
    so one of two identical lines going away is one removal, not two."""
    unmatched: dict[tuple, list[ProjectBoqItemIn]] = {}
    for line in before:
        unmatched.setdefault(line_key(line), []).append(line)

    changes: list[BoqChange] = []
    for line in after:
        candidates = unmatched.get(line_key(line))
        if not candidates:
            changes.append(BoqChange("added", None, line))
            continue
        old = candidates.pop(0)
        changed = [
            name for name in COMPARED_FIELDS if _comparable(getattr(old, name)) != _comparable(getattr(line, name))
        ]
        if changed:
            changes.append(BoqChange("changed", old, line, changed))

    for leftovers in unmatched.values():
        changes.extend(BoqChange("removed", old, None) for old in leftovers)
    return changes
