r"""The archive's EP folders, indexed once and then searched in the database.

Creating a project used to walk the synced OneDrive archive every time
someone typed an EP number: thousands of folders over a synced drive, for
one answer, on every attempt, and nothing to type against -- the engineer
had to already know the number. This module walks the archive *once*,
stores what it found, and answers "Find Project" and the search box's
suggestions out of the database.

Two rules make the index shareable rather than one person's:

  * **A folder is stored as its path below the archive root**, never as
    the absolute path the scanning machine saw. The archive is a
    SharePoint library OneDrive syncs, so it lands under each user's own
    profile -- `C:\Users\moham\...` on one PC and `C:\Users\mramadan\...`
    on the next. `relative_path` is the same on both, and each machine
    joins it to its own `PROJECTS_ROOT` (`absolute_path`). Nothing a user
    is named in ever reaches the database.
  * **The archive is identified by its name, not by its path**, for the
    same reason: keyed on the path, one shared database would hold one
    copy of the index per user, and whoever opened it second would index
    the whole archive again (`archive_identity`).

So the index survives being put in a shared `DATA_ROOT` folder: one
machine scans, and every machine that opens that database can search it.

What the scan does *not* do is create projects. A folder in the archive is
a folder; a Project is a record an engineer made. The directory is how the
engineer finds the folder to make one from.

    scan(db)                  -- walk the archive and record what is there
    search(db, "294")         -- what to suggest while someone types
    folders_for(db, "29495")  -- the folders for one EP number, no walk
    status(db)                -- what the pages say about the index

`backend/scripts/scan_archive.py` runs a scan from the command line, and
the server refreshes the index in the background (`start_background_scan`,
`refresh_forever`), so a folder added to OneDrive today is suggested
without anyone asking for a rescan.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.models import EpArchiveFolder, EpArchiveRoot, Project
from app.services.ep_resolver import (
    MAX_EP_SEARCH_DEPTH,
    ep_number_of,
    project_name_of,
    walk_with_errors,
)

log = logging.getLogger(__name__)

# One scan per server at a time. A scan is a walk of the whole archive;
# two at once would read the same synced drive twice for the same answer
# and race each other's rows.
_scan_lock = threading.Lock()
_stop_refresh = threading.Event()


class ArchiveUnavailable(RuntimeError):
    """The archive is not configured on this machine, or not reachable."""


def _digest(value: str) -> str:
    """A short fixed-length key for a long Windows path. A hash for its
    length, not for secrecy -- the path it stands for is in the row beside
    it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def archive_identity(root: Path) -> str:
    r"""What makes this archive *this* archive, across machines: the synced
    library's folder name, case- and space-insensitive.

    Not the absolute path. The same library is `C:\Users\moham\Juma Al
    Majid\SSD FIRE ALARM PROJECTS - ...` for one engineer and the same
    under another profile for the next; keyed on the path, a database
    shared between them would carry one index per engineer.
    """
    return " ".join(Path(root).name.split()).casefold()


def _walked_key(root: Path, directory: Path) -> str:
    """How a directory that was read is keyed, so a stored folder can ask
    whether its parent was among them. The archive root is the empty
    string, which is what `_parent_key` gives a folder at the top."""
    relative = relative_path_of(root, directory)
    return "" if relative == "." else relative.casefold()


def relative_path_of(root: Path, folder: Path) -> str:
    r"""A folder's path below the archive root, with forward slashes: the
    portable half of its location. `\` and `/` both appear in Windows
    paths, so the separator is normalised or the same folder keys twice."""
    return Path(folder).relative_to(Path(root)).as_posix()


def absolute_path(root: Path, relative_path: str) -> Path:
    """Where a stored folder is *on this machine*: the relative path joined
    to this machine's archive root."""
    return Path(root) / relative_path


def archive_root() -> Path:
    """The configured archive, or a refusal saying which of the two things
    is wrong -- not set, or set and not reachable."""
    settings = get_settings()
    if not settings.projects_root:
        raise ArchiveUnavailable("Project archive is not configured (PROJECTS_ROOT is unset)")
    root = Path(settings.projects_root)
    if not root.is_dir():
        raise ArchiveUnavailable("Project archive path is not reachable")
    return root


