"""Sheets and floors: which floor each device is on, and how many floors
each floor plan stands for.

A full-building IFC set is one DWG with a paper-space sheet per floor
(FA-01 BASEMENT FLOOR PLAN ... FA-17 ROOF FLOOR PLAN) plus riser and
detail sheets. Each sheet's viewport shows one area of model space: a
device drawn in that area is on that floor. The sheet's title block names
the floor, and a typical-floor title says how many floors the plan stands
for ("TYP 2-10,12-16 FLOOR PLAN" is 14 floors).

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

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "kind": self.kind, "floors": self.floors,
                "multiplier": self.multiplier, "note": self.note}


OUTSIDE = "(outside sheets)"


def parse_floors(title: str) -> list[int]:
    """Floor numbers a title stands for: '2-10,12-16' -> 2..10 + 12..16,
    '1ST TO 14TH' -> 1..14, '11TH FLOOR' -> [11]. Named levels
    ('BASEMENT-02', '3RD BASEMENT', '2ND PODIUM', 'GROUND', 'ROOF') give [],
    i.e. one floor."""
    t = title.upper()
    floors: set[int] = set()
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
        if _NAMED_LEVEL.match(t, m.end()):
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


def floor_name(title: str) -> str:
    """The floor a title names, without the drawing words:
    'HC FLOOR FIRE ALARM LAYOUT' -> 'HC FLOOR',
    '11TH FLOOR PLAN LAYOUT' -> '11TH FLOOR',
    'TYP(1ST TO 14TH) FLOORS FIRE ALARM LAYOUT' -> 'TYP (1ST TO 14TH) FLOORS'."""
    t = re.sub(r"\.(dxf|dwg)$", "", title, flags=re.I)
    # a file name such as '257-Sheet - FA-105 - TYP(...) FLOORS ...': keep the part after the sheet number
    parts = [p.strip() for p in re.split(r"\s+-\s+", t) if p.strip()]
    if len(parts) > 1:
        t = parts[-1]
    t = t.replace("_", " ")
    t = re.sub(r"\(", " (", t)
    t = _NOT_FLOOR.sub(" ", t)
    t = " ".join(t.split()).strip(" -,")
    return t.upper() or title.upper()


def _is_single_span(title: str) -> bool:
    return len(_RANGE.findall(title.upper())) == 1


def _title_of(layout) -> str:
    # 1. a title-block attribute that reads like a title
    for e in layout.query("INSERT"):
        for a in getattr(e, "attribs", []):
            s = " ".join(str(a.dxf.get("text", "")).split())
            if s and TITLE_WORDS.search(s) and not s.upper().startswith("SCALE"):
                return s
    # 2. the largest title-like text on the sheet
    best = None
    for e in layout.query("TEXT MTEXT"):
        s = G.plain_text(e)
        if not s or len(s) > 120 or _NOTE.match(s) or not TITLE_WORDS.search(s):
            continue
        h = float(e.dxf.get("height", 0) if e.dxftype() == "TEXT" else e.dxf.get("char_height", 0) or 0)
        key = (h, len(s))
        if best is None or key > best[0]:
            best = (key, s)
    if best:
        return best[1]
    return " ".join(G.plain_text(e) for e in list(layout.query("TEXT MTEXT"))[:2]).strip() or layout.name


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
        title = _title_of(layout)
        kind = "diagram" if DIAGRAM.search(title) else "plan"
        floors = parse_floors(title) if kind == "plan" else []
        sheets.append(Sheet(name=name, title=title, kind=kind, floors=floors, multiplier=floor_count(title, floors), windows=wins))
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
                            multiplier=floor_count(s["title"], floors)))
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


def single_sheet_from_name(filename: str) -> Sheet:
    """No sheet shows any device: the drawing is one floor, named by file."""
    title = re.sub(r"\.(dxf|dwg)$", "", filename, flags=re.I)
    floors = parse_floors(title)
    return Sheet(name="Model", title=title, kind="plan", floors=floors, multiplier=floor_count(title, floors))
