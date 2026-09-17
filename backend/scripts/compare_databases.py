r"""What one database holds that another does not.

    python -m scripts.compare_databases "C:\...\EP Platform\ep_platform.db"
    python -m scripts.compare_databases <other.db> --mine <this.db>

Two PCs that each kept their own database drift apart: a project opened
in the office is not on the laptop. Before pointing both at one shared
folder, this says exactly which projects would be missing from the one
you keep -- by EP number, with what each holds (BOQ lines, submittals,
documents), so nothing is discarded unnoticed.

It only reads. Nothing is copied or changed.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_MINE = BACKEND / "ep_platform.db"

COUNTS = (
    ("BOQ lines", "SELECT COUNT(*) FROM project_boq_items WHERE project_id = ?"),
    ("submittals", "SELECT COUNT(*) FROM project_submittals WHERE project_id = ?"),
    ("documents", "SELECT COUNT(*) FROM project_documents WHERE project_id = ?"),
)


def projects(database: Path) -> dict[str, dict]:
    """{EP number: what the project holds} for a database."""
    found: dict[str, dict] = {}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT id, ep_number, project_name, status, created_at FROM projects ORDER BY ep_number"):
            entry = {"name": row["project_name"], "status": row["status"], "created": row["created_at"]}
            for label, query in COUNTS:
                try:
                    entry[label] = int(connection.execute(query, (row["id"],)).fetchone()[0])
                except sqlite3.Error:
                    entry[label] = 0
            found[str(row["ep_number"])] = entry
    return found


def _line(ep: str, entry: dict) -> str:
    held = ", ".join(f"{entry.get(label, 0)} {label}" for label, _ in COUNTS)
    return f"  EP-{ep:<8} {(entry['name'] or '')[:42]:42} {held}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("other", help="the other database file")
    parser.add_argument("--mine", default=str(DEFAULT_MINE), help="the database to compare it with (this PC's by default)")
    args = parser.parse_args(argv)

    mine_path, other_path = Path(args.mine), Path(args.other)
    for path in (mine_path, other_path):
        if not path.is_file():
            print(f"{path} is not there")
            return 1

    mine, other = projects(mine_path), projects(other_path)
    only_mine = sorted(set(mine) - set(other))
    only_other = sorted(set(other) - set(mine))
    both = sorted(set(mine) & set(other))

    print(f"{mine_path} : {len(mine)} projects")
    print(f"{other_path} : {len(other)} projects")
    print(f"\nIn both: {len(both)}")
    print(f"\nOnly in {mine_path.name} ({len(only_mine)}):")
    for ep in only_mine:
        print(_line(ep, mine[ep]))
    print(f"\nOnly in {other_path.name} ({len(only_other)}):")
    for ep in only_other:
        print(_line(ep, other[ep]))
    if only_mine or only_other:
        print("\nKeep the database that holds the most work as the shared one; a project missing from it is "
              "opened again from its archive folder, which is where its documents live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
