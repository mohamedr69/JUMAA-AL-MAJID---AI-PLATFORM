"""Sample submissions, read off the company's document transmittals.

A sample board goes to the consultant under a transmittal, a Word form
kept in the project's Transmittal folder ("Transmittal", "EP-11285
Transmittal", "05- Transmittal"). Only that folder is looked in, and only
the document's content decides -- the file name does not ("EP-21152 PA
Sample Board.doc" is a technical submittal). The form reads:

    Date      : 02/09/2026          AASS Ref. : TR/230/26
    Subject   : Sample Board / Fire Alarm & Voice Evacuation & Central Battery System -EATON
    ITEM  Drawing No.  Description                                   Qty
    1     ---          Sample Board / Fire Alarm & Voice Evacuation  1 No.
    2     ---          Sample Board / Central Battery System -EATON  3 Nos

Each item that is a sample board (or sample material) and names a system
is one submission for that system; where no item names one -- "Sample
Board / Fire Alarm System" in the subject over a list of detector part
numbers -- the subject does. A transmittal records that a sample was
sent, not what the consultant made of it: every submission is under
review until a reply says otherwise, and a later one for the same system
supersedes it (see `number`).
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone

from app.services.document_control import ControlledDocument
from app.services.word_text import WORD_SUFFIXES, cells

# Any folder named for transmittals, however the project spells it.
FOLDER = re.compile(r"transmitt?al", re.I)

BOARD, MATERIAL = "board", "material"
KIND_TITLES = {BOARD: "Sample Board", MATERIAL: "Sample Material"}
# The group every submission of a kind is filed under, per system: the
# log lists one "Sample Board" document per system with its submissions
# as revisions.
GROUP = {BOARD: "SAMPLE BOARD", MATERIAL: "SAMPLE MATERIAL"}

# The spellings the forms use, typos included ("Sample Baord", "Samp Board", "Sam B").
_BOARD = re.compile(r"\bsam(?:ple|p)?s?\.?\s*-?\s*b(?:oa?rd|aord|ord|oad)s?\b|\bsam(?:ple|p)?\.?\s+b\b", re.I)
_MATERIAL = re.compile(r"\bsam(?:ple|p)?s?\.?\s*-?\s*(?:mat(?:erials?)?|units?|speakers?)\b", re.I)

# The platform's systems, as a transmittal names them. Voice evacuation is
# its own code here; the project's rules fold it into the fire alarm when
# the fire alarm is an integrated Edwards system (system_rules).
SYSTEMS = (
    ("FRC", re.compile(r"fire\s*[- ]?\s*(?:rated|resistan(?:t|ce)|proof)?\s*cables?|\bFRC\b", re.I)),
    ("ELS", re.compile(r"emergency\s*light|central\s*battery|\bCBS\b|self[\s-]*contained|\bEML\b|\bEM\s+light|(?:exit|emergency)\s*(?:light|luminaire)", re.I)),
    ("PAVA", re.compile(r"public\s*address|\bPA\s*/?\s*VA\b|\bPAVA\b|voice\s*alarm|background\s*music|\bBGM\b|\bPA\s+system", re.I)),
    ("VES", re.compile(r"voice\s*evac", re.I)),
    ("FAS", re.compile(r"fire\s*(?:alarm|detection|telephone)|\bFAVE\b|\bFA\s*(?:&|and)\s*VE\b"
                      # a sample named by its device rather than its system
                      r"|(?:smoke|heat|optical|multi[\s-]*sensor)\s*detector|call\s*point|pull\s*station|strobe|sounder"
                      r"|(?:input|output|monitor|control)\s*module|signature\s*series|\bSIGA\b", re.I)),
)
_DATE = re.compile(r"^(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})$")
_REF = re.compile(r"^TR\s*/\s*\d+\s*/\s*\d{2,4}$", re.I)
# Where the header ends: the body starts at the scope table or the salutation.
_BODY = re.compile(r"^(?:SCOPE\b|Dear\b|With\s+reference)", re.I)


def in_transmittal_folder(relative: str) -> bool:
    """Whether the file sits in (or under) a folder named for transmittals."""
    return any(FOLDER.search(part) for part in re.split(r"[\\/]", relative)[:-1])


def is_transmittal(relative: str) -> bool:
    suffix = relative[relative.rfind("."):].lower() if "." in relative else ""
    return suffix in WORD_SUFFIXES and in_transmittal_folder(relative) and not relative.rsplit("/", 1)[-1].startswith("~$")


def kind_of(text: str) -> str | None:
    if _BOARD.search(text):
        return BOARD
    if _MATERIAL.search(text):
        return MATERIAL
    return None


def systems_of(text: str) -> list[str]:
    codes = [code for code, pattern in SYSTEMS if pattern.search(text)]
    # "Sample Board / Fire Rated Cables for Fire Alarm & Emergency Lighting
    # System" is a board of cables: the systems named are what they serve.
    return ["FRC"] if "FRC" in codes else codes


def submissions(text: str) -> list[tuple[str, str]]:
    """(kind, system) for each sample a piece of text names. Each mention
    of a sample starts a clause of its own -- "Sample Board / Emergency
    Lighting System & Sample Material / Fire Alarm System" is two -- and a
    first clause that names no system takes the words before it ("Fire
    Alarm Sample Board -Edwards")."""
    mentions = sorted(((m.start(), m.end(), kind) for pattern, kind in ((_BOARD, BOARD), (_MATERIAL, MATERIAL))
                       for m in pattern.finditer(text)))
    found: list[tuple[str, str]] = []
    for n, (start, _end, kind) in enumerate(mentions):
        clause = text[start:mentions[n + 1][0] if n + 1 < len(mentions) else len(text)]
        for code in systems_of(clause) or (systems_of(text[:start]) if n == 0 else []):
            if (kind, code) not in found:
                found.append((kind, code))
    return found


def _value_after(parts: list[str], label: re.Pattern, value: re.Pattern | None = None, limit: int = 4) -> str | None:
    """The value a header label carries: the next cell after it that is
    not the ":" between them."""
    for i, part in enumerate(parts):
        head = label.match(part)
        if not head:
            continue
        for candidate in [part[head.end():]] + parts[i + 1:i + 1 + limit]:
            candidate = candidate.strip(" :")
            if candidate and (value is None or value.match(candidate)):
                return candidate
    return None


def _date(value: str | None) -> datetime | None:
    found = _DATE.match(value or "")
    if not found:
        return None
    day, month, year = (int(g) for g in found.groups())
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def read_transmittal(text: str, relative: str, modified: datetime) -> list[ControlledDocument]:
    """The sample submissions a transmittal carries: one record per system
    and kind. A transmittal of anything else (a material submittal, shop
    drawings) gives none."""
    parts = cells(text)
    subject = _value_after(parts, re.compile(r"^subject\b", re.I), limit=3) or ""
    body_start = next((i for i, p in enumerate(parts) if _BODY.match(p)), 0)
    date = _date(_value_after(parts, re.compile(r"^date\b", re.I), _DATE))
    reference = _value_after(parts, re.compile(r"^(?:AASS\s*)?ref(?:erence)?\.?(?:\s*no\.?)?\b", re.I), _REF)
    reference = re.sub(r"\s+", "", reference).upper() if reference else None

    found: list[tuple[str, str]] = []
    # The items: a cell that is a sample and names its system, or a sample
    # cell whose next cell names it ("Sample Board | BGM System").
    items = parts[body_start:]
    for i, part in enumerate(items):
        if re.match(r"^with\s+reference", part, re.I):
            continue   # "please find attached herewith sample board details" names nothing
        kind = kind_of(part)
        if not kind:
            continue
        following = items[i + 1] if i + 1 < len(items) else ""
        named = submissions(part) or ([] if kind_of(following) else [(kind, code) for code in systems_of(following)])
        found.extend(entry for entry in named if entry not in found)
    if not found:
        found = submissions(subject)
    return [ControlledDocument(
        system_code=code, name=KIND_TITLES[kind], path=relative, modified=date or modified,
        reference=reference or relative.rsplit("/", 1)[-1].rsplit(".", 1)[0], revision="R0", status="UR",
        source="transmittal", category="samples", group_reference=GROUP[kind],
    ) for kind, code in found]


def number(records: list[ControlledDocument]) -> list[ControlledDocument]:
    """The transmittal submissions as the log shows them: one per system,
    kind and transmittal (the same transmittal filed twice -- a OneDrive
    conflict copy, a copy in another folder -- is one), numbered R0, R1, ...
    in the order they were sent, each earlier one superseded by the next."""
    unique: dict[tuple, ControlledDocument] = {}
    # The original before its conflict copy ("…-DSTHBGLJ32.doc"), which sorts first by name.
    for row in sorted(records, key=lambda r: (r.modified, len(r.path), r.path)):
        # Without a TR number (older forms) the file name stands in, and a
        # conflict copy's name differs: the date alone decides then.
        number_ = row.reference.upper() if _REF.match(row.reference) else None
        key = (row.group_reference, row.system_code, number_, row.modified.date())
        unique.setdefault(key, row)
    chains: dict[tuple, list[ControlledDocument]] = {}
    for row in unique.values():
        chains.setdefault((row.group_reference, row.system_code), []).append(row)
    numbered = []
    for chain in chains.values():
        chain.sort(key=lambda r: (r.modified, r.reference))
        for n, row in enumerate(chain):
            numbered.append(replace(row, revision=f"R{n}", status="UR" if n == len(chain) - 1 else "SUPERSEDED"))
    return numbered
