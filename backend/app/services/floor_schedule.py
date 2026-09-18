r"""A floor-wise BOQ read off the schedule an engineer keeps in Excel.

The quantities are already written down, in the workbook the project is
run from, and the work is reading the sheet honestly rather than
recognising anything:

    a row per item      catalogue number, description, unit
    a column per floor  B1 | GF | 1 to 13 | 14 | ROOF
    a cell              how many of that item on that floor

Three things make it more than reading a grid.

**A typical column is not one floor.** A schedule does not write out
thirteen identical columns; it writes "1 to 13" once, and the platform
owes the engineer the thirteen. Every such column is expanded to a row
per floor (`app.services.floors.floor_of`).

**The quantity in a typical column is per floor.** "1 to 13" against a
quantity of 5 is five on each of thirteen floors, not five shared
between them -- that is what a typical floor means. It is a thirteenfold
difference, so it is never assumed silently: the workbook's own total
column is reconciled against it where there is one, and the reading that
closes is the one used (`reconcile`).

**A blank is not a zero.** An empty cell means the item is not on that
floor, and must not become a BOQ line of zero.
"""

from __future__ import annotations

import dataclasses
import re

from app.services.floors import floor_of
from app.services.symbol_taxonomy import device_in, family_of, system_of

# How far down the sheet to look for the header row.
HEADER_SEARCH_ROWS = 25

# What the columns that are not floors are called.
DESCRIPTION_RE = re.compile(
    r"\bDESCRIPTION\b|\bITEM\b|\bMATERIAL\b|\bPARTICULARS?\b|\bSPECIFICATION\b|"
    # What one real schedule heads the column with: the name the
    # drawing gives the device.
    r"\bDRG\.?\s*NAME\b|\bDRAWING\s*NAME\b|\bDEVICE\b|\bEQUIPMENT\b",
    re.IGNORECASE,
)
CATALOG_RE = re.compile(r"\bCAT(?:ALOG(?:UE)?)?\.?\s*(?:NO|NUMBER|#)?\b|\bPART\s*(?:NO|NUMBER)\b|\bMODEL\b|\bREF(?:ERENCE)?\b", re.IGNORECASE)
UNIT_RE = re.compile(r"^\s*(?:UNIT|UOM|U/M)\s*$", re.IGNORECASE)
TOTAL_RE = re.compile(r"\bTOTAL\b|\bGRAND\s*TOTAL\b|\bSUM\b|\bQTY\s*TOTAL\b", re.IGNORECASE)
SERIAL_RE = re.compile(r"^\s*(?:S\.?\s*NO|SR\.?\s*NO|SL\.?\s*NO|ITEM\s*NO|#|NO\.?)\s*$", re.IGNORECASE)
REMARKS_RE = re.compile(r"\bREMARKS?\b|\bNOTES?\b", re.IGNORECASE)
MANUFACTURER_RE = re.compile(r"\bMAKE\b|\bBRAND\b|\bMANUFACTURER\b|\bSUPPLIER\b", re.IGNORECASE)

# The words a floor column is headed by. A header that names no floor and
# matches none of the columns above is not read as a floor: a schedule
# carries price and weight columns too, and counting those as floors would
# invent quantities.
FLOOR_HEADER_RE = re.compile(
    r"\bBASEMENT\b|\bB\d\b|\bGROUND\b|\bG\.?F\b|\bMEZZANINE\b|\bPODIUM\b|\bP\d\b|\bROOF\b|\bR\.?F\b|"
    r"\bPENTHOUSE\b|\bLEVEL\b|\bFLOOR\b|\bLVL\b|\bTYPICAL\b|^\s*\d{1,3}\s*$|"
    r"\d{1,3}\s*(?:ST|ND|RD|TH)?\s*(?:TO|-|–|/|&)\s*\d{1,3}",
    re.IGNORECASE,
)

# The row a schedule states its own floor counts on ("No. of Floors": 1,
# 1, 14, 6 ...). It is not an item -- counted as one it adds a phantom
# device to every floor -- and it is the sheet checking the platform's
# own reading of "3rd to 16th floor" against its own arithmetic.
FLOOR_COUNT_ROW_RE = re.compile(r"^\s*(?:NO\.?|NUMBER)\s*OF\s*FLOORS?\s*$", re.IGNORECASE)

