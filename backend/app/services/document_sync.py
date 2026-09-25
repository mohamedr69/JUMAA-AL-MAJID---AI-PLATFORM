"""The project's document index, and the one sync that keeps it.

The rule across the platform:

    OneDrive   the source documents (synced to this machine as files)
    database   the project's working data and everything read off a document
    Python     change detection, dependency tracking, validation, arithmetic
    AI         reading a document -- only when it is new or changed

Every file in the project folder is a row of `project_documents`: where it
is, its size, time and content hash, what it is (`role`), what was read off
it (`extracted`, and the AI reading it points to), and its `state`. A page
reads the index and the readings; it never walks the folder. The folder is
walked by `sync` alone, on the first open of a project and on "Sync
documents": every file's size and time against the index (a stat, nothing
opened), the content hash of the ones that differ, and only a file whose
content is new or changed is read again -- by the document-control reader
for the logs, and by the model when it is a material submittal form. A
changed file marks what was built from it stale (`document_dependencies`):
a Design Sheet the BOQ was read from, the DRF behind Project Info, a
specification the compliance page found, a submittal's register row. What
did not change is not touched, and the previous result of a read that fails
is kept, marked failed, to be retried on its own.

There is no OneDrive API here: the folder is OneDrive's synced copy, so the
file's path is its id and its content hash its etag.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import SessionLocal
from app.models import DocumentDependency, Project, ProjectDocument, User
from app.services import document_control, spec_finder, submittal_scanner, transmittals

log = logging.getLogger(__name__)
INDEX_VERSION = "index-2026-09-24.4"   # a consultant's decision is read from the box the form
                                       # fills beside it: every document is read again
MAX_FILES = 2000
# The intake gate's rows (the DRF and the Design Sheets): indexed by it, watched here.
INTAKE_ROLES = ("drf", "design_sheet")
ROLE_SUBMITTAL = "submittal_form"
ROLE_SPEC = "spec"
ROLE_DOCUMENT = "document"
# A Word transmittal in the project's Transmittal folder: read for the samples it sent.
ROLE_TRANSMITTAL = "transmittal"
FRESH, STALE, PROCESSING, FAILED, REMOVED = "fresh", "stale", "processing", "failed", "removed"
# The job kind a sync runs as (app.services.jobs.WORKER_KINDS).
SYNC_JOB_KIND = "sync_documents"


class SyncError(Exception):
    pass


# --- the folder, cheaply -------------------------------------------------------------


def listing(root: Path) -> list[tuple[Path, str, int, float]]:
    """(path, relative path, size, mtime) of every PDF under the folder, and
    of every Word document in a Transmittal folder -- a stat each, nothing
    opened. Word documents anywhere else are not the index's."""
    found = []
    for path in sorted(root.rglob("*")):
        if len(found) >= MAX_FILES:
            break
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() != ".pdf" and not transmittals.is_transmittal(relative):
            continue
        try:
            stat = os.stat(document_control._os_path(path))
        except OSError:
            continue
        if not S_ISREG(stat.st_mode):   # a folder named like a file
            continue
        found.append((path, relative, stat.st_size, stat.st_mtime))
    return found


def listing_fingerprint(files: list[tuple[Path, str, int, float]]) -> str:
    digest = hashlib.sha256()
    for _path, relative, size, mtime in files:
        digest.update(f"{relative}|{size}|{int(mtime)}".encode("utf-8", "replace"))
    return digest.hexdigest()


