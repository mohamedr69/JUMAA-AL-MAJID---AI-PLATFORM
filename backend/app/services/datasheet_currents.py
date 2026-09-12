"""Read a part's standby and alarm current off its datasheet.

Datasheet current tables are laid out many ways, so a value is placed by
geometry rather than by text order:

- its **label** is the nearest "Standby" / "Alarm" / "Active" / "Current"
  word to its left on the same line, or failing that the nearest one above
  it on the same half of the page ("Current: Standby | 175 mA at 16 VDC"
  continues onto the lines below);
- its **model** is, in order: a model named right after it ("85mA for the
  3-ZA95"); else the nearest *heading* above it that names models -- a
  column header row ("3-ZA20A | 3-ZA20B | ..."), where the value belongs to
  the nearest column, or to every column when it is the only value on its
  row (one figure spanning the table); or a section title on the same half
  of the page ("4-FT Firefighter Telephone Master Handset"); else, in a
  datasheet named for the part, the part itself. Headings are told from
  row labels that merely start with a model ("SIGA-CC1/2 Support") by their
  type: bold, medium or larger than body text.

The part's values are those owned by the part, else by its family ("4-FWAL"
for 4-FWAL4, "4-LCD" for 4-LCDLE), else unowned values in its own datasheet.
Where more than one figure applies the **worst case** is taken -- the
platform owner's rule, since a larger figure can only oversize a battery --
with these refinements:

- figures per supply voltage ("130 mA at 24 VDC") are taken at the panel
  voltage;
- a per-unit figure ("0.23 mA/Indicator ON") is added to the base for every
  unit, when the datasheet has a row counting them ("Indicators | 24"),
  and is otherwise dropped in favour of an explicit maximum ("560 mA for a
  fully loaded 4-USBHUB");
- an alarm figure that is only a pointer to another module ("Alarm: See
  the 4-COMREL Common Relay Module") takes the standby figure -- the rest is
  that module's own line in the BOQ.

Anything that cannot be read this way is reported, never guessed.
"""

import re
from dataclasses import dataclass, field

import pymupdf

from app.services.battery_calculation import part_key

ROW_TOLERANCE_PT = 3.0
CELL_GAP_PT = 18.0
LABEL_SAME_LINE_PT = 8.0
LABEL_ABOVE_PT = 45.0
POINTER_BELOW_PT = 15.0
# Body text in these datasheets is 7-9 pt; headings are bold/medium or bigger.
HEADING_MIN_SIZE = 9.5
_HEADING_FONT_RE = re.compile(r"bd|bold|md|medium|heavy|black|semibold", re.IGNORECASE)

_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)(m\s?A)?(/\S*)?[;,.:]?$", re.IGNORECASE)
_MA_RE = re.compile(r"^m\s?A(/\S*)?[;,.:]?$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^\(?(\d-[A-Z0-9]+(?:[-/][A-Z0-9]+)*|[A-Z]{2,}-[A-Z0-9]+(?:[-/][A-Z0-9]+)*)-?[,;:.)]?$")
_VOLTS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*v(?:dc)?\b", re.IGNORECASE)
_PER_UNIT_RE = re.compile(r"^per\s+(\w+)", re.IGNORECASE)


def model_of(token: str) -> str | None:
    match = _MODEL_RE.match(token.upper())
    return part_key(match.group(1)) if match else None


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    row: int = -1
    cell: int = -1

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class _Heading:
    y: float
    # (x centre, x0, model) for each model the heading row names.
    models: list[tuple[float, float, str]]


@dataclass
class _Value:
    ma: float
    word: _Word
    trailing: str
    page: int
    label: str | None = None
    owners: set[str] = field(default_factory=set)
    per_unit: str | None = None
    volts: float | None = None


@dataclass
class CurrentReading:
    standby_ma: float
    alarm_ma: float
    pages: list[int]
    notes: list[str]


def _label_kind(text: str, previous: str | None) -> str | None:
    text = text.lower().strip(":;,")
    if "standby" in text and ("alarm" in text or "active" in text):
        return "both"
    if "standby" in text:
        return "standby"
    if "alarm" in text or "active" in text:
        return "alarm"
    if text == "current":
        # "Standby Current" / "Alarm Current": the word before decides.
        before = _label_kind(previous, None) if previous and previous.lower().strip(":;,") != "current" else None
        return before or "both"
    return None


