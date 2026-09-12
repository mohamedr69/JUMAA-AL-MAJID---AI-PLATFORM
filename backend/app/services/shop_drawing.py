"""Shop drawing title blocks, read from the sheet itself.

A drawing enters the log only when its own title block says what it is: the
drawing number, the title, the revision and the date it was issued. A PDF that
happens to sit in a drawings folder - a lux report, a reply to comments, a
sub-contractor's slab layout - carries no title block for our systems and is
not a drawing of ours to log.
"""
from dataclasses import dataclass
from datetime import date
import re

import pymupdf

# Title blocks are drawn rotated on the sheet, so every comparison here runs in
# display space, where the block reads left to right like the printed drawing.
LABEL_ROW = ["SCALE", "DRAWN", "CHECKED", "DATE", "SIZE", "REV. NO."]
DATE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")
NUMBER = re.compile(r"^[A-Z0-9][A-Z0-9./-]{4,}$", re.I)
# The ISO 19650 breakdown labels share the cell the number is written in.
NOT_A_NUMBER = re.compile(r"PROJECT|ORIGINATOR|DISCIP|ACTIVITY|ZONE|LEVEL|SERIAL|NUMBER|CODE|TYPE|SCALE|DRAWN|CHECKED|SIZE|REV", re.I)
# A drawing office types the number with whatever dash the font offers.
DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
LAYOUT = re.compile(r"\b(LAYOUT|PLAN|RISER|SCHEMATIC|DIAGRAM|DETAIL|SECTION)S?\b", re.I)

SYSTEM_OF_LAYOUT = [
    ("EML", r"emergency\s*light|self[ -]*contained"),
    ("FAS", r"fire\s*alarm|voice\s*evacuation|fire\s*telephone|fire\s*detection"),
]


@dataclass(frozen=True)
class TitleBlock:
    number: str
    title: str
    layout: str | None
    floor: str | None
    revision: str
    revision_source: str
    issued: date | None
    purpose: str | None
    note: str | None = None


def words_display(page) -> list[tuple[float, float, float, float, str]]:
    """Words with the sheet the right way up, whatever the page rotation."""
    matrix = page.rotation_matrix
    found = []
    for word in page.get_text("words"):
        if not word[4].strip():
            continue
        box = pymupdf.Rect(word[:4]) * matrix
        found.append((box.x0, box.y0, box.x1, box.y1, word[4]))
    return found


def rows(words, tol: float = 6) -> list[list]:
    grouped: dict[int, list] = {}
    for word in words:
        grouped.setdefault(round((word[1] + word[3]) / 2 / tol), []).append(word)
    return [sorted(row, key=lambda word: word[0]) for _, row in sorted(grouped.items())]


def find_phrase(words, phrase: str):
    wanted = [part.rstrip(":.").upper() for part in phrase.split()]
    for row in rows(words):
        for start in range(len(row) - len(wanted) + 1):
            span = row[start:start + len(wanted)]
            if [word[4].rstrip(":.").upper() for word in span] == wanted:
                return (min(w[0] for w in span), min(w[1] for w in span), max(w[2] for w in span), max(w[3] for w in span))
    return None


def _centre(box) -> float:
    return (box[0] + box[2]) / 2


