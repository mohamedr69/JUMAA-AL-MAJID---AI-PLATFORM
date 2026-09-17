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

# A block that is drawing furniture rather than a device.
FURNITURE_RE = re.compile(
    r"TITLE|FRAME|BORDER|LOGO|NORTH|SCALE|STAMP|SHEET|REVISION|CLOUD|TAG|GRID|LEVEL[- ]?MARK|SECTION|DETAIL|VIEWPORT|A\$C",
    re.IGNORECASE,
)

# The legend's own title, and the words a legend column is described by.
LEGEND_TITLE_RE = re.compile(r"\b(LEGEND|SYMBOLS?|KEY\s*PLAN\s*LEGEND|SYMBOL\s*(?:LIST|TABLE)|GENERAL\s*NOTES?)\b", re.IGNORECASE)
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
    """The device a block reference stands for, by its block name and then
    its layer; the block's own name when nothing matches."""
    for text in (block or "", layer or ""):
        for device, pattern in DEVICE_RULES:
            if re.search(pattern, text, re.IGNORECASE):
                return device
    return (block or layer or "Unnamed block").strip()


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


def floor_of(label: str) -> Floor:
    """The floor a label names."""
    name = re.sub(r"\s+", " ", (label or "").strip()) or "Unnamed floor"
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
                read.inserts.append(Insert(block=str(entity.dxf.name), layer=layer, x=float(point.x), y=float(point.y), space=name))
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
    files: list[dict] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

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
            "files": self.files,
            "warnings": self.warnings,
        }


def floor_labels(read: DrawingRead, space: str) -> list[Label]:
    """The floor names written on a space, biggest text first: a plan's
    title is set larger than the notes around it."""
    found = [
        label
        for label in read.labels
        if label.space == space and FLOOR_TEXT_RE.search(label.text) and not LEGEND_TITLE_RE.search(label.text)
        and len(label.text) <= 60
    ]
    return sorted(found, key=lambda label: (-label.height, label.text))


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


def extract(paths: list[Path], names: dict[Path, str] | None = None) -> Extraction:
    """The floor-wise schedule of every drawing handed in."""
    result = Extraction()
    counted: dict[str, FloorCount] = {}
    devices: set[str] = set()

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
            furniture = [i for i in inserts if FURNITURE_RE.search(i.block)]
            result.furniture_excluded += len(furniture)
            inserts = [i for i in inserts if i not in furniture]

            # The plan first: it is what the legend's own size is measured
            # against, and it is read from the architecture, which the
            # legend does not touch.
            plan = plan_box(read, space)
            legend = legend_box(read, space, plan)
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
            if not labels:
                # The file name is the floor when the drawing does not say
                # ("GF-FF & RF.dwg", "Level 03 FA layout.dxf").
                labels = [Label(text=Path(shown).stem, x=0.0, y=0.0, space=space)]
            groups = assign(inserts, labels)

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
                        device = device_of(insert.block, insert.layer)
                        count.devices[device] = count.devices.get(device, 0) + 1
                        devices.add(device)
                    file_devices += len(group)
                    if member not in file_floors:
                        file_floors.append(member)

        result.files.append({"file": shown, "floors": file_floors, "devices": file_devices, "layouts": read.layouts})

    result.floors = sorted(counted.values(), key=lambda count: (count.floor.order, count.floor.name))
    result.devices = sorted(devices)
    return result
