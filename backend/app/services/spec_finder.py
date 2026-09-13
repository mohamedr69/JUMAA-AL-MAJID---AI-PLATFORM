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
    # Section numbers as consultants actually number them: MasterFormat 2004+
    # (28 31 11, and 28 46 00 since 2012) and the 1995 five-digit numbers
    # (16721) older specifications still carry.
    "FAS": SystemSpec(
        code="FAS",
        name="Fire Alarm",
        sections=("283111", "283112", "283113", "283100", "283000", "284600", "284621", "284620",
                  "165721", "165700", "16721", "16720", "13851"),
        # Not "voice evacuation": a PA/BGM specification says that too. A
        # combined fire detection and voice evacuation section still reads as
        # fire alarm by "fire detection".
        keywords=("fire detection", "fire alarm", "fire telephone", "fire-alarm"),
    ),
    "EML": SystemSpec(
        code="EML",
        name="Emergency Lighting",
        sections=("265200", "265213", "265100", "264313", "165535", "16535", "16536"),
        keywords=("emergency lighting", "emergency light", "exit lighting", "central battery", "self contained",
                  "self-contained"),
    ),
    "VES": SystemSpec(
        code="VES",
        name="Voice Evacuation",
        sections=("283111", "275116", "275100", "284625"),
        keywords=("voice evacuation", "voice alarm", "public address"),
    ),
    "CBS": SystemSpec(
        code="CBS",
        name="Central Battery System",
        sections=("265200", "265213", "263353"),
        keywords=("central battery", "central emergency lighting"),
    ),
    "PAVA": SystemSpec(
        code="PAVA",
        name="Public Address & Voice Alarm",
        sections=("275116", "275100", "274116", "275113", "275123", "275126", "16780"),
        keywords=("public address", "voice alarm", "background music", "back ground music", "pa system", "pa/va",
                  "pava", "bgm"),
    ),
}

_SECTION_HEADING_RE = re.compile(r"\bSECTION\s*[-:]?\s*(\d{2})\s?(\d{2})\s?(\d{1,2})\b", re.IGNORECASE)
# A specification reads like one: a numbered section, then its parts.
_SPEC_BODY_RE = re.compile(r"PART\s*1\b|SUBMITTALS\b|QUALITY ASSURANCE", re.IGNORECASE)
# Where specifications are kept, and how their files are named.
_CANDIDATE_PATH_RE = re.compile(
    r"spec|tender|particular|division\s*(2\d|16)|input|estimation|consultant|back\s*doc|ifc\s*doc", re.IGNORECASE
)
# Documents by the platform itself: "Specification FAS 20260913-103340.pdf",
# an engineer's upload, trusted for the system they chose.
UPLOAD_NAME_RE = re.compile(r"^Specification\s+([A-Z]{2,5})\s+\d{8}-\d{6}\.pdf$", re.IGNORECASE)
_CSI_FILENAME_RE = re.compile(r"^\d{5,6}\b|\b\d{2}\s?\d{2}\s?\d{2}\b")

# A corrupt PDF in a synced folder is skipped; MuPDF need not print about it.
pymupdf.TOOLS.mupdf_display_errors(False)
# A drawing, a submittal or the compliance statement itself is not a spec,
# however much of the system's name it carries.
_NOT_A_SPEC_RE = re.compile(
    r"compliance|submittal|shop\s*drawing|layout|sample|approval|transmittal|warranty|certificate|form\b|"
    r"schematic|diagram|daigram|riser|[-_ ]DR[-_ ]|\bdwg\b",
    re.IGNORECASE,
)
# Past material submittals, which carry the section they answer. Opened only
# for a system nothing else was found for, a few at a time.
MAX_SUBMITTALS = 8
MAX_SUBMITTAL_BYTES = 150 * 1024 * 1024
_SUBMITTAL_PATH_RE = re.compile(
    r"(^|[\\/])(ms|mas|material\s*submittals?|technical\s*submittals?|submittals?)([\\/]|$)|material\s*submittal|\bMAT-",
    re.IGNORECASE,
)
_NOT_IN_SUBMITTAL_RE = re.compile(
    r"complian|warranty|licen[cs]e|certificate|\bcoo\b|origin|letter|transmittal|\bsom\b|calc|drawing|layout|"
    r"comment|reply|trade|\biso\b|\bcd\b|approval|\bapp\b|\back\b",
    re.IGNORECASE,
)
_ABBREVIATIONS = {
    "FAS": ("fa", "fas", "fave", "fafd"),
    "EML": ("em", "eml", "elm", "emlsc"),
    "VES": ("ve", "ves", "fave"),
    "CBS": ("cbs",),
    "PAVA": ("pa", "pava", "bgm"),
}
# The content pass: PDFs named for nothing, opened for their first page.
# Bounded because on a synced drive opening a file can mean downloading it.
MAX_CONTENT_PDFS = 150
MAX_CONTENT_BYTES = 40 * 1024 * 1024
_DRAWING_PATH_RE = re.compile(r"drawing|\bdwg\b|layout|-model\b|schematic|riser|\bsd\b|shop\s*dr|\bifc\b|\bcad\b", re.IGNORECASE)
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
    # Whether it is this project's specification, for this system
    # (app.compliance.verify), filled in by the caller that knows the project.
    verification: dict | None = None

    @property
    def pages(self) -> int | None:
        if self.first_page is None or self.last_page is None:
            return None
        return self.last_page - self.first_page + 1


