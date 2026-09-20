"""The Drawings Log: each floor of the IFC drawings, and where its shop
drawing stands at every revision.

The floors are the floor-plan sheets of the IFC drawings in force (the BOQ
page's As per IFC Drawings tab, each drawing at its latest revision), in
the drawing's own sheet order. A typical plan is one row -- "TYPICAL 3RD
TO 16TH FLOOR", fourteen floors -- because one shop drawing is issued for
it, as the IFC sheet was.

The statuses are never typed in. They are the shop-drawing submissions and
consultant replies the document control reads from the project folder
(`document_sync.log_records`, category "drawings"): the revision a
submission was filed under, the consultant's marked decision, the floor
its title names. A submission belongs to a row when the floors it names
meet the row's floors; one naming no floor, or a floor no IFC sheet has,
is listed apart rather than dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ifc.comparison import floor_key

# How a consultant decision reads in the log, worst first where one revision has several.
STATUS = {
    "rejected": ("not_approved", "Not Approved"),
    "UR": ("under_review", "Under Review"),
    "ANN": ("approved_as_noted", "Approved as Noted"),
    "approved": ("approved", "Approved"),
}
_NAMED_LEVEL = re.compile(r"BASEMENT|PODIUM|MEZZ|PARKING", re.I)
_SPAN = re.compile(r"\b[BP]?(\d{1,3})\s*(?:ST|ND|RD|TH)?\s*(?:TO|-|–|&)\s*[BP]?(\d{1,3})\s*(?:ST|ND|RD|TH)?", re.I)
MIN_REVISIONS = 3   # R0, R1, R2 shown even before anything is submitted


def floors_named(label: str | None) -> set[str]:
    """The floor keys a shop drawing's floor names: 'TYPICAL 3RD TO 16TH
    FLOOR' is L3..L16, '3rd Basement' B3, a range of basements each basement."""
    if not label:
        return set()
    text = str(label).upper()
    span = _SPAN.search(text)
    if span:
        low, high = int(span.group(1)), int(span.group(2))
        if 0 <= low < high <= 200:
            if _NAMED_LEVEL.search(text):
                prefix = floor_key(text)[0][:1]   # "B" or "P"
                return {f"{prefix}{n}" for n in range(low, high + 1)}
            return {f"L{n}" if n else "GF" for n in range(low, high + 1)}
    return {floor_key(text)[0]}


def _rev_number(revision: str | None) -> int:
    m = re.fullmatch(r"R0*(\d+)", (revision or "").strip().upper())
    return int(m.group(1)) if m else -1


@dataclass
class Row:
    key: str
    sheet: str
    drawing: str
    ifc_revision: str
    floor_name: str
    title: str
    floors: int
    keys: set[str]
    order: int


def _rows(drawings: list[dict]) -> list[Row]:
    """The floor-plan sheets of the IFC drawings in force, as rows."""
    rows = []
    for d in drawings:
        for sheet in d["sheets"]:
            if sheet.get("kind") != "plan":
                continue
            mult = int(sheet.get("multiplier") or 1)
            numbers = [int(n) for n in sheet.get("floors") or []]
            if mult > 1 and len(numbers) == mult:
                keys = {f"L{n}" for n in numbers}
            else:
                keys = floors_named(sheet["floor_name"]) if mult > 1 else {floor_key(sheet["floor_name"])[0]}
            rows.append(Row(key=f"{d['id']}:{sheet['name']}", sheet=sheet["name"], drawing=d["filename"],
                            ifc_revision=d.get("revision") or "R0", floor_name=sheet["floor_name"], title=sheet["title"],
                            floors=mult, keys=keys, order=len(rows)))
    return rows


def floor_label(name: str) -> str:
    """A floor as the log shows it: '3RD BASEMENT FLOOR' -> '3rd Basement Floor',
    a sheet number carried in from the title dropped ('119 STRUCTURAL SLAB')."""
    text = re.sub(r"^\d{2,}\s+(?=[A-Z])", "", name.strip(), flags=re.I)
    text = re.sub(r"\b(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), text.title())
    return re.sub(r"(?<=\s)(To|And|Of)(?=\s)", lambda m: m.group(1).lower(), text)


def _pick(entries: list) -> object:
    """One revision's submission for a floor: a decided one over one still
    under review, then the latest filed."""
    return sorted(entries, key=lambda r: (r.status != "UR", r.modified))[-1]


def build(drawings: list[dict], records: list, in_system=lambda code: (code or "").upper() in ("FAS", "FA")) -> dict:
    """`drawings`: the IFC drawings in force, resolved. `records`: document
    control records (`document_sync.log_records`); `in_system(code)` says
    which system codes are the fire alarm's (voice evacuation too, on an
    Edwards-integrated job -- `system_rules.effective_code`)."""
    rows = _rows(drawings)
    shop = [r for r in records if getattr(r, "category", None) == "drawings" and in_system(r.system_code)]
    by_row: dict[str, dict[str, list]] = {row.key: {} for row in rows}
    unplaced = []
    for rec in shop:
        named = floors_named(rec.floor)
        hits = [row for row in rows if named & row.keys]
        if not hits:
            unplaced.append(rec)
            continue
        for row in hits:
            by_row[row.key].setdefault(rec.revision or "R0", []).append(rec)

    latest_seen = max((_rev_number(r.revision) for r in shop), default=-1)
    revisions = [f"R{n}" for n in range(max(MIN_REVISIONS, latest_seen + 1))]

    def cell(rec) -> dict:
        state, label = STATUS.get(rec.status, STATUS["UR"])
        return {"status": state, "label": label, "reference": rec.reference, "path": rec.path, "page": rec.page,
                "floor_named": rec.floor, "remarks": rec.reply_text, "modified": rec.modified.isoformat()}

    out_rows = []
    for row in rows:
        cells = {rev: cell(_pick(recs)) for rev, recs in by_row[row.key].items()}
        latest = max(cells, key=_rev_number) if cells else None
        out_rows.append({
            "key": row.key, "sheet": row.sheet, "drawing": row.drawing, "ifc_revision": row.ifc_revision,
            "floor": floor_label(row.floor_name), "title": row.title, "floors": row.floors,
            "cells": {rev: cells.get(rev) or {"status": "not_submitted", "label": "Not Submitted"} for rev in revisions},
            "latest_revision": latest,
            "latest_status": cells[latest]["status"] if latest else "not_submitted",
            "remarks": (cells[latest].get("remarks") or "") if latest else "",
            "latest_path": cells[latest]["path"] if latest else None,
            "latest_page": cells[latest]["page"] if latest else 1,
        })

    counts: dict[str, int] = {}
    for r in out_rows:
        counts[r["latest_status"]] = counts.get(r["latest_status"], 0) + 1
    return {
        "revisions": revisions,
        "rows": out_rows,
        "counts": counts,
        "unplaced": [{"reference": r.reference, "revision": r.revision, "floor_named": r.floor,
                      "status": STATUS.get(r.status, STATUS["UR"])[0], "label": STATUS.get(r.status, STATUS["UR"])[1],
                      "path": r.path, "page": r.page, "name": r.name} for r in unplaced],
        "submissions": len(shop),
    }
