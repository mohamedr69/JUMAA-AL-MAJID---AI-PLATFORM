r"""The amplifier schedule as a document.

A voice evacuation calculation is issued: it goes to a consultant with
the submittal, and it has to read like a calculation rather than a screen
capture. So the page is laid out as an engineer would draw it --

    a summary of what the job needs, in words and figures
    the speaker loading floor by floor, ruled into its amplifiers
    a list of the amplifiers and the cabinets they sit in
    the rules the numbers were worked out by

-- and the amplifier and cabinet columns are ruled across, so a reader
can see at a glance which floors share a SIGA-AA50 and which share a
cabinet. That grouping is the whole point of the document.
"""

from __future__ import annotations

import pymupdf

_RED = (0.72, 0.11, 0.11)
_INK = (0.10, 0.13, 0.20)
_GREY = (0.45, 0.45, 0.45)
_RULE = (0.85, 0.85, 0.85)
_BAND = (0.96, 0.97, 0.99)
_AMP = (0.90, 0.95, 0.99)
_CAB = (0.90, 0.97, 0.93)
_WARN = (0.99, 0.95, 0.90)

WIDTH, HEIGHT = 595.32, 841.92           # A4 portrait: a calculation is read, not spread
LEFT, RIGHT = 40, WIDTH - 40
TOP, BOTTOM = 52, HEIGHT - 52
ROW = 15


def _text(page, x, y, value, *, size=8, bold=False, colour=_INK, right=None):
    font = "hebo" if bold else "helv"
    if right is not None:
        width = pymupdf.get_text_length(str(value), fontname=font, fontsize=size)
        x = right - width
    page.insert_text((x, y), str(value), fontname=font, fontsize=size, color=colour)


def _watts(value: float) -> str:
    """A wattage as a document should carry it: whole where it is whole,
    two places where it is not. "455.312 W" is arithmetic showing through."""
    rounded = round(float(value), 2)
    return f"{int(rounded)}" if rounded == int(rounded) else f"{rounded:.2f}"