# A quantity that is text ("Lot", "-") is kept as written rather than
# rewritten as a number, but only a number can be added up.
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


# How near a whole number a quantity has to be to be one. A typical
# column read across its floors divides -- 5 over 13 -- and thirteen of
# those thirds come back as 5.000000000000001, which is 5.
_WHOLE = 1e-9


def tidy(value: float | None) -> float | int | None:
    """A quantity as it should be written: 8 rather than 8.0, and 5 rather
    than 5.000000000000001.

    Excel hands back every number as a float and a schedule counts whole
    devices, so a quantity that is whole is written as one. A quantity
    that genuinely is not -- a typical column's five spread over thirteen
    floors -- keeps its full precision rather than being rounded here:
    rounding each floor and then adding them up is what made a row of 18
    devices read 18.005. The page rounds for display; the arithmetic does
    not.
    """
    if value is None:
        return None
    number = float(value)
    nearest = round(number)
    if abs(number - nearest) <= _WHOLE * max(1.0, abs(number)):
        return int(nearest)
    return number


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).replace(",", "")
    return float(text) if _NUMBER_RE.match(text) else None


class Grid:
    """A worksheet read through its merged cells.

    A floor-wise schedule heads its typical columns with a merged banner
    -- "TYPICAL FLOOR 1-13" across four sub-columns, or a two-row header
    -- and reading the cells raw gives the banner once and blanks after
    it. Every cell inside a merged range reads as its top-left one.
    """

    def __init__(self, ws):
        self.ws = ws
        self.max_row = ws.max_row or 0
        self.max_col = ws.max_column or 0
        self._anchor: dict[tuple[int, int], tuple[int, int]] = {}
        for merged in ws.merged_cells.ranges:
            top, left = merged.min_row, merged.min_col
            for row in range(merged.min_row, merged.max_row + 1):
                for col in range(merged.min_col, merged.max_col + 1):
                    self._anchor[(row, col)] = (top, left)

    def raw(self, row: int, col: int):
        if row < 1 or col < 1 or row > self.max_row or col > self.max_col:
            return None
        value = self.ws.cell(row=row, column=col).value
        return value.strip() or None if isinstance(value, str) else value

    def value(self, row: int, col: int):
        """The cell's value, taken from the top-left of its merged range."""
        top, left = self._anchor.get((row, col), (row, col))
        return self.raw(top, left)


@dataclasses.dataclass
class Column:
    """One column of the schedule, and what it turned out to be."""

    index: int
    heading: str
    kind: str                      # "floor", "description", "catalog", "unit", "total", "other"
    floors: tuple[str, ...] = ()   # the floors a floor column stands for
    order: float = 0.0

    @property
    def typical(self) -> bool:
        return len(self.floors) > 1


@dataclasses.dataclass
class Item:
    """One line of the schedule: what it is, and how many on each floor."""

    description: str
    catalog_no: str | None = None
    unit: str | None = None
    manufacturer: str | None = None
    remarks: str | None = None
    # What the line is, in the platform's own vocabulary -- "Smoke
    # detector", "Manual call point" -- read from the wording the
    # engineer wrote (`app.services.symbol_taxonomy.device_in`). None
    # where it names no device the platform knows; never guessed at.
    device: str | None = None
    family: str | None = None
    # "FAS" or "ELS": a fire alarm job and an emergency lighting job are
    # two BOQs, so the schedule is shown as two. None where the line names
    # no device the platform knows -- it is shown under neither rather
    # than dropped.
    system: str | None = None
    # {floor: quantity}, already expanded -- a typical column has put its
    # quantity against each of its floors.
    per_floor: dict[str, float] = dataclasses.field(default_factory=dict)
    # What the sheet's own total column said, when it had one.
    stated_total: float | None = None
    row: int = 0

    @property
    def total(self) -> float:
        return sum(self.per_floor.values())

    def as_dict(self) -> dict:
        return {
            "description": self.description, "catalog_no": self.catalog_no, "unit": self.unit,
            "manufacturer": self.manufacturer, "remarks": self.remarks,
            "device": self.device, "family": self.family, "system": self.system,
            "per_floor": {floor: tidy(count) for floor, count in self.per_floor.items()},
            "total": tidy(self.total),
            "stated_total": tidy(self.stated_total), "row": self.row,
        }


