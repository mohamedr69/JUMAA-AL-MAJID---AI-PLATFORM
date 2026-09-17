"""Assemble a material submittal package: cover, index, dividers, documents.

This is the submittal the team issues, built the way they build it by hand.
All of it is fixed here in Python -- the section list, where each section's
documents come from, how the cover and the dividers are made -- so the same
package comes out the same way on every project, with no model in the loop.

**The section list is the company's own.** It is read off
`templates/Index & divider.pdf` in the submittal builder (the index page lists
all seventeen), and a section keeps its number there whether or not this
submittal includes it: a document controller expects Technical Data Sheet to
be section 07 on every package, so leaving one out renumbers nothing.

**Where a section's documents come from** is one of three places:

- the **submittal builder** -- the company documents that are the same on
  every job (Company Profile, Trade License, certificates, test reports);
- the **project** -- its specification, its drawings, the schedule built from
  its BOQ, and the datasheets for the parts the BOQ actually quotes;
- **nowhere yet** -- a section the platform cannot produce, which is carried
  into the package as a divider with no content rather than silently dropped.

A document the builder does not hold is looked for in the project's own
folder before it is called missing -- not across the whole archive, which is
a synced OneDrive tree where that search costs more than an hour.

Two of the company's templates are not PDFs, and each is handled the way its
document works. **Country of Origin** (.xlsx) is a table, so it is drawn here
from the sheet's data, laid out like the Schedule of Material. The **warranty**
(.docx) is a letter on the company letterhead, so it is filled in and converted
by Word rather than redrawn -- a tidier page built from its words would be a
different document wearing its text. Any other non-PDF original in the builder
is reported as needing a PDF rather than quietly left out.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from app.services import boq_provenance

from app.models import Project

# The company's index, in template order. The number is the section's
# permanent identity, not its position in a particular package.
SECTIONS: list[tuple[int, str]] = [
    (1, "Company Profile"),
    (2, "Copy of Related Specification"),
    (3, "Approved List of Manufacturers"),
    (4, "Compliance Statement"),
    (5, "Schedule of Material"),
    # Added after the schedule on the platform owner's instruction. It is not
    # in the printed Index & divider template, which lists seventeen, so its
    # divider is made from one of the template's (see build_divider) and
    # every section after it moves down one.
    (6, "Battery Calculation"),
    (7, "Product Catalogue or Brochure"),
    (8, "Technical Data Sheet"),
    (9, "Copy of Related Drawings"),
    (10, "Trade License"),
    (11, "Certifications"),
    (12, "Test Reports"),
    (13, "Project Reference List"),
    (14, "Previous Approvals"),
    (15, "Country of Origin"),
    (16, "Warranty Certificate"),
    (17, "Material Sample Photos"),
    (18, "Others Documents (Certificates, Approvals Etc)"),
]

SECTION_NAMES = dict(SECTIONS)

# Which folder of the submittal builder holds each section's documents. A
# section not listed here is not a company document: it comes from the
# project, or it is not built yet.
LIBRARY_FOLDERS: dict[int, tuple[str, ...]] = {
    1: ("Company Profile",),
    7: ("Product Catalogue or Brochure",),
    10: ("Trade License",),
    11: ("ISO Certificates", "Civil defence certificates"),
    12: ("Test certificates",),
    13: ("Project Reference List",),
    14: ("Previous Approvals",),
    # 15 Country of Origin and 16 Warranty Certificate are drawn from their
    # templates, not merged -- see build_country_of_origin / build_warranty.
    17: ("templates/will_be_submitted_separatelly.pdf",),
    18: ("templates/not_applicable.pdf",),
}

# Sections the platform fills from the project itself.
SPEC_SECTION = 2
COMPLIANCE_SECTION = 4
SCHEDULE_SECTION = 5
BATTERY_SECTION = 6
DATASHEET_SECTION = 8
DRAWINGS_SECTION = 9

# Sections whose content the platform cannot produce yet. They are offered,
# and a package that includes one gets its divider and nothing behind it --
# which is what the checklist means by a section still to be filled.
NOT_BUILT: dict[int, str] = {
    3: "The approved list of manufacturers is not held by the platform yet.",
    4: "The compliance statement is written clause by clause and is not generated yet.",
    9: "Drawings are not assembled into the submittal yet.",
}

TEMPLATES = "templates"
COVER_TEMPLATE = "Cover Page - Material Submittal - R0.pdf"


def _template(library_root: Path, name: str) -> Path:
    """A template of the submittal builder. Templates are the company's, not
    a brand's, so they are found under COMMON (or the root, in the older
    layout); the path is returned even when the file is not there, so the
    caller's own "missing" handling still names it."""
    from app.services import company_library

    return company_library.submittal_path(library_root, None, f"{TEMPLATES}/{name}") or library_root / TEMPLATES / name
INDEX_TEMPLATE = "Index & divider.pdf"

_MERGEABLE = ".pdf"


@dataclass
class PackageDocument:
    """One file that goes into a section, or one that should and cannot."""

    name: str
    path: str | None = None
    source: str = ""          # "submittal builder", "project archive", "generated"
    pages: int = 0
    part_no: str | None = None
    # Every BOQ part this one sheet serves. Edwards documents a family on a
    # single sheet, so one PDF can cover several quoted parts -- it is merged
    # once and names them all, rather than appearing once per part.
    covers: list[str] = field(default_factory=list)
    missing_reason: str | None = None


@dataclass
class PackageSection:
    number: int
    name: str
    selected: bool
    documents: list[PackageDocument] = field(default_factory=list)
    note: str | None = None

    @property
    def found(self) -> int:
        # A generated section (the schedule) has no file until the package is
        # built, but it is not missing anything.
        return sum(1 for d in self.documents if d.path or d.source == "generated")

    @property
    def missing(self) -> int:
        return sum(1 for d in self.documents if not d.path and d.source != "generated")


@dataclass
class PackagePlan:
    sections: list[PackageSection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    library_found: bool = False
    library_path: str | None = None
    # The manufacturer the package is for; whose submittal-builder folder it draws on.
    brand: str | None = None

    @property
    def selected_sections(self) -> list[PackageSection]:
        return [s for s in self.sections if s.selected]


# A spare copy left in the library ("... _copy.pdf", "... - Copy.pdf") is the
# same document twice, and twice in an issued submittal is a defect.
_COPY_RE = re.compile(r"[ _-]*(?:copy|\(\d+\))$", re.IGNORECASE)


def _pdfs_in(folder: Path) -> list[Path]:
    """Every PDF under a library folder, shallowest first, in name order."""
    if not folder.is_dir():
        return []
    found = sorted(
        (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == _MERGEABLE),
        key=lambda p: (len(p.parts), p.name.lower()),
    )
    kept: list[Path] = []
    stems = set()
    for path in found:
        stem = _COPY_RE.sub("", path.stem).strip().lower()
        if stem in stems:
            continue
        stems.add(stem)
        kept.append(path)
    return kept


def _non_pdf_originals(folder: Path) -> list[Path]:
    """Controlled originals a section holds that cannot be merged as they are."""
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".xlsx", ".xls", ".docx", ".doc"}),
        key=lambda p: p.name.lower(),
    )


