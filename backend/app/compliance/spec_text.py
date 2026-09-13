"""The clauses of a specification, and who it was written for.

A CSI specification is a strict outline -- PART 1, article 1.2, paragraph A,
sub-paragraph 1, item a -- and a compliance statement answers it at that
grain. PDF text puts each label on a line of its own or at the start of the
line it opens ("1.2 \\nSUMMARY \\nA. \\nThis Section includes..."), so the
outline is rebuilt from the labels, checked against the sequence they must
follow (A comes before B, article 2.3 belongs to PART 2) so that a wrapped
line starting "10.5 mm" or "U.S." does not open a clause.

The running header and footer every page repeats is removed from the text
and kept apart: it is where a specification says which project it is for.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

PARSER_VERSION = "spec-text-1"

# A page of the table of contents is dot leaders, not clauses.
_TOC_LINE_RE = re.compile(r"\.{6,}|…{3,}")
_PAGE_NO_RE = re.compile(r"^\s*(page\s*)?\d+\s*(of\s*\d+)?\s*$", re.IGNORECASE)
_PART_RE = re.compile(r"^PART\s*[-–]?\s*(\d{1,2})\b\s*[-–—:.]?\s*(.*)$", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})((?:\.\d{1,2}){0,3})\.?(?:\s+(.*))?$")
_ALPHA_RE = re.compile(r"^([A-Z])[.)](?:\s+(.*))?$")
_NUMBER_RE = re.compile(r"^(\d{1,2})[.)](?:\s+(.*))?$")
_LOWER_RE = re.compile(r"^([a-z])[.)](?:\s+(.*))?$")
_ROMAN_RE = re.compile(r"^\(?((?:x|ix|iv|v?i{0,3}))[.)](?:\s+(.*))?$")
_END_OF_SECTION_RE = re.compile(r"^END\s+OF\s+SECTION", re.IGNORECASE)
_SECTION_RE = re.compile(r"\bSECTION\s*[-:]?\s*(\d{2}\s?\d{2}\s?\d{2}|\d{5})\b", re.IGNORECASE)

LEVELS = ("part", "article", "paragraph", "subparagraph", "item", "subitem")


@dataclass
class Clause:
    id: str              # "c12" -- stable within one reading
    ref: str             # the outline path, "2.3.B.1"
    label: str           # what the specification prints: "2.3", "B", "1"
    level: int           # index into LEVELS
    text: str
    page: int            # 1-based, in the document read
    heading: bool = False

    @property
    def display(self) -> str:
        return self.label if self.level <= 1 else f"{self.label}."


@dataclass
class SpecText:
    clauses: list[Clause]
    header_lines: list[str]      # repeated across pages: project, section title
    cover_text: str              # the first pages before PART 1, flattened
    section_numbers: list[str]
    title: str | None
    pages_read: int
    sha256: str
    warnings: list[str] = field(default_factory=list)

    @property
    def identity_text(self) -> str:
        """What a specification says about itself: its running header and
        its cover. Where a project name or plot number would be."""
        return "\n".join(self.header_lines) + "\n" + self.cover_text


def _norm_line(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line)).strip().lower()


def _page_lines(page: pymupdf.Page) -> list[str]:
    return [re.sub(r"[ \t ]+", " ", line).strip() for line in page.get_text().splitlines()]


def _repeated(pages: list[list[str]]) -> set[str]:
    """Lines (digits blurred) at the top or bottom of at least half the pages:
    the running header and footer."""
    if len(pages) < 3:
        return set()
    counts: Counter[str] = Counter()
    for lines in pages:
        edge = [l for l in lines if l][:6] + [l for l in lines if l][-4:]
        # A bare label ("1.", "A.") sits at the top of many pages too; only
        # lines with words in them can be a running header.
        counts.update({_norm_line(l) for l in edge if len(re.sub(r"[^A-Za-z]", "", l)) >= 6})
    return {line for line, n in counts.items() if line and n >= max(2, len(pages) // 2)}


def _to_roman(n: int) -> str:
    return ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][n - 1] if 1 <= n <= 10 else ""


_ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


class _Outline:
    """The labels seen so far, to judge whether a new one is the next in
    sequence at its level."""

    def __init__(self) -> None:
        self.part: int | None = None
        self.article: tuple[int, ...] | None = None
        self.last: dict[int, str] = {}

    def _reset_below(self, level: int) -> None:
        for lvl in list(self.last):
            if lvl > level:
                del self.last[lvl]

    @property
    def ref(self) -> str:
        """"2.3.B.1" -- the article and what is under it; "PART 2" alone."""
        below = [self.last[lvl] for lvl in sorted(self.last) if lvl >= 1]
        return ".".join(below) if below else f"PART {self.last.get(0, '')}".strip()

    def accept(self, level: int, label: str, alone: bool) -> bool:
        """Whether `label` is the next at `level`. A label on a line of its
        own is a strong sign, so it may skip one (specifications do: EP-30784
        goes from 2.2 straight to B); one that opens a line of text may not."""
        previous = self.last.get(level)
        slack = 2 if alone else 1
        if level in (2, 4):  # A, B, C / a, b, c
            step = ord(label.lower()) - (ord(previous.lower()) if previous else ord("a") - 1)
        elif level == 3:
            step = int(label) - (int(previous) if previous else 0)
        elif level == 5:
            step = _ROMANS.index(label) + 1 - (_ROMANS.index(previous) + 1 if previous else 0)
        else:
            step = 1
        if not 1 <= step <= slack:
            return False
        # A lower level cannot open before its parent exists.
        if level >= 3 and not ({1, 2} & set(self.last)):
            return False
        if level == 4 and 3 not in self.last and 2 not in self.last:
            return False
        if level == 5 and 4 not in self.last:
            return False
        self._reset_below(level)
        self.last[level] = label
        return True


def _classify(line: str, outline: _Outline) -> tuple[int, str, str] | None:
    """(level, label, rest of line) when the line opens a clause."""
    match = _PART_RE.match(line)
    if match:
        part = int(match.group(1))
        if outline.part is None or part >= outline.part:
            outline.part = part
            outline.article = None
            outline.last = {0: str(part)}
            return 0, f"PART {part}", match.group(2).strip(" -–—:")
        return None

    match = _ARTICLE_RE.match(line)
    if match:
        major, minor = int(match.group(1)), int(match.group(2))
        deeper = match.group(3) or ""
        # 2.3 belongs to PART 2; without PARTs, articles only move forward.
        if outline.part is not None and major != outline.part:
            return None
        number = (major, minor) + tuple(int(x) for x in deeper.split(".") if x)
        if outline.article is not None and number[:2] < outline.article[:2]:
            return None
        rest = (match.group(4) or "").strip()
        # "1.5 mm" is a measurement, not an article.
        if rest and re.match(r"^(mm|m|cm|kg|v|a|w|db|hz|sec|seconds|%|meters?|metres?)\b", rest, re.IGNORECASE):
            return None
        outline.article = number
        label = line.split()[0].rstrip(".")
        outline._reset_below(0)
        outline.last[1] = label
        return 1, label, rest

    for regex, level in ((_ALPHA_RE, 2), (_NUMBER_RE, 3), (_LOWER_RE, 4), (_ROMAN_RE, 5)):
        match = regex.match(line)
        if match and match.group(1):
            label = match.group(1)
            rest = (match.group(2) or "").strip()
            if level == 4 and label in ("i", "v", "x") and outline.last.get(4) != chr(ord(label) - 1):
                continue  # roman, not a letter
            if outline.accept(level, label, alone=not rest):
                return level, label, rest
    return None


def _is_heading(level: int, text: str) -> bool:
    if level == 0:
        return True
    if level == 1:
        letters = re.sub(r"[^A-Za-z]", "", text)
        return bool(letters) and len(text) <= 90 and (letters.isupper() or not text.rstrip().endswith("."))
    return False


def read_document(doc: pymupdf.Document, first_page: int = 1, last_page: int | None = None) -> SpecText:
    """The clauses of pages first_page..last_page (1-based, inclusive)."""
    last_page = min(last_page or doc.page_count, doc.page_count)
    pages = [_page_lines(doc[i]) for i in range(first_page - 1, last_page)]
    repeated = _repeated(pages)

    header_lines: list[str] = []
    for lines in pages[: min(4, len(pages))]:
        for line in [l for l in lines if l][:6]:
            if _norm_line(line) in repeated and line not in header_lines and not _PAGE_NO_RE.match(line):
                header_lines.append(line)

    clauses: list[Clause] = []
    outline = _Outline()
    cover: list[str] = []
    started = False
    current: Clause | None = None
    sections: list[str] = []
    title: str | None = None

    for page_index, lines in enumerate(pages):
        page_no = first_page + page_index
        body = [l for l in lines if l and _norm_line(l) not in repeated and not _PAGE_NO_RE.match(l)]
        joined = "\n".join(body)
        for m in _SECTION_RE.finditer(joined):
            number = m.group(1).replace(" ", "")
            if number not in sections:
                sections.append(number)
                if title is None:
                    after = joined[m.end(): m.end() + 160].strip().split("\n")
                    title = " ".join(x.strip() for x in after[:2] if x.strip() and not _PART_RE.match(x.strip()))[:120] or None
        if sum(1 for l in body if _TOC_LINE_RE.search(l)) >= 4:
            continue  # table of contents
        for line in body:
            if _END_OF_SECTION_RE.match(line):
                current = None
                continue
            opened = _classify(line, outline) if (started or _PART_RE.match(line)) else None
            if opened is None and not started and _ARTICLE_RE.match(line) and not _TOC_LINE_RE.search(line):
                # A specification without PARTs starts at its first article.
                opened = _classify(line, outline)
            if opened is not None:
                started = True
                level, label, rest = opened
                current = Clause(
                    id=f"c{len(clauses) + 1}",
                    ref=outline.ref,
                    label=label,
                    level=level,
                    text=rest,
                    page=page_no,
                )
                clauses.append(current)
                continue
            if not started:
                cover.append(line)
                continue
            if current is not None and not _TOC_LINE_RE.search(line):
                current.text = f"{current.text} {line}".strip() if current.text else line

    for clause in clauses:
        clause.text = re.sub(r"\s+", " ", clause.text).strip()
        clause.heading = _is_heading(clause.level, clause.text)

    warnings = []
    if not clauses:
        text_chars = sum(len("".join(p)) for p in pages)
        warnings.append(
            "No text could be read from the specification; it is probably a scan."
            if text_chars < 200 * max(1, len(pages))
            else "The specification's clause numbering could not be followed."
        )
    return SpecText(
        clauses=clauses,
        header_lines=header_lines,
        cover_text=re.sub(r"\s+", " ", " ".join(cover))[:3000],
        section_numbers=sections,
        title=title,
        pages_read=len(pages),
        sha256="",
        warnings=warnings,
    )


def read_bytes(content: bytes, first_page: int = 1, last_page: int | None = None) -> SpecText:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        spec = read_document(doc, first_page, last_page)
    digest = hashlib.sha256(content)
    digest.update(f"|{first_page}-{last_page}".encode())
    spec.sha256 = digest.hexdigest()
    return spec
