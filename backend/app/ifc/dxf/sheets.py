"""Sheets and floors: which floor each device is on, and how many floors
each floor plan stands for.

A full-building IFC set is one DWG with a paper-space sheet per floor
(FA-01 BASEMENT FLOOR PLAN ... FA-17 ROOF FLOOR PLAN) plus riser and
detail sheets. Each sheet's viewport shows one area of model space: a
device drawn in that area is on that floor. The sheet's title block names
the floor, and a typical-floor title says how many floors the plan stands
for ("TYP 2-10,12-16 FLOOR PLAN" is 14 floors).

The floor is read from the DRAWING TITLE in the title block, never from the
file name, the layout (sheet) name, the drawing number or the devices. A
title block labels its title ("DRAWING TITLE:") and the title is the lines
written under that label, down to the next field ("DWG NO:"): on EP-30784
that is "PODIUM-3 FLOOR PLAN" over "FIRE ALARM LAYOUT" -- the floor is the
first, though the second is written larger. Where no title names a floor,
the sheet says "Floor not identified" rather than being given a guess.

Riser, schematic and detail sheets repeat the devices as diagram symbols,
and devices outside every sheet are scratch copies: neither is counted.
A single Revit sheet (one Layout1 viewport) is simply one floor, named by
its title ("TYP(1ST TO 14TH) FLOORS FIRE ALARM LAYOUT": 14 floors).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import geometry as G

DIAGRAM = re.compile(r"SCHEMATIC|RISER|DIAGRAM|DETAIL|LEGEND|SECTION|ELEVATION|SINGLE\s*LINE|KEY\s*PLAN|WIRING", re.I)
TITLE_WORDS = re.compile(r"FLOOR|LEVEL|BASEMENT|ROOF|PODIUM|MEZZ|GROUND|TYP|PLAN|LAYOUT|SCHEMATIC|RISER|DIAGRAM|DETAIL|STOREY|STORY", re.I)
_NOTE = re.compile(r"^\s*\d+\s*[.)]")  # "1. FIRE ALARM CABLE SHALL BE ..."
_ORD = r"(?:ST|ND|RD|TH)?"
_RANGE = re.compile(rf"(?<![\w-])(\d{{1,3}})\s*{_ORD}\s*(?:-|–|TO)\s*(\d{{1,3}})\s*{_ORD}(?![\d])", re.I)
# Levels numbered with an L: "L23 TO L40 - RES 20 TO 37 (TYP 1A) FLOOR PLAN"
# is levels 23 to 40; the RES numbers after it are the flats', not floors.
_L_RANGE = re.compile(r"(?<![\w-])L\s?(\d{1,3})\s*(?:-|–|TO)\s*L\s?(\d{1,3})(?!\d)", re.I)
# An ordinal followed by a name of its own counts floors of that kind, not the
# building's: "3RD STRUCTURAL FLOOR", "2ND MECHANICAL FLOOR" are not floors 3 and
# 2 -- and taking them for those took floors 3 and 4 off "L03 TO L21".
_OWN_NAME = re.compile(r"\s+(?!(?:FLOORS?|LEVELS?|FLR|LVL|AND|TO)\b)[A-Z]{2,}", re.I)
_L_LIST = re.compile(r"(?<![\w-])L\s?\d{1,3}(?:\s*(?:&|,|AND)\s*L?\s?\d{1,3}(?!\d))+", re.I)
_L_SINGLE = re.compile(r"(?<![\w-])L\s?(\d{1,3})(?![\w])", re.I)
_SINGLE = re.compile(r"(?<![\w-])(\d{1,3})\s*(?:ST|ND|RD|TH)\b", re.I)
# A level with a name of its own: "3RD BASEMENT", "2ND PODIUM" count basements
# and podiums, not the building's numbered floors -- the typical "3RD TO 16TH
# FLOOR" plan still stands for floors 3 and 4 beside a 3rd basement and a 4th podium.
_NAMED_LEVEL = re.compile(r"\s*(?:SUB[\s-]*)?(?:BASEMENTS?|PODIUMS?|MEZZ\w*|PARKING|CAR\s*PARK|CELLAR|LOWER\s+GROUND|UPPER\s+GROUND)\b", re.I)


@dataclass
class Window:
    cx: float
    cy: float
    w: float
    h: float
    twist: float
    tx: float
    ty: float

    def contains(self, x: float, y: float) -> bool:
        dx, dy = x - self.tx, y - self.ty
        c, s = math.cos(self.twist), math.sin(self.twist)
        u, v = dx * c - dy * s, dx * s + dy * c
        return abs(u - self.cx) <= self.w / 2 and abs(v - self.cy) <= self.h / 2


@dataclass
class Sheet:
    name: str
    title: str
    kind: str                    # "plan" | "diagram" | "outside"
    floors: list[int] = field(default_factory=list)
    multiplier: int = 1
    note: str = ""
    windows: list[Window] = field(default_factory=list)
    # Where the title was read: "drawing title" (the lines under the title
    # block's DRAWING TITLE label), "title text" (the sheet's title-like
    # text, where the title block has no such label), "" (nothing titled).
    title_source: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "kind": self.kind, "floors": self.floors,
                "multiplier": self.multiplier, "note": self.note, "title_source": self.title_source}


OUTSIDE = "(outside sheets)"


def parse_floors(title: str) -> list[int]:
    """Floor numbers a title stands for: '2-10,12-16' -> 2..10 + 12..16,
    '1ST TO 14TH' -> 1..14, '11TH FLOOR' -> [11]. Named levels
    ('BASEMENT-02', '3RD BASEMENT', '2ND PODIUM', 'GROUND', 'ROOF') give [],
    i.e. one floor."""
    # "LEVEL-14TH & 21ST": the dash joins the word to its floors; it is not part of a number.
    t = re.sub(r"\b(LEVELS?|FLOORS?|LVL)\s*[-–]\s*", r"\1 ", title.upper())
    floors: set[int] = set()
    # Levels numbered with an L are the floors, and any other number in the
    # title is not: "L43 TO L50 RES 39 TO 46", "L52 & 53- RES 48 & 49", "L41 - 3RD
    # MECHANICAL FLOOR" (the 3rd mechanical floor is level 41).
    taken = []
    for m in _L_RANGE.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < a <= b <= 200:
            floors.update(range(a, b + 1))
            taken.append(m.span())
    for m in _L_LIST.finditer(t):
        if any(s <= m.start() < e for s, e in taken):
            continue
        numbers = [int(n) for n in re.findall(r"\d{1,3}", m.group(0))]
        floors.update(n for n in numbers if 0 < n <= 200)
        taken.append(m.span())
    for m in _L_SINGLE.finditer(t):
        if not any(s <= m.start() < e for s, e in taken) and 0 < int(m.group(1)) <= 200:
            floors.add(int(m.group(1)))
    if floors:
        return sorted(floors)
    spans = []
    for m in _RANGE.finditer(t):
        if _NAMED_LEVEL.match(t, m.end()):
            spans.append(m.span())  # "1ST TO 3RD BASEMENT": basements, not floors
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < a <= b <= 200:
            floors.update(range(a, b + 1))
            spans.append(m.span())
    for m in _SINGLE.finditer(t):
        if any(s <= m.start() < e for s, e in spans):
            continue
        if _NAMED_LEVEL.match(t, m.end()) or _OWN_NAME.match(t, m.end()):
            continue
        n = int(m.group(1))
        if 0 < n <= 200:
            floors.add(n)
    return sorted(floors)


# words that describe the drawing, not the floor
_NOT_FLOOR = re.compile(
    r"\b(FIRE\s+ALARM|FA|EMERGENCY(\s+LIGHT(ING)?)?|EXIT|VOICE\s+EVAC(UATION)?|LOW\s+CURRENT|"
    r"LAYOUT|PLAN|DRAWING|SHOP|IFC|SYSTEM|AND|&)\b",
    re.I,
)


def floor_count(title: str, floors: list[int]) -> int:
    """How many floors a plan stands for: its numbered floors, or a span of
    named levels ('1ST TO 3RD BASEMENT' is three), or one."""
    if floors:
        return len(floors)
    t = title.upper()
    for m in _RANGE.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if _NAMED_LEVEL.match(t, m.end()) and 0 < a <= b <= 200:
            return b - a + 1
    return 1


# A drawing or sheet number: "FA 119", "FA-101" at the head of a title, or a
# document number with its dashes ("LAC-653-PLN-L11-ELV-FA-117"). Never a floor.
# (Two letters at least, and not a level word: "L23" and "LVL 11" are floors.)
_SHEET_NUMBER = re.compile(r"^\s*(?!(?:LVL|LEV|FLR|LEVEL)\b)[A-Z]{2,4}[\s-]?\d{2,4}[A-Z]?\b\s*[-:]?\s*", re.I)
_DOC_NUMBER = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+){3,}\b", re.I)
_LEVEL_WORD = r"(?:SUB[\s-]*BASEMENT|BASEMENT|PODIUM|LEVEL|LVL|MEZZANINE|MEZZ|PARKING|DECK)"
# What names a floor, most specific first. The name is the words matched,
# tidied: "PODIUM- 3" is "PODIUM-3".
_FLOOR_PHRASES = [
    # a typical run: "TYPICAL 3RD TO 16TH FLOOR", "TYP (1ST TO 14TH) FLOORS", "TYP 2-4 FLOOR"
    re.compile(rf"\bTYP(?:ICAL)?\.?\s*\(?\s*\d{{1,3}}\s*{_ORD}\s*(?:TO|-|–)\s*\d{{1,3}}\s*{_ORD}\s*\)?\s*FLOORS?\b", re.I),
    # Levels numbered with an L come before any other number in the title:
    # "L43 TO L50 RES 39 TO 46 FLOOR PLAN" is levels 43 to 50, "L54 - RES 50TH
    # FLOOR PLAN" level 54 -- the RES numbers are the flats'.
    re.compile(r"(?<![\w-])L\s?\d{1,3}\s*(?:TO|-|–)\s*L\s?\d{1,3}(?!\d)", re.I),        # L23 TO L40
    re.compile(r"(?<![\w-])L\s?\d{1,3}(?:\s*(?:&|,|AND)\s*L?\s?\d{1,3}(?!\d))+", re.I),  # L52 & 53
    re.compile(r"(?<![\w-])L\s?\d{1,3}(?![\w])", re.I),                                   # L41
    # an ordinal run without TYP: "3RD TO 16TH FLOOR"
    re.compile(r"\b\d{1,3}(?:ST|ND|RD|TH)\s*(?:TO|-|–)\s*\d{1,3}(?:ST|ND|RD|TH)\s*FLOORS?\b", re.I),
    # ordinal floors, one or a list: "11TH FLOOR", "14TH & 21ST FLOOR"
    re.compile(r"\b\d{1,3}(?:ST|ND|RD|TH)(?:\s*(?:&|,|AND)\s*\d{1,3}(?:ST|ND|RD|TH))*\s+FLOORS?\b", re.I),
    # a named level with its number: "PODIUM-3", "BASEMENT- 4", "LEVEL-01", "LEVEL 11"
    re.compile(rf"\b{_LEVEL_WORD}\s*[-–]?\s*\d{{1,3}}[A-Z]?(?![A-Z0-9])", re.I),
    # an ordinal named level: "3RD BASEMENT", "2ND PODIUM"
    re.compile(rf"\b\d{{1,3}}(?:ST|ND|RD|TH)\s+{_LEVEL_WORD}\b", re.I),
    re.compile(r"\b(?:LOWER\s+|UPPER\s+)?GROUND\s+FLOOR\b", re.I),
    re.compile(r"\b(?:LOWER\s+|UPPER\s+)?GROUND\b", re.I),
    re.compile(r"\b(?:TOP\s+|UPPER\s+|MAIN\s+|LOWER\s+)?ROOF\b", re.I),
    re.compile(r"\bFLOOR\s*[-–]?\s*\d{1,3}\b", re.I),
    re.compile(r"\b(?:MEZZANINE|MEZZ)\b", re.I),
]
# A level with a name of its own, in the words before FLOOR, LEVEL or SLAB,
# kept as written with its ordinal: "1ST MECHANICAL FLOOR", "1ST STRUCTURAL
# (NON ACCESSIBLE) FLOOR", "WALK IN LIFT PIT LEVEL", "TOP OF LIFT MACHINE
# FLOOR", "STRUCTURAL SLAB", "HC FLOOR".
_NAMED_FLOOR = re.compile(r"\b((?:\d{1,3}(?:ST|ND|RD|TH)\s+)?[A-Z][A-Z .&/()-]{0,40}?)\s*\b(FLOOR|LEVEL|SLAB)\b", re.I)


def _clean_title(title: str) -> str:
    t = re.sub(r"\.(dxf|dwg)$", "", title or "", flags=re.I).replace("_", " ")
    t = _DOC_NUMBER.sub(" ", t)
    t = _SHEET_NUMBER.sub("", t)
    return " ".join(t.split())


def _tidy(name: str) -> str:
    name = re.sub(r"\s*([-–])\s*", "-", " ".join(name.upper().split()))
    name = re.sub(r"\(\s*", "(", name)
    return re.sub(r"\s*\)", ")", name).strip(" -,")


def identify_floor(title: str) -> str | None:
    """The floor a drawing title names, or None when it names none:
    'PODIUM-3 FLOOR PLAN FIRE ALARM LAYOUT' -> 'PODIUM-3',
    'LEVEL-01 FLOOR PLAN' -> 'LEVEL-01', 'GROUND FLOOR PLAN' -> 'GROUND FLOOR',
    'BASEMENT-2 FLOOR PLAN' -> 'BASEMENT-2', 'ROOF FLOOR PLAN' -> 'ROOF',
    'TYPICAL 3RD TO 16TH FLOOR PLAN' -> 'TYPICAL 3RD TO 16TH FLOOR',
    '1ST MECHANICAL FLOOR PLAN' -> '1ST MECHANICAL FLOOR',
    'FIRE ALARM LAYOUT' -> None. A drawing or sheet number in the title is
    not read: 'FA 119 STRUCTURAL SLAB' is 'STRUCTURAL SLAB', never '119'."""
    t = _clean_title(title)
    if not t:
        return None
    for phrase in _FLOOR_PHRASES:
        m = phrase.search(t)
        if m:
            return _tidy(m.group(0))
    for m in _NAMED_FLOOR.finditer(t):
        words = " ".join(_NOT_FLOOR.sub(" ", m.group(1)).split())
        if words.strip(" -&/.,()"):
            return _tidy(f"{words} {m.group(2)}")
    return None


def floor_name(title: str) -> str | None:
    """The floor a sheet is of, as the BOQ lists it (`identify_floor`)."""
    return identify_floor(title)


NOT_IDENTIFIED = "Floor not identified"


def _is_single_span(title: str) -> bool:
    return len(_RANGE.findall(title.upper())) == 1


# The title block's label for the title, and the labels of the fields below it.
_TITLE_LABEL = re.compile(r"^\s*(?:DRAWING|DWG|SHEET)\s*(?:TITLE|NAME)\s*:?\s*$|^\s*TITLE\s*:?\s*$", re.I)
_FIELD_LABEL = re.compile(r":\s*$|^\s*(?:DWG|DRAWING|SHEET|PROJECT|JOB|REV(?:ISION)?|SCALE|DATE|DRAWN|CHECKED|"
                          r"APPROVED|CLIENT|CONSULTANT|CONTRACTOR)\b.{0,12}(?:NO\.?|NUMBER)?\s*:?\s*$", re.I)


@dataclass
class _Text:
    text: str
    x: float
    y: float
    h: float
    in_block: bool       # fixed text inside an inserted block (the title block's own words)


def _position(e) -> tuple[float, float]:
    """Where a text sits: its alignment point when it is aligned, else its insertion point."""
    if e.dxftype() == "TEXT" and (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)) and e.dxf.hasattr("align_point"):
        p = e.dxf.align_point
    else:
        p = e.dxf.insert
    return float(p.x), float(p.y)


def _texts(layout, doc) -> list[_Text]:
    out: list[_Text] = []
    for e in layout.query("TEXT MTEXT"):
        try:
            x, y = _position(e)
            h = float(e.dxf.get("height", 0) if e.dxftype() == "TEXT" else e.dxf.get("char_height", 0) or 0)
            out.append(_Text(G.plain_text(e), x, y, h, False))
        except Exception:
            continue
    for ins in layout.query("INSERT"):
        for a in getattr(ins, "attribs", []):
            try:
                out.append(_Text(" ".join(str(a.dxf.get("text", "")).split()), float(a.dxf.insert.x),
                                 float(a.dxf.insert.y), float(a.dxf.get("height", 0) or 0), False))
            except Exception:
                continue
        # The title block's own words, where they sit on the sheet. Only an
        # unrotated block: that is how title blocks are inserted.
        if doc is None or abs(float(ins.dxf.get("rotation", 0) or 0)) > 0.01:
            continue
        try:
            block = doc.blocks.get(ins.dxf.name)
            sx, sy = float(ins.dxf.get("xscale", 1) or 1), float(ins.dxf.get("yscale", 1) or 1)
            bx, by = ins.dxf.insert.x, ins.dxf.insert.y
            base = block.block.dxf.base_point if block is not None and block.block is not None else None
            ox, oy = (float(base.x), float(base.y)) if base is not None else (0.0, 0.0)
            for e in block.query("TEXT MTEXT") if block is not None else []:
                x, y = _position(e)
                h = float(e.dxf.get("height", 0) if e.dxftype() == "TEXT" else e.dxf.get("char_height", 0) or 0)
                out.append(_Text(G.plain_text(e), bx + (x - ox) * sx, by + (y - oy) * sy, h * abs(sy), True))
        except Exception:
            continue
    return [t for t in out if t.text]


def _labelled_title(texts: list[_Text]) -> list[str] | None:
    """The lines written under the title block's DRAWING TITLE label, top
    first, down to the next field's label -- or None when the sheet has no
    such label, or nothing under it."""
    for label in (t for t in texts if _TITLE_LABEL.match(t.text)):
        size = label.h or max((t.h for t in texts), default=1.0) or 1.0
        left, right = label.x - 5 * size, label.x + 80 * size
        below = [t.y for t in texts
                 if t is not label and t.y < label.y - 0.1 * size and left <= t.x <= right and _FIELD_LABEL.search(t.text)]
        floor_y = max(below) if below else label.y - 25 * size
        lines = [t for t in texts
                 if not t.in_block and t is not label and floor_y < t.y < label.y + 0.1 * size and left <= t.x <= right
                 and not _FIELD_LABEL.search(t.text) and len(t.text) <= 120]
        if lines:
            lines.sort(key=lambda t: (-t.y, t.x))
            return [t.text for t in lines]
    return None


def _title_of(layout, doc=None) -> tuple[str, str]:
    """(title, where it was read). The lines under the title block's DRAWING
    TITLE label first; where there is no such label, a title-block attribute
    that reads like a title, then the largest title-like text on the sheet
    that is not another drawing's number."""
    texts = _texts(layout, doc)
    labelled = _labelled_title(texts)
    if labelled:
        return " ".join(labelled), "drawing title"
    # 1. a title-block attribute that reads like a title
    for e in layout.query("INSERT"):
        for a in getattr(e, "attribs", []):
            s = " ".join(str(a.dxf.get("text", "")).split())
            if s and TITLE_WORDS.search(s) and not s.upper().startswith("SCALE"):
                return s, "title text"
    # 2. the largest title-like text on the sheet
    best = None
    for t in texts:
        s = t.text
        if t.in_block or not s or len(s) > 120 or _NOTE.match(s) or not TITLE_WORDS.search(s):
            continue
        key = (t.h, len(s))
        if best is None or key > best[0]:
            best = (key, s)
    if best:
        return best[1], "title text"
    return "", ""