def _library_documents(library: Path, section: int, brand: str | None = None) -> list[PackageDocument]:
    from app.services import company_library

    found: list[PackageDocument] = []
    for entry in LIBRARY_FOLDERS.get(section, ()):
        # The brand's own folder first, then COMMON, then the older flat layout.
        target = company_library.submittal_path(library, brand, entry) or library / entry
        if target.is_file():
            if target.suffix.lower() == _MERGEABLE:
                found.append(PackageDocument(name=target.name, path=str(target), source="submittal builder"))
            else:
                # A controlled .docx/.xlsx original. Converting it here would
                # need Office and would change the document on its way in.
                found.append(PackageDocument(
                    name=target.name, source="submittal builder",
                    missing_reason=f"{target.suffix.lstrip('.').upper()} original -- add a PDF of it to the submittal builder.",
                ))
            continue
        pdfs = _pdfs_in(target)
        for pdf in pdfs:
            found.append(PackageDocument(name=pdf.name, path=str(pdf), source="submittal builder"))
        if not target.exists():
            found.append(PackageDocument(name=entry, missing_reason="Not in the submittal builder."))
        elif not pdfs:
            # The folder is there but holds only originals (Country of Origin
            # is an .xlsx). Name them, so the gap is a task and not a silence.
            originals = _non_pdf_originals(target)
            for original in originals:
                found.append(PackageDocument(
                    name=original.name, source="submittal builder",
                    missing_reason=f"{original.suffix.lstrip('.').upper()} original -- add a PDF of it to the submittal builder.",
                ))
            if not originals:
                found.append(PackageDocument(name=entry, missing_reason="The submittal builder folder is empty."))
    return found


# Searching for a missing company document is capped hard, because almost
# every call is a miss and a miss must be cheap.
_SEARCH_MAX_FILES = 5000


def _search_archive(folder: Path | None, name: str) -> Path | None:
    """Look for a document the builder does not hold, inside this project.

    The builder is the intended home for these, but a company document is
    sometimes filed only in the project that last used it -- so **the
    project's own folder** is where it is looked for.

    It is deliberately not the whole archive. The archive is a synced
    OneDrive tree, and walking it even to depth four took **seventy minutes**
    for a single document that was not there: every directory entry is a
    cloud-placeholder lookup. A project folder is a few hundred files and
    answers in well under a second.
    """
    import os

    if folder is None or not folder.is_dir():
        return None
    wanted = re.sub(r"[^a-z0-9]", "", name.lower())
    if not wanted:
        return None

    seen = 0
    for dirpath, _dirnames, filenames in os.walk(folder, onerror=lambda _exc: None):
        for filename in filenames:
            if not filename.lower().endswith(_MERGEABLE):
                continue
            seen += 1
            if seen > _SEARCH_MAX_FILES:
                return None
            if wanted in re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower()):
                return Path(dirpath) / filename
    return None


def datasheet_documents(project: Project, libraries: dict, system_code: str | None = None,
                        links: dict | None = None) -> list[PackageDocument]:
    """One datasheet per part the BOQ quotes, each included once.

    A part legitimately recurs all over a BOQ -- every panel has its own CPU
    -- and the same part number under two headings is still one product, so
    its datasheet belongs in the package once. Parts are taken in BOQ order
    and the first appearance of each part number is the one kept; a datasheet
    shared by several parts (one sheet covering a family) is also merged in
    only once.

    A submittal covers one system, so only that system's lines are read. A
    fire alarm package that walked the whole BOQ would carry the emergency
    lighting parts as well, and report every one of them as a missing
    datasheet because they are another manufacturer's.

    `links` is the equipment table by key (app.services.equipment_currents
    .index): a part with a datasheet recorded there takes that sheet before
    the library is searched. That is how SL2-65D3D-CGL-M, which no sheet is
    named for and which its sheet calls SL2MNM65D3D, gets its datasheet.
    """
    from app.services import equipment_currents
    from app.services.datasheet_library import libraries_for

    documents: list[PackageDocument] = []
    seen_parts: set[str] = set()
    seen_paths: set[str] = set()
    wanted = (system_code or "").strip().upper() or None

    for item in project.boq_items:
        if wanted and (item.system_code or "").strip().upper() != wanted:
            continue
        part = (item.catalog_no or "").strip()
        key = re.sub(r"[^A-Z0-9]", "", part.upper())
        if not key or key in seen_parts:
            continue
        seen_parts.add(key)

        match = None
        absolute: Path | None = None
        row = (links or {}).get(equipment_currents.key_of(part))
        if row is not None and row.datasheet_path:
            mapped = equipment_currents.mapped_match(row, libraries)
            if mapped is not None:
                library, match = mapped
                absolute = Path(library.folder) / match.path
        for library in ([] if match is not None else libraries_for(item.manufacturer, libraries)):
            found = library.find(part)
            if found:
                # Best first. A text match is kept: Edwards documents several
                # parts on one sheet, so 3-ZA20A is in ZA.pdf and 4-MIC is in
                # the audio/telephone sheet -- named only in the text, and
                # still the datasheet for that part. Rejecting those lost 17
                # of EP-30784's 51 fire alarm parts.
                match = found[0]
                absolute = Path(library.folder) / match.path
                break
        if match is None or absolute is None:
            documents.append(PackageDocument(
                name=part, part_no=part,
                missing_reason="No datasheet in the manufacturer's library.",
            ))
            continue
        key_path = str(absolute)
        if key_path in seen_paths:
            # One sheet covering several parts: already in, so record that it
            # serves this part too instead of merging it a second time.
            for existing in documents:
                if existing.path == key_path:
                    existing.covers.append(part)
                    break
            continue
        seen_paths.add(key_path)
        documents.append(PackageDocument(
            name=absolute.name, path=key_path,
            source=f"{match.library} datasheet library", part_no=part, covers=[part],
        ))
    return documents


