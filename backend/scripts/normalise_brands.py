"""Bring the brands already recorded to one spelling each.

New records are normalised as they are written (`ProjectSystemIn`,
`frc_cables`), but the rows made before that were not: the same supplier
was stored as "EDWARDS" and "Edwards", and as "MENVIER", "Menvier" and
"MENIVER". The misspelling is not a cosmetic problem -- a brand is the
key to the datasheet library, to a part's link and to the submittal
builder's shelf, so a project that recorded "MENIVER" matched none of
them and silently fell back to searching every brand.

    python scripts/normalise_brands.py --dry-run
    python scripts/normalise_brands.py

`part_datasheet_links` is left alone: it is written by the importers,
which already upper-case, and its 558 rows are already one spelling each.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import BrandSupplier, ProjectFrcCables, ProjectProposedMaterial, ProjectSystem  # noqa: E402
from app.services import brands  # noqa: E402

# Every column that records a manufacturer as free text.
COLUMNS = [
    (ProjectSystem, "brand"),
    (ProjectFrcCables, "brand"),
    (ProjectFrcCables, "monitoring_brand"),
    (ProjectProposedMaterial, "manufacturer"),
    (BrandSupplier, "brand"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        changes: list[tuple[str, int, str, str]] = []
        unchanged = Counter()
        for model, column in COLUMNS:
            for row in db.query(model).all():
                before = getattr(row, column)
                after = brands.normalise(before)
                if before and after and before != after:
                    changes.append((f"{model.__tablename__}.{column}", row.id, before, after))
                elif before:
                    unchanged[f"{model.__tablename__}.{column}"] += 1

        print(f"to change : {len(changes)}")
        print(f"already right: {sum(unchanged.values())} {dict(unchanged)}")
        if changes:
            print()
            for table, row_id, before, after in changes:
                print(f"  {table:42} #{row_id:<4} {before!r} -> {after!r}")

        if args.dry_run:
            print("\n(dry run -- nothing written)")
            return 0
        if not changes:
            return 0

        for model, column in COLUMNS:
            for row in db.query(model).all():
                before = getattr(row, column)
                after = brands.normalise(before)
                if before and after and before != after:
                    setattr(row, column, after)
        db.commit()
        print(f"\nchanged {len(changes)} value(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
