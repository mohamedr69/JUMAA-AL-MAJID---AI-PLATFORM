"""Panel battery calculations -> .xlsx.

One sheet per panel laid out like the engineers' own battery workbooks
(currents per module, totals, then Is / Ia / required Ah), with the totals
and the calculation as live formulas so the sheet follows edits made in
Excel. With more than one panel, a Summary sheet comes first.

Only parts that draw current are listed, as on the page; a part whose
current is still unknown is listed with no figures and named in a note, so
the sheet cannot pass for complete.
"""

import re
from collections.abc import Sequence
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas_design import BatteryPanelOut, BatterySetOut

_BOLD = Font(bold=True)
_TITLE = Font(bold=True, size=14)
_HEADER_FILL = PatternFill("solid", fgColor="E8EEF9")
_RESULT_FILL = PatternFill("solid", fgColor="F3F6FB")

COLUMNS = [
    ("Part No.", 16),
    ("Description", 52),
    ("Qty", 7),
    ("Standby (mA/unit)", 16),
    ("Alarm (mA/unit)", 16),
    ("Total standby (mA)", 18),
    ("Total alarm (mA)", 17),
    ("Source", 60),
]
HEADER_ROW = 6


def consuming_lines(panel: BatteryPanelOut):
    """The lines the page and the sheet list: loads that draw current, and
    loads whose current is not known yet."""
    return [
        line
        for line in panel.lines
        if line.kind == "load" and (line.missing_current or (line.standby_ma or 0) > 0 or (line.alarm_ma or 0) > 0)
    ]


def describe_bank(sets: Sequence[BatterySetOut], panel_voltage: float) -> str:
    if not sets:
        return "None quoted"
    parts = [f"{s.units:g} x {s.voltage:g} V, {s.capacity_ah:g} Ah" for s in sets]
    ah = sum(s.strings * s.capacity_ah for s in sets)
    return f"{' + '.join(parts)} (bank {panel_voltage:g} V, {ah:g} Ah)"


def describe_selection(sets: Sequence[BatterySetOut], panel_voltage: float) -> str:
    """"2 x ROCKET ES65-12 (12 V, 65 Ah) -- bank 24 V, 65 Ah"."""
    parts = [f"{s.units:g} x {(s.brand + ' ') if s.brand else ''}{s.part_no} ({s.voltage:g} V, {s.capacity_ah:g} Ah)" for s in sets]
    ah = sum(s.strings * s.capacity_ah for s in sets)
    strings = sum(s.strings for s in sets)
    wiring = "series" if strings == 1 else f"{strings:g} strings in parallel"
    return f"{' + '.join(parts)} -- {wiring}, bank {panel_voltage:g} V, {ah:g} Ah"