def plan_package(
    project: Project,
    selected: set[int],
    library_root: Path | None,
    project_folder: Path | None,
    datasheet_libraries: dict | None = None,
    spec_documents: list[tuple[str, str]] | None = None,
    system_code: str | None = None,
    battery_panels=None,
    brand: str | None = None,
    datasheet_links: dict | None = None,
) -> PackagePlan:
    """What would go into the package, section by section, without building it.
    `brand` is the manufacturer the submittal's system is for, which decides
    whose folder of the submittal builder the company documents come from."""
    plan = PackagePlan()
    plan.brand = brand
    if library_root is None or not library_root.is_dir():
        plan.warnings.append(
            "The submittal builder folder was not found; company documents cannot be collected. "
            "Set SUBMITTAL_LIBRARY to its path."
        )
    else:
        plan.library_found = True
        plan.library_path = str(library_root)

    for number, name in SECTIONS:
        section = PackageSection(number=number, name=name, selected=number in selected)
        if not section.selected:
            plan.sections.append(section)
            continue

        if number in NOT_BUILT:
            section.note = NOT_BUILT[number]
        elif number == SPEC_SECTION:
            for label, path in spec_documents or []:
                section.documents.append(PackageDocument(name=label, path=path, source="project archive"))
            if not section.documents:
                section.note = "No specification was found for this project."
        elif number == SCHEDULE_SECTION:
            if project.boq_items:
                section.documents.append(PackageDocument(name="Schedule of Material", source="generated"))
            else:
                section.note = "The BOQ is empty, so there is nothing to schedule."
        elif number == BATTERY_SECTION:
            # Sized from the saved BOQ, so it is the same calculation the
            # Battery page shows -- computed by the caller, which has the
            # database the part currents live in.
            if battery_panels:
                section.documents.append(PackageDocument(name="Battery Calculation", source="generated"))
            else:
                section.note = "No panel could be sized from the BOQ, so there is no calculation to enclose."
        elif number == COO_SECTION:
            # Built from the BOQ like the schedule, with the countries taken
            # from the company's own reference sheet.
            if project.boq_items:
                section.documents.append(PackageDocument(name="Country of Origin", source="generated"))
            else:
                section.note = "The BOQ is empty, so there is nothing to declare."
        elif number == WARRANTY_SECTION:
            section.documents.append(PackageDocument(name="Warranty Certificate", source="generated"))
        elif number == DATASHEET_SECTION:
            section.documents = datasheet_documents(project, datasheet_libraries or {}, system_code, datasheet_links)
            if not section.documents:
                section.note = (
                    f"The BOQ quotes no {system_code} part numbers to find datasheets for."
                    if system_code else "The BOQ quotes no part numbers to find datasheets for."
                )
        elif plan.library_found and library_root is not None:
            section.documents = _library_documents(library_root, number, brand)
            # Anything the builder does not hold is looked for in the archive.
            for document in section.documents:
                if document.path is None and document.missing_reason == "Not in the submittal builder.":
                    found = _search_archive(project_folder, document.name)
                    if found is not None:
                        document.path = str(found)
                        document.source = "project archive"
                        document.missing_reason = None
            if not section.documents:
                section.note = "No document is mapped to this section."

        plan.sections.append(section)
    return plan


# --- building ---------------------------------------------------------------


def _rgb(value: int) -> tuple[float, float, float]:
    """0xccdbeb -> (0.80, 0.86, 0.92)."""
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


# The cover's own fonts (Century Gothic, Arial Narrow) are the company's, and
# are installed on the machines this runs on. Where they are not -- a test
# box, a Linux host -- the base-14 equivalents keep the page correct if not
# identical, rather than failing the build.
_FONT_FILES = {
    "gothic": r"C:\Windows\Fonts\GOTHIC.TTF",
    "gothicb": r"C:\Windows\Fonts\GOTHICB.TTF",
    "arialb": r"C:\Windows\Fonts\arialbd.ttf",
    "arialn": r"C:\Windows\Fonts\arialn.ttf",
}
_FALLBACK = {"gothic": "helv", "gothicb": "hebo", "arialb": "hebo", "arialn": "helv"}


def _font(page, name: str) -> tuple[str, str | None]:
    """(fontname, fontfile) for a cover field, falling back to a base font."""
    path = _FONT_FILES.get(name)
    if path and Path(path).is_file():
        try:
            page.insert_font(fontname=name, fontfile=path, set_simple=True)
            return name, path
        except Exception:  # noqa: BLE001
            pass
    return _FALLBACK.get(name, "helv"), None


def _replace(page, old: str, new: str, size: float, colour=(0, 0, 0), font: str = "gothicb") -> bool:
    """Swap one string on a template page, keeping its place and its look.

    Redacted with **no fill**. The project block is a navy box drawn under its
    text, and blanking the text with white punched a white hole through it --
    which is what made the replaced project name look wrong. `fill=False`
    removes the glyphs and leaves the artwork beneath untouched.

    The replacement is written in the colour and size the template used for
    that field, so the white-on-navy title stays white on navy.
    """
    boxes = page.search_for(old)
    if not boxes:
        return False
    for found in boxes:
        page.add_redact_annot(found, fill=False)
    try:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    except TypeError:
        page.apply_redactions()

    fontname, fontfile = _font(page, font)
    # Shrink to fit rather than run over the edge of the box or the cell.
    width = pymupdf.get_text_length(new, fontname=_FALLBACK.get(font, "helv"), fontsize=size)
    room = max(boxes[0].width, 200)
    if width > room:
        size = max(6.0, size * room / width)

    # Every occurrence, not just the first: the template happens to name the
    # same company as Main Contractor and as MEP Contractor, and writing only
    # the first left the MEP cell blank on the issued cover.
    for found in boxes:
        # The template's own baseline, so the line sits where it did.
        page.insert_text((found.x0, found.y1 - found.height * 0.22), new,
                         fontname=fontname, fontfile=fontfile, fontsize=size, color=colour)
    return True


def _project_title(project: Project) -> str:
    return (project.project_name or f"EP-{project.ep_number}").strip()


def build_cover(library_root: Path, project: Project, revision: str, systems: str) -> pymupdf.Document | None:
    """The cover page, from the company's template.

    The template is the approved artwork -- logo, layout, the supplier block
    -- so it is opened and its project fields swapped, never redrawn.
    """
    template = _template(library_root, COVER_TEMPLATE)
    if not template.is_file():
        return None
    doc = pymupdf.open(template)
    page = doc[0]

    # Each field in the size, colour and font the template gave it. The
    # project name is white on the navy block; the two lines under it are the
    # paler blues that block uses; the parties are the company blue.
    plot = (project.plot_number or "").strip()
    location = ", ".join(part for part in [f"PLOT NO. {plot}" if plot else "", (project.location or "").strip()] if part)
    party = lambda value: (value or "").strip() or " "  # noqa: E731

    _replace(page, "Fire Alarm, Voice Evacuation & Fire Telephone System", systems, 13, _rgb(0x8C8C8E), "gothic")
    _replace(page, "SAMANA PARK MEADOWS (DLRC 4)", _project_title(project).upper(), 19, _rgb(0xFFFFFF), "gothicb")
    _replace(page, "PROPOSED 2B + G + 16 + R RESIDENTIAL BUILDING",
             (project.other_information or "").strip().upper() or " ", 11, _rgb(0xCCDBEB), "gothic")
    _replace(page, "PLOT NO. 648-8670, WADI AL SAFA 5, DLRC, DUBAI, U.A.E.",
             location.upper() or " ", 10, _rgb(0xA8BDD4), "gothic")
    _replace(page, "M/s. Samana IFS Holding Limited", party(project.client), 11, _rgb(0x0E4E89), "arialb")
    _replace(page, "M/s. Al Hilal Engineering Consultant", party(project.consultant), 11, _rgb(0x0E4E89), "arialb")
    _replace(page, "M/s. Italtech Contracting L.L.C", party(project.contractor), 11, _rgb(0x0E4E89), "arialb")
    _replace(page, "EP-30058", f"EP-{project.ep_number}", 9.5, _rgb(0x1F242B), "arialn")
    # Searched with its label's spacing so a bare "R0" elsewhere is not hit.
    _replace(page, "R0", revision, 9.5, _rgb(0x1F242B), "arialn")
    _replace(page, "06-09-2026", date.today().strftime("%d-%m-%Y"), 9.5, _rgb(0x1F242B), "arialn")
    return doc