def sha256_of(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(document_control._os_path(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


# --- what a file is ------------------------------------------------------------------


def classify(path: Path, relative: str) -> str:
    """A material submittal form (a MAS reference on its first page, or a
    scan filed under a submittal / approval folder), a specification, or
    any other document of the project's (a drawing, a reply, a catalogue)
    -- or, a Word document in the Transmittal folder, a transmittal."""
    if transmittals.is_transmittal(relative):
        return ROLE_TRANSMITTAL
    try:
        with document_control._open_pdf(path) as doc:
            text = doc[0].get_text() if doc.page_count else ""
    except Exception:  # noqa: BLE001 -- unreadable: still a document, read as such
        return ROLE_DOCUMENT
    from app.ai.submittal_reader import looks_like_a_form

    if looks_like_a_form(text, relative):
        return ROLE_SUBMITTAL
    if spec_finder.looks_like_a_spec(relative, path.name):
        return ROLE_SPEC
    return ROLE_DOCUMENT


# --- dependencies ----------------------------------------------------------------------


def depend(db: Session, row: ProjectDocument, dependent_type: str, dependent_id: str, reason: str) -> DocumentDependency:
    """Record that `dependent` was built from this document as it is now."""
    link = (db.query(DocumentDependency)
            .filter(DocumentDependency.source_document_id == row.id, DocumentDependency.dependent_type == dependent_type,
                    DocumentDependency.dependent_id == str(dependent_id)).one_or_none())
    if link is None:
        link = DocumentDependency(project_id=row.project_id, source_document_id=row.id, dependent_type=dependent_type,
                                  dependent_id=str(dependent_id), reason=reason, stale=False)
        db.add(link)
    link.reason = reason
    link.last_validated_sha256 = row.sha256
    link.stale = False
    link.updated_at = utc_now()
    return link


def mark_stale(db: Session, row: ProjectDocument, because: str) -> int:
    """Everything built from this document is stale now. Returns how many."""
    count = 0
    for link in db.query(DocumentDependency).filter(DocumentDependency.source_document_id == row.id):
        if link.last_validated_sha256 != row.sha256 or row.state == REMOVED:
            link.stale = True
            link.reason = f"{link.reason.split(' -- ')[0]} -- {because}"
            link.updated_at = utc_now()
            count += 1
    return count


def settle_from(db: Session, row: ProjectDocument, dependent_type: str) -> int:
    """This document has been read again, so what is derived straight from
    its own content is in step with it once more. Returns how many links
    were settled.

    The mirror of `mark_stale`, and it works from the source document
    rather than the dependent's name on purpose. The log entry for a
    submittal package the platform filed itself is first known by the
    reference the platform gave it (EP-30880-MAS-FA), and then by the
    reference the model reads off the form (EP-30880). Settling by name
    would never find the first, and the page would go on reporting a
    source document changed for a log entry that had already caught up.
    """
    # The session does not flush on its own, and `mark_stale` has usually
    # just marked these links in memory: without the flush the query below
    # reads them as they were on disk, finds none stale, and the commit
    # then writes the stale mark this was meant to clear.
    db.flush()
    count = 0
    for link in db.query(DocumentDependency).filter(DocumentDependency.source_document_id == row.id,
                                                    DocumentDependency.dependent_type == dependent_type,
                                                    DocumentDependency.stale.is_(True)):
        link.stale = False
        link.last_validated_sha256 = row.sha256
        link.updated_at = utc_now()
        count += 1
    return count


def stale_dependencies(db: Session, project: Project) -> list[dict]:
    rows = (db.query(DocumentDependency, ProjectDocument)
            .join(ProjectDocument, ProjectDocument.id == DocumentDependency.source_document_id)
            .filter(DocumentDependency.project_id == project.id, DocumentDependency.stale.is_(True))
            .order_by(DocumentDependency.dependent_type, DocumentDependency.dependent_id).all())
    return [{"dependent_type": link.dependent_type, "dependent_id": link.dependent_id, "reason": link.reason,
             "source": doc.relative_path or doc.filename, "source_role": doc.role, "source_state": doc.state}
            for link, doc in rows]


def settle(db: Session, project: Project, dependent_type: str, dependent_id: str | None = None) -> int:
    """The dependent was rebuilt from its sources as they are now: its
    links are fresh again. Returns how many links were settled."""
    query = db.query(DocumentDependency).filter(DocumentDependency.project_id == project.id,
                                                DocumentDependency.dependent_type == dependent_type,
                                                DocumentDependency.stale.is_(True))
    if dependent_id is not None:
        query = query.filter(DocumentDependency.dependent_id == str(dependent_id))
    count = 0
    for link in query:
        source = db.get(ProjectDocument, link.source_document_id)
        link.stale = False
        link.last_validated_sha256 = source.sha256 if source else link.last_validated_sha256
        link.updated_at = utc_now()
        count += 1
    return count


# --- reading one document --------------------------------------------------------------


def _record_dict(record, root: Path) -> dict:
    data = asdict(record)
    data["modified"] = record.modified.isoformat()
    return data


def record_for_the_log(extracted: dict, reading: dict, *, relative: str, modified: datetime,
                       ep_number: str | None = None) -> bool:
    """A submittal package is a scan of a form in front of a hundred
    datasheets, and the title block reader can come back from it with
    nothing at all. The log would then never hear of a form the model read
    perfectly well, and the project would report no submittal on file while
    its register held one.

    What the model read is a document-control record like any other: it
    stands for the form when nothing was read off the page, and gives way to
    the page when something was (`combine` prefers a record read from the
    document itself). Returns whether a record was added."""
    from app.ai import submittal_reader

    if not reading.get("is_submittal"):
        return False
    revision = f"R{reading['revision']}" if reading.get("revision") is not None else "R0"
    system = submittal_reader._system_code(reading, relative)
    reference = reading.get("reference") or ""
    if not reference:
        # A submittal we prepared ourselves and have not numbered yet: the
        # form is real, the covering letter simply carries no MAS
        # reference. Dropping it reported "no material submittal is filed
        # for this project" over a submittal sitting in the folder. Named
        # the way the platform names its own packages, so the register
        # keys them alike.
        if not ep_number:
            return False
        reference = f"EP-{ep_number}-MAS-{system}" if system else f"EP-{ep_number}-MAS"
    # Strictly a fallback: where the page gave a submittal record of its own
    # -- even one whose reference it read short -- that record is the
    # document's, and a second entry from the model would be the same
    # submission listed twice under two spellings.
    records = extracted.setdefault("records", [])
    if any(record["category"] == "submittals" for record in records):
        return False
    # A resubmission prints the comments it answers, so the reply the model
    # read off an R1 form is usually the consultant's word on R0. Left as
    # under review, the pass that reads the Received folders then gives this
    # revision whatever really came back on it -- which for a revision no
    # one has answered is nothing.
    from app.services import submittal_replies

    status = ("UR" if submittal_replies.answers_another_revision(reading)
              else submittal_reader._code(reading))
    records.append(_record_dict(document_control.ControlledDocument(
        system_code=system, name=reading.get("title") or Path(relative).stem, path=relative,
        modified=modified, reference=reference, revision=revision, status=status,
        reply_text=(reading.get("reply") or {}).get("evidence") if status != "UR" else None,
        source="submittal form", category="submittals"), Path(relative).parent))
    return True


def process(db: Session, project: Project, row: ProjectDocument, path: Path, root: Path, *, run=None,
            user_id: int | None, ocr: bool, read: tuple | None = None) -> None:
    """Read what this document holds and keep it on its row: the
    document-control records (reference, revision, decision, title block)
    for every document, and for a material submittal form the model's
    reading as well. `read` is the PDF's (records, notes) when a reader
    process already has them (`extract`). Raises on failure; the caller
    keeps the old result."""
    stat = os.stat(document_control._os_path(path))
    if row.role == ROLE_TRANSMITTAL:
        from app.services.word_text import read_word_text

        relative = path.relative_to(root).as_posix()
        records = transmittals.read_transmittal(read_word_text(path), relative,
                                                datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc))
        notes = ()
    else:
        records, notes = read if read is not None else document_control._read_pdf(
            str(path), stat.st_mtime_ns, stat.st_size, ocr, row.sha256)
    extracted: dict = {
        "records": [_record_dict(document_control.replace(r, path=path.relative_to(root).as_posix()), root) for r in records],
        "notes": list(notes),
    }
    if records:
        first = records[0]
        row.reference, row.revision, row.status = first.reference, first.revision, first.status
        row.system_code = row.system_code or first.system_code
    if row.role == ROLE_SUBMITTAL and run is not None:
        from app.ai import submittal_reader

        reading = submittal_reader.read_form(db, run, path, document_sha=row.sha256 or "", user_id=user_id)
        if reading is None:
            if run.exhausted:
                raise SyncError(f"the AI budget ran out ({run.exhausted.replace('_', ' ')}) before {path.name} was read")
            raise SyncError(run.notes[-1] if run.notes else "the model gave no reading of the form")
        stored = submittal_reader.stored(db, row.sha256 or "")
        row.reading_id = stored.id if stored else None
        extracted["form"] = reading
        if reading.get("is_submittal"):
            row.reference = reading.get("reference") or row.reference
            row.revision = f"R{reading['revision']}" if reading.get("revision") is not None else row.revision
            # The same vetting the log record gets: comments an R1 form
            # carries because it answers them are R0's, not this one's.
            from app.services import submittal_replies as _replies

            row.status = ("UR" if _replies.answers_another_revision(reading)
                          else submittal_reader._code(reading))
            row.system_code = submittal_reader._system_code(reading, row.relative_path or "") or row.system_code
            record_for_the_log(extracted, reading, relative=path.relative_to(root).as_posix(),
                               modified=datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc),
                               ep_number=project.ep_number)
    row.extracted = extracted
    row.last_processed_at = utc_now()
    row.index_version = INDEX_VERSION


# --- reading files in other processes ----------------------------------------------------
#
# Reading a PDF -- its text, the drawing's approval boxes, OCR -- is the slow
# part of a sync and needs nothing but the file: each is read in a process
# of its own (`SYNC_FILE_WORKERS` of them), while the worker writes the
# results to the index one at a time, in the listing's order, and asks the
# AI about material submittal forms one at a time. The processes get a path
# and return plain records; they never touch the database.


def extract(path: str, relative: str, sha256: str | None, ocr: bool) -> tuple[str, tuple | None, tuple | None]:
    """(role, records, notes) for one file. A transmittal is a Word document
    the worker reads itself: (role, None, None)."""
    role = classify(Path(path), relative)
    if role == ROLE_TRANSMITTAL:
        return role, None, None
    stat = os.stat(document_control._os_path(Path(path)))
    records, notes = document_control._read_pdf(path, stat.st_mtime_ns, stat.st_size, ocr, sha256)
    return role, records, notes


def _raise(error: BaseException):
    raise error


def _read_ahead(plan: list, ocr: bool, workers: int):
    """For each planned file, in order: a function returning its `extract`
    result or raising what reading it raised. With a pool the files are
    read ahead, `workers` at a time; without one (or if the pool breaks --
    a reader process that crashed) each is read here when its turn comes."""
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    def here(item):
        path, relative, _size, _mtime, sha, _row = item
        return lambda: extract(str(path), relative, sha, ocr)

    if workers < 2 or len(plan) < 2:
        for item in plan:
            yield here(item)
        return
    pool = ProcessPoolExecutor(max_workers=min(workers, len(plan)), max_tasks_per_child=50)
    try:
        futures = [pool.submit(extract, str(item[0]), item[1], item[4], ocr) for item in plan]
        broken = False
        for item, future in zip(plan, futures):
            if broken:
                yield here(item)
                continue
            try:
                result = future.result()
            except BrokenProcessPool:
                log.warning("A document reader process stopped; reading the rest in the worker itself")
                broken = True
                yield here(item)
                continue
            except Exception as exc:  # noqa: BLE001 -- this file's failure, raised where it is written
                yield (lambda error=exc: _raise(error))
                continue
            yield (lambda result=result: result)
    finally:
        # A stop or a failure leaves nothing queued: files not started are
        # dropped; the ones being read finish and are discarded.
        pool.shutdown(wait=False, cancel_futures=True)


# --- the sync ---------------------------------------------------------------------------


def sync(db: Session, project: Project, *, user: User | None = None, ctx=None, provider=None) -> dict:
    """Bring the index up to the folder: stat every file, hash the ones that
    differ, read only new or changed content, mark what depends on a changed
    document stale, and rebuild the submittal map when a form changed."""
    from app.ai import submittal_reader

    root = Path(project.source_folder_path or "")
    if not project.source_folder_path or not root.is_dir():
        raise SyncError("The project's archive folder is not reachable")
    now = utc_now()
    ocr = submittal_scanner.ocr_available()
    files = listing(root)
    fingerprint = listing_fingerprint(files)
    if not db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                            ProjectDocument.role.in_(INTAKE_ROLES)).count():
        # The first sync is the project's initial processing: the intake gate
        # indexes the DRF and the Design Sheets first, so they can be watched.
        from app.services import document_intake

        if ctx is not None:
            ctx.progress(0, max(len(files), 1), "Checking the DRF and the Design Sheets")
        document_intake.run(db, project)
        # Committed before any progress is reported: the job's progress is
        # written through a second session, and a SQLite file lets one
        # writer in at a time -- a flush here would lock it out.
        db.commit()
    rows = {row.path: row for row in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id)}
    intake_paths = {str(Path(p)) for _role, _system, p in _intake_documents(project)}
    counts = {"files": len(files), "new": 0, "changed": 0, "unchanged": 0, "removed": 0, "failed": 0, "read_by_ai": 0,
              "stale": 0}
    seen: set[str] = set()
    forms_changed = False
    ai_run = None
    can_read_forms = submittal_reader.available(project, provider) is None
    if can_read_forms:
        ai_run = submittal_reader._Run(db=db, project=project, provider=provider or submittal_reader.get_provider(),
                                       budget=submittal_reader._budget(db, project.id))

    # First pass: what changed. A stat each, a hash only where the stat
    # differs; nothing is opened.
    plan = []
    for index, (path, relative, size, mtime) in enumerate(files):
        key = str(path)
        seen.add(key)
        row = rows.get(key)
        if ctx is not None and index % 10 == 0:
            ctx.progress(index, len(files), f"Checking {path.name} ({index + 1} of {len(files)})")
        if key in intake_paths and row is None:
            continue   # the intake gate's document, not indexed by it yet: nothing to watch
        if row is not None and row.role in INTAKE_ROLES:
            # The DRF or a Design Sheet: watched, not read here. A change marks
            # what was read from it (the BOQ, Project Info) stale.
            _watch_intake(db, row, path, size, mtime, counts)
            continue
        # A file OneDrive had not brought down is tried again every time:
        # making the folder available offline changes neither its size nor
        # its time, so it would otherwise stay unread for ever.
        if (row is not None and row.size == size and row.mtime is not None and abs(row.mtime - mtime) < 1e-6
                and row.index_version == INDEX_VERSION and row.state in (FRESH, FAILED, STALE)
                and not _was_unavailable(row)):
            counts["unchanged"] += 1
            row.last_seen_at = now
            continue
        sha = sha256_of(path)
        # Not a row still marked processing: a sync stopped half-way through
        # reading it, and the content hash is written before the reading is.
        if (row is not None and sha and row.sha256 == sha and row.index_version == INDEX_VERSION
                and row.state != PROCESSING and not _was_unavailable(row)):
            # Touched, not changed (a re-sync, a copy): the same content.
            row.size, row.mtime, row.last_seen_at = size, mtime, now
            counts["unchanged"] += 1
            continue
        plan.append((path, relative, size, mtime, sha, row))
    db.commit()   # what the first pass saw, before the long reading pass

    # Second pass: read what is new or changed -- in reader processes, ahead
    # of the writing (`_read_ahead`) -- and write each to the index in turn.
    workers = max(0, get_settings().sync_file_workers)
    for number, ((path, relative, size, mtime, sha, row), reading) in enumerate(
            zip(plan, _read_ahead(plan, ocr, workers)), 1):
        key = str(path)
        read_error = None
        try:
            role, records, notes = reading()
        except Exception as exc:  # noqa: BLE001 -- recorded on the row below
            role, records, notes, read_error = ROLE_DOCUMENT, None, None, exc
        if row is None:
            row = ProjectDocument(project_id=project.id, role=role, path=key, relative_path=relative, filename=path.name,
                                  first_seen_at=now, acknowledged=[], findings=[])
            db.add(row)
            counts["new"] += 1
            is_new = True
        else:
            counts["changed"] += 1
            is_new = False
            row.role = role
            row.relative_path = relative
        row.sha256, row.size, row.mtime, row.last_seen_at = sha, size, mtime, now
        row.state = PROCESSING
        db.commit()   # the row says "processing" on disk, and the write lock is released for the progress report
        if ctx is not None:
            ctx.progress(number, len(plan), f"Reading {path.name} ({'new' if is_new else 'changed'}, "
                                            f"{number} of {len(plan)})")
        try:
            if read_error is not None:
                raise read_error
            calls_before = ai_run.calls if ai_run else 0
            process(db, project, row, path, root, run=ai_run if role == ROLE_SUBMITTAL else None,
                    user_id=user.id if user else None, ocr=ocr,
                    read=(records, notes) if records is not None else None)
            row.state, row.error = FRESH, None
            if ai_run and ai_run.calls > calls_before:
                counts["read_by_ai"] += 1
        except Exception as exc:  # noqa: BLE001 -- the previous result stays; this document is marked
            row.state, row.error = FAILED, f"{type(exc).__name__}: {exc}"[:1000]
            counts["failed"] += 1
        counts["stale"] += mark_stale(db, row, f"{path.name} changed on {now:%Y-%m-%d %H:%M}")
        if role == ROLE_SUBMITTAL:
            forms_changed = True
            if row.reference:
                depend(db, row, "submittal", row.reference, f"read from {relative}")
        elif role == ROLE_SPEC:
            # A specification arrived or changed: the compliance page's stored
            # locations are no longer the whole story; it searches again.
            project.spec_locations, project.specs_found_at = None, None
            depend(db, row, "compliance", str(project.id), f"specification {relative}")
        if row.reference and row.extracted and row.extracted.get("records"):
            depend(db, row, "log", row.reference, f"register row from {relative}")
        # The log is not rebuilt from a document, it is read off the index
        # every time the page is opened. A document that has just been read
        # again is therefore already in the log, and saying its entry is out
        # of date would be telling the engineer to go and fix what is right.
        if row.state == FRESH:
            counts["stale"] -= settle_from(db, row, "log")
        db.commit()

    for key, row in rows.items():
        if key in seen or row.role in INTAKE_ROLES or row.state == REMOVED:
            continue
        row.state = REMOVED
        row.last_seen_at = now
        counts["removed"] += 1
        counts["stale"] += mark_stale(db, row, f"{row.filename} is no longer in the folder")
        if row.role == ROLE_SUBMITTAL:
            forms_changed = True

    if forms_changed and can_read_forms:
        if ctx is not None:
            ctx.progress(len(files), len(files), "Drawing the submittal map from the readings")
        # Flushed first: a form marked removed just above is still "fresh"
        # to a query until it is, and the map was handed a file that was no
        # longer there (EP-30880: a filed package deleted from the folder).
        db.flush()
        form_paths = [Path(r.path) for r in db.query(ProjectDocument)
                      .filter(ProjectDocument.project_id == project.id, ProjectDocument.role == ROLE_SUBMITTAL,
                              ProjectDocument.state != REMOVED)]
        submittal_reader.check(db, project, user, ctx=None, provider=provider, files=sorted(set(form_paths)))
        settle(db, project, "submittal")
    project.documents_synced_at = now
    project.documents_listing_sha256 = fingerprint
    db.commit()
    counts["synced_at"] = now.isoformat()
    counts["forms_changed"] = forms_changed
    return counts


