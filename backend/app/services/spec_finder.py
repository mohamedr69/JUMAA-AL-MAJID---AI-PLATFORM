"""Find the specification for a project's systems in its own folder.

A compliance statement is written against the consultant's specification, so
the first question is always whether the project has one. Specifications
arrive in three shapes, and this reads all three:

- a section of its own, named for what it covers ("283111 - ADDRESSABLE FIRE
  DETECTION AND VOICE EVACUATION.pdf");
- the same, zipped ("Specification.zip"), which is how they usually arrive
  from the estimation team -- so archives are looked inside;
- one electrical specification covering every division, where the section
  for a system is a run of pages inside it. Those pages are found by their
  heading and reported as a page range, since that is the part the engineer
  needs and the rest is somebody else's system.

Matching is by CSI section number ("28 31 11", "265200") and by the words a
section is titled with, per system. Nothing is guessed: a document that
names no system is not offered as its specification.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MAX_PDFS = 1500
MAX_ZIP_MEMBER_BYTES = 80 * 1024 * 1024
# A section inside a combined specification rarely runs longer than this;
# past it, the heading was probably a mention in a table of contents.
MAX_SECTION_PAGES = 60
SNIPPET_CHARS = 400


@dataclass(frozen=True)
class SystemSpec:
    """What a system's specification is called and numbered."""

    code: str
    name: str
    # CSI section numbers, as written with or without spaces.
    sections: tuple[str, ...]
    # Words a section covering this system is titled with.
    keywords: tuple[str, ...]


SYSTEMS: dict[str, SystemSpec] = {
    "FAS": SystemSpec(
        code="FAS",
        name="Fire Alarm",
        sections=("283111", "283100", "283000", "165721", "165700"),
        keywords=("fire detection", "fire alarm", "voice evacuation", "voice alarm", "fire telephone"),
    ),
    "EML": SystemSpec(
        code="EML",
        name="Emergency Lighting",
        sections=("265200", "265100", "264313", "165535"),
        keywords=("emergency lighting", "emergency light", "central battery", "self contained", "self-contained"),
    ),
    "VES": SystemSpec(
        code="VES",
        name="Voice Evacuation",
        sections=("283111", "275116", "275100"),
        keywords=("voice evacuation", "voice alarm", "public address"),
    ),
    "CBS": SystemSpec(
        code="CBS",
        name="Central Battery System",
        sections=("265200", "263353"),
        keywords=("central battery", "central emergency lighting"),
    ),
}

_SECTION_HEADING_RE = re.compile(r"\bSECTION\s*[-:]?\s*(\d{2})\s?(\d{2})\s?(\d{2})\b", re.IGNORECASE)
# A specification reads like one: a numbered section, then its parts.
_SPEC_BODY_RE = re.compile(r"PART\s*1|GENERAL\b|SUBMITTALS\b|QUALITY ASSURANCE", re.IGNORECASE)
# Where specifications are kept, and how their files are named.
_CANDIDATE_PATH_RE = re.compile(r"spec|tender|particular|division\s*2\d", re.IGNORECASE)
_CSI_FILENAME_RE = re.compile(r"^\d{6}\b|\b\d{2}\s?\d{2}\s?\d{2}\b")
# A drawing, a submittal or the compliance statement itself is not a spec,
# however much of the system's name it carries.
_NOT_A_SPEC_RE = re.compile(
    r"compliance|submittal|shop\s*drawing|layout|sample|approval|transmittal|warranty|certificate|form\b",
    re.IGNORECASE,
)
# The fire-suppression sections that sit beside ours in the same folder.
_NOT_OURS_RE = re.compile(r"sprinkler|standpipe|fire pump|suppression|escutcheon|sleeve", re.IGNORECASE)


def looks_like_a_spec(relative: str, name: str) -> bool:
    """Worth opening: kept among specifications, or named as a section."""
    if _NOT_A_SPEC_RE.search(name):
        return False
    return bool(_CANDIDATE_PATH_RE.search(relative) or _CSI_FILENAME_RE.search(name))


@dataclass
class SpecMatch:
    """A specification for one system: a document of its own, or the pages of
    one inside a larger specification."""

    system_code: str
    # Path relative to the project folder; for a zipped one, the archive.
    path: str
    filename: str
    # The member inside the archive, when the spec is zipped.
    member: str | None = None
    kind: str = "document"  # or "section"
    section_no: str | None = None
    heading: str | None = None
    first_page: int | None = None
    last_page: int | None = None
    snippet: str = ""
    matched_on: str = "filename"  # or "heading"
    # Uploaded through the platform rather than found in the archive.
    uploaded: bool = False

    @property
    def pages(self) -> int | None:
        if self.first_page is None or self.last_page is None:
            return None
        return self.last_page - self.first_page + 1


def _normalized(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text).lower()


def _section_numbers(text: str) -> set[str]:
    """Section numbers as digits: "28 31 11", "283111" and "28-31-11" all
    read as 283111."""
    return {m.replace(" ", "").replace("-", "") for m in re.findall(r"\b\d{2}[\s-]?\d{2}[\s-]?\d{2}\b", text)}