def _normalized(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text).lower()


def section_numbers(text: str) -> set[str]:
    """Section numbers as digits: "28 31 11", "283111" and "28-31-11" all
    read as 283111."""
    found = {m.replace(" ", "").replace("-", "") for m in re.findall(r"\b\d{2}[\s-]?\d{2}[\s-]?\d{2}\b", text)}
    # The five-digit 1995 numbers only where a word says they are a section,
    # so a quantity or a plot number is not read as one.
    found |= set(re.findall(r"\b(?:section|division)\s*[-:]?\s*(\d{5})\b", text, re.IGNORECASE))
    found |= set(re.findall(r"^(\d{5})\b", text.strip()))
    return found


def system_of(text: str) -> list[SystemSpec]:
    """The systems a title or heading covers, by its number then its words."""
    numbers = section_numbers(text)
    normalized = _normalized(text)
    found = []
    for spec in SYSTEMS.values():
        if numbers & set(spec.sections) or any(k in normalized for k in spec.keywords):
            found.append(spec)
    return found


def _snippet(page: pymupdf.Page) -> str:
    return re.sub(r"\s+", " ", page.get_text()).strip()[:SNIPPET_CHARS]


def _sections_in(doc: pymupdf.Document, wanted: set[str], *, contiguous: bool = False) -> list[SpecMatch]:
    """Where each wanted system's section starts and ends inside a combined
    specification: from its heading to the next section's. With
    `contiguous`, the section ends at the last page of the run that still
    carries its number -- inside a submittal, the pages after it are
    datasheets, not the next section."""
    starts: list[tuple[int, str, str, list[SystemSpec]]] = []
    carrying: dict[str, set[int]] = {}
    for number, page in enumerate(doc, start=1):
        text = page.get_text()[:1500]
        heading = _SECTION_HEADING_RE.search(text)
        if not heading:
            continue
        section_no = "".join(heading.groups())
        carrying.setdefault(section_no, set()).add(number)
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
            if contiguous:
                end = page_no
                while end + 1 in carrying.get(section_no, ()):
                    end += 1
                next_page = min(next_page, end)
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
    # It has to read like a specification, not merely mention a system: a
    # section heading, or a specification's parts on a document of more than
    # one page (a drawing's notes say "GENERAL" too).
    if not reads_like_a_spec(doc):
        return []

    by_name = [spec for spec in system_of(name) if spec.code in wanted]
    matched_on = "filename"
    if not by_name or _NOT_OURS_RE.search(name):
        found = _sections_in(doc, wanted)
        if found:
            for match in found:
                match.path, match.filename, match.member = relative, name, member
            return found
        # No section number anywhere -- some consultants write "( I ) FIRE
        # DETECTION SYSTEM / PART 1 - GENERAL" and never number the section.
        # Then the document is the system's specification when its opening
        # names the system, whatever the file is called.
        by_name = [spec for spec in system_of(_opening(first)) if spec.code in wanted]
        matched_on = "content"
        if not by_name or _NOT_OURS_RE.search(_opening(first)):
            return []
    return [
        SpecMatch(
            system_code=spec.code,
            path=relative,
            filename=name,
            member=member,
            kind="document",
            section_no=next(iter(section_numbers(name) & set(spec.sections)), None),
            heading=Path(name).stem if matched_on == "filename" else _title(first) or Path(name).stem,
            first_page=1,
            last_page=doc.page_count,
            snippet=_snippet(doc[0]) if doc.page_count else "",
            matched_on=matched_on,
        )
        for spec in by_name
    ]


def _opening(first_page_text: str) -> str:
    """The title area of a first page: what comes before the body starts."""
    body = re.search(r"PART\s*1\b|1\.1\s|1\.0\s|GENERAL\b", first_page_text)
    return first_page_text[: body.start() if body and body.start() > 20 else 600][:600]