def _intake_documents(project: Project):
    from app.services.document_intake import project_documents

    return project_documents(project)


def _watch_intake(db: Session, row: ProjectDocument, path: Path, size: int, mtime: float, counts: dict) -> None:
    """The DRF and the Design Sheets are read by their own flows (the first
    BOQ open, the AI check of Project Info); here they are only watched:
    a change makes the BOQ / Project Info built from them stale."""
    now = utc_now()
    if row.size == size and row.mtime is not None and abs(row.mtime - mtime) < 1e-6:
        counts["unchanged"] += 1
        row.last_seen_at = now
        return
    sha = sha256_of(path)
    if row.sha256 == sha:
        row.size, row.mtime, row.last_seen_at = size, mtime, now
        counts["unchanged"] += 1
        return
    row.sha256, row.size, row.mtime, row.last_seen_at = sha, size, mtime, now
    row.state = STALE
    counts["changed"] += 1
    counts["stale"] += mark_stale(db, row, f"{path.name} changed on {now:%Y-%m-%d %H:%M}")


def register_intake_dependencies(db: Session, project: Project) -> None:
    """What the BOQ and Project Info were read from, as they are now: the
    Design Sheets and the DRF the intake gate indexed. Called when those
    are read (the first BOQ open, a re-read applied, the DRF check)."""
    for row in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                                ProjectDocument.role.in_(INTAKE_ROLES)):
        if row.role == "design_sheet":
            depend(db, row, "boq", str(project.id), f"BOQ lines read from {row.filename}")
        else:
            depend(db, row, "details", str(project.id), f"Project Info read from {row.filename}")
        row.state = FRESH
    db.commit()


