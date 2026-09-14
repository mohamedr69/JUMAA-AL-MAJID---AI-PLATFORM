"""The approved compliance statement as a PDF, laid out like the workbook.

Same title block and columns as app.compliance.writer -- SL.NO | PROJECT
SPECIFICATION | <COMPANY> COMPLIANCE | <CONTRACTOR> COMPLIANCE | REMARKS --
on landscape A4, with the column headings repeated on every page, the
engineer's approval printed at the end, and the company stamp and the page
number on every page.

The stamp is the company's own document, kept in the library
(library/submittal/templates/stamp.png); a library without one exports
without it.

The table is drawn row by row rather than through an HTML layout: a row is
never split across pages and the headings are redrawn on each one, which
PyMuPDF's HTML tables do not manage cleanly once a table runs to many pages.
Text is set in PyMuPDF's built-in Helvetica: it needs no font on the machine,
and the text copies and searches correctly out of the PDF (Arial and Calibri
extract their spaces and hyphens as other characters).
"""

from __future__ import annotations

import pymupdf

from app.core.config import get_settings
from app.models import ComplianceStatement, Project
from app.services.company_library import SUBMITTAL, library_root
from app.services.spec_finder import SYSTEMS

_PAGE = pymupdf.paper_rect("a4-l")
_MARGIN = 28.0
# Room under the table for the stamp and the page number.
_FOOTER = 40.0
_SIZE = 7.5
_LEADING = 9.2
_PAD = 3.0
# SL.NO | specification | company | contractor | remarks
_WIDTHS = (0.07, 0.45, 0.12, 0.12, 0.24)
_NAVY = (0x1F / 255, 0x38 / 255, 0x64 / 255)
_PART = (0xD9 / 255, 0xE1 / 255, 0xF2 / 255)
_GRID = (0.6, 0.6, 0.6)
_GREY = (0.35, 0.35, 0.35)
STAMP_FILE = "stamp.png"
# The stamp: bottom left of every page, under the table, about 60 pt across.
_STAMP_WIDTH = 60.0


def stamp_path():
    path = library_root() / SUBMITTAL / "templates" / STAMP_FILE
    return path if path.is_file() else None


class _Layout:
    def __init__(self) -> None:
        self.font, self.bold_font = pymupdf.Font("helv"), pymupdf.Font("hebo")
        self.doc = pymupdf.open()
        self.page: pymupdf.Page | None = None
        # Text is gathered per page and colour and written once, so each
        # page embeds the font a single time.
        self.writers: dict[tuple, pymupdf.TextWriter] = {}
        self._advances: dict[bool, dict[str, float]] = {False: {}, True: {}}
        self.y = 0.0
        width = _PAGE.width - 2 * _MARGIN
        self.columns: list[tuple[float, float]] = []
        x = _MARGIN
        for share in _WIDTHS:
            self.columns.append((x, x + width * share))
            x += width * share
        self.bottom = _PAGE.height - _MARGIN - _FOOTER

    # --- text ------------------------------------------------------------------------------

    def _font(self, bold: bool) -> pymupdf.Font:
        return self.bold_font if bold else self.font

    def width(self, text: str, *, bold: bool = False, size: float = _SIZE) -> float:
        """The set width of a line, from cached glyph advances: measuring
        through PyMuPDF per call is far too slow for hundreds of clauses."""
        cache = self._advances[bold]
        font = self._font(bold)
        total = 0.0
        for char in text:
            advance = cache.get(char)
            if advance is None:
                advance = cache[char] = font.glyph_advance(ord(char))
            total += advance
        return total * size

    def wrap(self, text: str, width: float, *, bold: bool = False, size: float = _SIZE) -> list[str]:
        lines: list[str] = []
        for paragraph in (text or "").replace("\r", "").split("\n"):
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}" if line else word
                if self.width(candidate, bold=bold, size=size) <= width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                # A word wider than the column is broken where it must be.
                while self.width(word, bold=bold, size=size) > width and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and self.width(word[:cut], bold=bold, size=size) > width:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                line = word
            lines.append(line)
        return lines or [""]

    def text(self, x: float, y: float, line: str, *, bold: bool = False, size: float = _SIZE,
             color: tuple = (0, 0, 0)) -> None:
        assert self.page is not None
        writer = self.writers.get(color)
        if writer is None:
            writer = self.writers[color] = pymupdf.TextWriter(self.page.rect, color=color)
        writer.append((x, y), line, font=self._font(bold), fontsize=size)

    def flush(self) -> None:
        if self.page is not None:
            for writer in self.writers.values():
                writer.write_text(self.page)
        self.writers = {}

    # --- pages -------------------------------------------------------------------------------

    def new_page(self, headings: list[str]) -> None:
        self.flush()
        self.page = self.doc.new_page(width=_PAGE.width, height=_PAGE.height)
        self.y = _MARGIN
        if headings:
            self.heading_row(headings)

    def heading_row(self, headings: list[str]) -> None:
        wrapped = [self.wrap(h, (right - left) - 2 * _PAD, bold=True, size=7) for h, (left, right) in zip(headings, self.columns)]
        height = max(len(lines) for lines in wrapped) * 8.6 + 2 * _PAD + 2
        assert self.page is not None
        for lines, (left, right) in zip(wrapped, self.columns):
            cell = pymupdf.Rect(left, self.y, right, self.y + height)
            self.page.draw_rect(cell, color=_GRID, fill=_NAVY, width=0.5)
            top = self.y + _PAD + 7
            for i, line in enumerate(lines):
                length = self.width(line, bold=True, size=7)
                self.text(left + ((right - left) - length) / 2, top + i * 8.6, line, bold=True, size=7, color=(1, 1, 1))
        self.y += height

    def row(self, cells: list[str], headings: list[str], *, bold: bool = False, fill: tuple | None = None,
            centred: tuple[int, ...] = (0, 2, 3), indent: float = 0.0) -> None:
        wrapped = []
        for index, (value, (left, right)) in enumerate(zip(cells, self.columns)):
            inset = indent if index == 1 else 0.0
            wrapped.append(self.wrap(value, (right - left) - 2 * _PAD - inset, bold=bold))
        height = max(len(lines) for lines in wrapped) * _LEADING + 2 * _PAD + 1
        if self.y + height > self.bottom:
            self.new_page(headings)
        assert self.page is not None
        for index, (lines, (left, right)) in enumerate(zip(wrapped, self.columns)):
            self.page.draw_rect(pymupdf.Rect(left, self.y, right, self.y + height), color=_GRID, fill=fill, width=0.5)
            inset = indent if index == 1 else 0.0
            for i, line in enumerate(lines):
                baseline = self.y + _PAD + _SIZE + i * _LEADING
                if index in centred:
                    length = self.width(line, bold=bold)
                    x = left + ((right - left) - length) / 2
                else:
                    x = left + _PAD + inset
                self.text(x, baseline, line, bold=bold)
        self.y += height