# How a typical column's quantity was read.
PER_FLOOR, ACROSS = "per_floor", "across"


@dataclasses.dataclass
class Schedule:
    """What one workbook says: the floors, the items, and how it was read."""

    sheet: str = ""
    floors: list[str] = dataclasses.field(default_factory=list)
    items: list[Item] = dataclasses.field(default_factory=list)
    columns: list[Column] = dataclasses.field(default_factory=list)
    # "per_floor" or "across": what a typical column's quantity was taken
    # to mean, and why.
    typical_reading: str = PER_FLOOR
    typical_reason: str = ""
    warnings: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "floors": self.floors,
            "items": [item.as_dict() for item in self.items],
            "columns": [
                {"heading": column.heading, "kind": column.kind, "floors": list(column.floors),
                 "typical": column.typical}
                for column in self.columns
            ],
            "typical_reading": self.typical_reading,
            "typical_reason": self.typical_reason,
            "warnings": self.warnings,
            # The devices the schedule carries, each once, so the page can
            # group its rows the way the drawings tab groups its columns.
            "devices": sorted({item.device for item in self.items if item.device}),
            # What each system's rows come to, so nothing can be shown in
            # one tab and lost from the arithmetic. A line the platform
            # cannot name is counted under "" and shown all the same.
            "systems": {
                system or "": tidy(sum(item.total for item in self.items if (item.system or "") == (system or "")))
                for system in dict.fromkeys(item.system or "" for item in self.items)
            },
            "totals": {floor: tidy(sum(item.per_floor.get(floor, 0.0) for item in self.items))
                       for floor in self.floors},
            "grand_total": tidy(sum(item.total for item in self.items)),
            # What the sheet's own total column comes to, where it has one.
            # It includes any line whose floor cells could not be read, so
            # the two differ exactly by what could not be counted per floor.
            "stated_grand_total": tidy(
                sum(item.stated_total for item in self.items if item.stated_total is not None)
            ) if any(item.stated_total is not None for item in self.items) else None,
        }


