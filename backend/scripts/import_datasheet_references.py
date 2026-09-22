"""Read each Edwards datasheet's document number off its pages.

The number in the footer -- "E85001-0495" -- is what an engineer quotes
when they name a sheet, and the library's file names do not carry it. This
reads it once per file and keeps it against that file
(`app/services/datasheet_documents.py`), so the library can be listed by
document number instead of by file name.

Nothing is renamed on disk, deliberately. Two reasons, both checked:

  - `scripts/sync_library.py` copies the archive over the local library and
    never deletes, so a file renamed here comes back under its old name on
    the next sync, and the library ends up holding both.
  - A number is not unique to a file. One Edwards datasheet covers a
    product family and the library files it under each product: five
    numbers cover twelve of the seventy-five sheets, E85001-0279 alone
    covering SIGA-270, SIGA-278 and SIGA-270P. Renaming to the number
    would collide and lose two of those three.

A number an engineer typed in is never overwritten: seventeen Edwards
sheets print none, and what someone entered by hand is the only record of
those there is.

    python scripts/import_datasheet_references.py --dry-run
    python scripts/import_datasheet_references.py
    python scripts/import_datasheet_references.py --library MENVIER
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.services import datasheet_documents  # noqa: E402
from app.services.datasheet_library import get_libraries  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", default="EDWARDS", help="the manufacturer's library (default EDWARDS)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    libraries = get_libraries()
    library = libraries.get(args.library.upper())
    if library is None:
        print(f"No such library: {args.library}. Held: {', '.join(sorted(libraries))}", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        held = datasheet_documents.stored(db, library.name)
        listing = library.listing()

        read: dict[str, str] = {}
        unprinted: list[str] = []
        for file in listing:
            reference = datasheet_documents.read_reference(library.folder / file.path)
            if reference:
                read[file.path] = reference
            else:
                unprinted.append(file.path)

        fresh = {p: r for p, r in read.items() if p not in held}
        corrected = {
            p: (held[p].reference_no, r)
            for p, r in read.items()
            if p in held and held[p].source == datasheet_documents.EXTRACTED and held[p].reference_no != r
        }
        by_engineer = [p for p in read if p in held and held[p].source == datasheet_documents.ENGINEER]

        shared: dict[str, list[str]] = defaultdict(list)
        for path, reference in read.items():
            shared[reference].append(path)

        print(f"library            : {library.name} ({len(listing)} datasheets)")
        print(f"number read        : {len(read)}")
        print(f"prints no number   : {len(unprinted)}")
        print(f"to record          : {len(fresh)}")
        print(f"to correct         : {len(corrected)}")
        print(f"left to the engineer: {len(by_engineer)}")

        families = {r: p for r, p in shared.items() if len(p) > 1}
        if families:
            print(f"\n--- one number, several files ({len(families)}; kept per file, never renamed) ---")
            for reference, paths in sorted(families.items()):
                print(f"  {reference}")
                for path in sorted(paths):
                    print(f"      {path}")
        if corrected:
            print("\n--- corrected ---")
            for path, (was, now) in sorted(corrected.items()):
                print(f"  {was} -> {now}   {path}")
        if unprinted:
            print(f"\n--- no number printed on these; type one in if they need one ---")
            for path in sorted(unprinted):
                print(f"  {path}")

        if args.dry_run:
            print("\n(dry run -- nothing written)")
            return 0

        for path, reference in {**fresh, **{p: n for p, (_, n) in corrected.items()}}.items():
            datasheet_documents.set_reference(
                db, library=library.name, path=path, reference_no=reference,
                source=datasheet_documents.EXTRACTED,
            )
        print(f"\nrecorded {len(fresh)}, corrected {len(corrected)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