class _Page:
    def __init__(self, page: pymupdf.Page, number: int):
        self.number = number
        self.mid = page.rect.width / 2

        rows: list[list[_Word]] = []
        for w in sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0])):
            word = _Word(w[0], w[1], w[2], w[3], w[4])
            if rows and abs(rows[-1][0].yc - word.yc) <= ROW_TOLERANCE_PT:
                rows[-1].append(word)
            else:
                rows.append([word])
        self.words: list[_Word] = []
        self.cells: list[list[list[_Word]]] = []
        for r, members in enumerate(rows):
            members.sort(key=lambda w: w.x0)
            cells: list[list[_Word]] = []
            previous = None
            for word in members:
                if previous is None or word.x0 - previous.x1 > CELL_GAP_PT:
                    cells.append([])
                word.row, word.cell = r, len(cells) - 1
                cells[-1].append(word)
                previous = word
            self.cells.append(cells)
            self.words.extend(members)

        self.headings = self._headings(page)

    @staticmethod
    def _headings(page: pymupdf.Page) -> list[_Heading]:
        spans = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    styled = bool(span["flags"] & 16) or _HEADING_FONT_RE.search(span["font"]) or span["size"] >= HEADING_MIN_SIZE
                    model = model_of(text.split()[0])
                    if styled and model:
                        x0, y0, x1, y1 = span["bbox"]
                        spans.append(((y0 + y1) / 2, (x0 + x1) / 2, x0, model))
        headings: list[_Heading] = []
        for y, xc, x0, model in sorted(spans):
            if headings and abs(headings[-1].y - y) <= ROW_TOLERANCE_PT:
                headings[-1].models.append((xc, x0, model))
            else:
                headings.append(_Heading(y, [(xc, x0, model)]))
        return headings

    def values(self) -> list[_Value]:
        found = []
        for cells in self.cells:
            for cell in cells:
                for i, word in enumerate(cell):
                    match = _NUMBER_RE.match(word.text)
                    if not match:
                        continue
                    per = match.group(3)
                    rest = cell[i + 1 :]
                    if not match.group(2):
                        unit = _MA_RE.match(rest[0].text) if rest else None
                        if not unit:
                            continue
                        per, rest = unit.group(1), rest[1:]
                    trailing = " ".join(w.text for w in rest).split(";")[0]
                    value = _Value(ma=float(match.group(1)), word=word, trailing=trailing, page=self.number)
                    per_word = _PER_UNIT_RE.match(trailing)
                    if per:
                        value.per_unit = per.strip("/") or "unit"
                    elif per_word:
                        value.per_unit = per_word.group(1)
                    # The voltage a figure is given at: "at 24 VDC", "@ 24VDC".
                    head = re.split(r"\d+(?:\.\d+)?\s*m\s?A", trailing)[0]
                    volts = _VOLTS_RE.search(head)
                    value.volts = float(volts.group(1)) if volts else None
                    found.append(value)
        return found

    def label_for(self, value: _Value) -> str | None:
        same_line, above = None, None
        for i, word in enumerate(self.words):
            if word.x0 > value.word.x0:
                continue
            previous = self.words[i - 1].text if i > 0 and self.words[i - 1].row == word.row else None
            kind = _label_kind(word.text, previous)
            if not kind:
                continue
            if abs(word.yc - value.word.yc) <= LABEL_SAME_LINE_PT:
                if same_line is None or word.x1 > same_line[0]:
                    same_line = (word.x1, kind)
            elif 0 < value.word.yc - word.yc <= LABEL_ABOVE_PT and (word.x0 < self.mid) == (value.word.x0 < self.mid):
                if above is None or word.yc > above[0]:
                    above = (word.yc, kind)
        if same_line:
            return same_line[1]
        return above[1] if above else None

    def owners_of(self, value: _Value, row_values: list[_Value]) -> set[str]:
        named = {m for token in value.trailing.split() if (m := model_of(token))}
        if named:
            return named
        half = value.word.x0 < self.mid
        single = len({v.word.cell for v in row_values}) == 1
        for heading in sorted((h for h in self.headings if h.y < value.word.y0), key=lambda h: -h.y):
            columns = heading.models
            if len({round(x0) for _, x0, _ in columns}) >= 2:
                lo = min(x0 for _, x0, _ in columns) - 80
                hi = max(xc for xc, _, _ in columns) + 80
                if lo <= value.word.xc <= hi:
                    if single:
                        return {m for _, _, m in columns}
                    return {min(columns, key=lambda c: abs(c[0] - value.word.xc))[2]}
            in_half = [c for c in columns if (c[1] < self.mid) == half]
            if in_half:
                return {min(in_half, key=lambda c: abs(c[0] - value.word.xc))[2]}
        return set()

    def unit_count(self, unit: str) -> float | None:
        """The largest count on a row counting `unit` ("Indicators | 24 | 24")."""
        stem = unit.lower().rstrip("s")[:6]
        for cells in self.cells:
            if cells and cells[0][0].text.lower().startswith(stem):
                numbers = [float(w.text) for cell in cells[1:] for w in cell if re.fullmatch(r"\d+", w.text)]
                if numbers:
                    return max(numbers)
        return None

    def alarm_pointer(self) -> str | None:
        """An "Alarm" label whose content is a pointer: "See the 4-COMREL"."""
        for i, word in enumerate(self.words):
            previous = self.words[i - 1].text if i > 0 and self.words[i - 1].row == word.row else None
            if _label_kind(word.text, previous) != "alarm":
                continue
            for other in self.words:
                if 0 <= other.yc - word.yc <= POINTER_BELOW_PT and other.x0 > word.x1 and other.text.lower() == "see":
                    return " ".join(w.text for w in self.words if w.row == other.row and w.x0 >= other.x0)
        return None