def changes_since(db: Session, project: Project) -> bool:
    """Whether the folder's listing differs from the one the last sync saw
    -- a stat per file, nothing opened. For a caller that wants to know
    before syncing; an open never asks."""
    root = Path(project.source_folder_path or "")
    if not project.source_folder_path or not root.is_dir():
        return False
    if project.documents_listing_sha256 is None:
        return True
    return listing_fingerprint(listing(root)) != project.documents_listing_sha256


# --- what the pages read ---------------------------------------------------------------


def status(db: Session, project: Project) -> dict:
    rows = db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id).all()
    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row.state or FRESH] = by_state.get(row.state or FRESH, 0) + 1
    return {
        "synced_at": project.documents_synced_at,
        "documents": len(rows),
        "by_state": by_state,
        "failed": [{"path": r.relative_path or r.filename, "error": r.error} for r in rows if r.state == FAILED][:50],
        "stale": stale_dependencies(db, project),
    }


# --- File Sync: what the last sync did with each file --------------------------------------
#
# Read off the index, not kept separately: every file the sync found is a row,
# its reading notes say what could not be read, and the last successful sync
# job says when the sync ran and who asked for it.

FILE_STATUSES = ("processed", "unchanged", "partial", "unavailable", "failed")


def _was_unavailable(row: ProjectDocument) -> bool:
    return any(document_control.describe_note(note)[0] == "unavailable"
               for note in (row.extracted or {}).get("notes") or [])


