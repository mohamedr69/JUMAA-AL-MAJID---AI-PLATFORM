"""Find a part's datasheet in a manufacturer's datasheet library and show
the current figures it gives.

This only finds and shows; it never decides a value. Datasheet current
tables are not uniform enough to read a number off unattended: a figure can
be conditional ("3.0 mA base + 0.23 mA per indicator ON"), one table can
cover several models (3-SSDC1 and 3-SDDC1 side by side), and the "Standby"
/ "Alarm" labels often sit in a column of their own. So the engineer is
shown the datasheet's current rows, with their page, and enters the value;
the catalogue records the datasheet as its source.

A row is rebuilt from word positions rather than taken from the PDF's text
order, which is what keeps a label on the same line as its value: the text
order of a two-column spec table puts all the labels first.
"""

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.services.battery_calculation import part_key

# Datasheets are a few pages; manuals run to hundreds and mention every
# part. Longer documents are matched by filename only.
MAX_TEXT_INDEX_PAGES = 30
# Words closer than this vertically are on one row; wider horizontal gaps
# than this separate table cells.
ROW_TOLERANCE_PT = 3.0
CELL_GAP_PT = 18.0
MAX_MATCHES = 3

_CURRENT_RE = re.compile(r"\d+(?:\.\d+)?\s*m\s?A\b")
_DOCUMENT_NO_RE = re.compile(r"D\s?A\s?T\s?A\s*S\s?H\s?E\s?E\s?T\s+([A-Z]\d{3,}[-\d]*)")


@dataclass
class _Indexed:
    mtime: float
    pages: int
    is_datasheet: bool
    document_no: str | None
    # Upper-cased page texts, for part-number search; empty for manuals.
    page_texts: list[str]
    # (page number, rebuilt row) for every row that gives a current.
    current_rows: list[tuple[int, str]]
    error: str | None = None
    # What a battery datasheet says it is.
    battery: "BatteryDatasheet | None" = None


@dataclass
class BatteryDatasheet:
    brand: str | None
    model: str  # as the maker writes the part number: "ES65-12"
    voltage: float
    capacity_ah: float


# A battery datasheet opens with its model and rating: "ES 65-12" over
# "12V - 65Ah" (ROCKET's layout).
_BATTERY_MODEL_RE = re.compile(r"^([A-Z]{1,5})\s?(\d+(?:\.\d+)?)\s?-\s?(\d+)([A-Z]{0,2})$")
_BATTERY_RATING_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*V\s*-\s*(\d+(?:\.\d+)?)\s*Ah$", re.IGNORECASE)
_BATTERY_BRANDS = ("ROCKET",)


def read_battery_datasheet(first_page: str) -> BatteryDatasheet | None:
    lines = [line.strip() for line in first_page.splitlines() if line.strip()][:12]
    for i, line in enumerate(lines[:-1]):
        model = _BATTERY_MODEL_RE.match(line)
        rating = _BATTERY_RATING_RE.match(lines[i + 1])
        if not (model and rating):
            continue
        voltage, capacity = float(rating.group(1)), float(rating.group(2))
        # The model encodes the rating (ES65-12 is 65 Ah at 12 V); a line
        # that disagrees with the one under it is not this battery's name.
        if float(model.group(2)) != capacity or float(model.group(3)) != voltage:
            continue
        upper = first_page.upper()
        brand = next((b for b in _BATTERY_BRANDS if b in upper), None)
        name = f"{model.group(1)}{model.group(2)}-{model.group(3)}{model.group(4)}"
        return BatteryDatasheet(brand=brand, model=name, voltage=voltage, capacity_ah=capacity)
    return None


@dataclass
class DatasheetMatch:
    library: str
    path: str  # relative to the library folder
    filename: str
    document_no: str | None
    # "filename": named for the part; "family": named for its family
    # (4-NET.pdf for 4-NET-TP); "text": only mentions it.
    matched_on: str
    pages: list[int]
    current_rows: list[tuple[int, str]] = field(default_factory=list)

    @property
    def source(self) -> str:
        """How the catalogue entry names where its value came from."""
        page = self.current_rows[0][0] if self.current_rows else (self.pages[0] if self.pages else None)
        name = f"{self.library.title()} datasheet {self.document_no + ' ' if self.document_no else ''}({self.filename})"
        return f"{name}, p.{page}" if page else name


def _rows(page: pymupdf.Page) -> list[str]:
    words = sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[tuple[float, list]] = []
    for word in words:
        centre = (word[1] + word[3]) / 2
        if lines and abs(lines[-1][0] - centre) <= ROW_TOLERANCE_PT:
            lines[-1][1].append(word)
        else:
            lines.append((centre, [word]))
    rows = []
    for _, members in lines:
        members.sort(key=lambda w: w[0])
        text, previous_end = "", None
        for word in members:
            if previous_end is not None:
                text += " | " if word[0] - previous_end > CELL_GAP_PT else " "
            text += word[4]
            previous_end = word[2]
        rows.append(text)
    return rows


