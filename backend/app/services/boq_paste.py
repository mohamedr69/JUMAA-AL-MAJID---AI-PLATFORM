"""A BOQ pasted out of a spreadsheet, read into lines.

A project whose BOQ never came as a Design Sheet still has one: it is in
the engineer's spreadsheet, and until now the only way in was the Add
Item button, a blank line at a time. This reads the block they copied.

**Nothing here is saved.** The lines come back for the engineer to look
over on the BOQ page and save with everything else, so a bad paste costs
a glance rather than a revision.

Two things make a paste more than a split on tabs:

* **Group headings.** A BOQ is headings with lines under them --
  "MAIN FIRE ALARM CONTROL PANEL & ACCESSORIES" and then the panel's
  parts. In a spreadsheet the heading is a row with words and no
  quantity, so it is read as a heading and carried down the lines that
  follow it rather than becoming a line of its own.
* **Quantities.** "1,250" is 1250 and "10 Nos" is 10 with the unit Nos,
  read by `extraction.values.parse_quantity` -- the same parser the
  extractor and the save path use, so a pasted quantity and an extracted
  one mean the same thing.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.extraction import values

# What a column of a BOQ is called, in the spellings the engineers'
# spreadsheets use. Checked longest first so "unit price" is a price and
# not a unit.
HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("total_price", ("total price", "total amount", "amount", "total")),
    ("unit_price", ("unit price", "unit rate", "rate", "price")),
    ("catalog_no", ("model / part no", "model/part no", "part no", "part number", "catalogue no",
                    "catalog no", "model no", "model", "part", "cat no", "item code", "code")),
    ("manufacturer", ("manufacturer", "make", "brand", "supplier")),
    ("description", ("description", "item description", "particulars", "material", "item")),
    ("quantity", ("quantity", "qty", "qty.", "nos", "no.")),
    ("unit", ("unit", "uom", "u/m")),
    ("remarks", ("remarks", "remark", "notes", "note", "comment")),
)
# A column of running numbers down the left of every BOQ, which is not a field.
SERIAL = ("sn", "s/n", "s.n", "sl", "sl no", "sr", "sr no", "item no", "#", "no")
FIELDS = tuple(name for name, _ in HEADINGS)


@dataclass
class PastedLine:
    """One line as it was read, and what it will be."""

    description: str
    group_heading: str | None = None
    manufacturer: str | None = None
    catalog_no: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    total_price: str | None = None
    remarks: str | None = None
    # Why a line is worth a second look before it is saved.
    problems: list[str] = field(default_factory=list)


@dataclass
class PastedBoq:
    lines: list[PastedLine]
    headings: list[str]
    columns: dict[str, int]
    # The rows that became headings rather than lines, and the ones dropped.
    heading_rows: int = 0
    skipped_rows: int = 0
    header_row: bool = False


def _delimiter(text: str) -> str:
    """Tabs when the paste came from a spreadsheet, which it usually has;
    a comma when it came from a CSV file instead."""
    head = text.splitlines()[:20]
    tabs = sum(line.count("\t") for line in head)
    commas = sum(line.count(",") for line in head)
    if tabs:
        return "\t"
    return "," if commas else "\t"


def grid(text: str) -> list[list[str]]:
    """The pasted block as rows of cells.

    Read with the csv module rather than split: a description carrying a
    comma, or a cell Excel wrapped in quotes because it holds a line
    break, is one cell and not three.
    """
    if not (text or "").strip():
        return []
    rows = [[cell.strip() for cell in row]
            for row in csv.reader(io.StringIO(text), delimiter=_delimiter(text))]
    return [row for row in rows if any(cell for cell in row)]


def _named(cell: str) -> str | None:
    """The field a header cell names, if it names one."""
    plain = re.sub(r"[^a-z0-9/. ]+", " ", (cell or "").lower())
    plain = " ".join(plain.split())
    if not plain or plain in SERIAL:
        return None
    for name, spellings in HEADINGS:
        if plain in spellings:
            return name
    for name, spellings in HEADINGS:
        if any(spelling in plain for spelling in spellings):
            return name
    return None


def header(row: list[str]) -> dict[str, int] | None:
    """The columns a header row names, or None when this is not one.

    Two named columns is the test: a line of the BOQ itself can hold a
    word that reads like a heading ("Unit" as a description), but not two
    of them across the row.
    """
    found: dict[str, int] = {}
    for index, cell in enumerate(row):
        name = _named(cell)
        if name is not None and name not in found:
            found[name] = index
    return found if len(found) >= 2 else None


def _by_shape(rows: list[list[str]]) -> dict[str, int]:
    """Where there is no header row, the columns are guessed from the
    lines themselves: the one that holds numbers is the quantity, the
    widest is the description, and a short coded column is the part
    number."""
    width = max((len(row) for row in rows), default=0)
    if not width:
        return {}
    columns = [[row[i] if i < len(row) else "" for row in rows] for i in range(width)]

    def numeric(cells: list[str]) -> float:
        filled = [c for c in cells if c]
        if not filled:
            return 0.0
        return sum(1 for c in filled if values.parse_quantity(c).ok) / len(filled)

    def length(cells: list[str]) -> float:
        filled = [c for c in cells if c]
        return sum(len(c) for c in filled) / len(filled) if filled else 0.0

    def counting(cells: list[str]) -> bool:
        """A column of running numbers -- 1, 2, 3 down the page -- which is
        the serial every BOQ carries at its left and not a quantity."""
        numbers = []
        for cell in cells:
            if not cell:
                continue
            if not re.fullmatch(r"\d{1,4}", cell):
                return False
            numbers.append(int(cell))
        return len(numbers) >= 3 and all(b - a == 1 for a, b in zip(numbers, numbers[1:]))

    found: dict[str, int] = {}
    described = max(range(width), key=lambda i: length(columns[i]))
    found["description"] = described
    serial = next((i for i in range(min(2, width)) if counting(columns[i])), None)
    quantities = [i for i in range(width)
                  if i not in (described, serial) and numeric(columns[i]) >= 0.6]
    if quantities:
        # The first such column: a BOQ puts the quantity before its rate.
        found["quantity"] = quantities[0]
    coded = [i for i in range(width)
             if i not in found.values() and i != serial and 0 < length(columns[i]) <= 18
             and any(re.search(r"\d", c) for c in columns[i] if c)]
    if coded:
        found["catalog_no"] = coded[0]
    return found


def price(raw: str | None) -> Decimal | None:
    """A rate as a spreadsheet writes it: "1,250.00", "AED 1,250", "1 250".

    None when the cell holds no number at all -- a dash, "included", a
    blank -- because a BOQ writes those where a line carries no rate, and
    they are not worth flagging.
    """
    plain = re.sub(r"[^0-9.,-]", "", raw or "")
    plain = plain.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", plain or ""):
        return None
    try:
        found = Decimal(plain)
    except InvalidOperation:
        return None
    return found if found >= 0 else None


def _cell(row: list[str], columns: dict[str, int], name: str) -> str | None:
    index = columns.get(name)
    if index is None or index >= len(row):
        return None
    return row[index].strip() or None


def read(text: str, *, columns: dict[str, int] | None = None) -> PastedBoq:
    """The pasted block as BOQ lines, with the headings carried down.

    `columns` maps a field to the column holding it, for a paste whose own
    header the engineer corrected. Left out, the header row is read where
    there is one and the shape of the lines used where there is not.
    """
    rows = grid(text)
    if not rows:
        return PastedBoq(lines=[], headings=[], columns={})

    header_row = False
    if columns:
        found = {name: index for name, index in columns.items() if name in FIELDS}
        # A header the engineer has already mapped is still a header, not a line.
        header_row = header(rows[0]) is not None
    else:
        found = header(rows[0]) or {}
        header_row = bool(found)
        if not found:
            found = _by_shape(rows)
    body = rows[1:] if header_row else rows

    lines: list[PastedLine] = []
    headings: list[str] = []
    heading = None
    heading_rows = skipped = 0
    for row in body:
        description = _cell(row, found, "description")
        quantity = _cell(row, found, "quantity")
        catalog = _cell(row, found, "catalog_no")
        if not description and not catalog:
            # Words somewhere on the row but nothing in the columns that
            # matter: a spacer, a page total, a note down the margin.
            skipped += 1
            continue
        # A heading is words with nothing quoted against them.
        if description and not quantity and not catalog and not _cell(row, found, "unit_price"):
            heading = description
            headings.append(description)
            heading_rows += 1
            continue
        line = PastedLine(
            description=description or catalog or "",
            group_heading=heading,
            manufacturer=_cell(row, found, "manufacturer"),
            catalog_no=catalog,
            quantity=quantity,
            unit=_cell(row, found, "unit"),
            unit_price=_cell(row, found, "unit_price"),
            total_price=_cell(row, found, "total_price"),
            remarks=_cell(row, found, "remarks"),
        )
        if quantity:
            parsed = values.parse_quantity(quantity)
            if parsed.status == values.EMPTY:
                line.quantity = None
            elif parsed.ok:
                line.quantity = parsed.text()
                if parsed.unit and not line.unit:
                    line.unit = parsed.unit
            else:
                # Kept as it was pasted, and flagged: the engineer can see
                # what the spreadsheet said and correct it on the page.
                line.problems.append(f"Quantity {quantity!r} is not a quantity: {parsed.rule}")
        else:
            line.problems.append("No quantity")
        if not line.description:
            line.problems.append("No description")
        lines.append(line)

    return PastedBoq(lines=lines, headings=headings, columns=found,
                     heading_rows=heading_rows, skipped_rows=skipped, header_row=header_row)