def file_status(row: ProjectDocument, window: tuple | None) -> tuple[str, str | None]:
    """(status, reason) of one indexed file, as File Sync shows it.

    failed       reading it raised an error, or it is not a readable PDF
    unavailable  online-only in OneDrive: nothing could be read
    partial      read, but not all of it (pages not checked, not OCRed)
    processed    new or changed, and read by the last sync (`window`, its
                 start and end); a Design Sheet or the DRF that changed --
                 watched, not read here -- counts too, since what was read
                 from it is now marked out of date
    unchanged    the same as when it was last read
    """
    if row.state == FAILED:
        return "failed", (row.error or "The file could not be processed.")[:500]
    found = {}
    for note in (row.extracted or {}).get("notes") or []:
        kind, reason = document_control.describe_note(note)
        found.setdefault(kind, reason)
    for kind in ("failed", "unavailable", "partial"):
        if kind in found:
            return kind, found[kind]
    if row.role in INTAKE_ROLES and row.state == STALE:
        return "processed", "Changed: what was read from it is marked out of date until it is read again."
    if window and row.last_processed_at and window[0] <= row.last_processed_at <= window[1]:
        return "processed", None
    return "unchanged", None


def _last_sync_job(db: Session, project: Project):
    from app.models import BackgroundJob

    return (db.query(BackgroundJob)
            .filter(BackgroundJob.project_id == project.id, BackgroundJob.kind == SYNC_JOB_KIND,
                    BackgroundJob.status == "succeeded")
            .order_by(BackgroundJob.finished_at.desc(), BackgroundJob.id.desc()).first())