def build_index(library_root: Path, plan: PackagePlan, project: Project) -> pymupdf.Document:
    """The index page, listing the sections this package carries.

    Drawn rather than taken from the template, because the template's index
    lists all seventeen and this one lists what was chosen. Section numbers
    stay the template's.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595.32, height=841.92)
    page.insert_text((44, 70), "PROJECT:", fontname="hebo", fontsize=9, color=(0.45, 0.45, 0.45))
    page.insert_text((44, 88), _project_title(project).upper()[:70], fontname="hebo", fontsize=13)
    page.insert_text((44, 130), "SUBMITTAL INDEX", fontname="hebo", fontsize=20, color=(0.75, 0.1, 0.15))

    top = 165
    page.draw_rect(pymupdf.Rect(44, top, 551, top + 24), color=None, fill=(0.75, 0.1, 0.15))
    page.insert_text((56, top + 16), "NO.", fontname="hebo", fontsize=9.5, color=(1, 1, 1))
    page.insert_text((100, top + 16), "DESCRIPTION", fontname="hebo", fontsize=9.5, color=(1, 1, 1))
    page.insert_text((420, top + 16), "STATUS", fontname="hebo", fontsize=9.5, color=(1, 1, 1))

    y = top + 24
    for section in plan.selected_sections:
        page.draw_rect(pymupdf.Rect(44, y, 551, y + 26), color=(0.85, 0.85, 0.85), width=0.5)
        page.insert_text((56, y + 17), f"{section.number:02d}", fontname="helv", fontsize=9.5)
        page.insert_text((100, y + 17), section.name[:58], fontname="helv", fontsize=9.5)
        status = "Enclosed" if section.found else "To be filled"
        page.insert_text((420, y + 17), status, fontname="helv", fontsize=9, color=(0.35, 0.35, 0.35))
        y += 26
    return doc


def _centre(page, bbox, text: str, size: float, colour, font: str = "hebo") -> None:
    """Replace a centred line, keeping it centred in the box it occupied."""
    page.add_redact_annot(pymupdf.Rect(bbox), fill=False)
    try:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    except TypeError:
        page.apply_redactions()
    middle = (bbox[0] + bbox[2]) / 2
    while size > 5 and pymupdf.get_text_length(text, fontname=font, fontsize=size) > 470:
        size -= 0.5
    width = pymupdf.get_text_length(text, fontname=font, fontsize=size)
    page.insert_text((middle - width / 2, bbox[3] - (bbox[3] - bbox[1]) * 0.14), text,
                     fontname=font, fontsize=size, color=colour)


def build_divider(library_root: Path, number: int, project: Project) -> pymupdf.Document | None:
    """The section divider, on the company's divider artwork.

    The template holds one divider per section after its index page, found by
    the **name** printed on it -- not by its number, which the template fixed
    when the index had seventeen sections. Adding Battery Calculation moved
    every section after it down one, so a number match would now miss every
    one of them.

    Two things are therefore rewritten on the page: the big number, to the
    section's number in *this* index, and the project. A section the template
    has no divider for -- Battery Calculation -- borrows another's artwork
    and has its title rewritten too, so it looks like the rest of the set
    rather than like a page from somewhere else.
    """
    template = _template(library_root, INDEX_TEMPLATE)
    if not template.is_file():
        return None
    name = SECTION_NAMES[number]
    wanted = name.split("(")[0].strip().lower()

    with pymupdf.open(template) as source:
        pages = [i for i in range(len(source)) if "SUBMITTAL SECTION" in source[i].get_text()]
        if not pages:
            return None
        match = next((i for i in pages if wanted in source[i].get_text().lower()), None)
        borrowed = match is None
        divider = pymupdf.open()
        divider.insert_pdf(source, from_page=match if match is not None else pages[0],
                           to_page=match if match is not None else pages[0])

    page = divider[0]
    spans = [
        span
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["text"].strip()
    ]
    # The big pale number, and the section title under it.
    printed = next((s for s in spans if s["text"].strip().isdigit() and s["size"] > 40), None)
    title = next((s for s in spans if 15 < s["size"] < 40), None)

    if borrowed and title is not None:
        _centre(page, title["bbox"], name, title["size"], _rgb(title["color"]))
    if printed is not None and printed["text"].strip() != f"{number:02d}":
        _centre(page, printed["bbox"], f"{number:02d}", printed["size"], _rgb(printed["color"]))
    # The small "PAGE 07" the template prints under the number is the
    # template's numbering too, and was left saying 07 on section 08.
    for span in spans:
        label = span["text"].strip()
        if re.fullmatch(r"PAGE\s*\d{1,2}", label, re.IGNORECASE) and label.upper() != f"PAGE {number:02d}":
            _centre(page, span["bbox"], f"PAGE {number:02d}", span["size"], _rgb(span["color"]), font="helv")

    _replace(page, "IVY GARDEN 2 - 1B+G+5P+34+R RESIDENTIAL BUILDING", _project_title(project).upper()[:60], 11)
    return divider


# What the BOQ's ungrouped lines are: the detectors, sounders, call points
# and modules that hang off the panels rather than sitting inside one. The
# design sheets give them a block of their own, so the schedule does too.
UNGROUPED_BLOCK = "Field Devices"

_RED = (0.75, 0.1, 0.15)


ADDED_BLOCK = "Proposed materials (added beyond the BOQ)"


def schedule_blocks(project: Project, system_code: str | None = None) -> list[tuple[str, str, list]]:
    """The BOQ as the design sheet lays it out: a lettered block per assembly.

    A flat run of every line in sequence is not how the engineers read a
    schedule -- the design sheet gives each assembly its own block (the main
    panel, the second panel, an amplifier cabinet, a booster power supply)
    and lists that assembly's parts under it. The BOQ already carries the
    grouping, because extraction kept each sheet's headings, so the blocks
    are its `group_heading`s in BOQ order.

    Returns (letter, title, lines). The title is the group heading -- "EST4
    Main Fire Alarm Control Panel", "Booster Power Supply" -- and never the
    group's own heading line, the one with no part number that spells the
    assembly out ("EST4 fire alarm control panel complete with power
    supply/charger, sealed lead acid..."). That line is a description of the
    assembly, not a name for it: using it made the main panel and the second
    panel read as the same block. It is left out of the parts as well, being
    the heading rather than one of them.
    """
    from app.services.system_rules import effective_code

    wanted = effective_code(system_code, project)
    order: list[str] = []
    grouped: dict[str, list] = {}
    for item in project.boq_items:
        if wanted and effective_code(item.system_code, project) != wanted:
            continue
        key = (item.group_heading or "").strip() or UNGROUPED_BLOCK
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    # The materials proposed on the Proposed Materials tab beyond the BOQ,
    # under the same system: the schedule is what is proposed for approval,
    # so they are on it, as a block of their own after the BOQ's.
    from sqlalchemy.orm import Session as _Session

    from app.models import ProjectProposedMaterial

    session = _Session.object_session(project)
    added = []
    if session is not None:
        for row in session.query(ProjectProposedMaterial).filter(ProjectProposedMaterial.project_id == project.id).order_by(ProjectProposedMaterial.id):
            if wanted and effective_code(row.system_code, project) != wanted:
                continue
            added.append(row)
    # The engineers' order, whatever the sheet's: the panel and its
    # equipment first, then the repeater panels, then the APS and BPS
    # cabinets, and the field devices last -- the BOQ's own order within
    # each; the materials added on the tab at the end.
    from app.services.battery_calculation import classify_group

    rank = {"panel": 0, "repeater": 1, "aps": 2, "bps": 3}
    order.sort(key=lambda key: rank.get(classify_group(None if key == UNGROUPED_BLOCK else key), 4))
    if added:
        order.append(ADDED_BLOCK)
        grouped[ADDED_BLOCK] = added

    blocks: list[tuple[str, str, list]] = []
    for index, key in enumerate(order):
        items = grouped[key]
        # The heading line describes the assembly; it is not one of its parts.
        parts = [i for i in items if (i.catalog_no or "").strip()]
        letter = chr(ord("A") + index) if index < 26 else f"A{index}"
        blocks.append((letter, key, parts or items))
    return blocks


def build_schedule(project: Project, system_code: str | None = None) -> pymupdf.Document:
    """The Schedule of Material, one block per assembly (see schedule_blocks).

    **No quantity or unit column**, on every project. The schedule says what
    material is proposed for approval; how much of it the project buys is the
    BOQ's business and changes after the submittal has been approved. Putting
    it here invites the consultant to review a number that the document is
    not the record of.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=841.92, height=595.32)  # landscape
    columns = [("SL.", 42), ("CAT. NO.", 82), ("DESCRIPTION", 200), ("MANUFACTURER", 640)]
    left, right = 36, 806

    def header(first: bool) -> float:
        nonlocal page
        if not first:
            page = doc.new_page(width=841.92, height=595.32)
        page.insert_text((40, 50), "SCHEDULE OF MATERIAL", fontname="hebo", fontsize=16, color=_RED)
        page.insert_text((40, 68), _project_title(project).upper()[:90], fontname="helv", fontsize=9, color=(0.4, 0.4, 0.4))
        if system_code:
            page.insert_text((40, 82), system_code, fontname="hebo", fontsize=8.5, color=(0.45, 0.45, 0.45))
        y = 94
        page.draw_rect(pymupdf.Rect(left, y, right, y + 20), color=None, fill=_RED)
        for label, x in columns:
            page.insert_text((x, y + 14), label, fontname="hebo", fontsize=8.5, color=(1, 1, 1))
        return y + 20

    y = header(first=True)

    def room(needed: float) -> None:
        nonlocal y
        if y + needed > 552:
            y = header(first=False)

    for letter, title, items in schedule_blocks(project, system_code):
        # Keep a block's heading with at least its first line.
        room(46)
        page.draw_rect(pymupdf.Rect(left, y, right, y + 22), color=None, fill=(0.93, 0.94, 0.96))
        page.insert_text((42, y + 15), letter, fontname="hebo", fontsize=9.5, color=_RED)
        page.insert_text((62, y + 15), title.upper()[:96], fontname="hebo", fontsize=9, color=(0.06, 0.12, 0.21))
        # How many line items the block lists -- not a quantity of anything.
        count = len(items)
        label = f"{count} item{'' if count == 1 else 's'}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=7.5)
        page.insert_text((right - 8 - width, y + 15), label,
                         fontname="helv", fontsize=7.5, color=(0.45, 0.45, 0.45))
        y += 22

        for number, item in enumerate(items, 1):
            room(20)
            page.draw_rect(pymupdf.Rect(left, y, right, y + 18), color=(0.87, 0.87, 0.87), width=0.4)
            cells = [
                (f"{letter}{number}", 42),
                ((item.catalog_no or "-")[:18], 82),
                ((item.description or "")[:104], 200),
                ((item.manufacturer or "-")[:24], 640),
            ]
            for text, x in cells:
                page.insert_text((x, y + 12), text, fontname="helv", fontsize=7.5)
            y += 18
        y += 8   # air between blocks, as the design sheet has
    return doc