def read_part_current(pdf_path, part_no: str, doc_named_for_part: bool, panel_voltage: float = 24) -> CurrentReading | None:
    """The part's (standby, alarm) mA from one datasheet, or None."""
    key = part_key(part_no)
    base = key.split("/")[0]
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:  # noqa: BLE001 -- unreadable: nothing read from it
        return None

    exact: list[_Value] = []
    family: list[_Value] = []
    unowned: list[_Value] = []
    page_of: dict[int, _Page] = {}
    pointer = None
    with doc:
        for number, raw in enumerate(doc, start=1):
            if not re.search(r"\d\s*m\s?A", raw.get_text(), re.IGNORECASE):
                continue
            page = _Page(raw, number)
            values = page.values()
            for value in values:
                value.label = page.label_for(value)
                if value.label is None:
                    continue  # "200 mA AUX", "100 mA per 3-SDC1 Card": not the part's draw
                row_values = [v for v in values if v.word.row == value.word.row]
                value.owners = page.owners_of(value, row_values)
                page_of[id(value)] = page
                if key in value.owners or base in value.owners:
                    exact.append(value)
                elif any(len(o) >= 4 and key.startswith(o) for o in value.owners):
                    family.append(value)
                elif not value.owners:
                    unowned.append(value)
            if doc_named_for_part and pointer is None:
                pointer = page.alarm_pointer()

    chosen = exact or family or (unowned if doc_named_for_part else [])
    if not chosen:
        return None
    notes: list[str] = []

    def worst(kind: str) -> float | None:
        members = [v for v in chosen if v.label in (kind, "both")]
        if any(v.volts is not None for v in members) and any(v.volts == panel_voltage for v in members):
            members = [v for v in members if v.volts in (None, panel_voltage)]
        bases = [v.ma for v in members if v.per_unit is None]
        if not bases:
            return None
        candidates = list(bases)
        for v in members:
            if v.per_unit is None:
                continue
            count = page_of[id(v)].unit_count(v.per_unit)
            if count is not None:
                total = min(bases) + v.ma * count
                candidates.append(round(total, 4))
                notes.append(f"{kind} {min(bases):g} mA + {v.ma:g} mA x {count:g} {v.per_unit} (all on)")
        if len(set(candidates)) > 1:
            notes.append(f"{kind}: worst case of {', '.join(f'{c:g}' for c in sorted(set(candidates)))} mA")
        return max(candidates)

    standby, alarm = worst("standby"), worst("alarm")
    if alarm is None and standby is not None and pointer:
        alarm = standby
        notes.append(f"alarm: the datasheet says \"{pointer}\"; the module's own draw is its standby figure")
    if standby is None or alarm is None:
        return None
    return CurrentReading(standby_ma=standby, alarm_ma=alarm, pages=sorted({v.page for v in chosen}), notes=notes)