def get_root(db: Session, root: Path, *, create: bool = True) -> EpArchiveRoot | None:
    """This archive's record, made on first sight. `root_path` is refreshed
    every time: it is the path *this* machine sees, kept for the status
    page, and it is allowed to differ from the one the last machine wrote."""
    key = _digest(archive_identity(root))
    record = db.scalar(select(EpArchiveRoot).where(EpArchiveRoot.root_key == key))
    if record is None:
        if not create:
            return None
        record = EpArchiveRoot(root_key=key, root_path=str(root))
        db.add(record)
        db.commit()
        db.refresh(record)
    elif record.root_path != str(root):
        record.root_path = str(root)
        db.commit()
    return record


@dataclass
class ScanResult:
    """What one walk of the archive found and what it changed."""

    root_path: str
    found: int = 0
    # EP numbers whose folder the index had never seen: the new projects.
    added: list[str] = field(default_factory=list)
    # Folders that were indexed as gone and are back (a folder restored, or
    # a drive that had not finished syncing when the last scan ran).
    returned: list[str] = field(default_factory=list)
    # Folders indexed before whose parent directory was read this time and
    # did not hold them any more -- see the sweep in `scan`.
    missing: list[str] = field(default_factory=list)
    # Folders the walk could not read. What is inside one of these is left
    # exactly as the index has it.
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def complete(self) -> bool:
        """Whether every folder this scan opened could be read. Incomplete
        does not invalidate the scan: what it did read is recorded, and
        only what sits inside an unreadable folder is left alone (the
        sweep in `scan`)."""
        return not self.errors

    @property
    def status(self) -> str:
        return "ready" if self.complete else "partial"

    def as_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "found": self.found,
            "added": self.added,
            "returned": self.returned,
            "missing": self.missing,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
            "complete": self.complete,
            "status": self.status,
            "duration_s": round(self.duration_s, 1),
        }


def _parent_key(relative_path: str) -> str:
    """The folder that contains this one, as `_walk_archive` keys it. The
    archive root itself is the empty string."""
    head, _, _ = relative_path.rpartition("/")
    return head.casefold()


def _walk_archive(root: Path, progress=None) -> tuple[dict[str, tuple[str, str, str]], list[str], set[str]]:
    """Every EP folder under the archive, in one pass.

    The same traversal `find_ep_folders` uses for a single number -- the
    same depth cap, and the same rule that a project folder is never
    descended into, so an internal subfolder named after another EP number
    is not indexed as a project of its own -- but collecting every number
    rather than matching one.

    Returns what it found, what it could not read, and *which directories
    it did read*. The third is what makes "this folder is gone" a
    defensible conclusion: it may only be drawn about a folder whose
    parent directory was listed successfully this time round.
    """
    errors: list[str] = []
    found: dict[str, tuple[str, str, str]] = {}
    walked: set[str] = set()
    seen_dirs = 0
    for dirpath, dirnames, _filenames in walk_with_errors(root, MAX_EP_SEARCH_DEPTH, errors):
        # os.walk only yields a directory it managed to list, so reaching
        # here is the proof that this one was read.
        walked.add(_walked_key(root, Path(dirpath)))
        for name in list(dirnames):
            number = ep_number_of(name)
            if number is None:
                continue
            relative = relative_path_of(root, Path(dirpath) / name)
            found[_digest(relative.casefold())] = (number, name, relative)
        dirnames[:] = [d for d in dirnames if ep_number_of(d) is None]
        seen_dirs += 1
        if progress is not None and seen_dirs % 200 == 0:
            progress(len(found), 0, f"Read {seen_dirs} folders, found {len(found)} projects")
    return found, errors, walked