def _number_pages(out: pymupdf.Document) -> None:
    """Continuous page numbers, bottom right, skipping the cover.

    Right-aligned by measuring the string: a fixed offset runs off the edge of
    a landscape page and clips the last digit.
    """
    total = out.page_count
    for index in range(1, total):
        page = out[index]
        text = f"Page {index + 1} of {total}"
        width = pymupdf.get_text_length(text, fontname="helv", fontsize=7.5)
        page.insert_text((page.rect.width - 40 - width, page.rect.height - 16), text,
                         fontname="helv", fontsize=7.5, color=(0.42, 0.42, 0.42))


def _battery_sheet(project: Project, panels, systems: str) -> pymupdf.Document:
    """The battery calculation, on the company's own sheet.

    The same document the Battery page exports, so the copy in the submittal
    and the copy sent separately are the one calculation.
    """
    from app.services.battery_pdf import battery_calculation_pdf, panel_manufacturer

    panels = list(panels or [])
    data = battery_calculation_pdf(
        project, panels, systems=systems.upper(),
        manufacturers={p.key: panel_manufacturer(project, p) for p in panels},
    )
    return pymupdf.open(stream=data, filetype="pdf")


@dataclass
class BuiltPackage:
    pdf: bytes
    manifest: list[tuple[str, str, int, int]]   # section, document, first page, last page
    warnings: list[str]
    pages: int


class PackageBuildError(RuntimeError):
    """A package that could not be assembled, naming the stage that failed.

    "The package could not be built" told the engineer nothing about which
    of forty documents to look at. The stage -- a section's generated page,
    a template, the final write -- is what makes the failure actionable.
    """


