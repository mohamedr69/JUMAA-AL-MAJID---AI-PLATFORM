"""The Drawings Log: each shop drawing, and where it stands at every revision.

The log is the shop drawings' own. Each row is a shop drawing as the
document control reads it from the project folder (`document_sync.log_records`,
category "drawings"): its own drawing reference (BBY006-GME-SDW-FP-FA-BSM-
B01-010001), the floor its title names, and every revision it went through
-- each revision's status the consultant's reply to that revision, as
filed. A typical plan is one row -- "TYPICAL 3RD TO 16TH FLOOR", fourteen
floors -- because one shop drawing is issued for it.

The IFC drawings (the BOQ's As per IFC Drawings) share only the floors with
it. A floor an IFC plan has and no shop drawing covers yet is a row of its
own, "not submitted", named by the floor alone. Nothing of the IFC
drawing's -- its sheet names ("FA 101"), its drawing numbers, its
revisions -- is a shop drawing's reference or status, and importing or
re-importing the IFC drawings changes nothing in this log but which floors
are still to be drawn.

A revision is never worked out from the latest one. Where R1 was
submitted, R0 was submitted and answered: if the answer to R0 is not in the
folder, R0 says exactly that ("Answered - reply not found"), never "Not
Submitted" or "Under Review".
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
    # A revision a later one replaced, whose consultant reply was not read:
    # it was answered -- the next revision exists -- and the answer is not
    # in the folder. Said as such, never "under review".
    "SUPERSEDED": ("reply_not_found", "Answered - Reply Not Found"),
}
NOT_SUBMITTED = {"status": "not_submitted", "label": "Not Submitted"}
_NAMED_LEVEL = re.compile(r"BASEMENT|PODIUM|MEZZ|PARKING", re.I)
_LIST = re.compile(r"(?<![\w.])(?:[BPL]\s*)?(\d{1,3})(?=\s*(?:,|&|AND|$))", re.I)
_SPAN = re.compile(r"\b[BPL]?(\d{1,3})\s*(?:ST|ND|RD|TH)?\s*(?:TO|-|–|&)\s*[BPL]?(\d{1,3})\s*(?:ST|ND|RD|TH)?", re.I)
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
    # A list rather than a run: "Basement 4, 3, 2" and "L52 & 53" are one
    # submission covering each of the floors it names, and taking only the
    # first left the others looking undrawn.
    listed = _LIST.findall(text)
    if len(listed) > 1:
        prefix = floor_key(text)[0][:1] if _NAMED_LEVEL.search(text) else "L"
        return {f"{prefix}{int(n)}" if int(n) or prefix != "L" else "GF" for n in listed}
    return {floor_key(text)[0]}


# A floor a title names by what it is rather than its level number: "1ST
# MECHANICAL", "2ND STRUCTURAL (NON ACCESSIBLE)". The ordinal belongs to it
# -- the 1st and 2nd mechanical floors are two floors.
_ORDINAL_NAMED = re.compile(r"\b(\d{1,2})\s*(?:ST|ND|RD|TH)\s+([A-Z]{3,})", re.I)
_NOT_A_NAME = {"FLOOR", "FLOORS", "FLR", "TO", "AND", "BASEMENT", "PODIUM", "LEVEL", "PLAN", "RES"}
# A level as the log keys it: basements, podiums, levels, ground and roofs.
_LEVEL_KEY = re.compile(r"(?:[BPL]\d+|GF|RF|TRF)")
# Words that say nothing about which floor it is.
_FILLER = {"PLAN", "FLOOR", "FLR", "ROOM", "LAYOUT", "THE"}


def _named_floors(text: str | None) -> set[str]:
    """The ordinal named floors a title names: "L02- 1ST MECHANICAL FLOOR
    PLAN" is the 1st mechanical floor ("MECHANICAL#1")."""
    return {f"{word.upper()}#{int(n)}" for n, word in _ORDINAL_NAMED.findall(text or "")
            if word.upper() not in _NOT_A_NAME}