def scan(db: Session, *, root: Path | None = None, progress=None) -> ScanResult:
    """Walk the archive and bring the index up to date with it.

    `progress` is a `jobs.JobContext.progress`-shaped callable when the scan
    runs as a background job; it is also where a cancellation is raised
    from, which leaves the scan failed and the index as it was.
    """
    root = root or archive_root()
    record = get_root(db, root)
    token = uuid4().hex[:32]
    started = utc_now()
    record.scan_status = "scanning"
    record.scan_token = token
    record.scan_started_at = started
    record.scan_finished_at = None
    record.last_error = None
    db.commit()

    try:
        found, errors, walked = _walk_archive(root, progress)
    except Exception as exc:  # noqa: BLE001 -- a walk that died leaves the index alone
        record.scan_status = "failed"
        record.scan_finished_at = utc_now()
        record.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        raise

    result = ScanResult(root_path=str(root), found=len(found), errors=errors)
    now = utc_now()
    existing = {
        row.path_key: row
        for row in db.scalars(select(EpArchiveFolder).where(EpArchiveFolder.archive_id == record.id))
    }

    for key, (number, name, relative) in found.items():
        row = existing.get(key)
        if row is None:
            db.add(EpArchiveFolder(
                archive_id=record.id, ep_number=number, folder_name=name, relative_path=relative,
                path_key=key, is_available=True, first_seen_at=now, last_seen_at=now,
                last_seen_scan_token=token,
            ))
            result.added.append(number)
            continue
        if not row.is_available:
            result.returned.append(number)
        # The same folder as last time (the key is its path, case-folded).
        # A folder that was *renamed* has a new path and so a new key: it
        # arrives above as an addition, and the sweep below retires the
        # name it used to have. Either way what the search suggests is the
        # name as the archive reads now.
        row.ep_number, row.folder_name, row.relative_path = number, name, relative
        row.is_available = True
        row.last_seen_at = now
        row.last_seen_scan_token = token

    for key, row in existing.items():
        # A folder is gone only if the directory that held it was read this
        # time and did not have it. Three folders in the real archive have
        # paths too long for Windows to open and so fail every scan;
        # judging the whole archive on that would either freeze the index
        # for good or mark folders missing that were merely unread.
        if key in found or not row.is_available:
            continue
        if _parent_key(row.relative_path) not in walked:
            continue
        # Kept, not deleted: an EP number that moved between contractors
        # still has its history, and a folder that comes back is the same
        # row again.
        row.is_available = False
        result.missing.append(row.ep_number)

    result.duration_s = (utc_now() - started).total_seconds()
    record.scan_status = result.status
    record.scan_finished_at = utc_now()
    record.last_error = (
        None if result.complete
        else f"{len(errors)} folder(s) could not be read; what is inside them was left as it was. "
             f"First: {errors[0]}"[:2000]
    )
    # The walk finished and the index was updated, so this is when the index
    # last matched the archive. "partial" says some corners of it could not
    # be opened, not that the scan did not happen.
    record.last_successful_scan_at = record.scan_finished_at
    db.commit()
    log.info("Archive scan: %s", result.as_dict())
    return result


def preview_scan(db: Session, root: Path) -> tuple[dict[str, tuple[str, str, str]], list[str], set[str]]:
    """What a scan would find, and which EP numbers would be new, without
    writing anything. `scripts/scan_archive.py --dry-run` is this."""
    found, errors, _walked = _walk_archive(root)
    record = get_root(db, root, create=False)
    known: set[str] = set()
    if record is not None:
        known = set(db.scalars(select(EpArchiveFolder.path_key).where(
            EpArchiveFolder.archive_id == record.id, EpArchiveFolder.is_available.is_(True))))
    new_numbers = {number for key, (number, _n, _r) in found.items() if key not in known}
    return found, errors, new_numbers


def observe(db: Session, root: Path, folders: list[Path], ep_number: str) -> None:
    """Record folders found by a live search that the index did not hold.

    A folder created since the last scan is found by the fallback walk, and
    this is what stops the next person paying for that walk too. It adds
    and refreshes only; a folder the index has and this search did not look
    for is untouched, because one EP number's search is not a view of the
    archive.
    """
    record = get_root(db, root)
    now = utc_now()
    for folder in folders:
        try:
            relative = relative_path_of(root, folder)
        except ValueError:
            continue  # outside the archive: not ours to index
        key = _digest(relative.casefold())
        row = db.scalar(select(EpArchiveFolder).where(
            EpArchiveFolder.archive_id == record.id, EpArchiveFolder.path_key == key))
        if row is None:
            db.add(EpArchiveFolder(
                archive_id=record.id, ep_number=ep_number, folder_name=Path(folder).name,
                relative_path=relative, path_key=key, is_available=True,
                first_seen_at=now, last_seen_at=now,
            ))
        else:
            row.is_available = True
            row.last_seen_at = now
            row.folder_name = Path(folder).name
    db.commit()


def is_indexed(db: Session, root: Path | None = None) -> bool:
    """Whether this archive has anything indexed. Separate from
    `scan_status`: a scan that could not read a few folders still leaves a
    usable index, and a "ready" status on an empty archive is not one."""
    root = root or Path(get_settings().projects_root or "")
    record = get_root(db, root, create=False)
    if record is None:
        return False
    return bool(db.scalar(select(func.count(EpArchiveFolder.id)).where(
        EpArchiveFolder.archive_id == record.id, EpArchiveFolder.is_available.is_(True))))