def read_sheets(doc) -> list[Sheet]:
    sheets: list[Sheet] = []
    try:
        names = doc.layouts.names_in_taborder()
    except Exception:
        names = [l.name for l in doc.layouts]
    for name in names:
        if name.upper() == "MODEL":
            continue
        layout = doc.layouts.get(name)
        wins = []
        for v in layout.query("VIEWPORT"):
            try:
                cx, cy = v.dxf.view_center_point.x, v.dxf.view_center_point.y
                if abs(cx - v.dxf.center.x) < 1e-6 and abs(cy - v.dxf.center.y) < 1e-6:
                    continue  # the sheet's own paper-space viewport
                vh = float(v.dxf.view_height)
                vw = vh * (float(v.dxf.width) / float(v.dxf.height)) if v.dxf.height else vh
                wins.append(Window(cx, cy, vw, vh, math.radians(v.dxf.get("view_twist_angle", 0.0)),
                                   v.dxf.view_target_point.x, v.dxf.view_target_point.y))
            except Exception:
                continue
        if not wins:
            continue
        title, source = _title_of(layout, doc)
        kind = "diagram" if DIAGRAM.search(title) else "plan"
        floors = parse_floors(title) if kind == "plan" else []
        sheets.append(Sheet(name=name, title=title, kind=kind, floors=floors, multiplier=floor_count(title, floors),
                            windows=wins, title_source=source))
    _resolve_overlaps(sheets)
    return sheets


