"""What a calculation was made from, and what it produced, as hashes.

A calculation here is always recomputed from its stored inputs, so a number
cannot drift from them. What a reader still cannot tell from a number is
*which* inputs it came from: the BOQ lines, the engineer's settings, and the
versions of the design rules and catalogue entries in force. `input_hash`
names those exactly and `result_hash` the figures they produced; an export,
a sign-off or a report that carries both can later be checked against the
project as it stands -- the same input hash means the same calculation.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

HASH_VERSION = "calc-hash-1"


def _default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def stable_hash(payload: Any) -> str:
    text = json.dumps({"v": HASH_VERSION, "payload": payload}, sort_keys=True, default=_default, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def battery_inputs(lines: list, design, sizing_rule, selection_rule, currents: dict, batteries: dict) -> dict:
    """Everything the battery calculation reads. Currents and batteries are
    reduced to the parts the BOQ actually names, so an unrelated catalogue
    change does not look like a change to this project's calculation."""
    from app.services.battery_calculation import part_key

    named = {part_key(line.catalog_no or "") for line in lines if line.catalog_no}
    return {
        "lines": [[l.system_code, l.group_heading, l.catalog_no, l.description, l.quantity, l.manufacturer] for l in lines],
        "design": design,
        "sizing_rule": [sizing_rule.id, sizing_rule.version, sizing_rule.data] if sizing_rule else None,
        "selection_rule": [selection_rule.id, selection_rule.version, selection_rule.data] if selection_rule else None,
        "currents": sorted([key, c.rule_id, c.rule_version, c.standby_ma, c.alarm_ma] for key, c in currents.items() if key in named),
        "batteries": sorted([key, b.capacity_ah, b.voltage, b.brand] for key, b in batteries.items()),
    }


def panel_inputs(heading: str, system_code: str | None, instance: int, kind: str, lines: list, sizing: dict,
                 extras: list, currents: dict, batteries: dict) -> dict:
    """Everything one panel's calculation reads -- its own BOQ lines, its
    sizing, the loads added by hand, the currents of its parts and the
    batteries on file -- so that a change elsewhere in the BOQ or the
    catalogue is not a change to this panel."""
    from app.services.battery_calculation import part_key

    named = {part_key(line.catalog_no or "") for line in lines if line.catalog_no}
    named |= {part_key(extra.get("part_no") or "") for extra in extras if extra.get("part_no")}
    return {
        "heading": heading, "system": system_code, "instance": instance, "kind": kind,
        "lines": [[l.system_code, l.group_heading, l.catalog_no, l.description, l.quantity, l.manufacturer] for l in lines],
        "sizing": sizing,
        "extras": extras,
        "currents": sorted([key, c.rule_id, c.rule_version, c.standby_ma, c.alarm_ma, c.datasheet]
                           for key, c in currents.items() if key in named),
        "batteries": sorted([key, b.capacity_ah, b.voltage, b.brand, b.datasheet] for key, b in batteries.items()),
    }


def battery_result(panels: list) -> list:
    return [[p.key, p.total_ah, p.required_ah, p.lower_bound, sorted(p.missing_parts), p.status,
             [[s.part_no, s.units, s.strings] for s in (p.selected or [])]] for p in panels]


def ve_inputs(design) -> dict:
    data = design.model_dump(mode="json")
    # When the workbook was imported does not change what was calculated.
    if data.get("source"):
        data["source"] = {k: v for k, v in data["source"].items() if k not in ("imported_at", "warnings")}
    return data