def folders_for(db: Session, ep_number: str, root: Path | None = None) -> list[Path] | None:
    """The folders this EP number has, from the index -- no walk.

    `None` means "the index cannot answer" (nothing indexed for this
    archive yet), which is the caller's signal to walk as before. An empty
    list is an answer: the index holds this archive and this number is not
    in it.
    """
    root = root or Path(get_settings().projects_root or "")
    record = get_root(db, root, create=False)
    if record is None:
        return None
    rows = db.scalars(select(EpArchiveFolder).where(
        EpArchiveFolder.archive_id == record.id,
        EpArchiveFolder.ep_number == str(ep_number).strip(),
        EpArchiveFolder.is_available.is_(True),
    ).order_by(EpArchiveFolder.relative_path)).all()
    if rows:
        return [absolute_path(root, row.relative_path) for row in rows]
    return [] if is_indexed(db, root) else None


@dataclass
class Suggestion:
    """One line of the search box's dropdown: an EP number, what it is
    called, and whether the platform already has a project for it."""

    ep_number: str
    project_name: str | None
    folder_name: str
    relative_path: str
    # How many folders in the archive carry this EP number. More than one
    # and creating it still goes through the "which folder is this?" step.
    locations: int
    # The platform's project for this number, when it has one: the search
    # then offers to open it rather than create it twice.
    project_id: int | None
    project_status: str | None


def normalise_query(query: str) -> str:
    """What someone typed, as the index stores it: "EP-294", "ep 294" and
    "294" are one query. Only the "EP" prefix and its separator are
    stripped -- a name is left exactly as typed."""
    text = " ".join(str(query or "").split())
    lowered = text.casefold()
    if lowered.startswith("ep") and not lowered[2:3].isalpha():
        return text[2:].lstrip(" -_").strip() or text.strip()
    return text.strip()


def search(db: Session, query: str, *, limit: int = 8, root: Path | None = None) -> list[Suggestion]:
    """What to suggest for what has been typed so far.

    Matches an EP number that *starts* with the digits typed, and a folder
    name that contains the words typed, so both "294" and "ivy" find
    EP-29495 IVY Garden 2. One suggestion per EP number: a number filed
    under two contractors is one project to the person typing, and which
    folder it is is the question the create flow already asks.
    """
    term = normalise_query(query)
    if len(term) < 2:
        return []
    root = root or Path(get_settings().projects_root or "")
    record = get_root(db, root, create=False)
    if record is None:
        return []

    # LIKE's own wildcards, typed into a search box, would match everything.
    plain = term.replace("%", "").replace("_", "")
    if not plain:
        return []
    rows = db.scalars(select(EpArchiveFolder).where(
        EpArchiveFolder.archive_id == record.id,
        EpArchiveFolder.is_available.is_(True),
        EpArchiveFolder.ep_number.like(f"{plain}%") | EpArchiveFolder.folder_name.like(f"%{plain}%"),
    ).order_by(EpArchiveFolder.ep_number, EpArchiveFolder.relative_path).limit(limit * 20)).all()
    if not rows:
        return []

    grouped: dict[str, list[EpArchiveFolder]] = {}
    for row in rows:
        grouped.setdefault(row.ep_number, []).append(row)

    projects = {
        p.ep_number: p
        for p in db.scalars(select(Project).where(Project.ep_number.in_(list(grouped))))
    }

    def rank(number: str) -> tuple:
        # The number typed, first; then the numbers it begins, then a name
        # match. Within a rank the highest EP number first -- an engineer
        # typing "30" is likelier to want the recent EP-30880 than EP-30001.
        return (0 if number == term else 1 if number.startswith(term) else 2,
                -int(number) if number.isdigit() else 0, number)

    suggestions: list[Suggestion] = []
    for number in sorted(grouped, key=rank)[:limit]:
        folders = grouped[number]
        project = projects.get(number)
        # The project's own name, once it has one, beats the folder's: it was
        # read off the DRF. Before that the folder name is all there is.
        name = (project.project_name if project and project.project_name else None) or next(
            (project_name_of(f.folder_name) for f in folders if project_name_of(f.folder_name)), None)
        suggestions.append(Suggestion(
            ep_number=number,
            project_name=name,
            folder_name=folders[0].folder_name,
            relative_path=folders[0].relative_path,
            locations=len(folders),
            project_id=project.id if project else None,
            project_status=project.status.value if project else None,
        ))
    return suggestions


