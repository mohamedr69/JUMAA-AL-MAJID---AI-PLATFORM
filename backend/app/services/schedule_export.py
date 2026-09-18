r"""The floor-wise BOQ as a document.

The tab is a wide table -- a line per item, a column per floor, and a
real job has forty-three floors -- so the page is landscape and the
floors are split across as many pages as they need, the item column
repeated on each so a reader never loses which line they are on.

One section per system, because a fire alarm BOQ and an emergency
lighting BOQ are two documents to two consultants; a line the platform
could not name follows them, under its own heading, so the pages account
for every row of the schedule.
"""

from __future__ import annotations

import pymupdf

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


def build(project, result: dict) -> pymupdf.Document:
    """The schedule as it is shown, system by system."""
    doc = pymupdf.open()
    floors: list[str] = list(result.get("floors", []))
    items = list(result.get("items", []))
    per_page = _floors_per_page()
    pages = [floors[at: at + per_page] for at in range(0, len(floors), per_page)] or [[]]

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
                heading += f"  ·  {shown[0]} to {shown[-1]}"
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
            for floor in shown:
                page.insert_text((x, y + 12), _clip(floor, FLOOR_WIDTH, 6), fontname="hebo", fontsize=6, color=(1, 1, 1))
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
                for floor in shown:
                    count = (item.get("per_floor") or {}).get(floor)
                    if count:
                        page.insert_text((x, y + 10), str(count), fontname="helv", fontsize=6.5)
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
            for floor in shown:
                total = sum((item.get("per_floor") or {}).get(floor) or 0 for item in rows)
                if total:
                    page.insert_text((x, y + 12), str(total), fontname="hebo", fontsize=6.5)
                x += FLOOR_WIDTH
            page.insert_text((x, y + 12), str(sum(item.get("total") or 0 for item in rows)),
                             fontname="hebo", fontsize=7)
    if doc.page_count == 0:
        page = doc.new_page(width=WIDTH, height=HEIGHT)
        page.insert_text((LEFT, 60), "BOQ FLOOR WISE", fontname="hebo", fontsize=15, color=_RED)
        page.insert_text((LEFT, 84), "No schedule has been read yet.", fontname="helv", fontsize=9, color=_GREY)
    return doc
