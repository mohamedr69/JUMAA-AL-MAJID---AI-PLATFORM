"""Read an engineer's Voice Evacuation amplifier calculation workbook into a
zone schedule.

There is no template. Across the archive the sheets agree on the idea -- one
row per floor or area, a column of speaker counts per speaker type, a tap
wattage for each type, the watts per row, and the engineer's grouping of
rows onto amplifiers drawn as merged cells down the right-hand side -- but
not on where any of it sits. So columns are found by what their heading
says, not by position:

- "Watts per Area" / "Watts per Floor" / "Load in watt" marks the header
  row and the zone-watts column;
- headings naming a speaker, flasher, horn or sounder between the zone names
  and that column are the speaker types;
- the tap wattages come from a "Tapping Wattage" row under the headings, or
  failing that from the heading itself ("Ceiling Speaker (1/2 Watt)");
- "Required Watts" / "Total Wattage" starts a new channel wherever it has a
  value; "Proposed Amplifier" gives that channel's rating; "Rack" / "APS"
  groups channels into racks.

One sheet in the archive (EP-29495) has no headings at all, only a row of
speaker models with their taps underneath; that layout is recognised
separately, and its zone-watts column is accepted only if it agrees with the
counts it sits next to.

The workbook's own computed figures (watts per row, required watts) are read
too, but only to point out where the sheet disagrees with its own counts --
the calculation always works from the counts.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas_design import Channel, Rack, SpeakerType, Zone

HEADER_SEARCH_ROWS = 20
# How far under the headings a "Tapping Wattage" row or a row of speaker
# models may sit.
TAP_ROW_SEARCH = 8

ZONE_WATTS_RE = re.compile(r"watts?\s*per|load\s*in\s*watt|per\s*(floor|area)")
# "AMP W" is EP-23315's: the watts each amplifier carries, on its first row.
REQUIRED_RE = re.compile(r"required|total\s*watt|^amp\.?\s*w(atts?)?$")
AMPLIFIER_RE = re.compile(r"proposed\s*amp")
RACK_RE = re.compile(r"\brack|\baps\b|panel")
LABEL_RE = re.compile(r"amplifier\s*name|amp\s*name|\bdevice|^amp(lifier)?s?$")
NAME_RE = re.compile(r"floor|level|area|zone|location")
SPEAKER_RE = re.compile(r"speaker|flasher|horn|sounder")
SPARE_RE = re.compile(r"spare")
TAP_LABEL_RE = re.compile(r"tap")
# "Grand Total Speakers", "TOTAL W": the sums under the schedule, not floors.
TOTAL_ROW_RE = re.compile(r"\btotal\b")

_NUMBER = r"(\d*\.\d+|\d+)"
_WATTS_IN_TEXT_RE = re.compile(_NUMBER + r"\s*(?:w\b|watt)", re.IGNORECASE)
# "2 x 50 W": two amplifiers on the one group.
_MULTIPLE_WATTS_RE = re.compile(r"(\d+)\s*[x×*]\s*" + _NUMBER + r"\s*(?:w\b|watt)", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^\s*" + _NUMBER + r"\s*$")
_TAP_IN_HEADING_RE = re.compile(r"(\d+\s*/\s*\d+|" + _NUMBER[1:-1] + r")\s*(?:w\b|watt)", re.IGNORECASE)

# A headerless sheet's zone-watts column must agree with counts x taps on at
# least this share of rows before it is believed.
MIN_WATTS_AGREEMENT = 0.8
MAX_PLAUSIBLE_TAP_WATTS = 10


class WorkbookReadError(ValueError):
    """The sheet is not a schedule this reader recognises."""


@dataclass
class WorkbookRead:
    sheet: str
    sheets: list[str]
    speaker_types: list[SpeakerType]
    zones: list[Zone]
    channels: list[Channel]
    racks: list[Rack]
    warnings: list[str] = field(default_factory=list)


# --- cell helpers --------------------------------------------------------------


class _Grid:
    """The sheet's values, with merged ranges resolved to their top-left cell
    so a merged block can be read as the run it draws.

    `formulas` is the same sheet loaded with its formulas rather than their
    cached results. A cell whose cached result is empty but which holds a
    formula was never calculated -- the workbook was saved by something that
    writes formulas without computing them -- and reading it as blank would
    count its speakers as none. Those cells are collected, never read as 0."""

    def __init__(self, ws: Worksheet, formulas: Worksheet | None = None):
        self.ws = ws
        self.formulas = formulas
        self.uncalculated: set[tuple[int, int]] = set()
        self.max_row = ws.max_row
        self.max_col = ws.max_column
        self._anchor: dict[tuple[int, int], tuple[int, int]] = {}
        self._bottom: dict[tuple[int, int], int] = {}
        for merged in ws.merged_cells.ranges:
            top, left = merged.min_row, merged.min_col
            self._bottom[(top, left)] = merged.max_row
            for r in range(merged.min_row, merged.max_row + 1):
                for c in range(merged.min_col, merged.max_col + 1):
                    self._anchor[(r, c)] = (top, left)

    def value(self, row: int, col: int):
        """The cell's own value -- None inside a merged range except at its
        top-left, which is what makes a merged block start a run once."""
        value = self.ws.cell(row=row, column=col).value
        if value is None and self.formulas is not None:
            formula = self.formulas.cell(row=row, column=col).value
            if isinstance(formula, str) and formula.startswith("="):
                self.uncalculated.add((row, col))
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    def is_uncalculated(self, row: int, col: int) -> bool:
        self.value(row, col)
        return (row, col) in self.uncalculated

    def anchor(self, row: int, col: int) -> tuple[int, int]:
        return self._anchor.get((row, col), (row, col))

    def bottom(self, row: int, col: int) -> int:
        top, left = self.anchor(row, col)
        return self._bottom.get((top, left), row)


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower() if value is not None else ""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_watts(value) -> float | None:
    """50, "50 W", "250WATTS", "1 No 250 Watt Amplifier Upto 11th Floor",
    "2 x 50 W" (100)."""
    if _is_number(value):
        return float(value)
    if not isinstance(value, str):
        return None
    if multiple := _MULTIPLE_WATTS_RE.search(value):
        return int(multiple.group(1)) * float(multiple.group(2))
    match = _WATTS_IN_TEXT_RE.search(value) or _BARE_NUMBER_RE.match(value)
    return float(match.group(1)) if match else None


def tap_from_heading(heading: str) -> float | None:
    """"Ceiling Speaker Qty (1/2 Watt)" -> 0.5, "(Corridor-0.75W)" -> 0.75."""
    match = _TAP_IN_HEADING_RE.search(heading)
    if not match:
        return None
    number = match.group(1)
    if "/" in number:
        numerator, denominator = (float(p) for p in number.split("/"))
        return numerator / denominator if denominator else None
    return float(number)


def _slug(name: str, taken: set[str]) -> str:
    base = "_".join(re.findall(r"[a-z0-9]+", name.lower()))[:40] or "speaker"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _display(value) -> str:
    if _is_number(value) and float(value).is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


# --- layout ----------------------------------------------------------------------


@dataclass
class _Layout:
    data_start: int
    name_col: int
    speaker_cols: list[int]
    speaker_names: dict[int, str]
    speaker_models: dict[int, str]
    taps: dict[int, float]
    zone_watts_col: int | None
    required_col: int | None = None
    required_is_plain: bool = True
    amplifier_col: int | None = None
    label_col: int | None = None
    rack_col: int | None = None
    rack_location_col: int | None = None
    channel_start_col: int | None = None


def _is_grouping_heading(heading: str) -> bool:
    return any(
        pattern.search(heading) for pattern in (ZONE_WATTS_RE, REQUIRED_RE, AMPLIFIER_RE, RACK_RE, LABEL_RE)
    )


def _is_speaker_heading(heading: str) -> bool:
    """"Ceiling Speaker", or an abbreviation carrying its tap ("WS-(.5W)")."""
    return not _is_grouping_heading(heading) and bool(
        SPEAKER_RE.search(heading) or tap_from_heading(heading) is not None
    )


def _find_headed_layout(grid: _Grid, warnings: list[str]) -> _Layout | None:
    """The header row is the first with a watts-per-area heading, or failing
    that with at least two speaker headings (EP-23315 has no watts-per-area
    column, only the amplifier totals)."""
    header_row = zone_watts_col = None
    for r in range(1, min(grid.max_row, HEADER_SEARCH_ROWS) + 1):
        row = {c: _text(grid.value(r, c)) for c in range(1, grid.max_col + 1)}
        zone_watts = next((c for c, h in row.items() if ZONE_WATTS_RE.search(h)), None)
        if zone_watts or sum(_is_speaker_heading(h) for h in row.values()) >= 2:
            header_row, zone_watts_col = r, zone_watts
            break
    if header_row is None:
        return None

    headings = {c: _text(grid.value(header_row, c)) for c in range(1, grid.max_col + 1)}
    # Speakers sit left of the watts-per-area column; everything right of it
    # (or of the last speaker, where there is none) groups rows onto amplifiers.
    boundary = zone_watts_col or max(c for c, h in headings.items() if _is_speaker_heading(h))
    right = [c for c in headings if c > boundary and headings[c]]

    required = [c for c in right if REQUIRED_RE.search(headings[c])]
    plain_required = [c for c in required if not SPARE_RE.search(headings[c])]
    required_col = (plain_required or required or [None])[0]
    required_is_plain = bool(plain_required)
    if required_col and not required_is_plain:
        warnings.append(
            f"The sheet's required watts include spare capacity (\"{_display(grid.value(header_row, required_col))}\"), "
            "so they are not compared with the calculated load."
        )
    amplifier_col = next((c for c in right if AMPLIFIER_RE.search(headings[c])), None)
    rack_col = next((c for c in right if RACK_RE.search(headings[c])), None)
    label_col = next(
        (c for c in headings if c not in (rack_col, amplifier_col) and LABEL_RE.search(headings[c])),
        None,
    )

    left = [c for c in headings if c < boundary or (c == boundary and not zone_watts_col)]
    header_bottom = max(grid.bottom(header_row, c) for c in range(1, grid.max_col + 1))

    # A "Tapping Wattage" row, with any row of speaker models above it.
    tap_row = None
    for r in range(header_row + 1, min(grid.max_row, header_row + TAP_ROW_SEARCH) + 1):
        if any(TAP_LABEL_RE.search(_text(grid.value(r, c))) for c in left):
            tap_row = r
            break

    speaker_cols = [
        c
        for c in left
        if c != label_col
        and not _is_grouping_heading(headings[c])
        and (
            _is_speaker_heading(headings[c])
            or (tap_row is not None and _is_number(grid.value(tap_row, c)))
        )
    ]
    if not speaker_cols:
        raise WorkbookReadError("found the watts column but no speaker columns beside it")

    name_col = next(
        (c for c in left if c < speaker_cols[0] and c != label_col and NAME_RE.search(headings[c])),
        None,
    )

    taps: dict[int, float] = {}
    models: dict[int, str] = {}
    for c in speaker_cols:
        if tap_row is not None and _is_number(grid.value(tap_row, c)):
            taps[c] = float(grid.value(tap_row, c))
        elif (tap := tap_from_heading(headings[c])) is not None:
            taps[c] = tap
        if tap_row is not None:
            for r in range(header_bottom + 1, tap_row):
                model = grid.value(r, c)
                if isinstance(model, str):
                    models[c] = _display(model)
                    break

    data_start = (tap_row if tap_row is not None else header_bottom) + 1
    if name_col is None:
        name_col = _most_varied_column(grid, [c for c in left if c < speaker_cols[0] and c != label_col], data_start)
    if name_col is None:
        raise WorkbookReadError("Could not find the column of floor / area names")

    return _Layout(
        data_start=data_start,
        name_col=name_col,
        speaker_cols=speaker_cols,
        speaker_names={c: _display(grid.value(header_row, c)) for c in speaker_cols},
        speaker_models=models,
        taps=taps,
        zone_watts_col=zone_watts_col,
        required_col=required_col,
        required_is_plain=required_is_plain,
        amplifier_col=amplifier_col,
        label_col=label_col,
        rack_col=rack_col,
        channel_start_col=required_col,
    )


def _most_varied_column(grid: _Grid, columns: list[int], start: int) -> int | None:
    """The column naming the rows is the one with the most distinct values --
    beside it sit circuit references repeated on every row and amplifier
    labels merged over many."""
    best, best_count = None, 0
    for c in columns:
        values = {_text(grid.value(r, c)) for r in range(start, grid.max_row + 1)} - {""}
        if len(values) > best_count:
            best, best_count = c, len(values)
    return best


def _find_headerless_layout(grid: _Grid) -> _Layout | None:
    """EP-29495's layout: speaker models in one row, their taps in the next,
    the names in the column to the left and nothing labelled."""
    for tap_row in range(2, min(grid.max_row, HEADER_SEARCH_ROWS) + 1):
        tap_cols = [
            c
            for c in range(2, grid.max_col + 1)
            if _is_number(grid.value(tap_row, c))
            and 0 < grid.value(tap_row, c) <= MAX_PLAUSIBLE_TAP_WATTS
            and isinstance(grid.value(tap_row - 1, c), str)
        ]
        # The taps sit side by side.
        runs = _contiguous_runs(tap_cols)
        for cols in runs:
            name_col = cols[0] - 1
            names = [grid.value(r, name_col) for r in range(tap_row + 1, grid.max_row + 1)]
            if sum(v is not None for v in names) < 3:
                continue
            taps = {c: float(grid.value(tap_row, c)) for c in cols}
            watts_col = _agreeing_watts_column(grid, cols, taps, cols[-1] + 1, tap_row + 1)
            if watts_col is None:
                continue
            text_cols = [
                c
                for c in range(watts_col + 1, grid.max_col + 1)
                if any(isinstance(grid.value(r, c), str) for r in range(tap_row + 1, grid.max_row + 1))
            ]
            label_col = text_cols[0] if text_cols else None
            rack_col = text_cols[1] if len(text_cols) > 1 and text_cols[1] == label_col + 1 else None
            location_col = rack_col + 1 if rack_col and rack_col + 1 in text_cols else None
            return _Layout(
                data_start=tap_row + 1,
                name_col=name_col,
                speaker_cols=cols,
                speaker_names={c: _display(grid.value(tap_row - 1, c)) for c in cols},
                speaker_models={},
                taps=taps,
                zone_watts_col=watts_col,
                label_col=label_col,
                rack_col=rack_col,
                rack_location_col=location_col,
                channel_start_col=label_col,
            )
    return None


def _contiguous_runs(columns: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for c in columns:
        if runs and c == runs[-1][-1] + 1:
            runs[-1].append(c)
        else:
            runs.append([c])
    return runs


def _agreeing_watts_column(grid: _Grid, cols, taps, first_col: int, start: int) -> int | None:
    for c in range(first_col, min(first_col + 3, grid.max_col + 1)):
        checked = agreed = 0
        for r in range(start, grid.max_row + 1):
            watts = grid.value(r, c)
            if not _is_number(watts):
                continue
            expected = sum(
                (grid.value(r, s) if _is_number(grid.value(r, s)) else 0) * taps[s] for s in cols
            )
            checked += 1
            agreed += abs(expected - watts) < 0.01
        if checked >= 3 and agreed / checked >= MIN_WATTS_AGREEMENT:
            return c
    return None


# --- reading rows --------------------------------------------------------------------


@dataclass
class _ZoneRow:
    row: int
    name: str
    counts: dict[int, int]  # by column
    sheet_watts: float | None


def _zone_rows(grid: _Grid, layout: _Layout, warnings: list[str]) -> list[_ZoneRow]:
    """One entry per schedule row. The table ends at the first row
    that has something in it but is not a schedule row -- a totals row (no
    name), a "Result" note (no numbers) -- so what sits under it is never
    read as floors. Blank rows are passed over."""
    watched = [layout.name_col, *layout.speaker_cols]
    if layout.zone_watts_col:
        watched.append(layout.zone_watts_col)

    rows: list[_ZoneRow] = []
    for r in range(layout.data_start, grid.max_row + 1):
        values = [grid.value(r, c) for c in watched]
        if all(v is None for v in values):
            continue

        name = _row_name(grid, r, layout.name_col)
        counts: dict[int, int] = {}
        for c in layout.speaker_cols:
            value = grid.value(r, c)
            if not _is_number(value):
                continue
            if value < 0 or not float(value).is_integer():
                warnings.append(f"Row {r}: speaker count {value!r} is not a whole number; read as {max(0, round(value))}.")
            counts[c] = max(0, round(value))
        sheet_watts = grid.value(r, layout.zone_watts_col) if layout.zone_watts_col else None
        sheet_watts = float(sheet_watts) if _is_number(sheet_watts) else None

        # A row whose counts are uncalculated formulas is a schedule row with
        # unknown counts, not the end of the table: it is kept so the check
        # after this refuses it, rather than silently ending the schedule.
        pending = any(grid.is_uncalculated(r, c) for c in layout.speaker_cols)
        if name is None or (not counts and sheet_watts is None and not pending) or TOTAL_ROW_RE.search(name.lower()):
            break
        rows.append(_ZoneRow(row=r, name=name, counts=counts, sheet_watts=sheet_watts))
    return rows


def _row_name(grid: _Grid, row: int, col: int) -> str | None:
    """A name merged down several rows names each of them; the rows after
    the first are numbered so every zone stays distinguishable."""
    top, left = grid.anchor(row, col)
    value = grid.value(top, left)
    if value is None:
        return None
    name = _display(value)
    return name if top == row else f"{name} ({row - top + 1})"


def _runs_down(grid: _Grid, col: int, rows: list[int]) -> list[tuple[int, object]]:
    """(row, value) where column `col` starts a new run within the schedule
    rows: every non-empty cell, a merged block counting once at its top. A
    block that starts above the first row (a heading merged into the data)
    is not a run start."""
    first, last = rows[0], rows[-1]
    starts = []
    for r in range(first, last + 1):
        value = grid.value(r, col)
        if value is not None and grid.anchor(r, col) == (r, col):
            starts.append((r, value))
    return starts


def _index_at_or_after(rows: list[int], row: int) -> int | None:
    for i, r in enumerate(rows):
        if r >= row:
            return i
    return None


def _read_channels(grid, layout: _Layout, zone_rows: list[int], warnings: list[str]) -> list[Channel]:
    if layout.channel_start_col is None:
        warnings.append("The sheet does not show how floors are grouped onto amplifiers; there are no channels.")
        return []

    starts: list[tuple[int, object]] = []
    for row, value in _runs_down(grid, layout.channel_start_col, zone_rows):
        index = _index_at_or_after(zone_rows, row)
        if index is not None and (not starts or index > starts[-1][0]):
            starts.append((index, value))

    channels: list[Channel] = []
    for n, (first, value) in enumerate(starts):
        last = (starts[n + 1][0] if n + 1 < len(starts) else len(zone_rows)) - 1
        span = range(zone_rows[first], zone_rows[last] + 1)

        rating = None
        if layout.amplifier_col is not None:
            ratings = _values_in_span(grid, layout.amplifier_col, span)
            parsed = [w for w in (parse_watts(v) for v in ratings) if w]
            if parsed:
                rating = parsed[0]
                if len(set(parsed)) > 1:
                    warnings.append(
                        f"Channel {n + 1}: the sheet gives more than one amplifier ({', '.join(_display(v) for v in ratings)}); used {_display(rating)} W."
                    )
            elif ratings:
                warnings.append(f"Channel {n + 1}: could not read an amplifier rating from {', '.join(repr(_display(v)) for v in ratings)}.")

        label = None
        if layout.label_col is not None:
            labels = _values_in_span(grid, layout.label_col, span)
            label = _display(labels[0]) if labels else None

        required = parse_watts(value) if layout.required_col is not None and layout.required_is_plain else None
        channels.append(
            Channel(first_zone=first, label=label, amplifier_watts=rating, sheet_required_watts=required)
        )
    return channels


def _values_in_span(grid: _Grid, col: int, span: range) -> list:
    """Values of the cells in `col` over the rows, reading a merged block that
    covers the first row from its top even if that is above the span."""
    values = []
    seen: set[tuple[int, int]] = set()
    for r in span:
        anchor = grid.anchor(r, col)
        if anchor in seen:
            continue
        seen.add(anchor)
        value = grid.value(*anchor)
        if value is not None:
            values.append(value)
    return values


def _read_racks(grid, layout: _Layout, zone_rows: list[int], channels: list[Channel], warnings) -> list[Rack]:
    if layout.rack_col is None or not channels:
        return []
    channel_starts = [c.first_zone for c in channels]

    racks: list[Rack] = []
    for row, value in _runs_down(grid, layout.rack_col, zone_rows):
        zone = _index_at_or_after(zone_rows, row)
        if zone is None:
            continue
        channel = max((i for i, s in enumerate(channel_starts) if s <= zone), default=None)
        if channel is None:
            continue
        if racks and channel <= racks[-1].first_channel:
            warnings.append(
                f"Rack {_display(value)} starts partway through a channel already on {racks[-1].name}; it was left out."
            )
            continue
        if channel_starts[channel] != zone:
            warnings.append(
                f"Rack {_display(value)} starts partway through channel {channel + 1}; the whole channel was put on it."
            )
        location = None
        if layout.rack_location_col is not None:
            loc = grid.value(row, layout.rack_location_col)
            location = _display(loc) if loc is not None else None
        racks.append(Rack(name=_display(value), location=location, first_channel=channel))
    return racks


# --- entry point -------------------------------------------------------------------


def read_amplifier_workbook(path: Path, sheet: str | None = None) -> WorkbookRead:
    """Read the named sheet, or else the first sheet that reads as a schedule."""
    try:
        workbook = openpyxl.load_workbook(path, data_only=True)
        formulas = openpyxl.load_workbook(path, data_only=False)
    except Exception as exc:  # noqa: BLE001 -- any unreadable file is the same failure to the engineer
        raise WorkbookReadError(f"Could not open the workbook: {exc}") from exc

    sheets = workbook.sheetnames
    if sheet is not None and sheet not in sheets:
        raise WorkbookReadError(f"The workbook has no sheet named '{sheet}'")

    failures: list[str] = []
    for name in [sheet] if sheet else sheets:
        try:
            read = _read_sheet(workbook[name], formulas[name])
        except WorkbookReadError as exc:
            failures.append(f"{name}: {exc}")
            continue
        read.sheet, read.sheets = name, sheets
        return read
    raise WorkbookReadError("No sheet reads as an amplifier calculation -- " + "; ".join(failures))


def _cell_name(row: int, col: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{get_column_letter(col)}{row}"


def _read_sheet(ws: Worksheet, formulas: Worksheet | None = None) -> WorkbookRead:
    grid = _Grid(ws, formulas)
    warnings: list[str] = []
    layout = _find_headed_layout(grid, warnings) or _find_headerless_layout(grid)
    if layout is None:
        raise WorkbookReadError("no \"Watts per Area\" heading, nor a row of speaker taps")

    zone_rows = _zone_rows(grid, layout, warnings)
    if not zone_rows:
        raise WorkbookReadError("found the columns but no floor rows under them")

    # FORMULA_NOT_CALCULATED: a count or tap the calculation needs is a
    # formula with no cached result. Refused -- a blank read as zero speakers
    # would make an under-loaded amplifier look fine.
    needed = [(zone.row, c) for zone in zone_rows for c in layout.speaker_cols]
    if layout.data_start > 1:
        needed += [(layout.data_start - 1, c) for c in layout.speaker_cols]
    blank = sorted(cell for cell in needed if grid.is_uncalculated(*cell))
    if blank:
        cells = ", ".join(_cell_name(r, c) for r, c in blank[:12])
        raise WorkbookReadError(
            f"FORMULA_NOT_CALCULATED: {len(blank)} speaker count or tap cell{'s' if len(blank) != 1 else ''} ({cells}"
            f"{'...' if len(blank) > 12 else ''}) hold formulas that were never calculated. Open the workbook in Excel, "
            "let it calculate, save it, and import again."
        )
    compared = [(zone.row, layout.zone_watts_col) for zone in zone_rows if layout.zone_watts_col]
    unchecked = [cell for cell in compared if grid.is_uncalculated(*cell)]
    if unchecked:
        warnings.append(
            f"FORMULA_NOT_CALCULATED: the sheet's own watts in {len(unchecked)} row{'s' if len(unchecked) != 1 else ''} "
            "were never calculated, so they could not be compared with the counts."
        )

    used = {c for zone in zone_rows for c, n in zone.counts.items() if n}
    missing_tap = [c for c in layout.speaker_cols if c not in layout.taps and c in used]
    if missing_tap:
        names = ", ".join(f"'{layout.speaker_names[c]}'" for c in missing_tap)
        raise WorkbookReadError(f"speakers are counted under {names} but the sheet gives no tapping wattage for them")

    taken: set[str] = set()
    keys: dict[int, str] = {}
    speaker_types: list[SpeakerType] = []
    for c in layout.speaker_cols:
        if c not in layout.taps:
            continue  # an unused column with no tap: nothing to carry
        keys[c] = _slug(layout.speaker_names[c], taken)
        speaker_types.append(
            SpeakerType(
                key=keys[c],
                name=layout.speaker_names[c],
                model=layout.speaker_models.get(c),
                tap_watts=layout.taps[c],
            )
        )

    zones = [
        Zone(
            name=zone.name,
            counts={keys[c]: n for c, n in zone.counts.items() if c in keys},
            sheet_watts=zone.sheet_watts,
        )
        for zone in zone_rows
    ]
    rows = [zone.row for zone in zone_rows]
    channels = _read_channels(grid, layout, rows, warnings)
    racks = _read_racks(grid, layout, rows, channels, warnings)
    return WorkbookRead(
        sheet=ws.title,
        sheets=[],
        speaker_types=speaker_types,
        zones=zones,
        channels=channels,
        racks=racks,
        warnings=warnings,
    )