def folder_display(project: Project) -> str | None:
    """The project folder as the engineers know it: below the synced archive
    (OneDrive), or the whole path when it is somewhere else."""
    if not project.source_folder_path:
        return None
    root = get_settings().projects_root
    if root:
        try:
            return "/" + Path(project.source_folder_path).relative_to(Path(root)).as_posix()
        except ValueError:
            pass
    return project.source_folder_path


def sync_files(db: Session, project: Project) -> list[dict]:
    """Every file the index holds for the project (not the ones removed from
    the folder), with its File Sync status, by path."""
    job = _last_sync_job(db, project)
    window = (job.started_at, job.finished_at) if job and job.started_at and job.finished_at else None
    rows = (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state != REMOVED)
            .order_by(ProjectDocument.relative_path).all())
    files = []
    for row in rows:
        status, reason = file_status(row, window)
        files.append({"name": row.filename, "path": row.relative_path or row.filename, "status": status,
                      "reason": reason, "role": row.role})
    return files


def sync_summary(db: Session, project: Project) -> dict:
    """What File Sync shows at the top: where the files come from, the last
    sync -- when, who asked, how long it took -- and how many files are in
    each status."""
    job = _last_sync_job(db, project)
    files = sync_files(db, project)
    counts = {status: 0 for status in FILE_STATUSES}
    for file in files:
        counts[file["status"]] += 1
    started_by = None
    if job is not None and job.created_by_id:
        user = db.get(User, job.created_by_id)
        started_by = user.full_name if user else None
    duration = (job.finished_at - job.started_at).total_seconds() if job and job.started_at and job.finished_at else None
    return {
        "source": "OneDrive",
        "folder": project.source_folder_path,
        "folder_display": folder_display(project),
        "synced_at": project.documents_synced_at,
        "started_by": started_by,
        "automatic": job is not None and job.created_by_id is None,
        "duration_s": round(duration) if duration is not None else None,
        "removed": (job.result or {}).get("removed", 0) if job else 0,
        "total": len(files),
        "counts": counts,
    }