def build(project, result: dict) -> pymupdf.Document:
    """The amplifier calculation, laid out to be issued."""
    doc = pymupdf.open()
    columns = result.get("columns", [])
    floors = result.get("floors", [])
    amplifiers = {a["name"]: a for a in result.get("amplifiers", [])}

    # The widths: the floor name, a column per speaker, the load, then the
    # amplifier and its cabinet.
    speaker_width = 54 if len(columns) <= 4 else max(34, int(210 / max(1, len(columns))))
    load_width, mod_width, amp_width, cab_width = 48, 56, 58, 48
    floor_width = (RIGHT - LEFT - speaker_width * len(columns)
                   - load_width - mod_width - amp_width - cab_width)

    state = {"page": None, "y": 0.0}

    def new_page(title: str) -> None:
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        state["page"] = page
        _text(page, LEFT, TOP, "AMPLIFIER CALCULATION", size=14, bold=True, colour=_RED)
        _text(page, LEFT, TOP + 15, f"EP-{project.ep_number} - {project.project_name or ''}".strip(" -")[:88],
              size=8, colour=_GREY)
        _text(page, LEFT, TOP + 27, title, size=8, bold=True, colour=_GREY)
        page.draw_line(pymupdf.Point(LEFT, TOP + 33), pymupdf.Point(RIGHT, TOP + 33), color=_RED, width=1)
        state["y"] = TOP + 46

    def room(needed: float, title: str) -> None:
        if state["page"] is None or state["y"] + needed > BOTTOM:
            new_page(title)

    # --- what the job needs ------------------------------------------------
    new_page("Summary")
    page = state["page"]
    facts = [
        ("Floors on the schedule", str(result.get("total_floors", 0))),
        ("Speakers", str(result.get("total_speakers", 0))),
        ("Connected load", f"{_watts(result.get('total_watts', 0))} W"),
        (f"With {int(result.get('spare_fraction', 0) * 100)}% spare",
         f"{_watts(result.get('total_watts_with_spare', 0))} W"),
        (f"{result.get('module_part', '')} required",
         f"{result.get('total_modules', 0)}  (one a floor, up to "
         f"{_watts(result.get('module_max_watts', 0))} W)"),
        (f"{result.get('amplifier_part', '')} required",
         f"{len(result.get('amplifiers', []))}  ({_watts(result.get('limit_watts', 0))} W each)"),
        ("APS cabinets",
         f"{len(result.get('cabinets', []))}  ({result.get('amplifiers_per_cabinet', 2)} per cabinet)"),
    ]
    y = state["y"]
    for index, (label, value) in enumerate(facts):
        box = pymupdf.Rect(LEFT, y, RIGHT, y + 18)
        if index % 2 == 0:
            page.draw_rect(box, color=None, fill=_BAND)
        _text(page, LEFT + 6, y + 12, label, size=8.5, colour=_GREY)
        _text(page, 0, y + 12, value, size=8.5, bold=True, right=RIGHT - 6)
        y += 18
    state["y"] = y + 18

    # --- the loading, floor by floor ---------------------------------------
    def header() -> None:
        page = state["page"]
        y = state["y"]
        page.draw_rect(pymupdf.Rect(LEFT, y, RIGHT, y + 20), color=None, fill=_RED)
        x = LEFT + 5
        _text(page, x, y + 13, "FLOOR", size=7.5, bold=True, colour=(1, 1, 1))
        x += floor_width
        for column in columns:
            label = column["key"] if len(column["key"]) <= 11 else column["key"][:10] + "..."
            _text(page, x, y + 9, label, size=6.5, bold=True, colour=(1, 1, 1))
            tap = "- W" if column["tap"] is None else f"{_watts(column['tap'])} W"
            _text(page, x, y + 17, tap, size=6, colour=(1, 1, 1))
            x += speaker_width
        _text(page, 0, y + 13, "LOAD (W)", size=7, bold=True, colour=(1, 1, 1),
              right=x + load_width - 4)
        x += load_width
        _text(page, x + 4, y + 13, "MODULE", size=6.5, bold=True, colour=(1, 1, 1))
        x += mod_width
        _text(page, x + 4, y + 13, result.get("amplifier_part", "AMP"), size=6.5, bold=True, colour=(1, 1, 1))
        x += amp_width
        _text(page, x + 4, y + 13, "APS", size=7, bold=True, colour=(1, 1, 1))
        state["y"] = y + 20

    room(160, "Speaker loading by floor")
    _text(state["page"], LEFT, state["y"], "SPEAKER LOADING BY FLOOR", size=9, bold=True)
    state["y"] += 8
    header()

    # An amplifier's floors are ruled across as one cell. A schedule of
    # forty-odd floors runs over a page, so a run that meets the bottom is
    # closed there and opened again at the top of the next page -- the cell
    # is split, never drawn off the page.
    runs: list[tuple[str | None, int, int]] = []
    for index, floor in enumerate(floors):
        name = floor.get("amplifier")
        if runs and runs[-1][0] == name:
            runs[-1] = (name, runs[-1][1], index)
        else:
            runs.append((name, index, index))
    run_of = {}
    for name, first, last in runs:
        for index in range(first, last + 1):
            run_of[index] = (name, last)

    left_in_cell = 0                  # rows still covered by the cell just drawn
    for index, floor in enumerate(floors):
        if state["y"] + ROW > BOTTOM:
            new_page("Speaker loading by floor (continued)")
            header()
            left_in_cell = 0          # the run is cut by the page; open a new cell
        page = state["page"]
        y = state["y"]
        x = LEFT + 5
        _text(page, x, y + 10, floor["floor"][:28], size=7.5)
        x += floor_width
        for column in columns:
            count = floor["counts"].get(column["key"])
            _text(page, 0, y + 10, count if count else "-", size=7.5,
                  colour=_INK if count else _RULE, right=x + speaker_width - 8)
            x += speaker_width
        _text(page, 0, y + 10, _watts(floor["watts"]), size=7.5, bold=True, right=x + load_width - 4)
        x += load_width
        modules = floor.get("modules", 0)
        if modules:
            _text(page, x + 4, y + 10, result.get("module_part", ""), size=6.5)
            if modules > 1:
                _text(page, 0, y + 10, f"x{modules}", size=6.5, bold=True,
                      colour=(0.55, 0.35, 0.05), right=x + mod_width - 4)
        else:
            _text(page, x + 4, y + 10, "-", size=7, colour=_RULE)
        x += mod_width

        if left_in_cell <= 0:
            name, last = run_of[index]
            # As many of the run's rows as this page has room for.
            fits = int((BOTTOM - y) // ROW)
            rows = max(1, min(last - index + 1, fits))
            left_in_cell = rows
            height = rows * ROW
            if name:
                amplifier = amplifiers.get(name, {})
                page.draw_rect(pymupdf.Rect(x, y, x + amp_width, y + height), color=None,
                               fill=_WARN if amplifier.get("over_limit") else _AMP)
                middle = y + height / 2
                _text(page, x + 5, middle - 1, name, size=7, bold=True)
                _text(page, x + 5, middle + 8, f"{_watts(amplifier.get('watts', 0))} W", size=6.5, colour=_GREY)
                page.draw_rect(pymupdf.Rect(x + amp_width, y, x + amp_width + cab_width, y + height),
                               color=None, fill=_CAB)
                _text(page, x + amp_width + 5, middle + 3, amplifier.get("cabinet") or "-", size=7, bold=True)
                # A cabinet is ruled off from the next, which is what a
                # reader looks for first.
                page.draw_line(pymupdf.Point(x, y), pymupdf.Point(RIGHT, y), color=_GREY, width=0.8)
            else:
                _text(page, x + 5, y + height / 2 + 3, "no speakers", size=6.5, colour=_RULE)
        left_in_cell -= 1
        page.draw_line(pymupdf.Point(LEFT, y + ROW), pymupdf.Point(RIGHT, y + ROW), color=_RULE, width=0.4)
        state["y"] = y + ROW

    # The totals.
    page, y = state["page"], state["y"]
    page.draw_line(pymupdf.Point(LEFT, y), pymupdf.Point(RIGHT, y), color=_RED, width=0.8)
    x = LEFT + 5
    _text(page, x, y + 12, "TOTAL", size=8, bold=True)
    x += floor_width
    for column in columns:
        _text(page, 0, y + 12, result.get("totals_by_column", {}).get(column["key"], 0), size=7.5,
              bold=True, right=x + speaker_width - 8)
        x += speaker_width
    _text(page, 0, y + 12, _watts(result.get("total_watts", 0)), size=8, bold=True, right=x + load_width - 4)
    x += load_width
    _text(page, x + 4, y + 12, str(result.get("total_modules", 0)), size=8, bold=True)
    x += mod_width
    _text(page, x + 5, y + 12, str(len(result.get("amplifiers", []))), size=8, bold=True)
    _text(page, x + amp_width + 5, y + 12, str(len(result.get("cabinets", []))), size=8, bold=True)
    state["y"] = y + 30

    # --- the amplifiers and their cabinets ---------------------------------
    room(120, "Amplifiers")
    page = state["page"]
    _text(page, LEFT, state["y"], "AMPLIFIERS AND CABINETS", size=9, bold=True)
    state["y"] += 12
    for cabinet in result.get("cabinets", []):
        if state["y"] + 16 + 14 * len(cabinet["amplifiers"]) > BOTTOM:
            new_page("Amplifiers and cabinets (continued)")
        page = state["page"]
        y = state["y"]
        page.draw_rect(pymupdf.Rect(LEFT, y, RIGHT, y + 15), color=None, fill=_CAB)
        _text(page, LEFT + 6, y + 11, cabinet["name"], size=8, bold=True)
        _text(page, 0, y + 11,
              f"{len(cabinet['amplifiers'])} x {result.get('amplifier_part', '')}",
              size=7.5, colour=_GREY, right=RIGHT - 6)
        y += 15
        for name in cabinet["amplifiers"]:
            amplifier = amplifiers.get(name, {})
            _text(page, LEFT + 16, y + 10, name, size=7.5, bold=True)
            _text(page, LEFT + 70, y + 10, f"{_watts(amplifier.get('watts', 0))} W", size=7.5)
            _text(page, LEFT + 110, y + 10,
                  ", ".join(amplifier.get("floors", []))[:92], size=7, colour=_GREY)
            y += 14
        state["y"] = y + 6

    # --- how it was worked out ----------------------------------------------
    rules = [
        "A floor's load is the sum over speaker types of count x tapping, the tapping taken "
        "off the speaker's datasheet.",
        f"Floors are added to an amplifier in the schedule's order until the next would take it over "
        f"{_watts(result.get('limit_watts', 0))} W "
        f"({_watts(result.get('amplifier_watts', 0))} W rated), then a new "
        f"{result.get('amplifier_part', '')} starts. A floor is never split between two.",
        f"{result.get('amplifiers_per_cabinet', 2)} x {result.get('amplifier_part', '')} = 1 APS cabinet; "
        "an odd amplifier still needs one.",
        f"Every floor with speakers is fed through one {result.get('module_part', '')}; a floor carrying "
        f"more than {_watts(result.get('module_max_watts', 0))} W needs another.",
        "A sounder, horn or flasher is on a notification circuit and is not counted here.",
        "Speaker quantities are the project's floor-wise BOQ.",
    ]
    room(40 + 12 * len(rules), "Basis of calculation")
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