def status(db: Session, root: Path | None = None) -> dict:
    """What the pages say about the index: whether it can be searched, how
    much is in it, and when it last matched the archive."""
    settings = get_settings()
    configured = bool(settings.projects_root)
    root = root or Path(settings.projects_root or "")
    reachable = configured and root.is_dir()
    record = get_root(db, root, create=False) if configured else None
    folders = 0
    numbers = 0
    if record is not None:
        folders = db.scalar(select(func.count(EpArchiveFolder.id)).where(
            EpArchiveFolder.archive_id == record.id, EpArchiveFolder.is_available.is_(True))) or 0
        numbers = db.scalar(select(func.count(func.distinct(EpArchiveFolder.ep_number))).where(
            EpArchiveFolder.archive_id == record.id, EpArchiveFolder.is_available.is_(True))) or 0
    return {
        "configured": configured,
        "reachable": reachable,
        "archive_path": str(root) if configured else None,
        "scan_status": record.scan_status if record else "pending",
        "scanning": bool(record and record.scan_status == "scanning") or _scan_lock.locked(),
        "projects": numbers,
        "folders": folders,
        "searchable": folders > 0,
        "last_scan_at": _iso(record.scan_finished_at) if record else None,
        "last_successful_scan_at": _iso(record.last_successful_scan_at) if record else None,
        "last_error": record.last_error if record else None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _scan_with_own_session(reason: str) -> None:
    from app.database import SessionLocal

    if not _scan_lock.acquire(blocking=False):
        log.info("Archive scan (%s) skipped: one is already running", reason)
        return
    db = SessionLocal()
    try:
        scan(db)
    except ArchiveUnavailable as exc:
        log.info("Archive scan (%s) skipped: %s", reason, exc)
    except Exception:  # noqa: BLE001 -- a background scan never takes the server down
        log.exception("Archive scan (%s) failed", reason)
    finally:
        db.close()
        _scan_lock.release()


def start_background_scan(reason: str = "requested") -> bool:
    """Scan on a thread of its own. False when one is already running --
    the caller is not made to wait for it either way."""
    if _scan_lock.locked():
        return False
    threading.Thread(target=_scan_with_own_session, args=(reason,), daemon=True, name="archive-scan").start()
    return True


def due_for_refresh(db: Session, minutes: int, root: Path | None = None) -> bool:
    """Whether it has been `minutes` since the last scan *attempt*. An
    archive that has never been scanned is always due.

    Paced on the attempt rather than on a flawless result: an archive with
    a folder Windows cannot open -- the real one has three, with paths past
    the 260-character limit -- would otherwise never record a clean scan,
    and the refresh would walk the whole drive again every minute.
    """
    root = root or Path(get_settings().projects_root or "")
    record = get_root(db, root, create=False)
    if record is None:
        return True
    last = record.scan_finished_at or record.scan_started_at
    if last is None:
        return True
    return utc_now() - last >= timedelta(minutes=max(minutes, 1))


def refresh_forever(scan_now: bool = True) -> None:
    """The background refresh: a scan every `ARCHIVE_INDEX_REFRESH_MINUTES`,
    so a folder someone added to OneDrive today is suggested without anyone
    asking for a rescan. Stopped with `stop_refresh` at shutdown.

    `scan_now` false (`ARCHIVE_INDEX_SCAN_ON_START=false`) holds the first
    scan back for one interval, for a machine that should not read the
    whole archive the moment it starts.
    """
    from app.database import SessionLocal

    minutes = max(get_settings().archive_index_refresh_minutes, 1)
    if not scan_now:
        _stop_refresh.wait(minutes * 60)
    while not _stop_refresh.is_set():
        db = SessionLocal()
        try:
            due = due_for_refresh(db, minutes)
        except Exception:  # noqa: BLE001 -- a database blip is not worth a crashed thread
            due = False
        finally:
            db.close()
        if due:
            _scan_with_own_session("scheduled")
        # The wait is short so a shutdown is not held up for the whole
        # refresh interval; `due_for_refresh` is what paces the scans.
        _stop_refresh.wait(60)


def start_refresh_thread(scan_now: bool = True) -> None:
    _stop_refresh.clear()
    threading.Thread(target=refresh_forever, args=(scan_now,), daemon=True, name="archive-refresh").start()


def stop_refresh() -> None:
    _stop_refresh.set()
