"""Which datasheet documents a part the library's file names do not carry.

The library finds a part's datasheet by its file name and its text. A
variant, an assembly or a sibling size is on a sheet named for another
number -- the Menvier NEXI300-3H-CGL-IPM on NEXI300-3H-CGL.pdf, every
SL2 exit sign on SL2-42D3D-CGL-M.pdf -- and the search comes back empty.
A link here settles it once, for every project (`part_datasheet_links`),
and is looked up before the search is tried. The links the engineers
asked for on 17 Sep 2026 are seeded; the Proposed Materials tab adds more.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.extraction.identity import part_key
from app.models import PartDatasheetLink

# (manufacturer, part number, library, datasheet path in the library)
SEED_LINKS: list[tuple[str, str, str, str]] = [
    ("EDWARDS", "3-CHAS7", "EDWARDS", "01- PANEL/11- E85014-0004 -- EST4 Cabinets and Chassis.pdf"),
    ("EDWARDS", "TP434", "EDWARDS", "11- Back Box/Concealed/01- TP606, TP434 SG,DG.pdf"),
    ("EDWARDS", "TP606", "EDWARDS", "11- Back Box/Concealed/01- TP606, TP434 SG,DG.pdf"),
    ("EDWARDS", "SIGA-UM", "EDWARDS", "04- Modules/02- Output devices/01- Notification Appliance Circuits/SIGA -UM.pdf"),
    ("MENVIER", "CTR400CGL2KS-M", "MENVIER", "CTR160CGL2KS-M.pdf"),
    ("MENVIER", "NEXI300-3H-CGL-IPM", "MENVIER", "NEXI300-3H-CGL.pdf"),
    ("MENVIER", "SL2-42D3D-CGL-M +SL2PPLR+SL2RB", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
    ("MENVIER", "SL2-42D3D-CGL-M+SL23I", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
    ("MENVIER", "SL2-65D3D-CGL-M", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
    ("MENVIER", "SL2-65D3D-CGL-M +SL2CD +SL2DC3I", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
    ("MENVIER", "SL2-65D3D-CGL-M+SL23I", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
    ("MENVIER", "SL2NM65D3-M", "MENVIER", "SL2-42D3D-CGL-M.pdf"),
]


def _brand(manufacturer: str | None) -> str:
    return (manufacturer or "").strip().upper()


def lookup(db: Session, manufacturer: str | None, part_no: str | None) -> PartDatasheetLink | None:
    """The link for a part, under its manufacturer -- or under any
    manufacturer when the line names none."""
    key = part_key(part_no or "")
    if not key:
        return None
    query = db.query(PartDatasheetLink).filter(PartDatasheetLink.key == key)
    brand = _brand(manufacturer)
    if brand:
        exact = query.filter(PartDatasheetLink.manufacturer == brand).first()
        if exact is not None:
            return exact
        # "Menvier Brand" / "EDWARDS (EST4)": the library's name inside the manufacturer's.
        for link in query.all():
            if link.manufacturer in brand or brand in link.manufacturer:
                return link
        return None
    return query.first()


def link(db: Session, *, manufacturer: str, part_no: str, library: str, path: str, note: str | None = None,
         user_id: int | None = None, source: str = "engineer") -> PartDatasheetLink:
    """Set (or replace) the datasheet a part is documented by."""
    brand, key = _brand(manufacturer), part_key(part_no)
    if not brand or not key:
        raise ValueError("A manufacturer and a part number are needed")
    row = db.query(PartDatasheetLink).filter(PartDatasheetLink.manufacturer == brand, PartDatasheetLink.key == key).first()
    if row is None:
        row = PartDatasheetLink(manufacturer=brand, key=key, part_no=part_no.strip(), created_by_id=user_id)
        db.add(row)
    row.part_no, row.library, row.path, row.note, row.source = part_no.strip(), library, path, note, source
    db.commit()
    db.refresh(row)
    return row


def unlink(db: Session, *, manufacturer: str, part_no: str) -> bool:
    row = db.query(PartDatasheetLink).filter(PartDatasheetLink.manufacturer == _brand(manufacturer),
                                            PartDatasheetLink.key == part_key(part_no)).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def seed(db: Session) -> int:
    """The links the engineers named, once: a link already set (by them,
    or from a seed) is never overwritten."""
    added = 0
    for manufacturer, part_no, library_name, path in SEED_LINKS:
        if db.query(PartDatasheetLink).filter(PartDatasheetLink.manufacturer == manufacturer,
                                              PartDatasheetLink.key == part_key(part_no)).first() is None:
            db.add(PartDatasheetLink(manufacturer=manufacturer, key=part_key(part_no), part_no=part_no,
                                     library=library_name, path=path, source="seed"))
            added += 1
    if added:
        db.commit()
    return added


def sync_library_links(db: Session, libraries: dict) -> int:
    """Retained for compatibility; library files require human confirmation.

    Part numbers found in a PDF are proposals, not database mappings. Use the
    review endpoint and ``link`` after an engineer confirms the relationship.
    """
    return 0


def remove_unconfirmed_library_links(db: Session) -> int:
    """Remove mappings created by the old filename-only indexer."""
    rows = db.query(PartDatasheetLink).filter(PartDatasheetLink.source == "library").all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
