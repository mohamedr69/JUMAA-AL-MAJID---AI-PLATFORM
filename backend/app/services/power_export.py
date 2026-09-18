r"""The 24 V power calculation as a document.

The same layout as the amplifier's, for the other half of the system: a
summary, the load floor by floor with each booster power supply ruled
across the floors it feeds, the supplies listed with their floors, and
the basis the numbers were worked out by.
"""

from __future__ import annotations

import pymupdf

_RED = (0.72, 0.11, 0.11)
_INK = (0.10, 0.13, 0.20)
_GREY = (0.45, 0.45, 0.45)
_RULE = (0.85, 0.85, 0.85)
_BAND = (0.96, 0.97, 0.99)
_SUPPLY = (0.90, 0.95, 0.99)
_WARN = (0.99, 0.95, 0.90)

WIDTH, HEIGHT = 595.32, 841.92
LEFT, RIGHT = 40, WIDTH - 40
TOP, BOTTOM = 52, HEIGHT - 52
ROW = 15


def _text(page, x, y, value, *, size=8, bold=False, colour=_INK, right=None):
    font = "hebo" if bold else "helv"
    if right is not None:
        x = right - pymupdf.get_text_length(str(value), fontname=font, fontsize=size)
    page.insert_text((x, y), str(value), fontname=font, fontsize=size, color=colour)


def _ma(value: float) -> str:
    rounded = round(float(value), 2)
    return f"{int(rounded)}" if rounded == int(rounded) else f"{rounded:.2f}"


def _amps(milliamps: float) -> str:
    return f"{round(float(milliamps) / 1000, 3):g}"


