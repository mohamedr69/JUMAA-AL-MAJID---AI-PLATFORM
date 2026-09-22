"""The manufacturer's document number for a sheet in the library.

Edwards prints it in the footer of every page -- "E85001-0495" -- and it is
what an engineer quotes when they name a datasheet. The library's file
names do not carry it, so it is read off the pages and kept here, against
the file it came from.

**Keyed by the file, never the number.** One Edwards datasheet covers a
product family and the library files the same document under each product:
E85001-0279 is SIGA-270, SIGA-278 and SIGA-270P, three files with one
number. Renaming the files to their number would collide and lose two of
the three, which is why this is a stored fact about a file rather than a
rename on disk.

`resolve` is what the API shows: an engineer's entry first, then what was
read off the pages, then the number the index found in the page-1 header,
and a sheet with none keeps its file name.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pymupdf
from sqlalchemy.orm import Session

from app.models import DatasheetDocument

# Edwards document numbers: "E85001-0495", and the S-series the older
# conventional sheets carry ("S85001-0227").
REFERENCE_RE = re.compile(r"\b[ES]\d{5}-\d{3,4}\b")
# A manual mentions dozens of other documents; a datasheet is a few pages.
MAX_PAGES = 30
EXTRACTED, ENGINEER = "extracted", "engineer"


def read_reference(pdf: Path) -> str | None:
    """The sheet's own document number, read off its pages.

    The number in the footer is on every page, while another document's
    number is mentioned once in the body -- 4-CPU's sheet names 4-NET's
    once and its own four times -- so the most frequent one is the sheet's
    own. That also beats the page-1 header the index reads, which on one
    sheet captures a trailing dash ("E85001-1007-").
    """
    try:
        with pymupdf.open(pdf) as document:
            if document.page_count > MAX_PAGES:
                return None
            counts: Counter[str] = Counter()
            for page in document:
                counts.update(REFERENCE_RE.findall(page.get_text()))
    except Exception:
        return None
    if not counts:
        return None
    # Ties go to the number seen first, which `Counter` already keeps.
    return counts.most_common(1)[0][0]


def stored(db: Session, library: str) -> dict[str, DatasheetDocument]:
    """Every reference kept for one library, by file path."""
    rows = db.query(DatasheetDocument).filter(DatasheetDocument.library == library.upper()).all()
    return {row.path: row for row in rows}


def set_reference(
    db: Session,
    *,
    library: str,
    path: str,
    reference_no: str,
    source: str = ENGINEER,
    user_id: int | None = None,
) -> DatasheetDocument:
    """Set (or correct) the number for one sheet."""
    name, reference = library.upper(), reference_no.strip().upper()
    if not reference:
        raise ValueError("A reference number is needed")
    row = (
        db.query(DatasheetDocument)
        .filter(DatasheetDocument.library == name, DatasheetDocument.path == path)
        .first()
    )
    if row is None:
        row = DatasheetDocument(library=name, path=path, created_by_id=user_id)
        db.add(row)
    row.reference_no, row.source = reference, source
    if source == ENGINEER:
        row.created_by_id = user_id
    db.commit()
    db.refresh(row)
    return row


def resolve(reference: str | None, document_no: str | None) -> str | None:
    """What to show as the sheet's number: what is kept here, else what the
    index read from the page-1 header. Neither means the sheet prints no
    number, and the file name stands."""
    return reference or document_no or None