def has_subheader(grid: Grid, row: int) -> bool:
    """Whether the header runs to a second row.

    A schedule often banners its floors -- "TYPICAL FLOOR" merged across
    several columns -- and names them one by one underneath. That second
    row is a header when it is text; when it holds numbers it is the first
    item of the schedule, and reading it as a heading would both lose the
    item and make nonsense of every column name ("B1 2").
    """
    below = [grid.raw(row + 1, col) for col in range(1, grid.max_col + 1)]
    texts = [value for value in below if isinstance(value, str) and value.strip()]
    numbers = [value for value in below if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return len(texts) >= 2 and not numbers


def _heading_at(grid: Grid, row: int, col: int, two_row: bool = False) -> str:
    """A column's heading.

    Where the header is two rows, the lower one is the column's own name
    ("1 to 13", "ROOF") and the upper one a banner over several columns
    ("TYPICAL FLOOR"); the particular name is the heading, and the banner
    stands in only where a column has no name of its own.
    """
    banner = _text(grid.value(row, col))
    if not two_row:
        return banner
    below = grid.value(row + 1, col)
    named = _text(below) if isinstance(below, str) else ""
    return named or banner


def _classify(heading: str) -> str:
    text = heading.strip()
    if not text:
        return "other"
    if SERIAL_RE.match(text):
        return "other"
    if UNIT_RE.match(text):
        return "unit"
    if TOTAL_RE.search(text):
        return "total"
    if DESCRIPTION_RE.search(text):
        return "description"
    if CATALOG_RE.search(text):
        return "catalog"
    if MANUFACTURER_RE.search(text):
        return "manufacturer"
    if REMARKS_RE.search(text):
        return "remarks"
    if FLOOR_HEADER_RE.search(text):
        return "floor"
    return "other"


def find_header(grid: Grid) -> int | None:
    """The header row: the first row near the top that names a description
    column and at least one floor."""
    best, best_score = None, 0
    for row in range(1, min(grid.max_row, HEADER_SEARCH_ROWS) + 1):
        kinds = _kinds(grid, row)
        floors = kinds.count("floor")
        described = "description" in kinds
        score = floors + (2 if described else 0)
        if floors >= 1 and described and score > best_score:
            best, best_score = row, score
    if best is not None:
        return best
    # A sheet that names no description column is still readable when it
    # heads its floors: the first text column is then the description.
    for row in range(1, min(grid.max_row, HEADER_SEARCH_ROWS) + 1):
        if _kinds(grid, row).count("floor") >= 2:
            return row
    return None


def _kinds(grid: Grid, row: int) -> list[str]:
    two_row = has_subheader(grid, row)
    return [_classify(_heading_at(grid, row, col, two_row)) for col in range(1, grid.max_col + 1)]


def read_columns(grid: Grid, header: int) -> list[Column]:
    """What each column of the sheet is."""
    two_row = has_subheader(grid, header)
    columns: list[Column] = []
    for index in range(1, grid.max_col + 1):
        heading = _heading_at(grid, header, index, two_row)
        kind = _classify(heading)
        floors: tuple[str, ...] = ()
        order = 0.0
        if kind == "floor":
            floor = floor_of(heading)
            floors = floor.covers or (floor.name,)
            order = floor.order
        columns.append(Column(index=index, heading=heading, kind=kind, floors=floors, order=order))

    _floors_between(columns)

    if not any(column.kind == "description" for column in columns):
        # No column said what it was. The description is the one carrying
        # the longest words -- not simply the first unclassified column,
        # which on a real schedule is the serial number and would make
        # every item read "A", "2", "3".
        first_floor = min((c.index for c in columns if c.kind == "floor"), default=grid.max_col + 1)
        best, best_length = None, 0
        for column in columns:
            if column.index >= first_floor or column.kind not in ("other", "unit"):
                continue
            length = _text_weight(grid, header, column.index)
            if length > best_length:
                best, best_length = column, length
        if best is not None:
            best.kind = "description"
    return columns


def _floors_between(columns: list[Column]) -> None:
    """A column sitting between two floor columns is a floor.

    A schedule's floor block is not all floor *words*: one real sheet runs
    "... Mech floor | Structural slab | 17th to 22nd floor ...", and a
    structural slab is a place devices are counted on however little it
    reads like a storey. Left out, its quantities are dropped silently --
    the one thing this reader must never do.
    """
    floors = [column.index for column in columns if column.kind == "floor"]
    if len(floors) < 2:
        return
    for column in columns:
        if column.kind != "other" or not (min(floors) < column.index < max(floors)):
            continue
        if not column.heading:
            continue
        column.kind = "floor"
        column.floors = (column.heading,)


def _text_weight(grid: Grid, header: int, index: int) -> int:
    """How wordy a column's values are, over the rows under the header --
    what tells a description column from a serial number."""
    total = 0
    for row in range(header + 1, min(grid.max_row, header + 40) + 1):
        value = grid.raw(row, index)
        if isinstance(value, str):
            total += len(value.strip())
    return total


def read(path, sheet_name: str | None = None) -> Schedule:
    """The floor-wise schedule a workbook holds."""
    import openpyxl

    result = Schedule()
    try:
        workbook = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- a file that is not a workbook, or a broken one
        result.warnings.append(f"The file could not be read as an Excel workbook ({exc}).")
        return result

    # The sheet asked for, else the first that reads as a schedule.
    sheets = [workbook[sheet_name]] if sheet_name and sheet_name in workbook.sheetnames else list(workbook.worksheets)
    chosen = header = None
    for worksheet in sheets:
        grid = Grid(worksheet)
        found = find_header(grid)
        if found is not None:
            chosen, header = grid, found
            result.sheet = worksheet.title
            break
    if chosen is None or header is None:
        result.warnings.append(
            "No floor-wise schedule was found in the workbook: a sheet with a description column and a "
            "column per floor (B1, GF, \"1 to 13\", ROOF) is expected."
        )
        return result

    grid = chosen
    columns = read_columns(grid, header)
    result.columns = columns
    floor_columns = [column for column in columns if column.kind == "floor"]
    if not floor_columns:
        result.warnings.append(f"The sheet {result.sheet!r} names no floor columns.")
        return result

    # The floors in the order the sheet sets them out, left to right: a
    # schedule is written deepest basement first and roof last, and that
    # order is the engineer's own. Reordering it by what the platform
    # makes of each name only ever loses to the sheet -- "3rd Basement"
    # and "1st Podium" are plain to a reader and awkward to a regular
    # expression. A range column takes its place where it stands.
    result.floors = []
    for column in sorted(floor_columns, key=lambda column: column.index):
        for name in column.floors:
            if name not in result.floors:
                result.floors.append(name)

    description_col = next((c.index for c in columns if c.kind == "description"), None)
    catalog_col = next((c.index for c in columns if c.kind == "catalog"), None)
    unit_col = next((c.index for c in columns if c.kind == "unit"), None)
    maker_col = next((c.index for c in columns if c.kind == "manufacturer"), None)
    remarks_col = next((c.index for c in columns if c.kind == "remarks"), None)
    total_col = next((c.index for c in columns if c.kind == "total"), None)

    # The header may run to a second row; the items start under it.
    start = header + (2 if has_subheader(grid, header) else 1)

    stated_floors: dict[int, float] = {}
    for row in range(start, grid.max_row + 1):
        description = _text(grid.raw(row, description_col)) if description_col else ""
        if FLOOR_COUNT_ROW_RE.match(description):
            # The sheet's own count of what each column stands for: not an
            # item, and a check on this reader's arithmetic.
            for column in floor_columns:
                count = _number(grid.raw(row, column.index))
                if count:
                    stated_floors[column.index] = count
            continue
        quantities: dict[str, float] = {}
        for column in floor_columns:
            value = _number(grid.raw(row, column.index))
            # A blank is not a zero: the item is simply not on that floor.
            if value is None or value == 0:
                continue
            for name in column.floors:
                quantities[name] = quantities.get(name, 0.0) + value
        if not description and not quantities:
            continue
        if not description:
            # A row with numbers and no description is a total row or a
            # spacer, not an item.
            continue
        stated = _number(grid.raw(row, total_col)) if total_col else None
        if not quantities and stated is None:
            continue
        # A spacer whose description cell holds a stray number and nothing
        # else is not an item: it would otherwise reach the page as a line
        # named "0" with no device and no quantity.
        if not quantities and _NUMBER_RE.match(description):
            continue
        # A row the sheet totals but whose floor cells cannot be read as
        # numbers -- one real schedule writes "7*2=14(Stair), 7*9=63(Lift)"
        # across a merged cell and totals it 77 -- is kept, with what the
        # sheet says it comes to. Dropping it would take a line out of the
        # BOQ silently, which is the one thing this reader must not do.
        # What the line is. The description is the wording an engineer
        # wrote, so it is read the same way a drawing's legend is; the
        # catalogue number is tried after it, since a part number carries
        # the device in it on most brands ("SIGA-PS" is a smoke detector).
        catalog = _text(grid.raw(row, catalog_col)) or None if catalog_col else None
        device = device_in(description) or device_in(catalog)
        result.items.append(Item(
            description=description,
            device=device,
            family=family_of(device) if device else None,
            system=system_of(device),
            catalog_no=catalog,
            unit=_text(grid.raw(row, unit_col)) or None if unit_col else None,
            manufacturer=_text(grid.raw(row, maker_col)) or None if maker_col else None,
            remarks=_text(grid.raw(row, remarks_col)) or None if remarks_col else None,
            per_floor=quantities,
            stated_total=stated,
            row=row,
        ))

    if not result.items:
        result.warnings.append(
            f"The sheet {result.sheet!r} has a header but no item rows under it were read."
        )
    for column in floor_columns:
        stated = stated_floors.get(column.index)
        if stated and int(stated) != len(column.floors):
            result.warnings.append(
                f"The sheet says {column.heading!r} is {int(stated)} floor"
                f"{'s' if int(stated) != 1 else ''}; it was read as {len(column.floors)}. "
                "The quantities are counted on the floors as read."
            )

    unread = [item for item in result.items if not item.per_floor and item.stated_total]
    if unread:
        result.warnings.append(
            f"{len(unread)} {'lines are' if len(unread) != 1 else 'line is'} totalled by the sheet but "
            f"{'their' if len(unread) != 1 else 'its'} floor cells could not be read as numbers "
            f"({', '.join(item.description[:30] for item in unread[:3])}"
            f"{', ...' if len(unread) > 3 else ''}). "
            f"{'They are' if len(unread) != 1 else 'It is'} listed with the total the sheet states and no "
            "quantity per floor."
        )

    unnamed = [item for item in result.items if not item.device]
    if unnamed:
        result.warnings.append(
            f"{len(unnamed)} {'lines name' if len(unnamed) != 1 else 'line names'} no device the platform knows "
            f"({', '.join(item.description[:30] for item in unnamed[:3])}"
            f"{', ...' if len(unnamed) > 3 else ''}). Their quantities are read all the same; only the "
            "device column is blank."
        )
    reconcile(result)
    return result


def recompute(result: dict) -> dict:
    """The stored schedule's own arithmetic, done again.

    An engineer changing one floor's quantity changes that item's total,
    that floor's total, the system's total and the grand total, and a
    footer that still showed the old ones would be worse than no footer.
    Everything derived is worked out again from `per_floor`, which is the
    only thing an edit touches.

    A fresh dict is returned rather than the one passed in: the stored
    column is JSON, and a value that compares equal to what is already
    there is not written at all.
    """
    items = [dict(item) for item in result.get("items", [])]
    for item in items:
        counts = {floor: float(count) for floor, count in (item.get("per_floor") or {}).items() if count}
        item["per_floor"] = {floor: tidy(count) for floor, count in counts.items()}
        item["total"] = tidy(sum(counts.values()))
    floors = list(result.get("floors", []))
    systems: dict[str, float] = {}
    for item in items:
        code = item.get("system") or ""
        systems[code] = systems.get(code, 0.0) + (item["total"] or 0)
    return {
        **result,
        "items": items,
        "totals": {floor: tidy(sum(float(item["per_floor"].get(floor) or 0) for item in items))
                   for floor in floors},
        "grand_total": tidy(sum(float(item["total"] or 0) for item in items)),
        "systems": {code: tidy(total) for code, total in systems.items()},
    }


def apply_edits(result: dict, edits: dict) -> dict:
    """Put an engineer's own quantities back over a fresh reading.

    The corrections are kept apart from what the sheet said
    (`ProjectFloorSchedule.edits`), keyed by the line's description rather
    than by its row: a workbook edited in Excel moves its rows about, and
    a correction that followed a row number would land on the wrong item.
    A correction whose line is no longer on the sheet is reported rather
    than dropped in silence.
    """
    if not edits:
        return result
    items = [dict(item) for item in result.get("items", [])]
    by_description = {item.get("description"): item for item in items}
    floors = set(result.get("floors", []))
    lost: list[str] = []
    for description, per_floor in edits.items():
        item = by_description.get(description)
        if item is None:
            lost.append(description)
            continue
        counts = dict(item.get("per_floor") or {})
        for floor, quantity in (per_floor or {}).items():
            if floor not in floors:
                continue
            if quantity:
                counts[floor] = tidy(float(quantity))
            else:
                counts.pop(floor, None)
        item["per_floor"] = counts
        item["edited"] = True
    result = {**result, "items": items}
    if lost:
        result = {**result, "warnings": list(result.get("warnings", [])) + [
            f"{len(lost)} line{'s that had' if len(lost) != 1 else ' that had'} a quantity set by hand "
            f"{'are' if len(lost) != 1 else 'is'} no longer on the sheet "
            f"({', '.join(name[:30] for name in lost[:3])}"
            f"{', ...' if len(lost) > 3 else ''}); {'those corrections' if len(lost) != 1 else 'that correction'} "
            "could not be applied."
        ]}
    return recompute(result)


def set_quantity(result: dict, *, row: int, floor: str, quantity: float | None) -> dict:
    """One cell of the schedule, changed by hand.

    A quantity of nothing puts the cell back to blank rather than to zero:
    a blank means the item is not on that floor, which is what the reader
    has been careful about from the start, and a zero would read as a BOQ
    line of none.
    """
    if floor not in result.get("floors", []):
        raise KeyError(f"{floor!r} is not a floor of this schedule")
    items = [dict(item) for item in result.get("items", [])]
    wanted = next((item for item in items if item.get("row") == row), None)
    if wanted is None:
        raise KeyError(f"no line at row {row}")
    per_floor = dict(wanted.get("per_floor") or {})
    if not quantity:
        per_floor.pop(floor, None)
    else:
        per_floor[floor] = tidy(max(0.0, float(quantity)))
    wanted["per_floor"] = per_floor
    # The line has been changed by hand, so it no longer claims to be what
    # the sheet totalled -- the reconciliation warning is then telling the
    # truth rather than nagging.
    wanted["edited"] = True
    return recompute({**result, "items": items})


def reconcile(schedule: Schedule) -> None:
    """Settle what a typical column's quantity meant, against the sheet's
    own total column.

    "1 to 13" against a quantity of 5 is thirteen times as many devices
    under one reading as the other, so it is checked rather than assumed.
    Where the sheet totals its own rows, whichever reading closes is the
    one used; where it does not, the per-floor reading stands -- that is
    what a typical floor column means -- and the page says so.
    """
    typical = [column for column in schedule.columns if column.kind == "floor" and column.typical]
    if not typical:
        schedule.typical_reading = PER_FLOOR
        schedule.typical_reason = "No column stands for more than one floor."
        return

    stated = [item for item in schedule.items if item.stated_total is not None]
    if not stated:
        schedule.typical_reading = PER_FLOOR
        schedule.typical_reason = (
            "A typical column's quantity is read as the quantity on each of its floors, which is what a "
            "typical floor is. The sheet totals nothing, so nothing confirms it -- check a floor against "
            "the schedule."
        )
        schedule.warnings.append(
            f"{len(typical)} column{'s' if len(typical) != 1 else ''} stand for a range of floors "
            f"({', '.join(column.heading for column in typical[:3])}"
            f"{', ...' if len(typical) > 3 else ''}). Their quantities are counted on every floor of the "
            "range. The sheet carries no total column to check that against."
        )
        return

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(0.5, abs(b) * 0.001)

    per_floor_ok = sum(1 for item in stated if close(item.total, item.stated_total))
    across_ok = 0
    for item in stated:
        # What the row would total if a typical column's quantity were
        # shared across its floors rather than repeated on each.
        shrunk = 0.0
        for column in schedule.columns:
            if column.kind != "floor":
                continue
            share = sum(item.per_floor.get(name, 0.0) for name in column.floors)
            shrunk += share / len(column.floors) if column.typical and column.floors else share
        if close(shrunk, item.stated_total):
            across_ok += 1

    if across_ok > per_floor_ok:
        schedule.typical_reading = ACROSS
        schedule.typical_reason = (
            f"The sheet's own total agrees on {across_ok} of {len(stated)} rows when a typical column's "
            "quantity is shared across its floors rather than counted on each, so it is read that way."
        )
        uneven: set[str] = set()
        for item in schedule.items:
            for column in schedule.columns:
                if column.kind == "floor" and column.typical and column.floors:
                    for name in column.floors:
                        if name in item.per_floor:
                            share = item.per_floor[name] / len(column.floors)
                            if abs(share - round(share)) > 1e-9:
                                uneven.add(column.heading)
                            item.per_floor[name] = share
        if uneven:
            schedule.warnings.append(
                f"{', '.join(sorted(uneven))} does not divide evenly over its floors, so some floors carry "
                "a fraction of a device. The totals are right; the per-floor figures are the sheet's own "
                "arithmetic spread out."
            )
        return

    schedule.typical_reading = PER_FLOOR
    schedule.typical_reason = (
        f"The sheet's own total agrees on {per_floor_ok} of {len(stated)} rows when a typical column's "
        "quantity is counted on each of its floors, so it is read that way."
    )
    off = [item for item in stated if not close(item.total, item.stated_total)]
    if off:
        schedule.warnings.append(
            f"{len(off)} row{'s' if len(off) != 1 else ''} do not add up to the total the sheet states "
            f"({', '.join(item.description[:30] for item in off[:3])}"
            f"{', ...' if len(off) > 3 else ''}). The floors are what was read; the total is what the sheet says."
        )