def _plain(key: str) -> str:
    """A floor named in words, in words that tell it apart: "LIFT MACHINE
    ROOM FLOOR PLAN" and "LIFT MACHINE FLOOR" are both the lift machine floor."""
    if _LEVEL_KEY.fullmatch(key) or "#" in key or " " not in key:
        return key
    words = [w for w in re.findall(r"[A-Z0-9]+", key.upper()) if w not in _FILLER]
    return " ".join(words) or key


def _named_label(key: str) -> str:
    """"MECHANICAL#1" as the log writes it: "1st Mechanical Floor"."""
    word, _, n = key.partition("#")
    number = int(n) if n.isdigit() else 0
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix} {word.title()} Floor"


def floor_aliases(pairs) -> dict[str, str]:
    """{named floor: its level}, from every title that names both: "L22-2ND
    MECHANICAL FLOOR PLAN" says the 2nd mechanical floor is L22. `pairs`:
    (floor, full title) of every drawing, shop and IFC alike -- read off
    the drawings, never assumed."""
    aliases: dict[str, str] = {}
    for floor, title in pairs:
        levels = {k for k in floors_named(floor) if _LEVEL_KEY.fullmatch(k)}
        named = _named_floors(floor) | _named_floors(title)
        if len(levels) == 1 and named:
            level = next(iter(levels))
            for name in named:
                aliases.setdefault(name, level)
    return aliases


def floor_identity(floor: str | None, title: str | None = None, aliases: dict[str, str] | None = None) -> set[str]:
    """The floors a drawing is of, from its floor and its full title: the
    levels it names, and the floors it names by what they are -- each
    brought to its level where another title ties them."""
    aliases = aliases or {}
    named = _named_floors(floor) | _named_floors(title)
    keys = floors_named(floor) if floor else set()
    if named:
        # "1ST MECHANICAL FLOOR" is not every mechanical floor: the generic
        # key gives way to the one the ordinal names.
        keys = {k for k in keys if _LEVEL_KEY.fullmatch(k)}
    return {aliases.get(k, k) for k in {_plain(k) for k in keys} | named}


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


def _rows(drawings: list[dict], aliases: dict[str, str] | None = None) -> list[Row]:
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
            elif mult > 1:
                keys = floors_named(sheet["floor_name"])
            else:
                keys = floor_identity(sheet["floor_name"], sheet.get("title"), aliases) or {floor_key(sheet["floor_name"])[0]}
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


def _unplaced_row(record, *, floor: str | None = None, cells: dict | None = None,
                  floors: int = 1, keys: set[str] | None = None) -> dict:
    """One shop drawing, as the log shows it.

    The drawing is one row at the revision that stands, and every revision
    it went through is a cell of its own -- R0 beside R1 beside R2 -- so
    the page can show what each one came back as rather than only the
    latest.
    """
    def cell(rec) -> dict:
        state, label = STATUS.get(rec.status, STATUS["UR"])
        return {"revision": rec.revision, "status": state, "label": label, "reference": rec.reference,
                "path": rec.path, "page": rec.page, "name": rec.name, "floor_named": rec.floor,
                "remarks": rec.reply_text, "modified": rec.modified.isoformat() if rec.modified else None}

    if cells is not None:
        revisions = {rev: cell(rec) for rev, rec in sorted(cells.items(), key=lambda kv: _rev_number(kv[0]))}
    else:
        revisions = {rec.revision: cell(rec) for rec in reversed(record.superseded)}
        revisions[record.revision] = cell(record)
    state, label = STATUS.get(record.status, STATUS["UR"])
    return {"reference": record.reference, "revision": record.revision,
            "floor_named": floor if floor is not None else record.floor,
            "status": state, "label": label, "path": record.path, "page": record.page,
            "name": record.name, "remarks": record.reply_text,
            # How many floors this one drawing stands for: a typical-floor
            # sheet is one row and nineteen floors.
            "floors": floors,
            "floor_keys": sorted(keys or (), key=_floor_order),
            "revisions": revisions}