def log_records(db: Session, project: Project) -> tuple[list, list[str]]:
    from app.services import system_rules

    """The document-control records of every indexed document, combined
    the way the folder scan combined them -- from the database."""
    records = []
    warnings: list[str] = []
    for row in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                                ProjectDocument.state != REMOVED):
        extracted = row.extracted or {}
        for data in extracted.get("records") or []:
            data = dict(data)
            data["modified"] = datetime.fromisoformat(data["modified"])
            if data.get("source") == "transmittal":
                # Under the project's rules before they are numbered: the
                # voice evacuation of an integrated Edwards fire alarm is the
                # fire alarm's sample board, not a second one.
                data["system_code"] = system_rules.effective_code(data["system_code"], project)
            # Never stored, and a record written before it existed has none:
            # the collapse puts it back when these are combined.
            data.pop("superseded", None)
            records.append(document_control.ControlledDocument(**data))
        warnings.extend(extracted.get("notes") or [])

    # The Drawings Log is the shop drawings we produced, not the ones we
    # were given: the consultant's enquiry pack has title blocks too, and
    # without this it fills the log. And a project whose DRF marks no
    # drawing has no shop drawings at all -- an empty log, not a log of
    # somebody else's drawings.
    # The consultant's answer is filed beside the form rather than
    # printed on it, so a submittal record built from the form alone says
    # "under review" over a revision that has been answered. The logs
    # read it the same way the map and the register do.
    from app.services import submittal_replies

    filed = submittal_replies.on_file(
        db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                         ProjectDocument.state != REMOVED))
    if filed:
        from app.ai import submittal_reader

        import dataclasses

        for index, row in enumerate(records):
            if row.category != "submittals" or (row.status or "") not in ("", "UR"):
                continue
            status, words = submittal_replies.for_revision(
                submittal_replies.revision_folder(row.path) or "", filed)
            if status:
                # A ControlledDocument is frozen: the record is what was
                # read, and this is a second reading put beside it.
                records[index] = dataclasses.replace(
                    row,
                    status=submittal_reader.CODES.get(status, row.status),
                    reply_text=row.reply_text or words,
                )

    ours = system_rules.drawings_in_scope(project)
    records = [
        row for row in records
        if row.category != "drawings" or (ours and document_control.is_shop_drawing(row))
    ]
    return document_control.combine(records), list(dict.fromkeys(warnings))