def _sheet_title(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", name)[:28] or "Panel"
    title, n = base, 2
    while title.lower() in taken:
        title, n = f"{base} ({n})", n + 1
    taken.add(title.lower())
    return title


def _write_panel(ws: Worksheet, project, panel: BatteryPanelOut, stamp: str) -> None:
    s = panel.settings
    ws["A1"] = f"EP-{project.ep_number}" + (f" — {project.project_name}" if project.project_name else "")
    ws["A1"].font = _TITLE
    ws["A2"] = f"{panel.name} · {panel.heading} — Battery Load Calculation"
    ws["A2"].font = _BOLD
    ws["A3"] = f"Location: {panel.location or 'not set'}"
    ws["A4"] = stamp
    for col, (title, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=HEADER_ROW, column=col, value=title)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
        ws.column_dimensions[get_column_letter(col)].width = width

    row = HEADER_ROW
    lines = consuming_lines(panel)
    for line in lines:
        row += 1
        ws.cell(row=row, column=1, value=line.part_no)
        ws.cell(row=row, column=2, value=line.description)
        ws.cell(row=row, column=3, value=line.quantity)
        if line.missing_current:
            ws.cell(row=row, column=8, value="Current not known yet")
            continue
        ws.cell(row=row, column=4, value=line.standby_ma)
        ws.cell(row=row, column=5, value=line.alarm_ma)
        ws.cell(row=row, column=6, value=f"=C{row}*D{row}")
        ws.cell(row=row, column=7, value=f"=C{row}*E{row}")
        ws.cell(row=row, column=8, value=line.current_source)
    first, last = HEADER_ROW + 1, row
    total = row + 1
    ws.cell(row=total, column=2, value="Total per panel" + (" (at least)" if panel.lower_bound else "")).font = _BOLD
    for col in (6, 7):
        letter = get_column_letter(col)
        ws.cell(row=total, column=col, value=f"=SUM({letter}{first}:{letter}{last})" if lines else 0).font = _BOLD

    r = total + 2
    settings = [
        ("Standby duration (h)", s.get("standby_hours")),
        ("Alarm duration (min)", s.get("alarm_minutes")),
        ("System voltage (V)", s.get("panel_voltage")),
    ]
    cells = {}
    for label, value in settings:
        ws.cell(row=r, column=2, value=label)
        ws.cell(row=r, column=4, value=value)
        cells[label] = f"D{r}"
        r += 1
    r += 1
    calc = [
        ("Is = total standby mA x standby hours (mAh)", f"=F{total}*{cells['Standby duration (h)']}"),
        ("Ia = total alarm mA x alarm minutes / 60 (mAh)", f"=G{total}*{cells['Alarm duration (min)']}/60"),
        ("Sum of Is + Ia (Ah)", f"=(D{r}+D{r + 1})/1000"),
        ("Required capacity (Ah)", f"=D{r + 2}"),
    ]
    for label, formula in calc:
        ws.cell(row=r, column=2, value=label)
        ws.cell(row=r, column=4, value=formula)
        r += 1
    required = f"D{r - 1}"
    ws.cell(row=r - 1, column=2).font = _BOLD
    ws.cell(row=r - 1, column=4).font = _BOLD

    r += 1
    volts = s.get("panel_voltage", 24)
    ws.cell(row=r, column=2, value="Selected battery").font = _BOLD
    ws.cell(row=r, column=4, value=describe_selection(panel.selected, volts) if panel.selected else "Not selected").font = _BOLD
    r += 1
    ws.cell(row=r, column=2, value="Battery bank capacity (Ah)")
    ws.cell(row=r, column=4, value=panel.selected_ah)
    r += 1
    ws.cell(row=r, column=2, value="Result").font = _BOLD
    result = ws.cell(
        row=r,
        column=4,
        value=f'=IF(D{r - 1}="","No battery selected",IF(D{r - 1}>={required},"Capacity sufficient","Capacity insufficient"))'
        if not panel.lower_bound
        else "Incomplete: currents missing",
    )
    result.font = _BOLD
    result.fill = _RESULT_FILL
    r += 2
    ws.cell(row=r, column=2, value="Battery quoted in the BOQ")
    ws.cell(row=r, column=4, value=describe_bank(panel.quoted, volts))
    if panel.quoted_short:
        ws.cell(row=r, column=5, value="Smaller than required: update the BOQ").font = Font(bold=True, color="B91C1C")
    if panel.lower_bound:
        r += 2
        ws.cell(row=r, column=2, value="Currents not known yet for: " + ", ".join(panel.missing_parts)
                + ". The load above is a lower bound.")


def _write_summary(ws: Worksheet, project, panels: Sequence[BatteryPanelOut], stamp: str) -> None:
    ws["A1"] = f"EP-{project.ep_number}" + (f" — {project.project_name}" if project.project_name else "")
    ws["A1"].font = _TITLE
    ws["A2"] = "Battery Calculations — Summary"
    ws["A2"].font = _BOLD
    ws["A3"] = stamp
    headers = [("Panel", 12), ("Type", 40), ("Location", 26), ("Standby (mA)", 13), ("Alarm (mA)", 12),
               ("Required (Ah)", 14), ("Selected battery", 60), ("Status", 18), ("BOQ battery", 40)]
    for col, (title, width) in enumerate(headers, start=1):
        cell = ws.cell(row=5, column=col, value=title)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
        ws.column_dimensions[get_column_letter(col)].width = width
    status = {"ok": "Calculated", "incomplete": "Needs input", "no_selection": "No battery selected"}
    for i, panel in enumerate(panels, start=6):
        prefix = ">= " if panel.lower_bound else ""
        volts = panel.settings.get("panel_voltage", 24)
        boq = describe_bank(panel.quoted, volts) + (" -- smaller than required" if panel.quoted_short else "")
        values = [panel.name, panel.heading, panel.location, panel.standby_ma, panel.alarm_ma,
                  f"{prefix}{panel.required_ah:.2f}" if panel.lower_bound else round(panel.required_ah, 2),
                  describe_selection(panel.selected, volts) if panel.selected else "—", status[panel.status], boq]
        for col, value in enumerate(values, start=1):
            ws.cell(row=i, column=col, value=value)


def battery_workbook(project, panels: Sequence[BatteryPanelOut], *, exported_by: str, exported_at: datetime) -> bytes:
    stamp = f"Exported {exported_at:%d %b %Y %H:%M} UTC by {exported_by}"
    workbook = Workbook()
    taken: set[str] = set()
    first = workbook.active
    if len(panels) > 1:
        first.title = "Summary"
        _write_summary(first, project, panels, stamp)
        sheets = [workbook.create_sheet(_sheet_title(p.name, taken)) for p in panels]
    else:
        first.title = _sheet_title(panels[0].name, taken)
        sheets = [first]
    for ws, panel in zip(sheets, panels):
        _write_panel(ws, project, panel, stamp)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
