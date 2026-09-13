"""Copy the company library out of the project archive into the local one.

The company documents, the templates and the manufacturers' datasheets are
the same on every job. Reading them from a synced OneDrive tree costs about
eleven seconds of indexing on every start and ties the platform to one
company's filing, so they are copied here once and read locally after that.
See `backend/library/README.md`.

    cd backend
    .\\venv\\Scripts\\python scripts\\sync_library.py             # copy it across
    .\\venv\\Scripts\\python scripts\\sync_library.py --status     # what is held
    .\\venv\\Scripts\\python scripts\\sync_library.py --datasheets MENVIER="D:/Menvier"

The archive is **read, never written**. A file whose size and timestamp
already match is left alone, so running this again after a datasheet is added
copies that datasheet and nothing else.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services import company_library  # noqa: E402


@dataclass
class Tally:
    copied: int = 0
    unchanged: int = 0
    failed: int = 0
    bytes_copied: int = 0

    def __iadd__(self, other: "Tally") -> "Tally":
        self.copied += other.copied
        self.unchanged += other.unchanged
        self.failed += other.failed
        self.bytes_copied += other.bytes_copied
        return self


def _same(source: Path, target: Path) -> bool:
    """Whether the copy is already there. Size and timestamp, not contents:
    reading both files to compare them would cost as much as copying."""
    try:
        a, b = source.stat(), target.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def copy_tree(source: Path, target: Path, *, dry_run: bool, label: str) -> Tally:
    tally = Tally()
    if not source.is_dir():
        print(f"  {label}: not found at {source}")
        tally.failed += 1
        return tally

    try:
        entries = [p for p in source.rglob("*") if p.is_file()]
    except OSError as exc:
        print(f"  {label}: could not be listed ({exc})")
        tally.failed += 1
        return tally

    for path in entries:
        # A OneDrive conflict copy is the same document twice, and twice in
        # an issued submittal is a defect.
        if path.name.startswith("~$") or path.name.lower() == "thumbs.db":
            continue
        destination = target / path.relative_to(source)
        if _same(path, destination):
            tally.unchanged += 1
            continue
        if dry_run:
            print(f"    would copy {path.relative_to(source)}")
            tally.copied += 1
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # copy2 keeps the timestamp, which is what makes the next run a
            # no-op and what the datasheet index keys on.
            shutil.copy2(path, destination)
            tally.copied += 1
            tally.bytes_copied += destination.stat().st_size
        except OSError as exc:
            print(f"    FAILED {path.relative_to(source)}: {exc}")
            tally.failed += 1

    verb = "would copy" if dry_run else "copied"
    print(f"  {label}: {verb} {tally.copied}, unchanged {tally.unchanged}, failed {tally.failed}")
    return tally


def print_status() -> int:
    root = company_library.library_root()
    print(f"Library root: {root}{'' if root.is_dir() else '  (does not exist yet)'}")
    print(f"Cache root:   {company_library.cache_root()}")
    print(f"Archive:      {get_settings().projects_root or '(PROJECTS_ROOT unset)'}")
    print()
    rows = company_library.status()
    width = max((len(r.name) for r in rows), default=10)
    for row in rows:
        mark = {"library": "local ", "archive": "ARCHIVE", "missing": "MISSING"}[row.source]
        detail = f"  {row.detail}" if row.detail else ""
        print(f"  [{mark}] {row.name:<{width}}  {row.path or ''}{detail}")
    archived = [r for r in rows if r.source == "archive"]
    if archived:
        print()
        print(f"{len(archived)} still read from the archive. Run this script without --status to copy them in.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="Show what the library holds and stop.")
    parser.add_argument("--dry-run", action="store_true", help="Say what would be copied, copy nothing.")
    parser.add_argument(
        "--datasheets",
        action="append",
        default=[],
        metavar="BRAND=PATH",
        help="A manufacturer's datasheet folder to copy in. Repeatable. "
        "Without this, the brands in ARCHIVE_DATASHEET_LIBRARIES are copied.",
    )
    parser.add_argument(
        "--submittal",
        metavar="PATH",
        help="The submittal builder folder to copy in. Without this, ARCHIVE_SUBMITTAL_LIBRARY is used.",
    )
    parser.add_argument(
        "--skip-datasheets", action="store_true", help="Copy the submittal builder only."
    )
    parser.add_argument(
        "--skip-submittal", action="store_true", help="Copy the datasheet libraries only."
    )
    args = parser.parse_args(argv)

    if args.status:
        return print_status()

    settings = get_settings()
    root = company_library.scaffold()
    print(f"Library root: {root}")
    if args.dry_run:
        print("Dry run: nothing will be written.\n")

    archive = (settings.projects_root or "").strip()
    total = Tally()

    # --- the manufacturers' datasheets ---------------------------------
    if not args.skip_datasheets:
        sources: dict[str, str] = {}
        for entry in args.datasheets:
            brand, _, location = entry.partition("=")
            if not location:
                parser.error(f"--datasheets expects BRAND=PATH, got {entry!r}")
            sources[brand.strip().upper()] = location.strip()
        if not sources:
            sources = {k.upper(): v for k, v in (settings.archive_datasheet_libraries or {}).items()}

        if not sources:
            print("  No datasheet libraries to copy.")
        for brand, location in sorted(sources.items()):
            source = Path(location)
            if not source.is_absolute():
                if not archive:
                    print(f"  {brand}: PROJECTS_ROOT is unset, so '{location}' cannot be found.")
                    total.failed += 1
                    continue
                source = Path(archive) / location
            total += copy_tree(
                source,
                root / company_library.DATASHEETS / brand,
                dry_run=args.dry_run,
                label=f"datasheets/{brand}",
            )

    # --- the submittal builder ------------------------------------------
    if not args.skip_submittal:
        location = args.submittal or settings.archive_submittal_library
        source = Path(location) if location else None
        if source is not None and not source.is_absolute():
            if archive:
                source = Path(archive) / location
            else:
                print("  submittal: PROJECTS_ROOT is unset, so the builder folder cannot be found.")
                source = None
                total.failed += 1
        if source is not None:
            total += copy_tree(
                source, root / company_library.SUBMITTAL, dry_run=args.dry_run, label="submittal"
            )

    print()
    megabytes = total.bytes_copied / (1024 * 1024)
    print(
        f"{'Would copy' if args.dry_run else 'Copied'} {total.copied} file(s)"
        + (f" ({megabytes:.1f} MB)" if not args.dry_run and total.copied else "")
        + f", {total.unchanged} already current, {total.failed} failed."
    )
    if not args.dry_run and total.copied:
        print("The datasheet index rebuilds itself on the next start, or from Reindex on the library page.")
    return 1 if total.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
