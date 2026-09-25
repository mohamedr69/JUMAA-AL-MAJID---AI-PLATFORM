r"""Read the drawing titles of stored IFC drawings again, from their DXF.

    python -m scripts.reread_ifc_titles              every drawing, and save
    python -m scripts.reread_ifc_titles --dry-run    say what would change
    python -m scripts.reread_ifc_titles 11 12        only these drawings

"BOQ -> As per IFC Drawings -> Floor Wise" names each sheet's floor from the
sheet's drawing title (app/ifc/dxf/sheets.py). Until 25 September 2026 the
title was taken as the largest title-like text on the sheet, and where a
title block writes the floor ("PODIUM-3 FLOOR PLAN") above a larger drawing
name ("FIRE ALARM LAYOUT"), every sheet was titled "FIRE ALARM LAYOUT" and
the Floor Wise BOQ listed thirty floors of that name. The stored title is
the only thing read from the sheet -- the floor, the floors a typical plan
stands for and the multiplier are worked out from it when the drawing is
opened -- so reading the titles again puts the Floor Wise BOQ right. The
symbols, the review decisions and the floor overrides are not touched.

It reads each drawing's converted DXF (a large one takes minutes and several
GB of memory), one at a time. Run it with the platform stopped or quiet.
"""
from __future__ import annotations

import argparse
import sys

import ezdxf
from sqlalchemy.orm.attributes import flag_modified

from app.database import SessionLocal
from app.ifc import storage
from app.ifc.dxf import sheets as SH
from app.models import ProjectIfcDrawing


def reread(drawing: ProjectIfcDrawing) -> list[tuple[str, str, str]]:
    """(sheet, title before, title after) for every sheet whose title changes;
    the drawing's stored sheets are updated in place (not committed)."""
    path = storage.dxf_path(drawing)
    if not path.is_file():
        raise FileNotFoundError(f"the working copy {path} is missing")
    doc = ezdxf.readfile(str(path))
    fresh = {s.name: s for s in SH.read_sheets(doc)}
    meta = dict(drawing.meta or {})
    changes = []
    stored = []
    for sheet in meta.get("sheets") or []:
        sheet = dict(sheet)
        new = fresh.get(sheet["name"])
        if new is not None and (new.title != sheet.get("title") or new.title_source != sheet.get("title_source")):
            changes.append((sheet["name"], sheet.get("title", ""), new.title))
            sheet["title"], sheet["title_source"], sheet["kind"] = new.title, new.title_source, new.kind
        stored.append(sheet)
    meta["sheets"] = stored
    drawing.meta = meta
    flag_modified(drawing, "meta")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", type=int, help="drawing ids (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args()
    db = SessionLocal()
    query = db.query(ProjectIfcDrawing).order_by(ProjectIfcDrawing.id)
    if args.ids:
        query = query.filter(ProjectIfcDrawing.id.in_(args.ids))
    failed = 0
    for drawing in query.all():
        label = f"#{drawing.id} {drawing.filename} {drawing.revision}"
        try:
            changes = reread(drawing)
        except Exception as exc:  # noqa: BLE001 -- one drawing is not the rest
            db.rollback()
            failed += 1
            print(f"{label}: not read again ({exc})", flush=True)
            continue
        if not changes:
            print(f"{label}: titles unchanged", flush=True)
            db.rollback()
            continue
        print(f"{label}: {len(changes)} sheet title(s) read again", flush=True)
        for name, before, after in changes:
            print(f"    {name}: {before!r} -> {after!r}  (floor: {SH.identify_floor(after) or SH.NOT_IDENTIFIED})")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