def build_pdf(project: Project, statement: ComplianceStatement, brands: set[str]) -> bytes:
    settings = get_settings()
    system = SYSTEMS.get(statement.system_code)
    system_name = system.name.upper() if system else statement.system_code
    section = ", ".join(statement.spec.get("section_numbers") or []) or "-"
    company = settings.company_name.upper()
    contractor = (project.contractor or "CONTRACTOR").upper()
    approved_on = statement.approved_at.strftime("%d %b %Y %H:%M UTC") if statement.approved_at else "-"
    approved_by = statement.approved_by_name or "-"
    headings = ["SL.NO", f"PROJECT SPECIFICATION - SECTION {section}", f"{company} COMPLIANCE",
                f"{contractor} COMPLIANCE", "REMARKS"]

    layout = _Layout()
    layout.new_page([])
    layout.text(_MARGIN, layout.y + 12, f"COMPLIANCE STATEMENT - {system_name} SYSTEM", bold=True, size=13, color=_NAVY)
    layout.y += 22
    for line in (
        f"PROJECT NAME : {(project.project_name or '').upper()}",
        f"EP NUMBER : EP-{project.ep_number}",
        f"CONSULTANT : {(project.consultant or '-').upper()}    MAIN CONTRACTOR : {contractor}",
        f"PRODUCT DESCRIPTION : {system_name} SYSTEM",
        f"MANUFACTURER : {', '.join(sorted(b.upper() for b in brands)) or '-'}",
        f"LOCAL SUPPLIER : M/s. {company}",
    ):
        layout.text(_MARGIN, layout.y + 8.5, line, size=8.5)
        layout.y += 11
    layout.y += 8
    layout.heading_row(headings)

    for item in statement.rows:
        level = item.get("level", 0)
        label = item["label"] if level <= 1 else f"{item['label']}."
        text = item["text"] if level > 0 else f"{item['label']} - {item['text']}"
        heading = bool(item.get("heading"))
        layout.row(
            [label if level > 0 else "", text, item.get("response") or "", "", item.get("remark") or ""],
            headings,
            bold=heading,
            fill=_PART if heading and level == 0 else None,
            indent=min(max(level - 1, 0), 4) * 8.0,
        )

    # The approval, kept whole on one page.
    box_height = 46.0
    if layout.y + 12 + box_height > layout.bottom:
        layout.new_page([])
    top = layout.y + 12
    assert layout.page is not None
    layout.page.draw_rect(pymupdf.Rect(_MARGIN, top, _PAGE.width - _MARGIN, top + box_height), color=_NAVY, width=1)
    layout.text(_MARGIN + 8, top + 13, "Engineer approval", bold=True, size=9)
    layout.text(_MARGIN + 8, top + 26, f"Approved by: {approved_by}", size=9)
    layout.text(_MARGIN + 8, top + 38, f"Approved on: {approved_on}", size=9)

    layout.flush()
    total = layout.doc.page_count
    footer_y = _PAGE.height - _MARGIN + 2
    stamp = stamp_path()
    stamp_bytes = stamp.read_bytes() if stamp else None
    stamp_xref = 0
    for number, page in enumerate(layout.doc, start=1):
        layout.page = page
        if stamp_bytes:
            height = _STAMP_WIDTH * 529 / 587
            bottom = _PAGE.height - 6
            box = pymupdf.Rect(_MARGIN, bottom - height, _MARGIN + _STAMP_WIDTH, bottom)
            # Embedded once and referenced from every page.
            stamp_xref = page.insert_image(box, stream=stamp_bytes if not stamp_xref else None, xref=stamp_xref, keep_proportion=True)
        label = f"Page {number} of {total}"
        layout.text((_PAGE.width - layout.width(label, size=7)) / 2, footer_y, label, size=7, color=_GREY)
        layout.flush()

    layout.doc.set_metadata({"title": f"EP-{project.ep_number} {statement.system_code} Compliance Statement", "author": company})
    layout.doc.subset_fonts()
    out = layout.doc.tobytes(garbage=3, deflate=True)
    layout.doc.close()
    return out
