r"""The devices a drawing shows, floor by floor.

A fire alarm layout is a CAD drawing: every device is a block reference
(an INSERT) dropped on the plan. Counting them per floor is what a BOQ
"floor wise" is, and it is arithmetic over the drawing -- no model, no
OCR, no guessing:

    DWG --> DXF (app.services.dwg_convert) --> this

Three things make it more than counting INSERTs:

**The legend is not the building.** Every layout carries a legend: one
copy of each symbol beside its description. Counted, it adds one phantom
device of every kind to every floor. The legend is found by its title
(LEGEND / SYMBOL / KEY) and everything in its column is left out.

**What is outside the plan is not installed.** Title blocks, notes,
revision clouds and the drawing frame carry symbols too. The plan's own
extent is taken from the architecture (the walls, grids and doors the
layout is drawn over) and a device outside it is left out.

**A floor is a region, not a file.** Sometimes each floor is its own
file, sometimes every floor sits side by side in one model space, and
sometimes one layout is issued for a range of floors ("TYPICAL 2ND TO
14TH"), which the schedule shows as a row per floor.

Nothing here reads the project's folder or writes to it: a drawing is
handed in, read, and reported.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path
from typing import Iterable

from app.services.device_symbols import (
    Classification,
    Geometry,
    LegendEntry,
    SymbolLibrary,
    classify,
    device_in,
    geometry_of,
    legend_entries,
)

# --- what a block is ---------------------------------------------------------------------

# (device, the block or layer names that mean it). The first match wins, so
# the more particular patterns come first. A block that matches none is
# reported under its own name rather than dropped: an unknown symbol on a
# fire alarm layout is a question for an engineer, not a silence.
DEVICE_RULES: tuple[tuple[str, str], ...] = (
    ("Duct smoke detector", r"DUCT|SIGA-?SD\b|SD-?T\d"),
    ("Multisensor detector", r"OSHD|MULTI[- ]?SENSOR|MULTISENSOR"),
    ("Smoke detector", r"\bSD\b|SMOKE|PHOTO|OSD|SIGA-?PS\b"),
    ("Heat detector", r"\bHD\b|HEAT|HRS\b|HRD\b|THERMAL"),
    ("Manual call point", r"MCP|CALL[- ]?POINT|PULL[- ]?STATION|BREAK[- ]?GLASS|\bBGU\b|SIGA-?278"),
    ("Speaker/strobe", r"SPEAKER.?STROBE|SPK.?STR|SS70|757-\d+A-SS"),
    ("Horn/strobe", r"HORN.?STROBE|HS\b|757-\d+A-T|G1AV|WSTIA"),
    ("Strobe", r"STROBE|BEACON|FLASHER|202-\d"),
    ("Speaker", r"SPEAKER|\bSPK\b|S186|G4S"),
    ("Sounder", r"SOUNDER|HORN|G1A|BELL"),
    ("Fire telephone", r"TELEPHONE|\bFT\b|6833|6830|TCS-\d"),
    ("Monitor/control module", r"MODULE|RELAY|SIGA-?C[CTR]|SIGA-?IO|SIGA-?UM|\bMM\b|\bCM\b"),
    ("Isolator", r"ISOLATOR|\bIB\b"),
    ("Door holder", r"DOOR[- ]?HOLD|MAGNET"),
    ("Control panel", r"FACP|CONTROL PANEL|\bEST4\b|PANEL"),
    ("Repeater panel", r"REPEATER|ANNUNCIATOR|\bANN\b"),
    ("Power supply", r"\bBPS\b|\bAPS\b|POWER SUPPLY|BOOSTER"),
    ("Emergency light", r"EMERGENCY LIGHT|\bEM\b|\bEL\b"),
    ("Exit light", r"EXIT"),
)

# A block that is drawing furniture rather than a device: the sheet's own
# parts, the architecture brought in as an external reference, and the
# anonymous blocks AutoCAD makes for dimensions and dynamic blocks
# ("*U29", "A$C0a1b2c3"), which every real layout carries.
FURNITURE_RE = re.compile(
    r"^\*|^A\$C|XREF|TITLE|FRAME|BORDER|LOGO|NORTH|SCALE|STAMP|SHEET|REVISION|CLOUD|TAG|GRID|LEVEL[- ]?MARK|SECTION|DETAIL|VIEWPORT",
    re.IGNORECASE,
)

# The legend's own title, and the words a legend column is described by.
LEGEND_TITLE_RE = re.compile(r"\b(LEGEND|SYMBOLS?|KEY\s*PLAN\s*LEGEND|SYMBOL\s*(?:LIST|TABLE)|GENERAL\s*NOTES?)\b", re.IGNORECASE)
# What is drawn on a sheet beside the plans and repeats their symbols: a
# riser diagram carries every device in the building, a key plan every
# floor of it, and a typical detail the same few devices again. Counting
# them counts the building twice.
DIAGRAM_TITLE_RE = re.compile(
    r"\b(RISER|SCHEMATIC|BLOCK\s*DIAGRAM|SINGLE\s*LINE|ONE\s*LINE|WIRING\s*DIAGRAM|"
    r"TYPICAL\s*DETAIL|TYPICAL\s*INSTALLATION|DETAIL\s*[A-Z0-9]?\b|KEY\s*PLAN|LOCATION\s*PLAN|"
    r"SITE\s*PLAN|MOUNTING\s*HEIGHT)\b",
    re.IGNORECASE,
)
# The layers the architecture is drawn on: the plan's own extent.
ARCHITECTURE_RE = re.compile(r"^(A[-_]|ARC|ARCH|WALL|DOOR|WIND|PART|COL|GRID|ROOM|FLOOR|SLAB|STAIR|XREF)", re.IGNORECASE)

FLOOR_WORDS = r"(?:BASEMENT|GROUND|MEZZANINE|PODIUM|TYPICAL|ROOF|LEVEL|FLOOR|LOWER|UPPER|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH)"
FLOOR_TEXT_RE = re.compile(rf"\b{FLOOR_WORDS}\b", re.IGNORECASE)

_ORDINALS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7,
    "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11, "TWELFTH": 12, "THIRTEENTH": 13,
    "FOURTEENTH": 14, "FIFTEENTH": 15,
}


def device_of(block: str, layer: str = "") -> str:
    """The device a block reference stands for, by its name and then its
    layer -- the weakest evidence there is, kept for the places that have
    nothing else. app.services.device_symbols.classify weighs it against
    the geometry, the legend and the attributes, which is what decides.
    """
    return device_in(block) or device_in(layer) or (block or layer or "Unnamed block").strip()


# --- floors -------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Floor:
    """A floor as a drawing names it, and where it sorts in a building."""

    name: str
    order: float
    # A layout issued for a range of floors ("TYPICAL 2ND TO 14TH") stands
    # for each of them; the schedule shows a row per floor.
    covers: tuple[str, ...] = ()

    @property
    def is_typical(self) -> bool:
        return len(self.covers) > 1


def _order_of(label: str) -> float:
    """Where a floor sits in a building: basements below the ground floor,
    the roof above everything."""
    text = label.upper()
    basement = re.search(r"\bB(?:ASEMENT)?\s*[- ]?(\d+)", text)
    if basement:
        return -int(basement.group(1))
    if re.search(r"\bBASEMENT\b|\bB\d?\b", text):
        return -1
    if re.search(r"\bROOF\b|\bRF\b|\bR\.?F\b", text):
        return 900
    if re.search(r"\bPENTHOUSE\b|\bPH\b", text):
        return 800
    if re.search(r"\bGROUND\b|\bGF\b|\bG\.?F\b|\bG\b", text):
        return 0
    if re.search(r"\bMEZZANINE\b|\bMF\b|\bMEZZ\b", text):
        return 0.5
    podium = re.search(r"\bP(?:ODIUM)?\s*[- ]?(\d+)", text)
    if podium:
        return 0.6 + int(podium.group(1)) / 100
    for word, value in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return float(value)
    number = re.search(r"\b(\d{1,3})(?:ST|ND|RD|TH)?\b", text)
    if number:
        return float(number.group(1))
    return 500.0   # named but unplaceable: between the last floor and the roof


def _range_of(label: str) -> list[str]:
    """The floors a typical layout covers: "TYPICAL 2ND TO 14TH FLOOR" and
    "FLOOR 1-14" are fourteen rows, "LEVEL 3" is one."""
    text = label.upper()
    span = re.search(r"(\d{1,3})\s*(?:ST|ND|RD|TH)?\s*(?:TO|-|–|/|&)\s*(\d{1,3})\s*(?:ST|ND|RD|TH)?", text)
    if span:
        low, high = int(span.group(1)), int(span.group(2))
        if 0 <= low < high <= 200:
            return [f"Level {n}" for n in range(low, high + 1)]
    return []


# A sheet title names every floor it carries: "GROUND, FIRST & ROOF FLOOR
# FIRE ALARM LAYOUT" is three plans on one sheet.
_FLOOR_TOKEN_RE = re.compile(
    rf"\b(BASEMENT\s*\d*|LOWER\s+GROUND|GROUND|MEZZANINE|PODIUM\s*\d*|ROOF|PENTHOUSE|"
    rf"(?:{'|'.join(_ORDINALS)})|\d{{1,3}}(?:ST|ND|RD|TH))\b",
    re.IGNORECASE,
)
_TITLE_WORDS_RE = re.compile(r"\b(FLOOR|LEVEL|PLAN|LAYOUT)\b", re.IGNORECASE)


def floor_names_in(title: str) -> list[str]:
    """The floors a sheet title lists, in the order it lists them. Empty
    when the title names none, or is not a sheet title at all."""
    if not title or not _TITLE_WORDS_RE.search(title):
        return []
    found: list[str] = []
    for match in _FLOOR_TOKEN_RE.finditer(title):
        name = re.sub(r"\s+", " ", match.group(1).strip()).title()
        if name.upper() in ("PLAN", "LAYOUT"):
            continue
        if name not in found:
            found.append(name)
    return found


def floor_of(label: str) -> Floor:
    """The floor a label names."""
    name = re.sub(r"\s+", " ", (label or "").strip()) or "Unnamed floor"
    if name == OTHER_AREA:
        return Floor(name=name, order=_AWAY_ORDER)      # after every floor of the building
    covers = tuple(_range_of(name))
    return Floor(name=name, order=_order_of(name), covers=covers)


# --- reading a drawing ----------------------------------------------------------------------


@dataclasses.dataclass
class Insert:
    block: str
    layer: str
    x: float
    y: float
    space: str
    # What the block reference itself carries: the device's address, and
    # on one real drawing the only thing saying an anonymous block is an
    # exit sign (TYP=EXIT).
    attributes: dict = dataclasses.field(default_factory=dict)
    rotation: float = 0.0

    @property
    def address(self) -> str:
        """The loop address written on the device, when it carries one."""
        for tag, value in self.attributes.items():
            if re.search(r"ADDR|LOOP|DEVICE|TAG|NO\b", str(tag), re.IGNORECASE) and str(value).strip():
                return str(value).strip()
        return ""


@dataclasses.dataclass
class Label:
    text: str
    x: float
    y: float
    space: str
    height: float = 0.0


@dataclasses.dataclass
class Box:
    left: float
    bottom: float
    right: float
    top: float

    def holds(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (self.left - margin) <= x <= (self.right + margin) and (self.bottom - margin) <= y <= (self.top + margin)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclasses.dataclass
class DrawingRead:
    """What one drawing file holds: its inserts, its texts, the extent of
    the architecture it is drawn over, and what could not be read."""

    inserts: list[Insert] = dataclasses.field(default_factory=list)
    labels: list[Label] = dataclasses.field(default_factory=list)
    architecture: dict[str, Box] = dataclasses.field(default_factory=dict)
    layouts: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    # The geometry of every block the drawing defines: what the symbol is
    # actually drawn as, which is the evidence that counts.
    geometries: dict[str, Geometry] = dataclasses.field(default_factory=dict)


def read_dxf(path: Path) -> DrawingRead:
    """Every block reference and every piece of text in a DXF, with the
    extent of the architectural geometry per space."""
    import ezdxf

    read = DrawingRead()
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:  # noqa: BLE001 -- a file that is not a DXF, or a broken one
        read.warnings.append(f"{path.name} could not be read as a DXF ({exc}).")
        return read

    for block in doc.blocks:
        name = str(block.name)
        if name.lower().startswith(("*model_space", "*paper_space")):
            continue
        read.geometries[name] = geometry_of(block)

    spaces = [("model", doc.modelspace())]
    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        spaces.append((layout.name, layout))
    read.layouts = [name for name, _ in spaces]

    for name, space in spaces:
        extent: list[float] = []
        for entity in space:
            kind = entity.dxftype()
            layer = str(getattr(entity.dxf, "layer", "") or "")
            if kind == "INSERT":
                point = entity.dxf.insert
                attributes = {}
                for attribute in getattr(entity, "attribs", ()) or ():
                    try:
                        attributes[str(attribute.dxf.tag)] = str(attribute.dxf.text)
                    except Exception:  # noqa: BLE001 -- an attribute that cannot be read is not evidence
                        continue
                read.inserts.append(Insert(block=str(entity.dxf.name), layer=layer, x=float(point.x), y=float(point.y),
                                           space=name, attributes=attributes,
                                           rotation=float(getattr(entity.dxf, "rotation", 0) or 0)))
            elif kind in ("TEXT", "MTEXT"):
                text = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text)
                point = entity.dxf.insert if kind == "MTEXT" else entity.dxf.insert
                read.labels.append(Label(text=text.strip(), x=float(point.x), y=float(point.y), space=name,
                                         height=float(getattr(entity.dxf, "height", 0) or 0)))
            elif ARCHITECTURE_RE.search(layer):
                for x, y in _points(entity):
                    extent.extend([x, y])
        if extent:
            xs, ys = extent[0::2], extent[1::2]
            read.architecture[name] = Box(min(xs), min(ys), max(xs), max(ys))
    return read


def _points(entity) -> Iterable[tuple[float, float]]:
    """The points an entity is drawn through, for the extent of the plan."""
    kind = entity.dxftype()
    try:
        if kind == "LINE":
            yield float(entity.dxf.start.x), float(entity.dxf.start.y)
            yield float(entity.dxf.end.x), float(entity.dxf.end.y)
        elif kind == "LWPOLYLINE":
            for point in entity.get_points("xy"):
                yield float(point[0]), float(point[1])
        elif kind == "POLYLINE":
            for vertex in entity.vertices:
                yield float(vertex.dxf.location.x), float(vertex.dxf.location.y)
        elif kind in ("CIRCLE", "ARC"):
            yield float(entity.dxf.center.x), float(entity.dxf.center.y)
    except Exception:  # noqa: BLE001 -- an entity whose geometry cannot be read adds no extent
        return


# --- what counts ------------------------------------------------------------------------------


def legend_box(read: DrawingRead, space: str, plan: "Box | None" = None) -> Box | None:
    """Where the legend is: its title, and the column of symbols running
    down from it. The column ends at the first real gap, so a symbol
    elsewhere on the sheet -- in the title block, in a note -- is not taken
    for part of it. None when the drawing has no legend.

    The limits are relative to the plan, because a drawing's units are its
    own: the same layout is metres on one job and millimetres on the next.
    """
    titles = [label for label in read.labels if label.space == space and LEGEND_TITLE_RE.search(label.text)]
    if not titles:
        return None
    title = titles[0]
    span = max(plan.width, plan.height) if plan is not None else _span(read, space)
    if span <= 0:
        return None
    width_limit = max(span * _LEGEND_WIDTH_SHARE, 1.0)
    gap_limit = max(span * _LEGEND_GAP_SHARE, 1.0)

    candidates = sorted(
        (
            insert
            for insert in read.inserts
            if insert.space == space and abs(insert.x - title.x) <= width_limit and insert.y <= title.y + gap_limit
        ),
        key=lambda insert: -insert.y,
    )
    column: list[Insert] = []
    last = title.y
    for insert in candidates:
        if last - insert.y > gap_limit:
            break
        column.append(insert)
        last = insert.y
    if not column:
        return None
    xs = [insert.x for insert in column] + [title.x]
    ys = [insert.y for insert in column] + [title.y]
    pad = max(span * 0.01, 1.0)
    return Box(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


# A legend is a column beside its title: its symbols sit within this share
# of the plan's size of the title, and no further apart than this.
_LEGEND_WIDTH_SHARE = 0.15
_LEGEND_GAP_SHARE = 0.12


def _span(read: DrawingRead, space: str) -> float:
    """How big the drawing is in its own units, from the devices on it."""
    points = [(insert.x, insert.y) for insert in read.inserts if insert.space == space]
    if len(points) < 2:
        return 0.0
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def plan_box(read: DrawingRead, space: str) -> Box | None:
    """The plan's own extent: the architecture the layout is drawn over,
    else the cluster the devices themselves make."""
    box = read.architecture.get(space)
    if box is not None and box.width > 0 and box.height > 0:
        return box
    points = [(insert.x, insert.y) for insert in read.inserts if insert.space == space]
    if len(points) < 8:
        return None
    xs = sorted(x for x, _ in points)
    ys = sorted(y for _, y in points)
    # The middle 90%: a symbol in the title block does not stretch the plan.
    def trimmed(values: list[float]) -> tuple[float, float]:
        cut = max(1, int(len(values) * 0.05))
        return values[cut], values[-cut - 1]

    left, right = trimmed(xs)
    bottom, top = trimmed(ys)
    if right <= left or top <= bottom:
        return None
    return Box(left, bottom, right, top)


OTHER_AREA = "Not on a named floor"
_AWAY_ORDER = 10_000.0


def _sheet_floors(read: DrawingRead) -> list[str]:
    """The floors this drawing's sheet title lists, from any space: the
    title block is usually on the layout, the plans in the model."""
    for label in sorted(read.labels, key=lambda label: -len(label.text)):
        named = floor_names_in(label.text)
        if len(named) > 1:
            return named
    for label in read.labels:
        named = floor_names_in(label.text)
        if named:
            return named
    return []


@dataclasses.dataclass
class FloorCount:
    floor: Floor
    devices: dict[str, int] = dataclasses.field(default_factory=dict)
    source: str = ""

    @property
    def total(self) -> int:
        return sum(self.devices.values())


@dataclasses.dataclass
class Extraction:
    """The schedule a set of drawings makes: a row per floor, a column per
    device, and what was left out on the way."""

    floors: list[FloorCount] = dataclasses.field(default_factory=list)
    devices: list[str] = dataclasses.field(default_factory=list)
    legend_excluded: int = 0
    outside_plan_excluded: int = 0
    furniture_excluded: int = 0
    # The riser diagram, the key plan, the typical details: the same
    # devices drawn again beside the plans.
    diagram_excluded: int = 0
    files: list[dict] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    # One row per symbol the drawings use -- what it was taken for, how
    # sure the platform is and on what evidence. A tower has thousands of
    # devices and a couple of dozen symbols; this is what an engineer
    # checks, not the devices.
    symbols: list[dict] = dataclasses.field(default_factory=list)
    # One row per device found: where it is, what it is, and how it was
    # recognised, so a quantity can be looked into.
    instances: list[dict] = dataclasses.field(default_factory=list)
    # What the drawings' legends taught, for the library to keep.
    learned: list[dict] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        rows = []
        for count in self.floors:
            rows.append({
                "floor": count.floor.name,
                "order": count.floor.order,
                "typical": count.floor.is_typical,
                "covers": list(count.floor.covers),
                "source": count.source,
                "devices": dict(count.devices),
                "total": count.total,
            })
        return {
            "floors": rows,
            "devices": self.devices,
            "legend_excluded": self.legend_excluded,
            "outside_plan_excluded": self.outside_plan_excluded,
            "furniture_excluded": self.furniture_excluded,
            "diagram_excluded": self.diagram_excluded,
            "files": self.files,
            "warnings": self.warnings,
            "symbols": self.symbols,
            "instances": self.instances,
            "learned": self.learned,
        }


def floor_labels(read: DrawingRead, space: str) -> list[Label]:
    """The floor names written on a space, biggest text first: a plan's
    title is set larger than the notes around it."""
    found = [
        label
        for label in read.labels
        # A plan is titled "GROUND FLOOR PLAN" or "LEVEL 3", not "ROOF":
        # a bare floor word is a room label, and every roof plan has one.
        if label.space == space and FLOOR_TEXT_RE.search(label.text) and _TITLE_WORDS_RE.search(label.text)
        and not LEGEND_TITLE_RE.search(label.text) and not DIAGRAM_TITLE_RE.search(label.text)
        and len(label.text) <= 60
    ]
    return sorted(found, key=lambda label: (-label.height, label.text))


def clusters(inserts: list[Insert]) -> list[list[Insert]]:
    """The separate plans a space holds, by where the devices are.

    A drawing that titles each plan says where its floors are; one that
    does not -- and the archive is full of them -- stacks the plans with a
    clear gap between them. Devices nearer to each other than three times
    the usual spacing are the same plan; a gap wider than that is the next
    one. Returned lowest plan first, which is how a building is drawn.
    """
    if len(inserts) < 2:
        return [list(inserts)] if inserts else []
    # The usual spacing: the median distance from a device to its nearest
    # neighbour, which is the drawing's own scale whatever its units.
    nearest: list[float] = []
    for index, insert in enumerate(inserts):
        best = min(
            (math.hypot(insert.x - other.x, insert.y - other.y) for position, other in enumerate(inserts) if position != index),
            default=0.0,
        )
        if best > 0:
            nearest.append(best)
    if not nearest:
        return [list(inserts)]
    nearest.sort()
    typical = nearest[len(nearest) // 2]
    reach = typical * _CLUSTER_REACH

    # Union-find over the devices within reach of each other.
    parent = list(range(len(inserts)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    order = sorted(range(len(inserts)), key=lambda index: (inserts[index].x, inserts[index].y))
    for position, index in enumerate(order):
        for other in order[position + 1:]:
            if inserts[other].x - inserts[index].x > reach:
                break
            if math.hypot(inserts[index].x - inserts[other].x, inserts[index].y - inserts[other].y) <= reach:
                left, right = root(index), root(other)
                if left != right:
                    parent[left] = right

    groups: dict[int, list[Insert]] = {}
    for index, insert in enumerate(inserts):
        groups.setdefault(root(index), []).append(insert)
    found = list(groups.values())
    found.sort(key=lambda group: (min(i.y for i in group), min(i.x for i in group)))
    return found


# How far apart two devices can be and still be the same plan, as a
# multiple of the drawing's usual device spacing.
_CLUSTER_REACH = 3.0
# A plan further from the others than this multiple of the usual gap
# between plans belongs to another drawing on the same sheet.
_AWAY_REACH = 3.0


def main_area(plans: list[list[Insert]]) -> tuple[list[list[Insert]], list[list[Insert]]]:
    """(the plans of this sheet, the plans drawn away from them).

    One model space often carries more than one drawing -- the fire alarm
    plans, and the emergency lighting layout eighty metres above them.
    The plans of a building are stacked at a regular distance; a plan
    further off than a few of those gaps is another drawing, and its
    devices are not on the floors this sheet names.
    """
    if len(plans) < 3:
        return plans, []
    ordered = sorted(plans, key=lambda plan: min(insert.y for insert in plan))
    tops = [max(insert.y for insert in plan) for plan in ordered]
    bottoms = [min(insert.y for insert in plan) for plan in ordered]
    gaps = [bottoms[index + 1] - tops[index] for index in range(len(ordered) - 1)]
    if not gaps:
        return plans, []
    typical = sorted(gaps)[len(gaps) // 2]
    if typical <= 0:
        return plans, []
    # Cut the run wherever the gap is far wider than the usual one, and
    # keep the stretch holding the most devices.
    runs: list[list[list[Insert]]] = [[ordered[0]]]
    for index, gap in enumerate(gaps, start=1):
        if gap > typical * _AWAY_REACH:
            runs.append([])
        runs[-1].append(ordered[index])
    runs = [run for run in runs if run]
    if len(runs) == 1:
        return plans, []
    runs.sort(key=lambda run: sum(len(plan) for plan in run), reverse=True)
    kept = runs[0]
    away = [plan for run in runs[1:] for plan in run]
    return kept, away


def diagram_labels(read: DrawingRead, space: str) -> list[Label]:
    """The titles of what is drawn beside the plans and repeats their
    symbols: the riser diagram, the key plan, the typical details."""
    return [
        label
        for label in read.labels
        if label.space == space and DIAGRAM_TITLE_RE.search(label.text) and len(label.text) <= 60
    ]


# How far beside a symbol its own tag is written, as a share of the usual
# spacing between devices: any further off and the text belongs to
# something else.
_BESIDE_REACH = 0.7


def nearby_text(inserts: list[Insert], labels: list[Label]) -> dict[str, str]:
    """What is written beside each kind of symbol on the plan.

    A drawing that names nothing else still tags its devices -- "SD-01",
    "HD 12", "MCP" -- and where most of a symbol's tags say the same
    thing, that is worth hearing. It is the weakest evidence there is, and
    is weighed as such.
    """
    if not inserts or not labels:
        return {}
    spacing = _spacing(inserts)
    if spacing <= 0:
        return {}
    reach = spacing * _BESIDE_REACH
    said: dict[str, dict[str, int]] = {}
    # A few instances of each symbol are enough to hear what it is called.
    seen: dict[str, int] = {}
    for insert in inserts:
        if seen.get(insert.block, 0) >= 30:
            continue
        seen[insert.block] = seen.get(insert.block, 0) + 1
        close = [
            label for label in labels
            if abs(label.x - insert.x) <= reach and abs(label.y - insert.y) <= reach
        ]
        for label in close:
            device = device_in(label.text)
            if device:
                counts = said.setdefault(insert.block, {})
                counts[label.text.strip()] = counts.get(label.text.strip(), 0) + 1
    return {
        block: max(counts, key=lambda text: (counts[text], text))
        for block, counts in said.items()
    }


def _spacing(inserts: list[Insert]) -> float:
    """The usual distance between devices: the drawing's own scale,
    whatever its units."""
    if len(inserts) < 2:
        return 0.0
    nearest: list[float] = []
    for index, insert in enumerate(inserts):
        best = min(
            (math.hypot(insert.x - other.x, insert.y - other.y)
             for position, other in enumerate(inserts) if position != index),
            default=0.0,
        )
        if best > 0:
            nearest.append(best)
    if not nearest:
        return 0.0
    nearest.sort()
    return nearest[len(nearest) // 2]


def assign(inserts: list[Insert], labels: list[Label]) -> dict[str, list[Insert]]:
    """Which floor each device belongs to: the floor whose title is nearest
    it. One title means one floor and everything on it."""
    if not labels:
        return {}
    if len(labels) == 1:
        return {labels[0].text: list(inserts)}
    groups: dict[str, list[Insert]] = {label.text: [] for label in labels}
    for insert in inserts:
        nearest = min(labels, key=lambda label: math.hypot(label.x - insert.x, label.y - insert.y))
        groups[nearest.text].append(insert)
    return groups


MAX_INSTANCES = 20_000


def extract(paths: list[Path], names: dict[Path, str] | None = None, library: SymbolLibrary | None = None) -> Extraction:
    """The floor-wise schedule of every drawing handed in.

    The symbols are recognised once each -- from their geometry, the
    drawing's own legend, what they carry and, last and least, what they
    are called -- and the devices are then counted by arithmetic
    (app.services.device_symbols).
    """
    result = Extraction()
    counted: dict[str, FloorCount] = {}
    devices: set[str] = set()
    library = library if library is not None else SymbolLibrary()
    symbols: dict[str, dict] = {}

    for path in paths:
        shown = (names or {}).get(path, path.name)
        read = read_dxf(path)
        result.warnings.extend(read.warnings)
        if not read.inserts and not read.warnings:
            result.warnings.append(f"{shown}: no block reference on the drawing, so no device could be counted.")
        file_floors: list[str] = []
        file_devices = 0

        for space in read.layouts:
            inserts = [i for i in read.inserts if i.space == space]
            if not inserts:
                continue

            # The plan first: it is what the legend's own size is measured
            # against, and it is read from the architecture, which the
            # legend does not touch.
            plan = plan_box(read, space)
            legend = legend_box(read, space, plan)

            # The legend is this drawing's own dictionary: symbol, then
            # what it is. It is read before anything is counted, and what
            # it teaches is kept for the next project.
            entries = legend_entries([i for i in read.inserts if i.space == space],
                                     [l for l in read.labels if l.space == space], legend, read.geometries)
            spoken = {entry.block.upper(): entry.description for entry in entries}
            for entry in entries:
                device = device_in(entry.description) or entry.description.title()
                library.learn(device, geometry=read.geometries.get(entry.block), block=entry.block)
                result.learned.append({"device": device, "block": entry.block, "fingerprint": entry.fingerprint,
                                       "shape": (read.geometries.get(entry.block) or Geometry("", "", {})).shape,
                                       "description": entry.description, "file": shown})

            # What is written beside the symbols on the plan: a tag like
            # "SD-01" is a weak word for what they are, and the only word
            # some drawings give.
            beside = nearby_text(inserts, [l for l in read.labels if l.space == space])

            # Each symbol is recognised once, however many times it is used.
            found: dict[str, Classification] = {}
            for insert in inserts:
                if insert.block in found:
                    continue
                found[insert.block] = classify(
                    block=insert.block, layer=insert.layer, geometry=read.geometries.get(insert.block),
                    legend=spoken, library=library, attributes=insert.attributes,
                    nearby=beside.get(insert.block),
                )

            # Drawing furniture: the sheet's own parts and the architecture
            # brought in as a reference. An anonymous block that any
            # evidence speaks for -- the exit signs of one real drawing are
            # called *U29 and say TYP=EXIT -- is a device, not furniture.
            # The test is whether something named it, not how sure that
            # left the platform: a device recognised from one weak source
            # is counted, and shown for review.
            furniture = [
                insert for insert in inserts
                if FURNITURE_RE.search(insert.block) and not found[insert.block].evidence
            ]
            result.furniture_excluded += len(furniture)
            inserts = [i for i in inserts if i not in furniture]

            if legend is not None:
                inside = [i for i in inserts if legend.holds(i.x, i.y)]
                result.legend_excluded += len(inside)
                inserts = [i for i in inserts if i not in inside]

            if plan is not None:
                margin = max(plan.width, plan.height) * 0.02
                outside = [i for i in inserts if not plan.holds(i.x, i.y, margin)]
                result.outside_plan_excluded += len(outside)
                inserts = [i for i in inserts if i not in outside]

            labels = floor_labels(read, space)
            if labels:
                # The riser diagram and the key plan are titled like a plan
                # and are not one: the devices nearest their titles belong
                # to them, and are left out rather than counted again.
                diagrams = diagram_labels(read, space)
                grouped = assign(inserts, labels + diagrams)
                repeated = [
                    insert
                    for label in diagrams
                    for insert in grouped.get(label.text, [])
                ]
                if repeated:
                    result.diagram_excluded += len(repeated)
                    result.warnings.append(
                        f"{shown}: {len(repeated)} symbol{'s' if len(repeated) != 1 else ''} are drawn on the "
                        f"{', '.join(sorted({label.text.strip() for label in diagrams}))}; they are the same devices "
                        "shown again, and are not counted."
                    )
                groups = {label.text: grouped.get(label.text, []) for label in labels}
            else:
                # No plan is titled: the plans are where the devices are,
                # and the sheet title says which floors they are.
                named = _sheet_floors(read)
                plans = clusters(inserts)
                # A drawing sharing the sheet -- the lighting layout beside
                # the fire alarm plans -- is not one of this sheet's floors.
                plans, elsewhere = main_area(plans)
                if named and len(plans) > len(named):
                    # More plans than the title names: the biggest are the
                    # floors, the rest a detail or a key plan.
                    biggest = sorted(plans, key=len, reverse=True)[: len(named)]
                    elsewhere += [plan for plan in plans if plan not in biggest]
                    plans = [plan for plan in plans if plan in biggest]
                if named and len(plans) == len(named):
                    groups = {name: plan for name, plan in zip(named, plans)}
                    if elsewhere:
                        away: list[Insert] = [insert for plan in elsewhere for insert in plan]
                        groups[OTHER_AREA] = away
                        result.warnings.append(
                            f"{shown}: {len(away)} device{'s' if len(away) != 1 else ''} are drawn away from the "
                            f"{len(named)} plan{'s' if len(named) != 1 else ''} the title names "
                            f"({', '.join(named)}); they are listed under \"{OTHER_AREA}\"."
                        )
                elif len(plans) + len(elsewhere) > 1:
                    plans = plans + elsewhere
                    groups = {f"Plan {number}": plan for number, plan in enumerate(plans, start=1)}
                    result.warnings.append(
                        f"{shown}: {len(plans)} plans are drawn on the sheet and none is titled; they are listed as "
                        "Plan 1 upwards, lowest first. Name them on the drawing, or hand in one file per floor."
                    )
                else:
                    # One plan: the file name is the floor ("GF.dxf").
                    groups = {Path(shown).stem: inserts}

            for name, group in groups.items():
                if not group:
                    continue
                floor = floor_of(name)
                for member in (floor.covers or (floor.name,)):
                    key = member.upper()
                    count = counted.get(key)
                    if count is None:
                        count = counted[key] = FloorCount(
                            floor=floor if not floor.covers else Floor(name=member, order=_order_of(member), covers=floor.covers),
                            source=shown,
                        )
                    for insert in group:
                        recognised = found.get(insert.block) or classify(block=insert.block, layer=insert.layer)
                        device = recognised.device
                        count.devices[device] = count.devices.get(device, 0) + 1
                        devices.add(device)
                        entry = symbols.setdefault(f"{shown}:{insert.block}", {
                            "file": shown, "block": insert.block, "layer": insert.layer,
                            "fingerprint": (read.geometries.get(insert.block) or Geometry("", "", {})).fingerprint,
                            "shape": (read.geometries.get(insert.block) or Geometry("", "", {})).shape,
                            "features": (read.geometries.get(insert.block) or Geometry("", "", {})).features,
                            "instances": 0, "floors": [], **recognised.as_dict(),
                        })
                        entry["instances"] += 1
                        if member not in entry["floors"]:
                            entry["floors"].append(member)
                        if len(result.instances) < MAX_INSTANCES:
                            result.instances.append({
                                "floor": member, "device": device, "block": insert.block,
                                "address": insert.address, "x": round(insert.x, 1), "y": round(insert.y, 1),
                                "confidence": round(recognised.confidence, 1), "method": recognised.method,
                                "file": shown,
                            })
                    file_devices += len(group)
                    if member not in file_floors:
                        file_floors.append(member)

        result.files.append({"file": shown, "floors": file_floors, "devices": file_devices, "layouts": read.layouts})

    result.floors = sorted(counted.values(), key=lambda count: (count.floor.order, count.floor.name))
    result.devices = sorted(devices)
    result.symbols = sorted(symbols.values(), key=lambda symbol: (-symbol["instances"], symbol["block"]))
    for symbol in result.symbols:
        if symbol["conflict"]:
            result.warnings.append(f"{symbol['file']}: {symbol['conflict']}")
    unsure = [symbol for symbol in result.symbols if symbol["state"] in ("review", "unresolved")]
    if unsure:
        result.warnings.append(
            f"{len(unsure)} symbol{'s' if len(unsure) != 1 else ''} could not be recognised with confidence "
            f"({', '.join(symbol['block'] for symbol in unsure[:6])}"
            f"{', ...' if len(unsure) > 6 else ''}): check them against the drawing's legend."
        )
    return result