def build_package(
    project: Project,
    plan: PackagePlan,
    library_root: Path | None,
    revision: str = "R0",
    systems: str = "Fire Alarm System",
    system_code: str | None = None,
    battery_panels=None,
) -> BuiltPackage:
    """Merge the package in index order and return it as bytes."""
    out = pymupdf.open()
    manifest: list[tuple[str, str, int, int]] = []
    warnings: list[str] = list(plan.warnings)

    def append(label: str, name: str, source: pymupdf.Document) -> None:
        start = out.page_count + 1
        out.insert_pdf(source)
        manifest.append((label, name, start, out.page_count))

    def attempt(stage: str, produce):
        """Run one stage of the build; a failure names the stage."""
        try:
            return produce()
        except PackageBuildError:
            raise
        except Exception as exc:  # noqa: BLE001 -- pymupdf raises bare exceptions
            raise PackageBuildError(f"{stage} could not be produced ({exc})") from exc

    if library_root is not None and library_root.is_dir():
        cover = attempt("The cover page", lambda: build_cover(library_root, project, revision, systems))
        if cover is not None:
            append("Cover", COVER_TEMPLATE, cover)
            cover.close()
        else:
            warnings.append("The cover template was not found in the submittal builder; the package starts at the index.")

    index = attempt("The index page", lambda: build_index(library_root or Path("."), plan, project))
    append("Index", "index", index)
    index.close()

    for section in plan.selected_sections:
        label = f"{section.number:02d} {section.name}"
        if library_root is not None and library_root.is_dir():
            divider = attempt(f"The divider for {label}", lambda: build_divider(library_root, section.number, project))
            if divider is not None:
                append(f"{label} -- divider", "divider", divider)
                divider.close()

        # The three sections the platform draws rather than merges.
        generated = any(d.source == "generated" for d in section.documents)
        if generated and section.number in (SCHEDULE_SECTION, BATTERY_SECTION, COO_SECTION, WARRANTY_SECTION):
            builder = {
                SCHEDULE_SECTION: lambda: build_schedule(project, system_code),
                BATTERY_SECTION: lambda: _battery_sheet(project, battery_panels, systems),
                COO_SECTION: lambda: build_country_of_origin(project, library_root, system_code, plan.brand),
                WARRANTY_SECTION: lambda: build_warranty(project, library_root, system_code),
            }[section.number]
            page = attempt(f"{label} (generated)", builder)
            append(label, SECTION_NAMES[section.number], page)
            page.close()
            continue

        for document in section.documents:
            if not document.path:
                warnings.append(f"{label}: {document.name} -- {document.missing_reason or 'not found'}")
                continue
            try:
                with pymupdf.open(document.path) as source:
                    append(label, document.name, source)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{label}: {document.name} could not be read ({exc}).")
        if section.note:
            warnings.append(f"{label}: {section.note}")

    _number_pages(out)
    out.set_metadata({
        "title": f"EP-{project.ep_number} - {_project_title(project)} - Material Submittal - {revision}",
        "author": "Al Arabia for Safety & Security L.L.C",
        "subject": f"Material Submittal - {systems}",
        "creator": "Engineering Project Platform",
    })
    pdf = attempt("The finished package", lambda: out.tobytes(deflate=True, garbage=3))
    pages = out.page_count
    out.close()
    return BuiltPackage(pdf=pdf, manifest=manifest, warnings=warnings, pages=pages)


# --- reading a filled-in checklist -----------------------------------------

# The company's Material Submittal Checklist is a table: one numbered row per
# index section, and a tick in one of three columns. The tick is a character
# with a position, not a value in a field, so which column it is in is decided
# by its x against the column headings, and which row by its y against the
# row numbers. Reading it by text alone cannot work -- every row's text is the
# same tick.
_TICKS = "\u2713\u2714\u2612\u00d7xX"
CHECKLIST_COLUMNS = ("yes", "no", "na")


def read_checklist(data: bytes) -> tuple[dict[int, str], list[str]]:
    """Which sections a filled-in checklist marks Yes / No / N/A.

    Returns the answers by section number, and what could not be read. A row
    with no tick is left out rather than assumed, because "not ticked" on a
    checklist is not the same as "not included" -- it is not filled in.
    """
    warnings: list[str] = []
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if not len(doc):
                return {}, ["The checklist has no pages."]
            page = doc[0]
            words = page.get_text("words")
    except Exception as exc:  # noqa: BLE001
        return {}, [f"The checklist could not be read ({exc})."]

    headers: dict[str, float] = {}
    for x0, _y0, x1, _y1, text, *_ in words:
        key = text.strip().lower().replace(".", "")
        if key in ("yes", "no", "n/a", "na") and key not in headers:
            headers["na" if key in ("n/a", "na") else key] = (x0 + x1) / 2
    if "yes" not in headers:
        return {}, ["This does not look like the material submittal checklist: no Yes/No/N/A columns."]

    # The row numbers down the left edge give each section's y.
    rows: dict[int, float] = {}
    left = min(x0 for x0, *_ in words) if words else 0
    for x0, y0, _x1, y1, text, *_ in words:
        text = text.strip()
        if text.isdigit() and x0 < left + 120:
            number = int(text)
            if number in SECTION_NAMES and number not in rows:
                rows[number] = (y0 + y1) / 2
    if not rows:
        return {}, ["No numbered rows were found on the checklist."]

    answers: dict[int, str] = {}
    for x0, y0, x1, y1, text, *_ in words:
        if text.strip() not in _TICKS:
            continue
        tick_x, tick_y = (x0 + x1) / 2, (y0 + y1) / 2
        number = min(rows, key=lambda n: abs(rows[n] - tick_y))
        if abs(rows[number] - tick_y) > 12:
            continue  # not on any row
        column = min(headers, key=lambda c: abs(headers[c] - tick_x))
        if abs(headers[column] - tick_x) > 30:
            continue  # not in any column
        answers[number] = column

    for number, _name in SECTIONS:
        if number not in answers:
            warnings.append(f"Section {number:02d} {SECTION_NAMES[number]} is not ticked on the checklist.")
    return answers, warnings


# --- Country of Origin and the warranty certificate -------------------------
#
# Both sections have a template in the submittal builder that is not a PDF --
# COO.xlsx and Draft Warranty.docx -- so neither could be merged as it stood.
# They are produced here instead, from those same templates, so the company's
# own wording and its own country data stay the authority, and the page is
# built the way the Schedule of Material is.

COO_SECTION = 15
WARRANTY_SECTION = 16

COO_TEMPLATE = "Country Of Origin/COO.xlsx"
WARRANTY_TEMPLATE = "templates/Draft Warranty.docx"

# The standard warranty the company gives. The draft in the builder still
# says TWO YEARS, which is not the standard any more; the period is stated
# here so every project gets the same one.
WARRANTY_YEARS = 1
_YEAR_WORDS = {1: "ONE YEAR", 2: "TWO YEARS", 3: "THREE YEARS", 5: "FIVE YEARS"}


def read_origin_rows(library_root: Path | None, brand: str | None = None) -> list[tuple[str, str, str, str]]:
    """The rows of the company's country-of-origin sheet: (model,
    description, made in, shipped from), header and blanks left out. The
    sheet is the manufacturer's: under the brand's folder of the submittal
    builder, else COMMON, else the older layout."""
    if library_root is None:
        return []
    from app.services import company_library

    template = company_library.submittal_path(library_root, brand, COO_TEMPLATE) or library_root / COO_TEMPLATE
    if not template.is_file():
        return []
    try:
        import openpyxl
        sheet = openpyxl.load_workbook(template, data_only=True).worksheets[0]
    except Exception:  # noqa: BLE001
        return []
    rows: list[tuple[str, str, str, str]] = []
    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row):
        cells = [c.value for c in row]
        if len(cells) < 6:
            continue
        model, description, made_in, shipped = cells[1], cells[2], cells[4], cells[5]
        if not model or not (made_in or shipped):
            continue
        if str(made_in).strip().upper() == "MADE IN":
            continue  # the header row
        rows.append((str(model), str(description or ""), str(made_in or "").strip(), str(shipped or "").strip()))
    return rows