def _title(first_page_text: str) -> str | None:
    lines = [l.strip(" -–:()IVX") for l in _opening(first_page_text).splitlines()]
    lines = [l for l in lines if len(l) > 6 and re.search(r"[A-Za-z]{3}", l)]
    return lines[0][:120] if lines else None


def reads_like_a_spec(doc: pymupdf.Document) -> bool:
    """Whether a document opens the way a specification does."""
    first = doc[0].get_text() if doc.page_count else ""
    return bool(_SECTION_HEADING_RE.search(first[:2000]) or (doc.page_count > 1 and _SPEC_BODY_RE.search(first[:2000])))


def _submittal_rank(relative: str, name: str, missing: set[str]) -> int | None:
    """Worth opening to look for the specification pages a past submittal
    carries: a material submittal, preferably one named for the system.
    None when it is not a submittal, or is one of its loose attachments."""
    if not _SUBMITTAL_PATH_RE.search(relative) or _NOT_IN_SUBMITTAL_RE.search(name):
        return None
    named = {s.code for s in system_of(name)} | set(_systems_by_abbreviation(name))
    return 0 if named & missing else 1


def _systems_by_abbreviation(name: str) -> list[str]:
    padded = f" {re.sub(r'[^a-z0-9]+', ' ', name.lower())} "
    return [code for code, words in _ABBREVIATIONS.items() if any(f" {w} " in padded for w in words)]


def _content_rank(relative: str, name: str, missing: set[str]) -> int | None:
    """Worth opening to read its first page, once nothing was found by name:
    any PDF that is not a drawing, the ones named for the system first."""
    if _NOT_A_SPEC_RE.search(name) or _DRAWING_PATH_RE.search(relative):
        return None
    named = {s.code for s in system_of(name)} | set(_systems_by_abbreviation(name))
    return 0 if named & missing else 1


def find_specs(root: Path, wanted: set[str], *, look_in_submittals: bool = True,
               read_contents: bool = True) -> tuple[list[SpecMatch], list[str]]:
    """Every specification in the project folder for the wanted systems.

    Three passes, each only for the systems the one before left without:
    documents named or filed as specifications; the project's past material
    submittals, which carry the section they were submitted against
    (EP-30353's PA/BGM submittal holds its 27 51 16); and finally the first
    page of every other PDF that is not a drawing, for a specification filed
    under a name that says nothing ("Project specs.pdf", "Scan0012.pdf")."""
    matches: list[SpecMatch] = []
    warnings: list[str] = []
    submittals: list[tuple[str, Path]] = []
    others: list[tuple[str, Path]] = []
    seen = 0
    for path in sorted(root.rglob("*")):
        if seen >= MAX_PDFS:
            warnings.append(f"Stopped after {MAX_PDFS} files; the folder holds more.")
            break
        suffix = path.suffix.lower()
        relative = str(path.relative_to(root))
        if suffix == ".pdf":
            if not looks_like_a_spec(relative, path.name):
                if look_in_submittals and _submittal_rank(relative, path.name, wanted) is not None:
                    submittals.append((relative, path))
                elif read_contents and _content_rank(relative, path.name, wanted) is not None:
                    others.append((relative, path))
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

    missing = wanted - {m.system_code for m in matches}
    if missing and submittals:
        ranked = sorted(submittals, key=lambda item: (_submittal_rank(item[0], item[1].name, missing), item[0]))
        for relative, path in ranked[:MAX_SUBMITTALS]:
            if not missing:
                break
            try:
                if path.stat().st_size > MAX_SUBMITTAL_BYTES:
                    continue
                with pymupdf.open(path) as doc:
                    found = _sections_in(doc, missing, contiguous=True)
            except Exception:  # noqa: BLE001 -- unreadable or online-only
                continue
            for match in found:
                match.path, match.filename, match.member = relative, path.name, None
                match.matched_on = "submittal"
            matches += found
            missing -= {m.system_code for m in found}

    missing = wanted - {m.system_code for m in matches}
    if missing and others:
        ranked = sorted(others, key=lambda item: (_content_rank(item[0], item[1].name, missing), item[0]))
        opened = 0
        for relative, path in ranked:
            if not missing or opened >= MAX_CONTENT_PDFS:
                break
            try:
                if path.stat().st_size > MAX_CONTENT_BYTES:
                    continue
                opened += 1
                with pymupdf.open(path) as doc:
                    found = _read(doc, path.name, relative, None, missing)
            except Exception:  # noqa: BLE001 -- unreadable or online-only
                continue
            matches += found
            missing -= {m.system_code for m in found}
        if opened >= MAX_CONTENT_PDFS and missing:
            warnings.append(f"Read the first page of {MAX_CONTENT_PDFS} unnamed PDFs without finding "
                            f"{', '.join(sorted(missing))}; the folder holds more.")
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