_TYPICAL = re.compile(r"\bTYP(?:ICAL|E)?\b|\(TYP", re.I)


def _is_typical(record) -> bool:
    """Whether this is one drawing for a run of identical floors.

    A typical-floor sheet says so: "L23 TO L40 - RES 20 TO 37 (TYP 1A)",
    "TYPICAL 23RD TO 38TH FLOOR PLAN". It is drawn once and issued for
    the whole run, so it is one row.

    A submission that covers several floors without saying that --
    "Shop Drawing for Basement 4, 3, 2 Floor Plan" -- is several
    floors, each drawn on its own and sent together. Those are a row
    each: the engineer tracks a basement, not the envelope it came
    in.
    """
    return bool(_TYPICAL.search(f"{record.name or ''} {record.floor or ''}"))


def _spelled(key: str) -> str:
    """A floor nothing drew on its own, written out: "B4" is Basement 4.

    Only reached for a floor that no single-floor sheet names -- one drawn
    as part of a run, say -- because a floor the engineers have written is
    shown their way.
    """
    if key == "GF":
        return "Ground Floor"
    digits = re.search(r"\d+", key)
    number = digits.group() if digits else ""
    word = {"B": "Basement", "P": "Podium", "L": "Level"}.get(key[:1])
    return f"{word} {number}" if word and number else floor_label(key)


def _floor_order(key: str) -> tuple:
    """Floors in building order: basements downwards, then up the tower."""
    kind = {"B": 0, "GF": 1, "P": 3, "L": 4}.get(key[:2] if key[:2] == "GF" else key[:1], 5)
    digits = re.search(r"\d+", key)
    number = int(digits.group()) if digits else 0
    return (kind, -number if key[:1] == "B" else number, key)


def _by_floor(unmatched: list, aliases: dict[str, str] | None = None) -> list[dict]:
    """The shop drawings that no IFC floor plan claimed, a row per floor.

    A floor has one shop drawing, and a submission can cover several
    floors at once -- "Basement 4, 3, 2" is the drawing for B4, B3 and B2.
    Listed a row per *submission*, B4 appeared three times over: once under
    its own sheet, once under "Basement 4 & 3" and once under "Basement 4,
    3, 2", each showing a different status for the same floor.

    So the floors are what the rows are. Every submission is entered
    against each floor it names, and where more than one covers a floor at
    the same revision, `_pick` takes the one that decides it and failing
    that the one filed last -- the latest in the folder, as the engineer
    reads it.

    A drawing whose floor was never read has no floor to be entered
    against and keeps a row of its own.
    """
    by_floor: dict[str, dict[str, list]] = {}
    nameless: list = []
    # What the drawings call each floor. A sheet drawn for one floor names
    # it the way the engineers write it ("BASEMENT-4", "GROUND FLOOR"), and
    # that reads better than the key it is matched on ("B4", "GF").
    called: dict[str, str] = {}
    for record in unmatched:
        # The revisions it went through, not only the one that stands:
        # `combine` folds the answered earlier ones onto the record.
        entries = [record, *getattr(record, "superseded", ())]
        # The floor and the full title: "L22" filed with the title "L22-2ND
        # MECHANICAL FLOOR PLAN" is the 2nd mechanical floor as well.
        named = floor_identity(record.floor, record.name, aliases) if record.floor else set()
        if not named:
            nameless.append(record)
            continue
        if len(named) == 1:
            called.setdefault(next(iter(named)), record.floor)
        for key in named:
            for entry in entries:
                by_floor.setdefault(key, {}).setdefault(entry.revision or "R0", []).append(entry)

    # Every floor decides which drawing stands for it -- that is what drops
    # a combined basement sheet once each of its basements has one of its
    # own. Then the floors are gathered back under the drawing that won
    # them, because a drawing issued for a run of floors is one drawing and
    # the log should read the way the drawing does: "L03 TO L21", not
    # nineteen rows saying Level 3, Level 4, Level 5.
    standing: dict[str, list[str]] = {}
    cells_for: dict[str, dict[str, list]] = {}
    for key in sorted(by_floor, key=_floor_order):
        per_revision = {rev: _pick(entries) for rev, entries in by_floor[key].items()}
        winner = per_revision[max(per_revision, key=_rev_number)]
        # A typical-floor sheet is one drawing for the whole run and gathers
        # its floors into one row. Floors drawn separately and merely sent
        # together keep a row each -- the engineer tracks the basement, not
        # the envelope it arrived in -- so those are held under the floor.
        held = (f"{winner.reference}|{winner.system_code}" if _is_typical(winner)
                else f"{winner.reference}|{winner.system_code}|{key}")
        standing.setdefault(held, []).append(key)
        for rev, entry in per_revision.items():
            cells_for.setdefault(held, {}).setdefault(rev, []).append(entry)

    rows = []
    for held, keys in standing.items():
        revisions = {rev: _pick(entries) for rev, entries in cells_for[held].items()}
        latest = revisions[max(revisions, key=_rev_number)]
        # The drawing's own words, where it stands for every floor it names
        # -- a typical-floor sheet should read "L03 TO L21", not nineteen
        # levels. Where a later sheet has taken some of its floors, it is
        # named for the ones it is still the drawing for, so the row does
        # not claim floors it no longer covers.
        covers = floor_identity(latest.floor, latest.name, aliases) if latest.floor else set()
        held_all = covers and covers == set(keys)
        label = (latest.floor if held_all
                 else ", ".join(called.get(k) or _spelled(k)
                                for k in sorted(keys, key=_floor_order)))
        rows.append(_unplaced_row(latest, floor=label, cells=revisions, floors=len(keys), keys=set(keys)))
    rows.sort(key=lambda row: _floor_order(sorted(floors_named(row["floor_named"]) or {""},
                                                  key=_floor_order)[0] or ""))
    rows += [_unplaced_row(record) for record in nameless]
    return rows


