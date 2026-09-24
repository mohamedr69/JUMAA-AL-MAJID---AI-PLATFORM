"""Every part number the platform knows for a manufacturer, in one list.

The Proposed Materials tab completes a part number as the engineer types
it, from what is already on file for the brand -- no model, no folder walk
on every keystroke:

- the brand's datasheet library: the sheets are named for their parts
  ("01- 4-CPU.pdf", "3-SDDC2 & 3-SSDC2.pdf", "CTR160CGL2KS-M.pdf");
- the equipment current table (Edwards);
- the brand's country-of-origin sheet in the submittal builder;
- every BOQ line of every project that names the brand -- the parts the
  company actually quotes, with the descriptions it quotes them by.

Merged by part key (app.extraction.identity.part_key), the description
taken from the first source that has one, in that order of trust reversed:
a BOQ's or the equipment table's wording over a file name.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.extraction.identity import part_key
from app.models import PartDatasheetLink, ProjectBoqItem
from app.services import equipment_currents
from app.services.datasheet_library import get_libraries

# A catalogue number in a file name: hyphenated ("4-CPU", "SIGA-OSD-FCN",
# "CTR160CGL2KS-M") or letters then a digit ("EST4"); never a plain word.
_PART_RE = re.compile(r"(?<![A-Z0-9])(?:[A-Z0-9]+(?:[-/][A-Z0-9]+)+|[A-Z]+\d[A-Z0-9]*)(?![A-Z0-9])")
# File names that are not parts: cover pages, manuals, document numbers.
_NOT_A_PART_RE = re.compile(r"cover\s*page|manual|guide|white\s*paper|catalog|brochure|^E\d{5}-\d{4}$", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(r"^\d{1,3}-\s+")


@dataclasses.dataclass
class PartEntry:
    part_no: str
    description: str
    sources: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {"part_no": self.part_no, "description": self.description, "sources": self.sources}


def _from_filename(name: str) -> list[str]:
    """The part numbers a datasheet's file name carries."""
    stem = _LEADING_NUMBER_RE.sub("", Path(name).stem)
    if _NOT_A_PART_RE.search(stem):
        return []
    found = []
    for piece in re.split(r"\s*&\s*|\s*,\s*|\s+--\s+", stem):
        piece = piece.strip().upper().replace("_", "-")   # "SIGA_LED.pdf" is SIGA-LED
        if _NOT_A_PART_RE.search(piece):
            continue   # a document number ("E85014-0004") is not a part
        for match in _PART_RE.findall(piece):
            if len(match) >= 3 and match not in found:
                found.append(match)
    return found


def catalog(db: Session, brand: str | None) -> list[PartEntry]:
    """The parts on file for a brand (EDWARDS, MENVIER ...), each once."""
    from app.services.submittal_package import read_origin_rows

    brand_key = (brand or "").strip().upper()
    entries: dict[str, PartEntry] = {}

    def add(part_no: str, description: str | None, source: str) -> None:
        key = part_key(part_no)
        if not key or len(key) < 3:
            return
        entry = entries.get(key)
        if entry is None:
            entry = entries[key] = PartEntry(part_no=part_no.strip(), description="")
        if description and (not entry.description or entry.sources == ["datasheet"]):
            entry.description = description.strip()[:200]
        if source not in entry.sources:
            entry.sources.append(source)

    linked_query = db.query(PartDatasheetLink)
    if brand_key:
        linked_query = linked_query.filter(PartDatasheetLink.manufacturer == brand_key)
    linked_rows = linked_query.all()
    for row in linked_rows:
        add(row.part_no, None, "datasheet")

    if not brand_key or brand_key == equipment_currents.MANUFACTURER.upper():
        for row in equipment_currents.all_rows(db):
            if (row.manufacturer or "").upper() == (brand_key or row.manufacturer or "").upper():
                add(row.part_no, row.description, "equipment table")

    try:
        from app.routers.submittal import _submittal_library

        for model, description, _made_in, _shipped in read_origin_rows(_submittal_library(), brand_key or None):
            if re.search(r"\d", model) and len(model) <= 40 and not re.search(r"\s{2,}", model):
                add(model.split("\n")[0].strip(), description, "country of origin")
    except Exception:  # noqa: BLE001 -- no builder, no origins
        pass

    # A BOQ line may still hold a scan's spelling of a part ("SIGA-AASO"):
    # it enters the catalogue under the catalogue's spelling, not its own.
    from app.services import boq_provenance

    library = boq_provenance.part_library(db)
    query = db.query(ProjectBoqItem.catalog_no, ProjectBoqItem.description, ProjectBoqItem.manufacturer).filter(ProjectBoqItem.catalog_no.isnot(None))
    if brand_key:
        query = query.filter(ProjectBoqItem.manufacturer.isnot(None))
    for catalog_no, description, manufacturer in query:
        if brand_key and (manufacturer or "").strip().upper() != brand_key:
            continue
        if not catalog_no or not re.search(r"[A-Za-z]", catalog_no):
            continue   # a quantity in the part column (old OCR lines) is not a part
        settled, _record = boq_provenance.catalogued(catalog_no, library)
        add(settled or catalog_no, description, "BOQ")

    return sorted(entries.values(), key=lambda e: e.part_no.upper())


def search(db: Session, brand: str | None, query: str, limit: int = 20) -> list[PartEntry]:
    """The parts whose number starts with what was typed, then the ones
    that contain it; the description searched too when the number gives
    nothing."""
    typed = part_key(query or "")
    if not typed:
        return catalog(db, brand)[:limit]
    entries = catalog(db, brand)
    starts = [e for e in entries if part_key(e.part_no).startswith(typed)]
    contains = [e for e in entries if e not in starts and typed in part_key(e.part_no)]
    described = [e for e in entries if e not in starts and e not in contains
                 and (query or "").strip().lower() in e.description.lower()]
    return (starts + contains + described)[:limit]