def _index_file(path: Path) -> _Indexed:
    mtime = path.stat().st_mtime
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 -- an online-only or broken file: skip it, say why
        return _Indexed(mtime, 0, False, None, [], [], error=str(exc))
    with doc:
        first = doc[0].get_text() if doc.page_count else ""
        document_no = _DOCUMENT_NO_RE.search(first)
        is_datasheet = bool(document_no) or "DATASHEET" in first.upper().replace(" ", "")
        texts: list[str] = []
        rows: list[tuple[int, str]] = []
        if doc.page_count <= MAX_TEXT_INDEX_PAGES:
            for number, page in enumerate(doc, start=1):
                texts.append(page.get_text().upper())
                rows += [(number, row) for row in _rows(page) if _CURRENT_RE.search(row)]
        return _Indexed(
            mtime, doc.page_count, is_datasheet, document_no.group(1) if document_no else None, texts, rows,
            battery=read_battery_datasheet(first),
        )


class DatasheetLibrary:
    """One manufacturer's folder of datasheet PDFs, indexed on first use and
    re-read file by file as files change."""

    def __init__(self, name: str, folder: Path):
        self.name = name
        self.folder = folder
        self._index: dict[Path, _Indexed] = {}
        self._lock = threading.Lock()

    def _refresh(self) -> dict[Path, _Indexed]:
        with self._lock:
            seen = set()
            for path in self.folder.rglob("*"):
                if path.suffix.lower() != ".pdf" or not path.is_file():
                    continue
                seen.add(path)
                cached = self._index.get(path)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if cached is None or cached.mtime != mtime:
                    self._index[path] = _index_file(path)
            for gone in set(self._index) - seen:
                del self._index[gone]
            return dict(self._index)

    def find(self, part_no: str) -> list[DatasheetMatch]:
        key = part_key(part_no)
        if not key:
            return []
        # "4-PPS/M" is documented in the 4-PPS datasheet.
        base = key.split("/")[0]
        token = re.compile(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])")
        base_token = re.compile(rf"(?<![A-Z0-9]){re.escape(base)}(?![A-Z0-9])")

        scored = []
        for path, entry in self._refresh().items():
            if entry.error:
                continue
            name = path.stem.upper()
            by_name = bool(token.search(name) or base_token.search(name))
            # A datasheet named for the part's family: 4-NET-TP is in
            # "4-NET.pdf", 4-AUDTELS in "4-AUDTEL.pdf" -- ahead of the CPU
            # datasheet that merely lists them among its accessories.
            by_family = any(
                len(t) >= 3 and "-" in t and key.startswith(t) for t in re.findall(r"[A-Z0-9]+(?:-[A-Z0-9]+)+", name)
            )
            pages = [n for n, text in enumerate(entry.page_texts, start=1) if token.search(text)]
            if not by_name and not pages:
                continue
            match = DatasheetMatch(
                library=self.name,
                path=path.relative_to(self.folder).as_posix(),
                filename=path.name,
                document_no=entry.document_no,
                matched_on="filename" if by_name else "family" if by_family else "text",
                pages=pages,
                current_rows=entry.current_rows,
            )
            rank = (not by_name, not by_family, not entry.is_datasheet, -len(pages), entry.pages, match.path)
            scored.append((rank, match))
        return [match for _, match in sorted(scored, key=lambda s: s[0])[:MAX_MATCHES]]

    def batteries(self) -> list[tuple[BatteryDatasheet, str]]:
        """(battery, path relative to the library) for every battery
        datasheet in the library -- one per model, the first found."""
        found: dict[str, tuple[BatteryDatasheet, str]] = {}
        for path, entry in sorted(self._refresh().items()):
            if entry.battery and entry.battery.model not in found:
                found[entry.battery.model] = (entry.battery, path.relative_to(self.folder).as_posix())
        return list(found.values())

    def warm(self) -> None:
        try:
            self._refresh()
        except Exception:  # noqa: BLE001 -- a warm-up must never take the app down; lookups retry
            pass

    def unreadable(self) -> list[str]:
        return [p.relative_to(self.folder).as_posix() for p, e in self._refresh().items() if e.error]

    def resolve(self, relative: str) -> Path | None:
        """The library file at `relative`, or None if that is outside it."""
        folder = self.folder.resolve()
        path = (folder / relative).resolve()
        if path.is_relative_to(folder) and path.suffix.lower() == ".pdf" and path.is_file():
            return path
        return None


_libraries: dict[tuple[str, str], DatasheetLibrary] = {}
_libraries_lock = threading.Lock()


def get_libraries(configured: dict[str, str], projects_root: str | None) -> dict[str, DatasheetLibrary]:
    """The configured libraries whose folder exists, by manufacturer."""
    found: dict[str, DatasheetLibrary] = {}
    for name, location in configured.items():
        folder = Path(location)
        if not folder.is_absolute():
            if not projects_root:
                continue
            folder = Path(projects_root) / location
        if not folder.is_dir():
            continue
        with _libraries_lock:
            library = _libraries.setdefault((name.upper(), str(folder)), DatasheetLibrary(name.upper(), folder))
        found[name.upper()] = library
    return found


def libraries_for(manufacturer: str | None, libraries: dict[str, DatasheetLibrary]) -> list[DatasheetLibrary]:
    """The library for a BOQ line's manufacturer ("EDWARDS", "Edwards
    (EST4)"), or every library when it names none of them."""
    if manufacturer:
        wanted = manufacturer.upper()
        chosen = [lib for name, lib in libraries.items() if name in wanted or wanted in name]
        if chosen:
            return chosen
    return list(libraries.values())