def answered_revisions(revisions: dict[str, dict]) -> dict[str, dict]:
    """A shop drawing's revisions as filed, keyed R0, R1, ... -- each the
    consultant's reply to that revision, never one worked out from another.

    What a later revision proves is kept to: where R1 was submitted, R0 was
    submitted and answered, so an R0 whose answer is not in the folder (no
    file for it, or a file with no reply read off it) is "answered - reply
    not found", never "not submitted" or "under review". Where R2 was
    submitted, the same holds for R0 and R1."""
    by_number = {_rev_number(rev): cell for rev, cell in revisions.items() if _rev_number(rev) >= 0}
    top = max(by_number, default=-1)
    out: dict[str, dict] = {}
    for n in range(top + 1):
        cell = by_number.get(n)
        if n < top and (cell is None or cell.get("status") in ("under_review", "not_submitted", "reply_not_found")):
            cell = {**(cell or {"revision": f"R{n}", "reference": None, "path": None, "page": 1, "name": None,
                                "floor_named": None, "remarks": None, "modified": None}),
                    "status": "reply_not_found", "label": STATUS["SUPERSEDED"][1],
                    "note": (f"R{top} was submitted, so R{n} was submitted and answered; "
                             + ("the consultant's reply to it was not found in the project folder."
                                if cell is None or not cell.get("path")
                                else "no consultant reply was read off its file."))}
        if cell is not None:
            out[f"R{n}"] = {**cell, "revision": f"R{n}"}
    return out