# --- reading everything again when the rules change -------------------------------
#
# `INDEX_VERSION` says how documents are being read. Bumping it makes a
# sync read every document again rather than only the ones that changed
# on the drive -- which is what a change to the rules needs, because the
# files are the same and only our reading of them is different.
#
# Until now that still waited for somebody to press Sync documents on
# each project, one project at a time, remembering which ones. So a
# corrected rule reached whichever projects happened to be opened and
# quietly missed the rest. The projects catch themselves up instead: when
# the worker starts, it queues a sync for every project already synced
# under older rules, and runs them one after another.
#
# Only projects that have been synced before. A project's first sync is a
# long job over a folder nobody has asked the platform to look at yet,
# and that stays something a person starts.


def projects_on_old_rules(db: Session) -> list[int]:
    """The projects whose documents were read under older rules, oldest
    sync first -- so the one left longest is caught up first."""
    rows = (db.query(Project.id)
            .join(ProjectDocument, ProjectDocument.project_id == Project.id)
            .filter(Project.source_folder_path.isnot(None),
                    Project.documents_synced_at.isnot(None),
                    ProjectDocument.index_version != INDEX_VERSION,
                    ProjectDocument.state != REMOVED)
            .order_by(Project.documents_synced_at)
            .distinct().all())
    return [row[0] for row in rows]


def queue_projects_on_old_rules(db: Session) -> list[int]:
    """Queue a sync for every such project, oldest sync first, and return
    the jobs. The worker runs them one at a time, like any other sync -- a
    sync reads PDFs and runs OCR over a synced drive, and several at once
    would make the machine unusable for the engineer working on it. A
    project that already has a sync queued or running keeps that one, and
    one that fails -- an unreachable folder, a file OneDrive has not brought
    down -- is failed on its own without stopping the ones after it.

    Returns [] when the setting is off."""
    from app.services import jobs

    if not get_settings().reread_on_rules_change:
        return []
    queued = []
    for project_id in projects_on_old_rules(db):
        job, created = jobs.enqueue(db, kind=SYNC_JOB_KIND, project_id=project_id, user_id=None,
                                    message="Queued: documents are read again under updated rules")
        if created:
            log.info("Queued job %s to read project %s again under %s", job.id, project_id, INDEX_VERSION)
        queued.append(job.id)
    return queued
