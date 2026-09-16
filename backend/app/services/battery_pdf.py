"""The battery calculation as the company issues it.

Laid out to `FACP_Battery_Calculation_Clean.pdf` in the submittal builder --
the sheet the team hands to a consultant. Its geometry is reproduced here
rather than the file being filled in, because the template is one panel's
calculation with one project's numbers baked in: a project has several
panels and a different number of connected modules on each, so the page has
to be drawn to fit what it is reporting.

Everything that is *design* -- the A3 landscape sheet, the navy banner, the
two panels, the column positions, the type sizes and the colours -- is the
template's and is pinned here. What changes is the project, the equipment
rows and the totals.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pymupdf

from app.models import Project
from app.services import system_rules

# The template's own page and palette.
PAGE = (1190.551, 841.89)          # A3 landscape
BACKDROP = (0.961, 0.973, 0.98)
BANNER = (0.078, 0.157, 0.239)
CARD = (1.0, 1.0, 1.0)
STRIPE = (0.969, 0.98, 0.988)
HIGHLIGHT = (0.929, 0.961, 0.976)
LABEL = (0.329, 0.412, 0.475)      # 0x546979
INK = (0.078, 0.157, 0.239)        # 0x14283d
WHITE = (1, 1, 1)

BANNER_HEIGHT = 94.0
MARGIN_L, MARGIN_R = 42.0, 1148.6

# Connected-equipment columns, at the template's x positions.
COLUMNS = [
    ("Part no.", 59.0),
    ("Description", 160.0),
    ("Qty", 433.0),
    ("Standby / unit", 478.0),
    ("Alarm / unit", 558.0),
    ("Standby total", 640.0),
    ("Alarm total", 720.0),
]
ROW_HEIGHT = 24.5
TABLE_TOP = 320.0
TABLE_LEFT, TABLE_RIGHT = 56.0, 780.0
EQUIPMENT_CARD = (42.0, 282.9, 796.0)
SIZING_CARD = (811.0, 282.9, 1148.6)

# The notes card under the table, and the footer under that. What is left
# between the table's first row and the notes is how many rows the first
# page holds; a panel with more parts continues its table on further pages
# rather than running off the bottom of this one. EP-30175's FACP-01 has 26
# loads, and the sheet clipped it at about the twentieth.
NOTES_HEIGHT = 151.0
NOTES_GAP = 15.0
FOOTER = 28.0
CONTINUATION_TABLE_TOP = 130.0

# The notes the template carries. They are the engineers' own, and are part
# of the sheet rather than a function of the numbers -- a reviewer expects
# them on every calculation.
NOTES = [
    "1- The calculation has been carried out based on the full-load condition. The current consumption of 3-SDDC2 Dual "
    "Loop Cards has been considered based on a maximum of 500 connected devices, including all detectors and modules.",
    "2- A separate cabinet is provided for the NAC circuits, utilizing SIGA-BPS10A/230. Refer to the NAC Circuit "
    "Calculation and SIGA-BPS10A/230 Battery Calculation for details.",
    "3- A separate cabinet is provided for the VES circuits, utilizing SIGA-APS6A/230. Refer to the VES Circuit "
    "Calculation and SIGA-APS6A/230 Battery Calculation for details.",
    "4- 24 hours of standby operation and 30 minutes of alarm operation have been considered in the battery "
    "calculations, in accordance with DCD requirements and NFPA 72 standards.",
]

SUPPLIER = "M/s. AL ARABIA FOR SAFETY & SECURITY LLC"


def _number(value: float | None, places: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{places}f}"


def _fit(text: str, font: str, size: float, width: float) -> str:
    """Trim to the column, because the template has no room to wrap."""
    if pymupdf.get_text_length(text, fontname=font, fontsize=size) <= width:
        return text
    while text and pymupdf.get_text_length(text + "...", fontname=font, fontsize=size) > width:
        text = text[:-1]
    return text + "..."


def _field(page, x_label: float, x_value: float, y: float, label: str, value: str, width: float) -> None:
    page.insert_text((x_label, y), label.upper(), fontname="hebo", fontsize=8, color=LABEL)
    page.insert_text((x_value, y), _fit(value or "-", "helv", 8.3, width), fontname="helv", fontsize=8.3, color=INK)


def _rows_per_page(first: bool) -> int:
    """Equipment rows a page holds. The first page also carries the sizing
    card beside the table and the notes beneath it; a continuation page is
    the table alone."""
    if first:
        available = PAGE[1] - FOOTER - NOTES_GAP - NOTES_HEIGHT - (TABLE_TOP + 45)
    else:
        available = PAGE[1] - FOOTER - 12 - (CONTINUATION_TABLE_TOP + 45)
    return max(1, int(available // ROW_HEIGHT))


def _banner(page, manufacturer: str, title: str) -> None:
    page.draw_rect(pymupdf.Rect(0, 0, *PAGE), color=None, fill=BACKDROP)
    page.draw_rect(pymupdf.Rect(0, 0, PAGE[0], BANNER_HEIGHT), color=None, fill=BANNER)
    brand = (manufacturer or "").strip().upper()
    # "EST4" is Edwards' platform, not a suffix for every brand.
    label = (f"{brand}  /  EST4" if "EDWARDS" in brand else brand) or "MANUFACTURER NOT ESTABLISHED"
    page.insert_text((42, 34.5), label, fontname="hebo", fontsize=12, color=WHITE)
    page.insert_text((42, 62), title, fontname="hebo", fontsize=20, color=WHITE)


def _equipment_table(page, lines, table_top: float, card_right: float, continued: tuple[int, int] | None) -> float:
    """Draw the connected-equipment card from `table_top`; return its bottom."""
    bottom = max(612.9 if continued is None else 0, table_top + 40 + len(lines) * ROW_HEIGHT + 14)
    page.draw_rect(pymupdf.Rect(EQUIPMENT_CARD[0], table_top - 37.1, card_right, bottom), color=None, fill=CARD)
    heading = "Connected equipment"
    if continued is not None:
        heading += f" (continued, sheet {continued[0]} of {continued[1]})"
    page.insert_text((58, table_top - 15), heading, fontname="hebo", fontsize=12, color=INK)
    for label, x in COLUMNS:
        page.insert_text((x, table_top + 14), label, fontname="helv", fontsize=7.5, color=LABEL)

    y = table_top + 45
    for index, line in enumerate(lines):
        if index % 2:
            page.draw_rect(pymupdf.Rect(TABLE_LEFT, y - 9, TABLE_RIGHT, y + 14), color=None, fill=STRIPE)
        cells = [
            (_fit(line.part_no or "-", "helv", 7.6, 95), 59.0),
            (_fit(line.description or "", "helv", 7.6, 265), 160.0),
            (_number(line.quantity), 433.0),
            (_number(line.standby_ma), 478.0),
            (_number(line.alarm_ma), 558.0),
            (_number(line.total_standby_ma), 640.0),
            (_number(line.total_alarm_ma), 720.0),
        ]
        for text, x in cells:
            page.insert_text((x, y), text, fontname="helv", fontsize=7.6, color=INK)
        y += ROW_HEIGHT
    return bottom


def _status_lines(panel) -> list[str]:
    """What the sheet must say beside the selection so the download tells
    the same story as the screen: an incomplete load, a missing selection.
    (Whether the BOQ's battery is short, and the charger's compatibility,
    are the page's business, not the issued sheet's -- platform owner,
    16 September 2026.)"""
    notes: list[str] = []
    if panel.lower_bound:
        missing = len(getattr(panel, "missing_parts", None) or [])
        notes.append(
            f"Load is a lower bound: {missing} part{'s have' if missing != 1 else ' has'} no current on file. "
            "Do not issue until every part is entered."
        )
    status = str(getattr(panel, "status", "") or "")
    if status == "no_selection":
        notes.append("No battery of the catalogued brand covers this requirement; selection pending.")
    return notes


def _panel_page(doc, project: Project, panel, systems: str, manufacturer: str) -> None:
    lines = [line for line in panel.lines if line.kind == "load"]
    first_rows = _rows_per_page(first=True)
    more_rows = _rows_per_page(first=False)
    head, tail = lines[:first_rows], lines[first_rows:]
    continuation_sheets = -(-len(tail) // more_rows) if tail else 0
    title = f"{panel.name or panel.heading} BATTERY CALCULATION".upper()

    page = doc.new_page(width=PAGE[0], height=PAGE[1])
    _banner(page, manufacturer, title)

    # --- project card -------------------------------------------------------
    page.draw_rect(pymupdf.Rect(MARGIN_L, 114.9, MARGIN_R, 279.9), color=None, fill=CARD)
    plot = (project.plot_number or "").strip()
    where = ", ".join(p for p in [f"PLOT NO: {plot}" if plot else "", (project.location or "").strip()] if p)
    _field(page, 58, 180, 140, "Project",
           f"{(project.project_name or '').strip()}{f' @ {where}' if where else ''}", 950)
    _field(page, 58, 190, 186, "Client", (project.client or "").strip(), 400)
    _field(page, 58, 190, 209, "Consultant", (project.consultant or "").strip(), 400)
    _field(page, 58, 190, 232, "MEP Contractor", (project.contractor or "").strip(), 400)
    _field(page, 58, 190, 255, "FAS Supplier", SUPPLIER, 400)
    _field(page, 613, 745, 186, "Manufacturer", (manufacturer or "").strip().upper() or "Not established", 380)
    _field(page, 613, 745, 209, "Submittal", systems, 380)
    _field(page, 613, 745, 232, "Date", date.today().strftime("%d.%m.%Y"), 380)

    # --- connected equipment ------------------------------------------------
    bottom = _equipment_table(page, head, TABLE_TOP, EQUIPMENT_CARD[2], None)
    if tail:
        page.insert_text(
            (58, bottom - 4),
            f"{len(tail)} more part{'s' if len(tail) != 1 else ''} on the following sheet"
            f"{'s' if continuation_sheets != 1 else ''}; totals include them.",
            fontname="helv", fontsize=7.4, color=LABEL,
        )

    # --- battery sizing -----------------------------------------------------
    page.draw_rect(pymupdf.Rect(SIZING_CARD[0], SIZING_CARD[1], SIZING_CARD[2], bottom), color=None, fill=CARD)
    page.insert_text((827, 305), "Battery sizing", fontname="hebo", fontsize=12, color=INK)

    settings = panel.settings or {}
    hours = settings.get("standby_hours", 24)
    minutes = settings.get("alarm_minutes", 30)
    spare = settings.get("spare_factor", 1.2)
    # ">=" rather than the sign: the base-14 PDF fonts have no U+2265 and
    # pymupdf substitutes a middle dot for it, which would turn "at least
    # 110 Ah" into what looks like a bulleted "110 Ah" on an issued sheet.
    at_least = ">= " if panel.lower_bound else ""

    rows = [
        ("Standby current", f"{at_least}{_number(panel.standby_ma, 3)} mA"),
        ("Alarm current", f"{at_least}{_number(panel.alarm_ma, 3)} mA"),
        (f"Standby, {_number(hours)} h", f"{at_least}{_number(panel.standby_mah / 1000 if panel.standby_mah else 0, 3)} Ah"),
        (f"Alarm, {_number(minutes)} min", f"{at_least}{_number(panel.alarm_mah / 1000 if panel.alarm_mah else 0, 3)} Ah"),
        ("Base requirement", f"{at_least}{_number(panel.total_ah, 3)} Ah"),
        (f"With {_number((spare - 1) * 100)}% spare", f"{at_least}{_number(panel.required_ah, 3)} Ah"),
    ]
    y = 350
    for label, value in rows:
        page.insert_text((827, y), label, fontname="helv", fontsize=8.5, color=LABEL)
        page.insert_text((1010, y), value, fontname="hebo", fontsize=8.8, color=INK)
        y += 33

    selected = panel.selected or []
    if selected:
        units = " + ".join(f"{_number(b.units)} NOS {_number(b.capacity_ah)}AH" for b in selected)
    elif panel.lower_bound:
        units = "Pending: a part has no current yet"
    else:
        units = "No catalogued battery covers this load"
    page.draw_rect(pymupdf.Rect(825, y - 6, 1135.6, y + 31), color=None, fill=HIGHLIGHT)
    page.insert_text((837, y + 8), "SELECTED BATTERY", fontname="hebo", fontsize=8, color=LABEL)
    page.insert_text((837, y + 24), _fit(units, "hebo", 11, 290), fontname="hebo", fontsize=11, color=INK)

    y += 52
    # The screen's warnings travel with the sheet: what is pending on the
    # page is pending on the download.
    for note in _status_lines(panel):
        for chunk in _wrap(note, "helv", 7.4, SIZING_CARD[2] - 827 - 12):
            page.insert_text((827, y), chunk, fontname="helv", fontsize=7.4, color=(0.55, 0.3, 0.05))
            y += 10
        y += 2

    # --- notes --------------------------------------------------------------
    notes_top = bottom + NOTES_GAP
    page.draw_rect(pymupdf.Rect(MARGIN_L, notes_top, MARGIN_R, notes_top + NOTES_HEIGHT), color=None, fill=CARD)
    page.insert_text((58, notes_top + 24), "Calculation notes", fontname="hebo", fontsize=10, color=INK)
    y = notes_top + 46
    for note in NOTES:
        for chunk in _wrap(note, "helv", 7.8, MARGIN_R - 58 - 16):
            page.insert_text((58, y), chunk, fontname="helv", fontsize=7.8, color=INK)
            y += 11
        y += 3

    # --- the rest of the equipment, a sheet at a time ------------------------
    for sheet in range(continuation_sheets):
        chunk = tail[sheet * more_rows : (sheet + 1) * more_rows]
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        _banner(page, manufacturer, title)
        _equipment_table(page, chunk, CONTINUATION_TABLE_TOP, MARGIN_R, (sheet + 2, continuation_sheets + 1))


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if pymupdf.get_text_length(trial, fontname=font, fontsize=size) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def panel_manufacturer(project: Project, panel) -> str:
    """The brand a panel's sheet names, from that panel's own evidence.

    The BOQ lines the panel is built from name their manufacturer; failing
    that, the DRF's brand for the panel's system; failing that, the one
    brand the DRF names if it names only one. A project whose DRF splits
    the systems between two brands and whose BOQ lines carry none gets no
    brand at all, and the sheet says so -- EP-30175's sheet said COOPER
    over a table of Edwards parts because the first brand on the DRF was
    taken as the panel's.
    """
    counted = Counter(
        (getattr(line, "manufacturer", None) or "").strip().upper()
        for line in getattr(panel, "lines", [])
        if getattr(line, "kind", "load") == "load" and (getattr(line, "manufacturer", None) or "").strip()
    )
    if counted:
        return counted.most_common(1)[0][0]
    rows = system_rules.drf_rows(getattr(panel, "system_code", None), project)
    from_drf = {s.brand.strip().upper() for s in project.systems if s.name in rows and s.brand and s.brand.strip()}
    if len(from_drf) == 1:
        return from_drf.pop()
    every = {s.brand.strip().upper() for s in project.systems if s.brand and s.brand.strip()}
    return every.pop() if len(every) == 1 else ""


# The DRF rows a fire alarm panel's sheet is submitted under: the panel,
# the voice evacuation and the fire telephone the one design sheet quotes.
FAS_FAMILY_ROWS = ("Fire Alarm", "Voice Evacuation", "Fire Telephone")


def battery_systems_title(project: Project, panels) -> str:
    """What the sheet's Submittal line names: the DRF systems the panels
    belong to, not every system the project has."""
    wanted: set[str] = set()
    for panel in panels:
        wanted.update(system_rules.drf_rows(getattr(panel, "system_code", None), project) or FAS_FAMILY_ROWS)
    names = [s.name for s in project.systems if s.name in wanted]
    return ", ".join(names) or "FIRE ALARM SYSTEM"


def battery_calculation_pdf(
    project: Project,
    panels,
    systems: str = "",
    manufacturer: str = "",
    manufacturers: dict[str, str] | None = None,
) -> bytes:
    """One sheet per panel, in the template's layout; a panel with more
    parts than the sheet holds continues on further sheets.

    `manufacturers` names the brand per panel key (see `panel_manufacturer`);
    `manufacturer` is the fallback for a panel not in it. Neither defaults to
    a brand: a sheet with no evidence says the manufacturer is not
    established rather than naming one.
    """
    doc = pymupdf.open()
    for panel in panels:
        brand = (manufacturers or {}).get(getattr(panel, "key", ""), None) or manufacturer
        _panel_page(doc, project, panel, systems or "FIRE ALARM SYSTEM", brand)

    total = doc.page_count
    for index in range(total):
        page = doc[index]
        page.insert_text((MARGIN_R - 60, PAGE[1] - 28), f"{index + 1:02d} / {total:02d}",
                         fontname="helv", fontsize=8, color=LABEL)

    doc.set_metadata({
        "title": f"EP-{project.ep_number} Battery Calculation",
        "author": "Al Arabia for Safety & Security L.L.C",
        "creator": "Engineering Project Platform",
    })
    data = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return data
