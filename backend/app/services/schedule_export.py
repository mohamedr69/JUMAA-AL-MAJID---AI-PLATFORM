r"""The floor-wise BOQ as a document.

The tab is a wide table -- a line per item, a column per floor as the
workbook has them (a typical column, "1 to 13", once, with the quantity
on each of its floors), and a real job has many -- so the page is
landscape and the columns are split across as many pages as they need,
the item column repeated on each so a reader never loses which line they
are on. The totals count every floor a typical column stands for.

One section per system, because a fire alarm BOQ and an emergency
lighting BOQ are two documents to two consultants; a line the platform
could not name follows them, under its own heading, so the pages account
for every row of the schedule.
"""

from __future__ import annotations

import pymupdf

from app.services.floor_schedule import sheet_columns, tidy

_RED = (0.72, 0.11, 0.11)
_GREY = (0.45, 0.45, 0.45)
_RULE = (0.85, 0.85, 0.85)

WIDTH, HEIGHT = 841.92, 595.32        # A4 landscape, as the material schedule uses
LEFT, RIGHT = 30, WIDTH - 30
ITEM_WIDTH, DEVICE_WIDTH, PART_WIDTH = 150, 96, 80
FLOOR_WIDTH, TOTAL_WIDTH = 34, 42
ROW_HEIGHT, BOTTOM = 15, HEIGHT - 40

SYSTEM_NAMES = {"FAS": "Fire alarm", "ELS": "Emergency lighting", "": "Not recognised"}


