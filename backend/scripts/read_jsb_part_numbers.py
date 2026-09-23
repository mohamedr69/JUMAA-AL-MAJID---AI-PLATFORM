"""Read the JSB (Eaton) ordering tables out of the datasheet library.

Edwards and Menvier arrived as CSVs someone had already read out and
labelled, each row saying whether the sheet *documents* that part
("primary") or merely lists it as an accessory ("related"). JSB has no
such CSV, and its sheets give no way to tell the two apart: the SafeLite
sheet's SL23A, SL2CD and SL2RB are pictogram sets and diffusers -- exactly
the accessories the other two brands' imports left out.

So this proposes rather than imports. It writes a CSV in the same shape
the importer already reads, with `role` left blank for an engineer to
fill in, and nothing is linked until they do:

    python scripts/read_jsb_part_numbers.py
    # fill in the role column: primary | related
    python scripts/import_part_datasheets.py --manufacturer JSB --csv <that file>

Two identifier shapes appear, under three different table headings:

    Order details   / Model / Included with delivery / Order no.  -> 40071362670
    Ordering details/ Type  / Scope of supply        / Order-No.  -> FT2ED4ICGS
    (bare)                                             Order No.  -> SL23A

The eleven-digit Eaton order number is unambiguous. The alphanumeric type
code is not -- it is only a run of capitals and digits -- so the second
kind is over-read on purpose and the blank `role` column is where that
gets settled.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.datasheet_library import get_libraries  # noqa: E402

LIBRARY = "JSB"
DEFAULT_OUT = Path(r"C:\dev\Claude outputs\JSB-part-numbers.csv")

# The line that opens an ordering table, however the sheet spells it.
TABLE_HEAD = re.compile(r"^\s*(order|ordering)\s+(details|no\.?)\s*$", re.IGNORECASE)
# The column header the identifiers sit under.
ORDER_COLUMN = re.compile(r"^\s*order[\s-]*no\.?\s*$", re.IGNORECASE)
# Eaton's eleven-digit order number, and the alphanumeric type code the
# luminaire sheets use instead.
ORDER_NO = re.compile(r"^\d{11}$")
TYPE_CODE = re.compile(r"^[A-Z][A-Z0-9][A-Z0-9.\-/+]{3,}$")
# Lines that are never a part: headings, and the units and ratings that
# sit in the specification table under the same column.
NOT_A_PART = re.compile(
    r"^(model|type|included with delivery|scope of supply|order)"
    # The earthing systems a cabinet may be wired to ("TN-C-S/IT") sit
    # in the specification under the same column and are not a part.
    r"|^(TN(-[CS])*|TT|IT)([/-](TN(-[CS])*|TT|IT))*$",
    re.IGNORECASE,
)


def _identifier(line: str) -> str | None:
    text = line.strip()
    if not text or NOT_A_PART.match(text):
        return None
    if ORDER_NO.match(text):
        return text
    if TYPE_CODE.match(text) and not text.isdigit():
        return text
    return None


def read_sheet(pdf: Path) -> list[dict]:
    """Every identifier under an ordering table, with the lines above it.

    The table reads in text order -- model, then its description, then the
    number -- so what precedes an identifier is its description, back to
    the previous identifier or the table head.
    """
    with pymupdf.open(pdf) as document:
        lines = [l.strip() for page in document for l in page.get_text().splitlines()]

    rows: list[dict] = []
    in_table = False
    buffer: list[str] = []
    for line in lines:
        if TABLE_HEAD.match(line) or ORDER_COLUMN.match(line):
            in_table, buffer = True, []
            continue
        if not in_table:
            continue
        identifier = _identifier(line)
        if identifier is None:
            if line:
                buffer.append(line)
            # The table is not closed on a run of prose. These sheets put
            # the specification block between an ordering table and the
            # accessory table under it, and any cutoff short enough to end
            # the first lost the second: SafeLite's twenty-three accessory
            # codes sit past a long specification. What keeps the
            # specification out is NOT_A_PART and the shape of an
            # identifier, not the distance from the heading.
            continue
        rows.append({
            "part_number": identifier,
            "description": " ".join(buffer).strip()[:300],
            "datasheet_path": None,  # filled by the caller
        })
        buffer = []
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    library = get_libraries().get(LIBRARY)
    if library is None:
        print(f"No {LIBRARY} library is held", file=sys.stderr)
        return 1

    out_rows: list[dict] = []
    for file in library.listing():
        found = read_sheet(library.folder / file.path)
        for row in found:
            row["datasheet_path"] = file.path
            row["brand"] = LIBRARY
            row["role"] = ""  # for an engineer: primary | related
            row["source"] = "pdf_table"
        out_rows += found
        print(f"{file.filename:34} {len(found):3} candidate part number(s)")

    # One sheet can list the same code twice (wall and ceiling variants of
    # one luminaire); the engineer sees it once per sheet.
    seen, unique = set(), []
    for row in out_rows:
        key = (row["datasheet_path"], row["part_number"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["part_number", "description", "datasheet_path", "brand", "role", "source"]
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unique)

    eleven = sum(1 for r in unique if ORDER_NO.match(r["part_number"]))
    print(f"\n{len(unique)} candidate(s): {eleven} Eaton order number(s), {len(unique) - eleven} type code(s)")
    print(f"written to {args.out}")
    print("Fill in the role column (primary | related), then:")
    print(f'  python scripts/import_part_datasheets.py --manufacturer JSB --csv "{args.out}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