def refresh_floors(stored: list[dict]) -> list[dict]:
    """Stored sheets with their floors read again from their titles: the
    floors depend on the title alone, so a drawing read before a fix to the
    reading is counted by the reading in force, without reading it again."""
    sheets = []
    for s in stored:
        floors = parse_floors(s["title"]) if s.get("kind") == "plan" else []
        sheets.append(Sheet(name=s["name"], title=s["title"], kind=s.get("kind", "plan"), floors=floors,
                            multiplier=floor_count(s["title"], floors), title_source=s.get("title_source", "")))
    _resolve_overlaps(sheets)
    return [{**old, **new.to_dict()} for old, new in zip(stored, sheets)]


def _resolve_overlaps(sheets: list[Sheet]) -> None:
    """Two typical plans cannot both stand for the same floor. When a plan
    written as one span ('21ST TO 31ST FLOOR') overlaps a plan that lists
    its floors explicitly ('TYP 18-20,22-30'), the explicit list wins:
    FA-12 keeps floors 21 and 31."""
    plans = [s for s in sheets if s.kind == "plan" and s.floors]
    for s in plans:
        if not _is_single_span(s.title):
            continue
        taken = set()
        for o in plans:
            if o is not s and not _is_single_span(o.title):
                taken |= set(s.floors) & set(o.floors)
        if taken:
            kept = [f for f in s.floors if f not in taken]
            if kept:
                s.note = f"floors {_fmt(sorted(taken))} are on another sheet; this plan counts floors {_fmt(kept)}"
                s.floors = kept
                s.multiplier = len(kept)
    # anything still claimed twice is flagged, not guessed
    seen: dict[int, str] = {}
    for s in plans:
        for f in s.floors:
            if f in seen and seen[f] != s.name:
                s.note = (s.note + "; " if s.note else "") + f"floor {f} is also on {seen[f]}"
            seen.setdefault(f, s.name)


def _fmt(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev != start else f"{start}")
            start = prev = n
    return ", ".join(out)


def sheet_for(sheets: list[Sheet], x: float, y: float) -> str:
    for s in sheets:
        for w in s.windows:
            if w.contains(x, y):
                return s.name
    return OUTSIDE