def _floors_per_page() -> int:
    room = RIGHT - LEFT - ITEM_WIDTH - DEVICE_WIDTH - PART_WIDTH - TOTAL_WIDTH
    return max(1, int(room // FLOOR_WIDTH))


def _clip(text: str, width: float, size: float = 6.5) -> str:
    """As much of a name as fits the column."""
    room = max(1, int(width / (size * 0.52)))
    text = (text or "").strip()
    return text if len(text) <= room else text[: room - 1] + "…"


def cell(per_floor: dict, floors: list[str]) -> str:
    """What one column shows for a line: its quantity on a floor, and for a
    typical column the quantity on each of its floors -- or the range, where
    floors of it were changed by hand and no longer agree."""
    values = {tidy(per_floor.get(floor) or 0) for floor in floors}
    if values == {0}:
        return ""
    shown = str(values.pop()) if len(values) == 1 else f"{min(values)}-{max(values)}"
    # "3 x14": three on each of the typical column's fourteen floors.
    return f"{shown} x{len(floors)}" if len(floors) > 1 else shown


def build(project, result: dict) -> pymupdf.Document:
    """The schedule as it is shown, system by system."""
    doc = pymupdf.open()
    columns = sheet_columns(result)
    items = list(result.get("items", []))
    per_page = _floors_per_page()
    pages = [columns[at: at + per_page] for at in range(0, len(columns), per_page)] or [[]]

    systems = sorted(
        {(item.get("system") or "") for item in items},
        key=lambda code: (code == "", code != "FAS", code),
    )
    title = f"EP-{project.ep_number} — {project.project_name or ''}".strip(" —")

    for system in systems:
        rows = [item for item in items if (item.get("system") or "") == system]
        if not rows:
            continue
        for shown in pages:
            page = doc.new_page(width=WIDTH, height=HEIGHT)
            page.insert_text((LEFT, 40), "BOQ FLOOR WISE", fontname="hebo", fontsize=15, color=_RED)
            page.insert_text((LEFT, 56), title.upper()[:110], fontname="helv", fontsize=8, color=_GREY)
            heading = SYSTEM_NAMES.get(system, system)
            if len(pages) > 1 and shown:
                heading += f"  ·  {shown[0]['heading']} to {shown[-1]['heading']}"
            page.insert_text((LEFT, 70), heading, fontname="hebo", fontsize=8.5, color=_GREY)

            y = 82
            page.draw_rect(pymupdf.Rect(LEFT, y, RIGHT, y + 18), color=None, fill=_RED)
            x = LEFT + 3
            page.insert_text((x, y + 12), "ITEM", fontname="hebo", fontsize=7, color=(1, 1, 1))
            x += ITEM_WIDTH
            page.insert_text((x, y + 12), "DEVICE", fontname="hebo", fontsize=7, color=(1, 1, 1))
            x += DEVICE_WIDTH
            page.insert_text((x, y + 12), "PART", fontname="hebo", fontsize=7, color=(1, 1, 1))
            x += PART_WIDTH
            for column in shown:
                if len(column["floors"]) > 1:
                    # A typical column: its heading, and under it how many floors it stands for.
                    page.insert_text((x, y + 8), _clip(column["heading"], FLOOR_WIDTH, 6), fontname="hebo", fontsize=6,
                                     color=(1, 1, 1))
                    page.insert_text((x, y + 15.5), f"x{len(column['floors'])} floors", fontname="helv", fontsize=5,
                                     color=(1, 1, 1))
                else:
                    page.insert_text((x, y + 12), _clip(column["heading"], FLOOR_WIDTH, 6), fontname="hebo", fontsize=6,
                                     color=(1, 1, 1))
                x += FLOOR_WIDTH
            page.insert_text((x, y + 12), "TOTAL", fontname="hebo", fontsize=7, color=(1, 1, 1))
            y += 18

            for item in rows:
                if y + ROW_HEIGHT > BOTTOM:
                    page = doc.new_page(width=WIDTH, height=HEIGHT)
                    y = 40
                x = LEFT + 3
                page.insert_text((x, y + 10), _clip(item.get("description", ""), ITEM_WIDTH, 7),
                                 fontname="helv", fontsize=7)
                x += ITEM_WIDTH
                page.insert_text((x, y + 10), _clip(item.get("device") or "—", DEVICE_WIDTH),
                                 fontname="helv", fontsize=6.5, color=_GREY)
                x += DEVICE_WIDTH
                part = (item.get("material") or {}).get("part_no") or "—"
                page.insert_text((x, y + 10), _clip(part, PART_WIDTH), fontname="helv", fontsize=6.5)
                x += PART_WIDTH
                for column in shown:
                    shown_count = cell(item.get("per_floor") or {}, column["floors"])
                    if shown_count:
                        page.insert_text((x, y + 10), shown_count, fontname="helv", fontsize=6.5)
                    x += FLOOR_WIDTH
                page.insert_text((x, y + 10), str(item.get("total") or 0), fontname="hebo", fontsize=6.5)
                page.draw_line(pymupdf.Point(LEFT, y + ROW_HEIGHT - 2), pymupdf.Point(RIGHT, y + ROW_HEIGHT - 2),
                               color=_RULE, width=0.4)
                y += ROW_HEIGHT

            # What this system comes to, on the floors this page shows.
            x = LEFT + 3
            page.draw_line(pymupdf.Point(LEFT, y), pymupdf.Point(RIGHT, y), color=_RED, width=0.8)
            page.insert_text((x, y + 12), f"{SYSTEM_NAMES.get(system, system)} total",
                             fontname="hebo", fontsize=7)
            x += ITEM_WIDTH + DEVICE_WIDTH + PART_WIDTH
            for column in shown:
                # Per floor, like the column's cells: the column's floors each carry it.
                totals = {floor: sum((item.get("per_floor") or {}).get(floor) or 0 for item in rows)
                          for floor in column["floors"]}
                shown_total = cell(totals, column["floors"])
                if shown_total:
                    page.insert_text((x, y + 12), shown_total, fontname="hebo", fontsize=6.5)
                x += FLOOR_WIDTH
            page.insert_text((x, y + 12), str(sum(item.get("total") or 0 for item in rows)),
                             fontname="hebo", fontsize=7)
    if doc.page_count == 0:
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.insert_text((LEFT, 60), "BOQ FLOOR WISE", fontname="hebo", fontsize=15, color=_RED)
        page.insert_text((LEFT, 84), "No schedule has been read yet.", fontname="helv", fontsize=9, color=_GREY)
    return doc