def build(drawings: list[dict], records: list, in_system=lambda code: (code or "").upper() in ("FAS", "FA")) -> dict:
    """`drawings`: the IFC drawings in force, resolved -- read for their
    floors only. `records`: document control records
    (`document_sync.log_records`); `in_system(code)` says which system codes
    are the fire alarm's (voice evacuation too, on an Edwards-integrated job
    -- `system_rules.effective_code`)."""
    # A drawing is one we submitted, not a line in the schedule that says we
    # will. The project's "Shop drawings log.pdf" lists every drawing it
    # plans -- FA 101 UNDER GROUND PLAN, FA 102 BASEMENT-4 -- with the dates
    # they are due, and those rows carry no revision and no consultant
    # reply. Read as drawings they filled the log with floors nobody had
    # drawn yet. They are still read, and `combine` uses them to give an
    # issued drawing the scheduled floor it belongs to; they are simply not
    # drawings themselves.
    shop = [r for r in records
            if getattr(r, "category", None) == "drawings"
            and getattr(r, "source", None) != "drawing schedule"
            and in_system(r.system_code)]

    latest_seen = max((_rev_number(r.revision) for entry in shop for r in (entry, *getattr(entry, "superseded", ()))),
                      default=-1)
    revisions = [f"R{n}" for n in range(max(MIN_REVISIONS, latest_seen + 1))]

    # Which level each named floor is, from every title that names both --
    # the shop drawings' and the IFC sheets' ("L41 - 3RD MECHANICAL FLOOR").
    aliases = floor_aliases(
        [(r.floor, r.name) for entry in shop for r in (entry, *getattr(entry, "superseded", ()))]
        + [(sheet.get("floor_name"), sheet.get("title")) for d in drawings for sheet in d.get("sheets") or []
           if sheet.get("kind") == "plan"])
    named_as = {level: name for name, level in aliases.items()}

    # Every shop drawing, from the shop drawings alone.
    rows: list[dict] = []
    covered: set[str] = set()
    for index, drawing in enumerate(_by_floor(shop, aliases)):
        covered |= set(drawing["floor_keys"])
        floor = floor_label(drawing["floor_named"]) if drawing["floor_named"] else "Floor not named on the drawing"
        # A level its title also names by what it is: "L02 - 1st Mechanical Floor".
        extra = [_named_label(named_as[k]) for k in drawing["floor_keys"] if k in named_as]
        if len(drawing["floor_keys"]) == 1 and extra and extra[0].upper() not in floor.upper():
            floor = f"{floor} - {extra[0]}"
        history = answered_revisions(drawing["revisions"])
        latest = f"R{_rev_number(drawing['revision'])}" if _rev_number(drawing["revision"]) >= 0 else drawing["revision"]
        rows.append({
            **drawing,
            "key": f"sd:{index}:{drawing['reference']}",
            "source": "shop_drawing",
            "floor": floor,
            "revisions": history,
            "cells": {rev: history.get(rev) or dict(NOT_SUBMITTED) for rev in revisions},
            "latest_revision": latest,
            "latest_status": drawing["status"],
            "remarks": drawing["remarks"] or "",
            "latest_path": drawing["path"],
            "latest_page": drawing["page"],
        })

    # The floors the IFC drawings have that no shop drawing covers yet:
    # the floor alone -- no IFC sheet name, number or revision.
    seen: set[frozenset] = set()
    for plan in _rows(drawings, aliases):
        keys = frozenset(plan.keys)
        if not keys or keys & covered or keys in seen:
            continue
        seen.add(keys)
        ordered = sorted(keys, key=_floor_order)
        rows.append({
            "key": f"floor:{','.join(ordered)}", "source": "ifc_floor", "reference": None,
            "floor": floor_label(plan.floor_name), "floor_named": None, "floor_keys": ordered, "floors": plan.floors,
            "revision": None, "status": "not_submitted", "label": "Not Submitted", "path": None, "page": 1,
            "name": None, "revisions": {},
            "cells": {rev: dict(NOT_SUBMITTED) for rev in revisions},
            "latest_revision": None, "latest_status": "not_submitted", "remarks": "", "latest_path": None,
            "latest_page": 1,
        })

    # Building order, lowest floor first; a drawing naming no floor last.
    rows.sort(key=lambda row: (not row["floor_keys"],
                               _floor_order(row["floor_keys"][0]) if row["floor_keys"] else ()))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["latest_status"]] = counts.get(row["latest_status"], 0) + 1
    return {
        "revisions": revisions,
        "rows": rows,
        "counts": counts,
        "submissions": len(shop),
    }
