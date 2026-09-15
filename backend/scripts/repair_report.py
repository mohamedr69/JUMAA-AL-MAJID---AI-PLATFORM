"""Report what repairing existing projects would change, before anything does.

    venv\\Scripts\\python scripts\\repair_report.py 30208 30175 30058 31112
    venv\\Scripts\\python scripts\\repair_report.py --all --out repair.json
    venv\\Scripts\\python scripts\\repair_report.py 30208 --write

Read-only by default: documents are checked and the sheets read in memory,
and nothing is stored. `--write` stores the document findings and records
each re-read as a BOQ candidate to review on the project's Re-read page; the
BOQ itself is never changed by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, engine  # noqa: E402
from app.migrations import upgrade_to_head  # noqa: E402
from app.models import Project, RoleEnum, User  # noqa: E402
from app.services import repair_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ep_numbers", nargs="*", help="EP numbers, with or without the EP- prefix")
    parser.add_argument("--all", action="store_true", help="every project")
    parser.add_argument("--no-read", action="store_true", help="skip reading the sheets (documents and folder only)")
    parser.add_argument("--write", action="store_true", help="store findings and record re-reads as candidates")
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    args = parser.parse_args()

    # The same schema the app runs on, reached the same way (with a verified
    # backup first when migrations are pending).
    upgrade_to_head(engine)
    db = SessionLocal()
    try:
        query = db.query(Project).order_by(Project.ep_number)
        wanted = [n.upper().removeprefix("EP-").removeprefix("EP") for n in args.ep_numbers]
        projects = query.all() if args.all else [p for p in query if p.ep_number in wanted]
        if not projects:
            print("No matching projects.")
            return 1
        user = db.query(User).filter(User.role == RoleEnum.admin).first() if args.write else None
        reports = []
        for project in projects:
            report = repair_report.build(db, project, read_sheets=not args.no_read, write=args.write, user=user)
            reports.append(report.as_dict())
            _print(report)
        if args.out:
            args.out.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
            print(f"\nFull report written to {args.out}")
    finally:
        db.close()
    return 0


def _print(report: repair_report.RepairReport) -> None:
    print(f"\n=== EP-{report.ep_number} {report.project_name or ''}")
    for document in report.documents:
        codes = ", ".join(f["code"] for f in document["findings"]) or "ok"
        print(f"  {document['role']:12} {document['system_code'] or '':5} {document['filename']}: {codes}")
    folder = report.folder
    if folder.get("reachable"):
        for sheet in folder["new_sheets"]:
            print(f"  folder: not on the project -> {sheet['filename']} ({sheet['system_code'] or 'no system'})")
        for name in folder["superseded_attached"]:
            print(f"  folder: superseded revision attached -> {name}")
    reader = report.reader
    print(f"  reader: current {reader['current']}; lines by version {reader['lines_by_parser_version']}; "
          f"{reader['recorded_runs']} recorded runs; {reader['unassigned_lines']} unassigned")
    boq = report.boq
    if boq.get("read"):
        print(f"  BOQ: {boq['old_lines']} lines / {boq['old_quantity']} units stored -> {boq['new_lines']} lines / "
              f"{boq['new_quantity']} units read; {boq['changed']} changed ({boq['probable']} uncertain), "
              f"{boq['added']} added, {boq['removed']} removed, {boq['unchanged']} unchanged")
    for line in report.blocking:
        print(f"  BLOCKING: {line}")
    for line in report.notes:
        print(f"  note: {line}")
    if report.candidate_id:
        print(f"  recorded as re-read #{report.candidate_id} (review it on the project's Re-read page)")


if __name__ == "__main__":
    raise SystemExit(main())