def system_of(text: str) -> list[SystemSpec]:
    """The systems a title or heading covers, by its number then its words."""
    numbers = _section_numbers(text)
    normalized = _normalized(text)
    found = []
    for spec in SYSTEMS.values():
        if numbers & set(spec.sections) or any(k in normalized for k in spec.keywords):
            found.append(spec)
    return found


def _snippet(page: pymupdf.Page) -> str:
    return re.sub(r"\s+", " ", page.get_text()).strip()[:SNIPPET_CHARS]


def _sections_in(doc: pymupdf.Document, wanted: set[str]) -> list[SpecMatch]:
    """Where each wanted system's section starts and ends inside a combined
    specification: from its heading to the next section's."""
    starts: list[tuple[int, str, str, list[SystemSpec]]] = []
    for number, page in enumerate(doc, start=1):
        text = page.get_text()[:1500]
        heading = _SECTION_HEADING_RE.search(text)
        if not heading:
            continue
        section_no = "".join(heading.groups())
        # The line the heading sits on, plus the one under it: specifications
        # put the number on one line and the title on the next.
        after = text[heading.end() : heading.end() + 120]
        title = " ".join(after.split("\n")[:2]).strip()
        starts.append((number, section_no, title, system_of(f"{section_no} {title}")))

    # A specification repeats its section number in the header of every
    # page; only the first of a run starts a section.
    starts = [s for i, s in enumerate(starts) if i == 0 or s[1] != starts[i - 1][1]]

    matches: list[SpecMatch] = []
    for index, (page_no, section_no, title, systems) in enumerate(starts):
        for spec in systems:
            if spec.code not in wanted:
                continue
            next_page = starts[index + 1][0] - 1 if index + 1 < len(starts) else doc.page_count
            matches.append(
                SpecMatch(
                    system_code=spec.code,
                    path="",
                    filename="",
                    kind="section",
                    section_no=section_no,
                    heading=title or spec.name,
                    first_page=page_no,
                    last_page=min(next_page, page_no + MAX_SECTION_PAGES - 1),
                    snippet=_snippet(doc[page_no - 1]),
                    matched_on="heading",
                )
            )
    return matches


def _read(doc: pymupdf.Document, name: str, relative: str, member: str | None, wanted: set[str]) -> list[SpecMatch]:
    """A document's specifications: itself when it is named for a system,
    else the sections inside it that are."""
    first = doc[0].get_text() if doc.page_count else ""
    # It has to read like a specification, not merely mention a system.
    if not (_SECTION_HEADING_RE.search(first[:2000]) or _SPEC_BODY_RE.search(first[:2000])):
        return []

    by_name = [spec for spec in system_of(name) if spec.code in wanted]
    if by_name and not _NOT_OURS_RE.search(name):
        return [
            SpecMatch(
                system_code=spec.code,
                path=relative,
                filename=name,
                member=member,
                kind="document",
                section_no=next(iter(_section_numbers(name) & set(spec.sections)), None),
                heading=Path(name).stem,
                first_page=1,
                last_page=doc.page_count,
                snippet=_snippet(doc[0]) if doc.page_count else "",
            )
            for spec in by_name
        ]

    found = _sections_in(doc, wanted)
    for match in found:
        match.path, match.filename, match.member = relative, name, member
    return found


def find_specs(root: Path, wanted: set[str]) -> tuple[list[SpecMatch], list[str]]:
    """Every specification in the project folder for the wanted systems."""
    matches: list[SpecMatch] = []
    warnings: list[str] = []
    seen = 0
    for path in sorted(root.rglob("*")):
        if seen >= MAX_PDFS:
            warnings.append(f"Stopped after {MAX_PDFS} files; the folder holds more.")
            break
        suffix = path.suffix.lower()
        relative = str(path.relative_to(root))
        if suffix == ".pdf":
            if not looks_like_a_spec(relative, path.name):
                continue
            seen += 1
            try:
                with pymupdf.open(path) as doc:
                    matches += _read(doc, path.name, relative, None, wanted)
            except Exception:  # noqa: BLE001 -- unreadable or online-only
                continue
        elif suffix == ".zip":
            # Specifications usually arrive zipped; the sections are inside.
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        if not info.filename.lower().endswith(".pdf") or info.file_size > MAX_ZIP_MEMBER_BYTES:
                            continue
                        name = Path(info.filename).name
                        if not looks_like_a_spec(f"{relative}/{info.filename}", name):
                            continue
                        seen += 1
                        try:
                            with pymupdf.open(stream=io.BytesIO(archive.read(info)), filetype="pdf") as doc:
                                matches += _read(doc, name, relative, info.filename, wanted)
                        except Exception:  # noqa: BLE001
                            continue
            except (zipfile.BadZipFile, OSError) as exc:
                warnings.append(f"{relative}: {exc}")
    return matches, warnings


def open_spec(root: Path, relative: str, member: str | None) -> bytes | None:
    """The bytes of a specification, out of the archive if that is where it
    lives. None when the path is not inside the project folder."""
    folder = root.resolve()
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        return None
    if member is None:
        return path.read_bytes() if path.suffix.lower() == ".pdf" else None
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                return None
            return archive.read(info)
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
