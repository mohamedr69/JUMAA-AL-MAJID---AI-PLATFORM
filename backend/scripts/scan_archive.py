r"""Index the archive's EP folders, and say which ones are new.

    python -m scripts.scan_archive                 scan and report
    python -m scripts.scan_archive --dry-run       report, change nothing
    python -m scripts.scan_archive --search 294    what the search box would suggest
    python -m scripts.scan_archive --status        what is in the index now

The server does this by itself, on start and every
ARCHIVE_INDEX_REFRESH_MINUTES (app/services/ep_directory.py). This is the
same scan from the command line: for the first build on a new machine, for
a scheduled task, or to see what a scan would do before letting it.

What it records is the folder's path *below* the archive root, never the
absolute path this machine sees. The archive is a SharePoint library
OneDrive syncs under each user's own profile, so an index full of
C:\Users\<somebody>\... would only work for that somebody. Every machine
joins the stored path to its own PROJECTS_ROOT, and the index can live in
a shared DATA_ROOT folder for everyone.

The archive is only ever read. Nothing here creates a project: a folder in
the archive is a folder, and a project is a record an engineer makes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.services import ep_directory  # noqa: E402


def print_status(db) -> None:
    state = ep_directory.status(db)
    print(f"Archive     : {state['archive_path'] or '(not configured)'}")
    print(f"Reachable   : {'yes' if state['reachable'] else 'no'}")
    print(f"Indexed     : {state['projects']} EP numbers in {state['folders']} folders")
    print(f"Last scan   : {state['scan_status']} at {state['last_scan_at'] or 'never'}")
    print(f"Last full   : {state['last_successful_scan_at'] or 'never'}")
    if state["last_error"]:
        print(f"Last error  : {state['last_error']}")


def print_search(db, term: str) -> None:
    hits = ep_directory.search(db, term, limit=15)
    if not hits:
        print(f"Nothing indexed matches {term!r}.")
        return
    print(f"{len(hits)} suggestion(s) for {term!r}:")
    for hit in hits:
        where = f" ({hit.locations} locations)" if hit.locations > 1 else ""
        known = f"  [project #{hit.project_id}]" if hit.project_id else ""
        print(f"  EP-{hit.ep_number}  {hit.project_name or '(unnamed)'}{where}{known}")
        print(f"      {hit.relative_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="Scan this folder instead of PROJECTS_ROOT")
    parser.add_argument("--dry-run", action="store_true",
                        help="Walk the archive and report, without touching the index")
    parser.add_argument("--status", action="store_true", help="Show what is in the index and stop")
    parser.add_argument("--search", metavar="TEXT", help="Show what the search box would suggest and stop")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary line")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.status:
            print_status(db)
            return 0
        if args.search:
            print_search(db, args.search)
            return 0

        try:
            root = Path(args.root) if args.root else ep_directory.archive_root()
        except ep_directory.ArchiveUnavailable as exc:
            print(f"Cannot scan: {exc}", file=sys.stderr)
            return 2
        if not root.is_dir():
            print(f"Cannot scan: {root} is not a folder", file=sys.stderr)
            return 2

        if args.dry_run:
            found, errors, new_numbers = ep_directory.preview_scan(db, root)
            numbers = {number for number, _name, _rel in found.values()}
            print(f"Would index {len(found)} folder(s), {len(numbers)} EP number(s), under {root}")
            if not args.quiet:
                for number in sorted(new_numbers):
                    print(f"  + EP-{number} (new since the last scan)")
            if errors:
                print(f"{len(errors)} folder(s) could not be read; a dry run marks nothing missing either way")
                for line in errors[:10]:
                    print(f"  ! {line}")
            return 0

        result = ep_directory.scan(db, root=root)
        if not args.quiet:
            for number in sorted(set(result.added)):
                print(f"  + EP-{number}")
            for number in sorted(set(result.missing)):
                print(f"  - EP-{number} (not found this scan)")
            for line in result.errors[:10]:
                print(f"  ! {line}")
        print(f"{result.found} folder(s) indexed under {result.root_path}: "
              f"{len(result.added)} new, {len(result.returned)} back, {len(result.missing)} missing, "
              f"{len(result.errors)} unreadable, in {result.duration_s:.1f}s")
        if not result.complete:
            # Not a failure: the index was updated with everything that
            # could be read, and only what sits inside an unreadable folder
            # was left as it was. The real archive has three folders whose
            # paths are past Windows' limit, so this is the normal result,
            # and a scheduled task that reported failure every night would
            # be ignored within a week.
            print(f"{len(result.errors)} folder(s) could not be read; what is inside them was left as it was")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