def _parse_date(value: str) -> date | None:
    match = DATE.match(value.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def revision_table(words) -> list[tuple[int, date | None, str]]:
    """The Rev | Date | Description history a draughtsman keeps on the sheet."""
    header = find_phrase(words, "Rev Date Description")
    if header is None:
        return []
    band = [w for w in words if header[3] - 2 < w[1] < header[3] + 200 and header[0] - 20 < w[0] < header[2] + 700]
    history = []
    for row in rows(band):
        text = [word[4] for word in row]
        if len(text) > 1 and re.fullmatch(r"\d{1,3}", text[0]) and DATE.match(text[1]):
            history.append((int(text[0]), _parse_date(text[1]), " ".join(text[2:]).strip()))
    return sorted(history)


def floor_of(title: str) -> str | None:
    """The floor a sheet covers, as the title states it - ranges included."""
    head = LAYOUT.split(title.upper())[0].strip(" -,")
    head = re.sub(r"\s+", " ", head)
    if not head or len(head) > 60:
        return None
    return head or None


def read_title_block(page) -> TitleBlock | None:
    words = words_display(page)
    title_label = find_phrase(words, "DRAWING TITLE")
    if title_label is None:
        return None
    # Scale, drawn, checked, date, size and revision share one row under the
    # title. Anchoring on the revision box keeps a label of the same name
    # elsewhere on the sheet - a revision table header, say - out of the block.
    rev_label = find_phrase(words, "REV. NO.")
    if rev_label is None or rev_label[1] < title_label[1] - 400:
        return None
    middle = (rev_label[1] + rev_label[3]) / 2
    field_row = [word for word in words if abs((word[1] + word[3]) / 2 - middle) < 8]
    labels = {}
    for name in LABEL_ROW:
        box = find_phrase(field_row, name)
        if box:
            labels[name] = box
    block = [w for w in words if title_label[0] - 400 < w[0] < title_label[0] + 500 and title_label[1] - 200 < w[1] < title_label[1] + 400]

    # Title: the lines under the label, down to the next row of labels.
    stop = min((box[1] for box in labels.values() if box[1] > title_label[3]), default=title_label[3] + 90)
    # The title cell starts at its label; a sheet number printed to the left
    # of the cell belongs to another box.
    band = [w for w in block if w[1] > title_label[3] - 2 and w[3] <= stop + 2 and w[0] > title_label[0] - 20]
    lines = [" ".join(word[4] for word in row).strip() for row in rows(band)]
    # A sheet count printed beside the title is not part of the title.
    lines = [line for line in lines if line and not re.fullmatch(r"\d{1,3}", line)]
    if not lines:
        return None
    title = " ".join(lines)
    layout = next((line for line in lines if LAYOUT.search(line)), None)
    if layout == lines[0] and len(lines) > 1:
        layout = lines[-1]

    # Field row: each value belongs to the label its centre sits under.
    top = min(box[3] for box in labels.values())
    label_words = {word for box in labels.values() for word in block if box[0] - 2 <= word[0] and word[2] <= box[2] + 2 and box[1] - 2 <= word[1] <= box[3] + 2}
    field_band = [w for w in block if top - 3 < w[1] < top + 40 and w not in label_words]
    fields: dict[str, str] = {}
    if field_band:
        for word in rows(field_band)[0]:
            name = min(labels, key=lambda key: abs(_centre(labels[key]) - _centre(word)))
            fields[name] = (fields.get(name, "") + " " + word[4]).strip()

    # Drawing number: the bottom line of the block, under the ISO 19650 split.
    below = [w for w in block if w[1] > top + 40]
    number = None
    for row in reversed(rows(below)):
        text = " ".join(word[4] for word in row).translate(DASHES)
        text = re.sub(r"\s*-\s*", "-", text).strip()
        if NOT_A_NUMBER.search(text) or LAYOUT.search(text) or not re.search(r"\d", text):
            continue
        if NUMBER.match(text):
            number = text
            break
    if number is None:
        return None

    history = revision_table(words)
    box_revision = re.sub(r"\D", "", fields.get("REV. NO.", ""))
    issued = _parse_date(fields.get("DATE", ""))
    note = None
    if history:
        revision, table_date, _ = history[-1]
        # The sheet is issued at the last revision its own history records; a
        # revision box left behind is reported, never quietly followed.
        if box_revision and int(box_revision) != revision:
            note = f"Revision box reads {int(box_revision):02d} while the revision table ends at {revision:02d}."
        source = "revision table"
        issued = table_date or issued
    elif box_revision:
        revision, source = int(box_revision), "revision box"
    else:
        return None
    return TitleBlock(
        number=number,
        title=re.sub(r"\s+", " ", title).strip(),
        layout=layout,
        floor=floor_of(title),
        revision=f"R{revision}",
        revision_source=source,
        issued=issued,
        purpose=fields.get("Purpose of Issue"),
        note=note,
    )


def system_of(block: TitleBlock) -> str | None:
    """Which of our systems the sheet draws, by what its title says it shows."""
    for text in (block.layout, block.title):
        if not text:
            continue
        for code, pattern in SYSTEM_OF_LAYOUT:
            if re.search(pattern, text, re.I):
                return code
    return None


def normalize_floor(value: str) -> str:
    value = value.upper().strip()
    value = re.sub(r"\bBASEMENT[ -]*(\d+)", r"B\1", value)
    value = re.sub(r"\bPODIUM[ -]*(\d+)", r"P\1", value)
    value = re.sub(r"\b(\d+)(?:ST|ND|RD|TH)\s+FLOOR\b", r"L\1", value)
    value = re.sub(r"\bLEVEL[ -]*(\d+)", r"L\1", value)
    value = value.replace("GROUND FLOOR", "GF").replace("TOP ROOF", "TOR").replace("ROOF FLOOR", "RF")
    value = re.sub(r"\bRES\s*\d+(?:\s*(?:TO|&|AND)\s*\d+)?\b", "", value)
    value = re.sub(r"[(),.]", " ", value)
    return re.sub(r"[\s-]+", "", value)