def read_origins(library_root: Path | None, brand: str | None = None) -> dict[str, tuple[str, str]]:
    """Where each model is made and shipped from, out of the COO template.
    The country of origin of a part is a fact about the part, not about the
    project, so the company's filled-in sheet is read as the reference for
    every project. Nothing is inferred: a model the sheet does not list comes
    back blank for an engineer to complete, because inventing a country on a
    customs declaration is not something software should do.
    """
    origins: dict[str, tuple[str, str]] = {}
    for model, description, made_in, shipped in read_origin_rows(library_root, brand):
        where = (made_in, shipped)
        model_text = model.strip().upper()
        # The model as written is a key before it is split: "APS6A/230" is
        # the one part number the BOQ quotes, as well as the pair a slash
        # would make of it. And a model written with a wildcard digit --
        # "757-XA-SS70" covers 757-3A-SS70 and 757-7A-SS70 -- is every
        # number it stands for.
        keys = [re.sub(r"[^A-Z0-9]", "", model_text)]
        if re.search(r"-X[A-Z]?-", model_text):
            keys += [re.sub(r"[^A-Z0-9]", "", model_text.replace("X", digit, 1)) for digit in "0123456789"]
        keys += [re.sub(r"[^A-Z0-9]", "", part.upper()) for part in re.split(r"[\n,/]+", model)]
        # A row can cover a whole set rather than one model: "PANEL
        # ACCESSORIES" declares one origin for the parts named in its
        # description. Those are the part numbers a BOQ actually quotes, so
        # without reading them the panel's own modules all come back blank.
        if description:
            keys += [
                re.sub(r"[^A-Z0-9]", "", token.upper())
                for token in re.split(r"[,\n]+", description)
                # A catalogue number, not prose: short, carrying a digit, and
                # made only of the characters a part number uses. Spaces are
                # allowed because the sheet wraps them ("4- FWAL4", "4-NET- TP")
                # and the key strips them out anyway.
                if re.fullmatch(r"[A-Za-z0-9 /.+-]{3,18}", token.strip()) and re.search(r"\d", token)
            ]
        for key in keys:
            if key and len(key) >= 3 and key not in origins:
                origins[key] = where
    return origins


def origin_key(item, origins: dict, library: dict[str, str]) -> str:
    """The key a BOQ line's origin is under: its own part number when the
    sheet lists it, else the catalogue's spelling of it -- what the part
    library settled it to when it was read (`catalog_canonical`), the
    equipment table's alias for it, or a one-confusion match now."""
    from app.services import equipment_currents

    def k(text) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    key = k(item.catalog_no)
    if not key or key in origins:
        return key
    for candidate in (getattr(item, "catalog_canonical", None), equipment_currents.canonical(item.catalog_no)):
        if candidate and k(candidate) in origins:
            return k(candidate)
    if library:
        settled, _record = boq_provenance.catalogued(item.catalog_no, library)
        if settled and k(settled) in origins:
            return k(settled)
    return key


def build_country_of_origin(
    project: Project, library_root: Path | None, system_code: str | None = None,
    brand: str | None = None,
) -> pymupdf.Document:
    """The Country of Origin table, laid out as the Schedule of Material is.

    Same lettered blocks in the same order, so the two read as one document
    set -- a reviewer who finds a part in the schedule finds it in the same
    place here. The two extra columns are the declaration itself.
    """
    origins = read_origins(library_root, brand)
    # A BOQ line read off a scan may still hold the scan's spelling of a
    # part ("SIGA-AASO" for SIGA-AA50): its origin is looked up under the
    # catalogue's spelling when the reading itself is not on the sheet.
    from sqlalchemy.orm import Session as _Session

    session = _Session.object_session(project)
    library = boq_provenance.part_library(session) if session is not None else {}
    doc = pymupdf.open()
    page = doc.new_page(width=841.92, height=595.32)
    columns = [("SL.", 42), ("MODEL", 82), ("DESCRIPTION", 214), ("MADE IN", 566), ("SHIPPED FROM", 688)]
    left, right = 36, 806

    def header(first: bool) -> float:
        nonlocal page
        if not first:
            page = doc.new_page(width=841.92, height=595.32)
        page.insert_text((40, 50), "COUNTRY OF ORIGIN", fontname="hebo", fontsize=16, color=_RED)
        page.insert_text((40, 68), _project_title(project).upper()[:90], fontname="helv", fontsize=9, color=(0.4, 0.4, 0.4))
        if system_code:
            page.insert_text((40, 82), system_code, fontname="hebo", fontsize=8.5, color=(0.45, 0.45, 0.45))
        y = 94
        page.draw_rect(pymupdf.Rect(left, y, right, y + 20), color=None, fill=_RED)
        for label, x in columns:
            page.insert_text((x, y + 14), label, fontname="hebo", fontsize=8.5, color=(1, 1, 1))
        return y + 20

    y = header(first=True)

    def room(needed: float) -> None:
        nonlocal y
        if y + needed > 552:
            y = header(first=False)

    for letter, title, items in schedule_blocks(project, system_code):
        room(46)
        page.draw_rect(pymupdf.Rect(left, y, right, y + 22), color=None, fill=(0.93, 0.94, 0.96))
        page.insert_text((42, y + 15), letter, fontname="hebo", fontsize=9.5, color=_RED)
        page.insert_text((62, y + 15), title.upper()[:96], fontname="hebo", fontsize=9, color=(0.06, 0.12, 0.21))
        y += 22

        for number, item in enumerate(items, 1):
            room(20)
            page.draw_rect(pymupdf.Rect(left, y, right, y + 18), color=(0.87, 0.87, 0.87), width=0.4)
            key = origin_key(item, origins, library)
            made_in, shipped = origins.get(key, ("", ""))
            cells = [
                (f"{letter}{number}", 42),
                ((item.catalog_no or "-")[:18], 82),
                ((item.description or "")[:78], 214),
                (made_in[:22], 566),
                (shipped[:20], 688),
            ]
            for text, x in cells:
                page.insert_text((x, y + 12), text, fontname="helv", fontsize=7.5)
            y += 18
        y += 8

    room(30)
    page.insert_text(
        (left + 6, y + 12),
        "A blank country is one the reference sheet does not list; it is to be completed before issue.",
        fontname="helv", fontsize=7, color=(0.45, 0.45, 0.45),
    )
    return doc