def build(project, result: dict) -> pymupdf.Document:
    """The 24 V calculation, laid out to be issued."""
    doc = pymupdf.open()
    columns = result.get("columns", [])
    floors = result.get("floors", [])
    supplies = {s["name"]: s for s in result.get("supplies", [])}

    device_width = 56 if len(columns) <= 4 else max(34, int(224 / max(1, len(columns))))
    load_width, mod_width, sup_width = 58, 54, 62
    floor_width = RIGHT - LEFT - device_width * len(columns) - load_width - mod_width - sup_width

    state: dict = {"page": None, "y": 0.0}

    def new_page(title: str) -> None:
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        state["page"] = page
        _text(page, LEFT, TOP, "24 V POWER CALCULATION", size=14, bold=True, colour=_RED)
        _text(page, LEFT, TOP + 15,
              f"EP-{project.ep_number} - {project.project_name or ''}".strip(" -")[:88],
              size=8, colour=_GREY)
        _text(page, LEFT, TOP + 27, title, size=8, bold=True, colour=_GREY)
        page.draw_line(pymupdf.Point(LEFT, TOP + 33), pymupdf.Point(RIGHT, TOP + 33), color=_RED, width=1)
        state["y"] = TOP + 46

    def room(needed: float, title: str) -> None:
        if state["page"] is None or state["y"] + needed > BOTTOM:
            new_page(title)

    new_page("Summary")
    page = state["page"]
    facts = [
        ("Floors on the schedule", str(result.get("total_floors", 0))),
        ("24 V appliances", str(result.get("total_devices", 0))),
        ("Connected load", f"{_amps(result.get('total_ma', 0))} A ({_ma(result.get('total_ma', 0))} mA)"),
        (f"{result.get('supply_part', '')} required",
         f"{len(result.get('supplies', []))}  ({_amps(result.get('limit_ma', 0))} A each)"),
        (f"{result.get('module_part', '')} required",
         f"{result.get('total_modules', 0)}  (one a floor with a notification circuit)"),
    ]
    y = state["y"]
    for index, (label, value) in enumerate(facts):
        if index % 2 == 0:
            page.draw_rect(pymupdf.Rect(LEFT, y, RIGHT, y + 18), color=None, fill=_BAND)
        _text(page, LEFT + 6, y + 12, label, size=8.5, colour=_GREY)
        _text(page, 0, y + 12, value, size=8.5, bold=True, right=RIGHT - 6)
        y += 18
    state["y"] = y + 18

    def header() -> None:
        page = state["page"]
        y = state["y"]
        page.draw_rect(pymupdf.Rect(LEFT, y, RIGHT, y + 20), color=None, fill=_RED)
        x = LEFT + 5
        _text(page, x, y + 13, "FLOOR", size=7.5, bold=True, colour=(1, 1, 1))
        x += floor_width
        for column in columns:
            label = column["key"] if len(column["key"]) <= 11 else column["key"][:10] + "."
            _text(page, x, y + 9, label, size=6.5, bold=True, colour=(1, 1, 1))
            draw = "- mA" if column["current_ma"] is None else f"{_ma(column['current_ma'])} mA"
            _text(page, x, y + 17, draw, size=6, colour=(1, 1, 1))
            x += device_width
        _text(page, 0, y + 13, "LOAD (mA)", size=7, bold=True, colour=(1, 1, 1), right=x + load_width - 4)
        x += load_width
        _text(page, x + 4, y + 13, "MODULE", size=6.5, bold=True, colour=(1, 1, 1))
        x += mod_width
        _text(page, x + 4, y + 13, "SUPPLY", size=6.5, bold=True, colour=(1, 1, 1))
        state["y"] = y + 20

    room(150, "24 V load by floor")
    _text(state["page"], LEFT, state["y"], "24 V LOAD BY FLOOR", size=9, bold=True)
    state["y"] += 8
    header()

    runs: list[tuple[str | None, int, int]] = []
    for index, floor in enumerate(floors):
        name = floor.get("supply")
        if runs and runs[-1][0] == name:
            runs[-1] = (name, runs[-1][1], index)
        else:
            runs.append((name, index, index))
    run_of = {}
    for name, first, last in runs:
        for index in range(first, last + 1):
            run_of[index] = (name, last)

    left_in_cell = 0
    for index, floor in enumerate(floors):
        if state["y"] + ROW > BOTTOM:
            new_page("24 V load by floor (continued)")
            header()
            left_in_cell = 0
        page = state["page"]
        y = state["y"]
        x = LEFT + 5
        _text(page, x, y + 10, floor["floor"][:28], size=7.5)
        x += floor_width
        for column in columns:
            count = floor["counts"].get(column["key"])
            _text(page, 0, y + 10, count if count else "-", size=7.5,
                  colour=_INK if count else _RULE, right=x + device_width - 8)
            x += device_width
        _text(page, 0, y + 10, _ma(floor["current_ma"]), size=7.5, bold=True, right=x + load_width - 4)
        x += load_width
        if floor.get("modules"):
            _text(page, x + 4, y + 10, result.get("module_part", ""), size=6.5)
        else:
            _text(page, x + 4, y + 10, "-", size=7, colour=_RULE)
        x += mod_width

        if left_in_cell <= 0:
            name, last = run_of[index]
            fits = int((BOTTOM - y) // ROW)
            rows = max(1, min(last - index + 1, fits))
            left_in_cell = rows
            height = rows * ROW
            if name:
                supply = supplies.get(name, {})
                page.draw_rect(pymupdf.Rect(x, y, x + sup_width, y + height), color=None,
                               fill=_WARN if supply.get("over_limit") else _SUPPLY)
                middle = y + height / 2
                _text(page, x + 5, middle - 1, name, size=7, bold=True)
                _text(page, x + 5, middle + 8, f"{_amps(supply.get('current_ma', 0))} A", size=6.5,
                      colour=_GREY)
                page.draw_line(pymupdf.Point(x, y), pymupdf.Point(RIGHT, y), color=_GREY, width=0.8)
            else:
                _text(page, x + 5, y + height / 2 + 3, "no 24 V load", size=6.5, colour=_RULE)
        left_in_cell -= 1
        page.draw_line(pymupdf.Point(LEFT, y + ROW), pymupdf.Point(RIGHT, y + ROW), color=_RULE, width=0.4)
        state["y"] = y + ROW

    page, y = state["page"], state["y"]
    page.draw_line(pymupdf.Point(LEFT, y), pymupdf.Point(RIGHT, y), color=_RED, width=0.8)
    x = LEFT + 5
    _text(page, x, y + 12, "TOTAL", size=8, bold=True)
    x += floor_width
    for column in columns:
        _text(page, 0, y + 12, result.get("totals_by_column", {}).get(column["key"], 0), size=7.5,
              bold=True, right=x + device_width - 8)
        x += device_width
    _text(page, 0, y + 12, _ma(result.get("total_ma", 0)), size=8, bold=True, right=x + load_width - 4)
    x += load_width
    _text(page, x + 4, y + 12, str(result.get("total_modules", 0)), size=8, bold=True)
    x += mod_width
    _text(page, x + 4, y + 12, str(len(result.get("supplies", []))), size=8, bold=True)
    state["y"] = y + 30

    room(110, "Power supplies")
    page = state["page"]
    _text(page, LEFT, state["y"], "POWER SUPPLIES", size=9, bold=True)
    y = state["y"] + 12
    circuits = result.get("circuits", [])
    limit = result.get("circuit_limit_ma", 0)
    for supply in result.get("supplies", []):
        if y + 16 > BOTTOM:
            new_page("Power supplies (continued)")
            page, y = state["page"], state["y"]
        _text(page, LEFT + 6, y + 10, supply["name"], size=7.5, bold=True)
        _text(page, LEFT + 60, y + 10, f"{_amps(supply['current_ma'])} A", size=7.5)
        _text(page, LEFT + 100, y + 10, ", ".join(supply["floors"])[:96], size=7, colour=_GREY)
        y += 14
        # The circuits of this supply, each with its load against the limit.
        for circuit in (c for c in circuits if c["supply"] == supply["name"]):
            if y + 14 > BOTTOM:
                new_page("Power supplies (continued)")
                page, y = state["page"], state["y"]
            _text(page, LEFT + 18, y + 9, circuit["name"], size=7)
            _text(page, LEFT + 60, y + 9, f"{_ma(circuit['current_ma'])} / {_ma(limit)} mA"
                  + ("  OVER LIMIT" if circuit.get("over_limit") else ""), size=7,
                  bold=bool(circuit.get("over_limit")))
            _text(page, LEFT + 150, y + 9, ", ".join(circuit["floors"])[:84], size=6.5, colour=_GREY)
            y += 11
        y += 3
    state["y"] = y + 10

    rules = [
        "A floor's load is the sum over appliances of count x the current its datasheet states.",
        "Everything that runs on 24 V is counted: sounders, flashers, the flasher half of a "
        "speaker-flasher, and sounder bases. A plain speaker is on the amplifier's 70 V line and "
        "draws nothing here.",
        f"Each {result.get('supply_part', '')} has {result.get('circuits_per_supply', 4)} circuits, each "
        f"worked to {_ma(result.get('circuit_limit_ma', 0))} mA: the supply's {result.get('supply_amps', '')} A "
        "shared between its circuits with the same spare kept, so circuits within their limit keep the "
        f"supply within its {_amps(result.get('limit_ma', 0))} A.",
        "Floors are wired to a circuit in the schedule's order until the next would take it over its "
        f"limit, then the next circuit starts; after {result.get('circuits_per_supply', 4)} circuits, the "
        "next supply. A floor is never split between two circuits.",
        f"A floor with a notification circuit is driven from one {result.get('module_part', '')}. "
        "A sounder base is on the Signature loop and needs none, so a floor carrying only bases draws "
        "power and takes no module.",
    ]
    room(40 + 14 * len(rules), "Basis of calculation")
    page = state["page"]
    _text(page, LEFT, state["y"], "BASIS OF CALCULATION", size=9, bold=True)
    y = state["y"] + 12
    for rule in rules:
        for index, line in enumerate(_wrap(rule, 104)):
            _text(page, LEFT + 8, y, f"- {line}" if index == 0 else f"   {line}", size=7.5, colour=_GREY)
            y += 10
        y += 2
    state["y"] = y

    warnings = result.get("warnings", [])
    if warnings:
        room(24 + 12 * len(warnings), "Notes")
        page = state["page"]
        _text(page, LEFT, state["y"] + 8, "NOTES", size=9, bold=True)
        y = state["y"] + 20
        for warning in warnings:
            for line in _wrap(warning, 104):
                _text(page, LEFT + 8, y, line, size=7.5, colour=(0.55, 0.35, 0.05))
                y += 10
            y += 2
    return doc


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines or [""]
