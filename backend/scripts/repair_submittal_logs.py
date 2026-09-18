r"""Put the material submittals the model has already read into the log.

A submittal package is a scan of a form in front of a hundred datasheets.
The document-control reader can come back from one with nothing at all, and
until now the log took its entries from that reader alone -- so a form the
model had read perfectly well never reached the log. The project then
reported no material submittal on file, with 0% progress on its home page,
while its register held the submittal and the file sat in the project
folder all along.

The sync reads a file again only when its content changes, so an index
built before the fix stays as it was. This puts the missing entries in,
from what is already stored: no file is opened, no page is read, and the
model is not called.

    cd backend
    .\venv\Scripts\python scripts\repair_submittal_logs.py            # what would change
    .\venv\Scripts\python scripts\repair_submittal_logs.py --apply    # change it
    .\venv\Scripts\python scripts\repair_submittal_logs.py --project 17

Only a document whose stored reading says it is a submittal is touched, and
only where the log has no entry for that reference, revision and system
already. Nothing else on the row is changed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Project, ProjectDocument  # noqa: E402
from app.services import document_sync  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=int, help="one project's id (the default is every project)")
    parser.add_argument("--apply", action="store_true", help="make the change (without this it only reports)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        query = (db.query(ProjectDocument)
                 .filter(ProjectDocument.role == document_sync.ROLE_SUBMITTAL,
                         ProjectDocument.state != document_sync.REMOVED))
        if args.project:
            query = query.filter(ProjectDocument.project_id == args.project)

        added: dict[int, list[str]] = {}
        for row in query.order_by(ProjectDocument.project_id, ProjectDocument.id):
            extracted = dict(row.extracted or {})
            reading = extracted.get("form") or {}
            if not reading:
                continue
            extracted["records"] = list(extracted.get("records") or [])
            modified = (datetime.fromtimestamp(row.mtime, timezone.utc) if row.mtime
                        else datetime.now(timezone.utc))
            if not document_sync.record_for_the_log(extracted, reading,
                                                    relative=(row.relative_path or row.filename or "").replace("\\", "/"),
                                                    modified=modified):
                continue
            entry = extracted["records"][-1]
            added.setdefault(row.project_id, []).append(
                f"{entry['system_code'] or '--'} {entry['reference']} {entry['revision']} {entry['status']}  {entry['path']}")
            if args.apply:
                row.extracted = extracted  # reassigned, so SQLAlchemy sees the JSON change

        if not added:
            print("Every material submittal the model has read is already in the log. Nothing to do.")
            return 0

        for project_id, entries in sorted(added.items()):
            project = db.get(Project, project_id)
            print(f"\nProject {project_id} -- EP-{project.ep_number if project else '?'} {project.project_name if project else ''}")
            for entry in entries:
                print(f"    + {entry}")
        total = sum(len(entries) for entries in added.values())
        print(f"\n{total} log entr{'y' if total == 1 else 'ies'} across {len(added)} project{'' if len(added) == 1 else 's'}.")

        if not args.apply:
            print("\nNothing was changed. Run it again with --apply to make it so.")
            return 0
        db.commit()
        print("\nDone. The logs and each project's home page now show them.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