def _docx_paragraphs(path: Path) -> list[str]:
    """The text of a .docx, paragraph by paragraph.

    A .docx is a zip of XML, so its wording can be read without Word and
    without a new dependency -- which is what lets the warranty keep the
    company's own text instead of a copy of it pasted into this file.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    paragraphs: list[str] = []
    for block in re.split(r"</w:p>", xml):
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", block))
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        if text:
            paragraphs.append(text)
    return paragraphs


# --- the warranty, as the company's own document ---------------------------
#
# The warranty is a letter on the company's letterhead, and it is issued as
# that letter. So the .docx in the submittal builder is **filled in and
# converted**, not redrawn here: its layout, fonts, logo and signature block
# are the document, and a tidier page built from its words would be a
# different document wearing its text.
#
# Filling happens in the file's own XML, so every run keeps its formatting.
# Converting needs Word, which is what the machines this runs on have; where
# Word is not there, the drawn fallback below keeps the package buildable and
# says on its face that it is not the letterhead.

_WORD_PDF = 17          # wdFormatPDF


def _docx_runs(xml: str) -> list[str]:
    return re.findall(r"<w:t[^>]*>[^<]*</w:t>", xml)


def _fill_docx(template: Path, replace) -> bytes | None:
    """The template with its text replaced, still a .docx.

    Word splits a paragraph into runs wherever formatting changes, and a
    phrase can straddle them ("TWO" in one run, "YEARS" in the next), so the
    paragraph is joined, rewritten, and the result put back on its first run
    with the rest of that paragraph's text cleared. Runs a paragraph does not
    change are left exactly as they were.
    """
    import zipfile

    try:
        with zipfile.ZipFile(template) as archive:
            names = archive.namelist()
            parts = {name: archive.read(name) for name in names}
    except Exception:  # noqa: BLE001
        return None

    document = parts.get("word/document.xml")
    if document is None:
        return None
    xml = document.decode("utf-8", "replace")

    out: list[str] = []
    for index, block in enumerate(re.split(r"(</w:p>)", xml)):
        if index % 2 or "<w:t" not in block:
            out.append(block)
            continue
        runs = _docx_runs(block)
        # Unescaped on the way in and escaped on the way out. Reading the raw
        # XML and escaping it again turned the "&" of "manufactured &
        # supplied" into "&amp;" on the issued letter.
        texts = [_unescape(re.sub(r"<[^>]+>", "", run)) for run in runs]
        joined = "".join(texts)
        rewritten = replace(joined)
        if rewritten == joined:
            out.append(block)
            continue
        # Put the whole paragraph on its first run; blank the others. The
        # first run carries the paragraph's own formatting, which is what a
        # line like "PROJECT: ..." is set in.
        new_block = block
        first = True
        for run, text in zip(runs, texts):
            body = _escape(rewritten) if first else ""
            new_block = new_block.replace(run, re.sub(r">[^<]*</w:t>$", f">{body}</w:t>", run), 1)
            first = False
        out.append(new_block)

    parts["word/document.xml"] = "".join(out).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, parts[name])
    return buffer.getvalue()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unescape(text: str) -> str:
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def docx_to_pdf(data: bytes) -> bytes | None:
    """Convert a .docx to PDF with Word, or None where Word is not there.

    Word is asked to export, which is what keeps the letterhead identical to
    the one the team sends out. Everything is torn down in a finally block:
    a Word process left running would hold the file and the next build would
    wait on it.
    """
    if os.name != "nt":
        return None
    try:
        import pythoncom  # noqa: F401
        import win32com.client
    except ImportError:
        return None

    folder = Path(tempfile.mkdtemp(prefix="warranty-"))
    source, target = folder / "warranty.docx", folder / "warranty.pdf"
    source.write_bytes(data)
    word = None
    try:
        pythoncom.CoInitialize()
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(source), ReadOnly=False, Visible=False)
        try:
            document.SaveAs(str(target), FileFormat=_WORD_PDF)
        finally:
            document.Close(SaveChanges=0)
        return target.read_bytes() if target.is_file() else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:  # noqa: BLE001
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(folder, ignore_errors=True)


def warranty_replacements(project: Project, years: int = WARRANTY_YEARS):
    """How a line of the draft is rewritten for this project.

    Only two kinds of change: the parties, and the period. Every other word
    is the company's and is left alone.
    """
    period = _YEAR_WORDS.get(years, f"{years} YEARS")
    brand = next((s.brand for s in project.systems if s.brand), None)
    party = lambda value: (value or "").strip() or "-"  # noqa: E731
    fields = {
        "DATE": date.today().strftime("%d/%m/%Y"),
        "PROJECT": _project_title(project),
        "CLIENT": party(project.client),
        "CONSULTANT": party(project.consultant),
        "MEP CONTRACTOR": party(project.contractor),
        "CONTRACTOR": party(project.contractor),
    }

    def replace(text: str) -> str:
        stripped = text.strip()
        for label, value in fields.items():
            # "MEP CONTRACTOR :" and "CONTRACTOR:" both occur; the longer
            # label is tried first because the dict is ordered that way.
            match = re.match(rf"^({re.escape(label)}\s*:\s*)", stripped, re.I)
            if match:
                return f"{match.group(1)}{value}"
        if re.match(r"^REF\s*NO", stripped, re.I):
            return re.sub(r"(:\s*).*$", rf"\g<1>EP-{project.ep_number}", stripped)
        new = re.sub(r"\b(ONE|TWO|THREE|FOUR|FIVE)\s+YEARS?\b", period, text, flags=re.I)
        if brand:
            new = re.sub(r"M/s\.\s*EDWARDS", f"M/s. {brand}", new, flags=re.I)
        return new

    return replace


def build_warranty(
    project: Project, library_root: Path | None, system_code: str | None = None, years: int = WARRANTY_YEARS
) -> pymupdf.Document:
    """The warranty certificate: the company's letter, filled in.

    The draft is the document. It is filled and converted, so what goes into
    the package is the letterhead the team issues -- only the project's
    details and the warranty period differ from the file in the builder,
    which still reads TWO YEARS.
    """
    from app.services import company_library

    template = (company_library.submittal_path(library_root, None, WARRANTY_TEMPLATE) or library_root / WARRANTY_TEMPLATE)         if library_root else None
    if template is not None and template.is_file():
        filled = _fill_docx(template, warranty_replacements(project, years))
        if filled is not None:
            pdf = docx_to_pdf(filled)
            if pdf:
                return pymupdf.open(stream=pdf, filetype="pdf")

    return _drawn_warranty(project, template, years)


def _drawn_warranty(project: Project, template: Path | None, years: int) -> pymupdf.Document:
    """A plain rendering, for when Word is not available to convert the draft.

    It says so on the page. A warranty that silently did not look like the
    company's letter would be issued by someone who never noticed.
    """
    period = _YEAR_WORDS.get(years, f"{years} YEARS")
    paragraphs = _docx_paragraphs(template) if template and template.is_file() else []
    replace = warranty_replacements(project, years)
    body = [(replace(text), bool(re.match(r"^[A-Z ]+\s*:", text))) for text in paragraphs]

    if not body:
        body = [
            (f"The warranty draft was not found in the submittal builder ({WARRANTY_TEMPLATE}).", True),
            (f"The standard warranty period is {period} from Taking Over Certificate.", False),
        ]

    doc = pymupdf.open()
    page = doc.new_page(width=595.32, height=841.92)
    y = 70
    page.insert_text((56, y), "WARRANTY CERTIFICATE", fontname="hebo", fontsize=15, color=_RED)
    y += 18
    page.insert_text((56, y), "Not on the company letterhead: Word was not available to convert the draft.",
                     fontname="helv", fontsize=8, color=(0.65, 0.2, 0.2))
    y += 24

    for text, bold in body:
        if y > 770:
            page = doc.new_page(width=595.32, height=841.92)
            y = 70
        font = "hebo" if bold else "helv"
        size = 9.5 if bold else 9
        line = ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if pymupdf.get_text_length(trial, fontname=font, fontsize=size) > 480 and line:
                page.insert_text((56, y), line, fontname=font, fontsize=size)
                y += size + 4
                line = word
                if y > 790:
                    page = doc.new_page(width=595.32, height=841.92)
                    y = 70
            else:
                line = trial
        if line:
            page.insert_text((56, y), line, fontname=font, fontsize=size)
            y += size + 4
        y += 5
    return doc
