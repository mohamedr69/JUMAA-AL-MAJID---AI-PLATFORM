"""Import the Compliance Response Database into the application database.

What "Update knowledge base" does on the Settings page, from a terminal
with progress -- the easier way to do the first (long) import, and the way
to do it on a machine without a browser. Reads the workbook named in
COMPLIANCE_KNOWLEDGE_SOURCE (or --source) and writes the knowledge tables
in one transaction; a failure leaves the last usable knowledge untouched.
No model is called.

    cd backend
    .\\venv\\Scripts\\python scripts\\import_compliance_knowledge.py
    .\\venv\\Scripts\\python scripts\\import_compliance_knowledge.py --force      # even if the workbook is unchanged
    .\\venv\\Scripts\\python scripts\\import_compliance_knowledge.py --inspect    # list the source, import nothing
    .\\venv\\Scripts\\python scripts\\import_compliance_knowledge.py --source "D:\\copy\\Compliance_Response_Database"

Only files are read; nothing in the source folder is written.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from app.database import SessionLocal, engine  # noqa: E402
from app.knowledge import importer  # noqa: E402
from app.migrations import upgrade_to_head  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Import even if the workbook has not changed.")
    parser.add_argument("--inspect", action="store_true", help="List what the source folder holds and stop.")
    parser.add_argument("--source", metavar="FOLDER", help="The source folder, instead of COMPLIANCE_KNOWLEDGE_SOURCE.")
    args = parser.parse_args(argv)

    root = Path(args.source) if args.source else None
    inspection = importer.inspect_source(root)
    if not inspection.reachable:
        print(inspection.problem)
        return 1
    counts = inspection.counts()
    print(f"Source: {inspection.label}: {len(inspection.files)} files -- " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    workbook = inspection.workbook
    if workbook is None:
        print(f"  {importer.CANONICAL_WORKBOOK} not found: nothing can be imported.")
        return 1
    print(f"  canonical: {workbook.relative_path} ({(workbook.size or 0) / 1e6:.1f} MB)"
          + (" -- ONLINE-ONLY placeholder, make it available on this device first" if workbook.online_only else ""))
    if args.inspect:
        return 0

    upgrade_to_head(engine)
    done = threading.Event()

    def report() -> None:
        while not done.wait(5):
            s = importer.state()
            print(f"  {s['phase'] or '...'} {s['detail'] or ''}".rstrip(), flush=True)

    threading.Thread(target=report, daemon=True).start()
    started = time.time()
    db = SessionLocal()
    try:
        record = importer.run_import(db, force=args.force, root=root)
    finally:
        done.set()
        db.close()
    print(f"{record.status} in {time.time() - started:.0f}s: files discovered {record.files_discovered}, imported "
          f"{record.files_imported}, unchanged {record.files_unchanged}, skipped {record.files_skipped}; records added "
          f"{record.records_added}, updated {record.records_updated}, inactive {record.records_inactive}, "
          f"flagged {record.records_flagged}" + (f"\n  {record.error}" if record.error else ""))
    return 0 if record.status in ("succeeded", "unchanged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
