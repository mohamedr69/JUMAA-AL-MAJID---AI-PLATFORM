"""Link a manufacturer's part numbers to the datasheets that document them.

The brand's ordering tables were read out of its library into a CSV --
one row per part number found on a datasheet. This turns the rows that say
"this sheet documents this part" into `part_datasheet_links`, so a BOQ line
naming a part the library's file names do not carry still finds its sheet
(app/services/datasheet_links.py).

Only `role == "primary"` rows are imported. A `related` row means the part
merely appeared in another part's accessories or compatible-equipment
table -- BC-1 is listed on the amplifier sheet, but the amplifier sheet is
not what documents BC-1, and linking it there would answer the wrong
question.

Three kinds of row are refused rather than guessed at, and named in the
report so an engineer can settle them:

  ambiguous  the same part is primary on more than one datasheet; the CSV
             gives no way to say which is the real one.
  missing    the datasheet path does not exist in the library -- a rename
             or a typo in the source CSV.
  unusable   the part number is not a part number (a weight read out of a
             specification table).

A link already set -- seeded, or made by an engineer on the Proposed
Materials tab -- is never overwritten: those were decided deliberately,
and some of them are right where this CSV is wrong. Four Edwards parts
are exactly that case, and must keep their seeded paths:

    3-CHAS7, TP434, TP606, SIGA-UM

For TP434 and TP606 the CSV names "01- TP606, TP404 SG,DG.pdf" where the
library holds "...TP434...", and for SIGA-UM it names a folder the
library does not have. If a corrected CSV ever makes these importable,
check them against the library before letting them through.

    python scripts/import_part_datasheets.py --dry-run
    python scripts/import_part_datasheets.py
    python scripts/import_part_datasheets.py --manufacturer MENVIER --dry-run
    python scripts/import_part_datasheets.py --manufacturer MENVIER --undo

`--undo` removes only what this script inserted (source="ordering_table").
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.extraction.identity import part_key  # noqa: E402
from app.models import PartDatasheetLink  # noqa: E402

# What this script's rows are marked with, so `--undo` can find exactly
# them and leave the seeds and the engineers' own links alone.
SOURCE = "ordering_table"

# Where each brand's ordering tables were read out to. A CSV kept somewhere
# else is given with --csv.
DEFAULT_CSVS = {
    "EDWARDS": Path(
        r"C:\Users\ramadan.mohamed\OneDrive - Juma Al Majid\Desktop\Systems"
        r"\01- FAVE\01- Edwards - UL&EN\01- EST4\EST4-part-numbers.csv"
    ),
    "MENVIER": Path(
        r"C:\Users\ramadan.mohamed\OneDrive - Juma Al Majid\Desktop\Systems"
        r"\03- EML\01- Menvier\MENVIER-part-numbers.csv"
    ),
}
LIBRARY_ROOT = Path(__file__).resolve().parents[1] / "library" / "datasheets"

# A part number carries no bracketed measurement and no unit hung off a
# number: "27.0lb(12.25kg" is a shipping weight the reader took from a
# specification table. The unit must follow a digit -- 4-NET-MM is a
# network card, not millimetres.
_NOT_A_PART = re.compile(r"[()]|\d\s*(?:lb|kg|mm|cm|in|oz)\b", re.IGNORECASE)


def _usable(part_no: str) -> bool:
    return bool(part_key(part_no)) and not _NOT_A_PART.search(part_no)


def read_rows(csv_path: Path, library_root: Path) -> dict:
    """Sort every primary row into what can be linked and what cannot."""
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if (r.get("role") or "").strip() == "primary"]

    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_key[part_key(row["part_number"])].append(row)

    linkable: dict[str, dict] = {}
    ambiguous: dict[str, list[dict]] = {}
    missing: list[dict] = []
    unusable: list[dict] = []

    for key, group in by_key.items():
        paths = {r["datasheet_path"].strip() for r in group}
        if len(paths) > 1:
            ambiguous[key] = group
            continue
        row = group[0]
        if not _usable(row["part_number"]):
            unusable.append(row)
        elif not (library_root / row["datasheet_path"].strip()).exists():
            missing.append(row)
        else:
            linkable[key] = row

    return {
        "primary": len(rows),
        "linkable": linkable,
        "ambiguous": ambiguous,
        "missing": missing,
        "unusable": unusable,
    }


def report(found: dict) -> None:
    print(f"primary rows read        : {found['primary']}")
    print(f"ready to link            : {len(found['linkable'])}")
    print(f"ambiguous (>1 datasheet) : {len(found['ambiguous'])}")
    print(f"datasheet file missing   : {len(found['missing'])}")
    print(f"not a part number        : {len(found['unusable'])}")

    if found["ambiguous"]:
        print("\n--- ambiguous: the same part is primary on several datasheets ---")
        for key in sorted(found["ambiguous"]):
            group = found["ambiguous"][key]
            print(f"  {group[0]['part_number']}")
            for path in sorted({r['datasheet_path'].strip() for r in group}):
                print(f"      {path}")
    if found["missing"]:
        print("\n--- datasheet path not found in the library ---")
        for row in sorted(found["missing"], key=lambda r: r["part_number"]):
            print(f"  {row['part_number']:22} {row['datasheet_path']}")
    if found["unusable"]:
        print("\n--- not a part number ---")
        for row in found["unusable"]:
            print(f"  {row['part_number']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manufacturer", default="EDWARDS", help="the brand whose library and CSV to use")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--library-root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--undo", action="store_true", help="delete the links this script inserted")
    args = parser.parse_args()

    manufacturer = args.manufacturer.strip().upper()
    csv_path = args.csv or DEFAULT_CSVS.get(manufacturer)
    library_root = args.library_root or (LIBRARY_ROOT / manufacturer)
    if csv_path is None:
        print(f"No CSV is known for {manufacturer}; give one with --csv", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        if args.undo:
            rows = db.query(PartDatasheetLink).filter(
                PartDatasheetLink.manufacturer == manufacturer, PartDatasheetLink.source == SOURCE
            ).all()
            print(f"removing {len(rows)} link(s) with source={SOURCE!r}")
            for row in rows:
                db.delete(row)
            db.commit()
            return 0

        if not csv_path.exists():
            print(f"CSV not found: {csv_path}", file=sys.stderr)
            return 1
        if not library_root.exists():
            print(f"Library not found: {library_root}", file=sys.stderr)
            return 1

        print(f"manufacturer             : {manufacturer}")
        found = read_rows(csv_path, library_root)
        report(found)

        held = {
            key for (key,) in db.query(PartDatasheetLink.key)
            .filter(PartDatasheetLink.manufacturer == manufacturer)
        }
        fresh = {k: r for k, r in found["linkable"].items() if k not in held}
        kept = sorted(set(found["linkable"]) & held)

        print(f"\nalready linked, left alone : {len(kept)}" + (f"  {kept}" if kept else ""))
        print(f"to insert                  : {len(fresh)}")

        if args.dry_run:
            print("\n(dry run -- nothing written)")
            return 0

        for key, row in fresh.items():
            db.add(PartDatasheetLink(
                manufacturer=manufacturer,
                key=key,
                part_no=row["part_number"].strip(),
                library=manufacturer,
                path=row["datasheet_path"].strip(),
                note=(row.get("description") or "").strip() or None,
                source=SOURCE,
            ))
        db.commit()
        total = db.query(PartDatasheetLink).count()
        print(f"\ninserted {len(fresh)}; part_datasheet_links now holds {total} row(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
