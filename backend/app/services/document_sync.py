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
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import SessionLocal
from app.models import DocumentDependency, Project, ProjectDocument, User
from app.services import document_control, shop_drawings, spec_finder, submittal_scanner, transmittals

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
# A row's `state`:
#   fresh       the reading on the row is of the file as it is
#   pending     the file is there (found by the sync) but its current content
#               is not read yet -- new, changed, or to be read again under
#               new rules; a changed file keeps its previous reading meanwhile
#   processing  being read now (app.services.document_processing)
#   failed      the last reading failed; the previous reading, if any, stays
#   stale       the DRF or a Design Sheet changed: what was read from it is out of date
#   removed     the file is no longer in the folder; its data is kept
FRESH, STALE, PROCESSING, FAILED, REMOVED = "fresh", "stale", "processing", "failed", "removed"
PENDING = "pending"
# The job kind a sync runs as (app.services.jobs.WORKER_KINDS).
SYNC_JOB_KIND = "sync_documents"


class SyncError(Exception):
    pass


class TooManyFilesError(SyncError):
    """The folder holds more supported files than the index allows
    (`MAX_FILES`). Raised rather than truncating: a sync that quietly
    dropped the files past the limit would report a folder fully synced
    with documents missing from every page."""


# --- the folder, cheaply -------------------------------------------------------------


def listing(root: Path, progress=None) -> list[tuple[Path, str, int, float]]:
    """(path, relative path, size, mtime) of every PDF under the folder, and
    of every Word document in a Transmittal folder -- a stat each, nothing
    opened. Word documents anywhere else are not the index's. `progress`,
    when given, is called with the count found so far every few files.

    More than `MAX_FILES` supported files is a `TooManyFilesError`, never a
    shortened list."""
    found = []
    for path in root.rglob("*"):
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
        if len(found) > MAX_FILES:
            raise TooManyFilesError(
                f"Project contains more than {MAX_FILES} supported files; sync was not completed.")
        if progress is not None and len(found) % 25 == 0:
            progress(len(found))
    # The same order `sorted(root.rglob("*"))` gave: by path, so the index is
    # written in a stable order whatever order the drive listed the folders.
    found.sort(key=lambda item: item[0])
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
    -- or, a Word document in the Transmittal folder, a transmittal.

    Opens the file for its first page. `extract` has the file open already
    and calls `classify_text` with the page instead."""
    if transmittals.is_transmittal(relative):
        return ROLE_TRANSMITTAL
    try:
        with document_control._open_pdf(path) as doc:
            text = doc[0].get_text() if doc.page_count else ""
    except Exception:  # noqa: BLE001 -- unreadable: still a document, read as such
        return ROLE_DOCUMENT
    return classify_text(text, path, relative)


def classify_text(first_page_text: str | None, path: Path, relative: str) -> str:
    """`classify`, given the first page's text (None: the file could not be
    opened -- still a document, read as such). The one place the role is
    decided from content."""
    if transmittals.is_transmittal(relative):
        return ROLE_TRANSMITTAL
    if first_page_text is None:
        return ROLE_DOCUMENT
    from app.ai.submittal_reader import looks_like_a_form

    if looks_like_a_form(first_page_text, relative):
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
            user_id: int | None, ocr: bool, read: tuple | None = None, evidence: dict | None = None) -> None:
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
        # The parser the records were read with (document_control.PARSER_VERSION):
        # a reading under an earlier one is read again when the row is next
        # processed, whatever the file's hash.
        "parser_version": document_control.PARSER_VERSION,
    }
    if evidence is not None:
        extracted["evidence"] = evidence
    if records:
        first = records[0]
        row.reference, row.revision, row.status = first.reference, first.revision, first.status
        row.system_code = row.system_code or first.system_code
    if row.role == ROLE_SUBMITTAL and run is not None:
        reading = read_form_or_raise(db, run, path, row.sha256 or "", user_id=user_id)
        apply_form_reading(db, project, row, path, root, reading, extracted=extracted,
                           modified=datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc))
    row.extracted = extracted
    row.last_processed_at = utc_now()
    row.index_version = INDEX_VERSION


def read_form_or_raise(db: Session, run, path: Path, sha256: str, *, user_id: int | None) -> dict:
    """The model's reading of a form, or the reason there is none as a
    SyncError (the budget ran out, the model gave nothing)."""
    from app.ai import submittal_reader

    reading = submittal_reader.read_form(db, run, path, document_sha=sha256, user_id=user_id)
    if reading is None:
        if run.exhausted:
            raise SyncError(f"the AI budget ran out ({run.exhausted.replace('_', ' ')}) before {path.name} was read")
        raise SyncError(run.notes[-1] if run.notes else "the model gave no reading of the form")
    return reading


def apply_form_reading(db: Session, project: Project, row: ProjectDocument, path: Path, root: Path, reading: dict, *,
                       extracted: dict | None = None, modified: datetime | None = None) -> None:
    """Put the model's reading of a material submittal form on its row: the
    reading itself, the reference, revision and status it gives, and the
    log record it stands for where the page gave none. `extracted` is the
    row's reading under construction (`process`), else the row's own --
    the processing job applies a reading after the row was written
    (app.services.document_processing, the AI stage)."""
    from app.ai import submittal_reader
    from app.services import submittal_replies as _replies

    if extracted is None:
        extracted = dict(row.extracted or {"records": [], "notes": []})
        row.extracted = extracted
    if modified is None:
        modified = datetime.fromtimestamp(row.mtime or 0, timezone.utc)
    stored = submittal_reader.stored(db, row.sha256 or "")
    row.reading_id = stored.id if stored else None
    extracted["form"] = reading
    if reading.get("is_submittal"):
        row.reference = reading.get("reference") or row.reference
        row.revision = f"R{reading['revision']}" if reading.get("revision") is not None else row.revision
        # The same vetting the log record gets: comments an R1 form
        # carries because it answers them are R0's, not this one's.
        row.status = ("UR" if _replies.answers_another_revision(reading)
                      else submittal_reader._code(reading))
        row.system_code = submittal_reader._system_code(reading, row.relative_path or "") or row.system_code
        record_for_the_log(extracted, reading, relative=path.relative_to(root).as_posix(),
                           modified=modified, ep_number=project.ep_number)


# --- reading files in other processes ----------------------------------------------------
#
# Reading a PDF -- its text, the drawing's approval boxes, OCR -- is the slow
# part of a sync and needs nothing but the file: each is read in a process
# of its own (`SYNC_FILE_WORKERS` of them), while the worker writes the
# results to the index one at a time, as each reading finishes, and asks
# the AI about material submittal forms one at a time. The processes get a
# path and return plain records; they never touch the database.
#
# Completion order, not plan order. Reading in plan order and writing in
# plan order let one slow file hold every finished file behind it: on
# EP-30784 the progress sat at file 70 while files 71 to 73 were read and
# waiting. A finished file is written the moment it finishes, and progress
# counts finished files. The pool is kept fed with about twice as many
# files as it has readers rather than the whole plan at once, so a stop
# leaves little unstarted work to cancel and memory stays bounded.


def extract(path: str, relative: str, sha256: str | None, ocr: bool) -> tuple[str, tuple | None, tuple | None, dict]:
    """(role, records, notes, timing) for one file. A transmittal is a Word
    document the worker reads itself: (role, None, None, timing). `timing`
    is the document's stage clock (document_control.StageClock): where the
    reading spent its time and what it did, with `total_ms`."""
    started = time.perf_counter()
    clock = document_control.begin_stage_clock()
    target = Path(path)
    if transmittals.is_transmittal(relative):
        return ROLE_TRANSMITTAL, None, None, _timing(clock, started)
    # One pass: the PDF is opened once, its first page's text is extracted
    # once and serves both the classification and the reading, and the
    # reading reuses every page text it has (document_control.page_text).
    # Before, `classify` opened the file and read page 1, then `_read_pdf`
    # opened it again and read page 1 again: 710 opens for 355 documents on
    # EP-30784.
    stat = os.stat(document_control._os_path(target))
    modified = datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc)
    document_control.counted("read_pdf")
    page_texts: dict[int, str] = {}
    try:
        pdf = document_control._open_pdf(target)
    except Exception as exc:  # noqa: BLE001 -- unreadable or online-only: a document, with the note it always got
        pdf = None
        open_error = exc
    if pdf is None:
        with clock.stage("classification"):
            role = classify_text(None, target, relative)
        records, notes = (), document_control.open_failure_notes(target, open_error)
        evidence = None
    else:
        # Opened. From here a failure is the reader's own -- a defect on a
        # page, a crash -- and it is raised, not written up as "unreadable":
        # the caller (document_processing.run) then keeps the previous
        # reading and marks the row failed, instead of an empty reading
        # standing as fresh over a good one (M2). Only the file itself
        # going away mid-read (OSError) is still a note.
        with pdf:
            try:
                first = document_control.page_text(pdf[0], 0, page_texts) if pdf.page_count else ""
            except OSError as exc:
                with clock.stage("classification"):
                    role = classify_text(None, target, relative)
                return role, (), document_control.open_failure_notes(target, exc), {**_timing(clock, started), "evidence": None}
            with clock.stage("classification"):
                role = classify_text(first, target, relative)
            records, notes = document_control.read_open_pdf(pdf, path, modified, ocr, sha256, page_texts=page_texts)
            # What the first pages say the document is, beside the records
            # (app.services.content_evidence): text already extracted, OCR
            # already cached; nothing rendered here. Carried in the timing
            # channel to the caller (document_processing.read_task).
            try:
                from app.services import content_evidence

                with clock.stage("content_evidence"):
                    evidence = content_evidence.scan_pdf(pdf, page_texts, sha256)
            except Exception:  # noqa: BLE001 -- evidence is a finding, never a failure of the reading
                evidence = None
    timing = _timing(clock, started)
    timing["evidence"] = evidence
    return role, records, notes, timing


def _timing(clock, started: float) -> dict:
    snapshot = clock.snapshot()
    snapshot["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return snapshot


def timing_seconds(timing) -> float:
    """The seconds a reading took, from its timing (a dict since phase 0 of
    Document Processing V2; a plain number before, and in the tests' fakes)."""
    if isinstance(timing, dict):
        return float(timing.get("total_ms", 0.0)) / 1000
    return float(timing or 0.0)


def _raise(error: BaseException):
    raise error


# The pool the readers run in. A test substitutes a thread pool here so it
# can time fake readings without starting Python several times over.
_pool_factory = None


def _make_pool(workers: int):
    if _pool_factory is not None:
        return _pool_factory(max_workers=workers)
    from concurrent.futures import ProcessPoolExecutor

    # No `max_tasks_per_child`: the readers are replaced by this module
    # (`read_in_completion_order`) once they have had their share of files.
    # EP-30784's first sync stopped dead after 147 readings -- three readers
    # at fifty tasks each -- with no reader process left and the worker
    # waiting for ever on a result that never came: the pool's own
    # replacement of a spent reader lost the work in flight. Recycling the
    # whole pool between files, with nothing in flight, cannot.
    return ProcessPoolExecutor(max_workers=workers)


def _stop_pool(pool, *, kill: bool = False) -> None:
    """Shut a pool down without waiting. `kill` also terminates its reader
    processes: for readers that have stopped answering."""
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001 -- a pool already gone
        log.debug("Could not shut the reader pool down cleanly", exc_info=True)
    if kill:
        processes = getattr(pool, "_processes", None) or {}
        for process in list(processes.values()):
            try:
                process.terminate()
            except Exception:  # noqa: BLE001 -- already gone
                pass


def _stalled(name: str, seconds: float) -> SyncError:
    return SyncError(f"Reading {name} did not finish within {seconds:g} seconds; the reader processes were stopped "
                     "and the file is left to be read again")


def read_in_completion_order(plan: list, ocr: bool, workers: int, *, check=None, in_flight: int | None = None,
                             stall_seconds: float | None = None, recycle_after: int | None = None, task=None):
    """Yield `(item, reading)` for every planned file, each as soon as its
    reading has finished: `reading()` returns the `task` result or raises
    what reading it raised. `task(path, relative, sha, ocr)` is `extract`
    by default (document_processing.read_task hashes first). With a pool,
    about `in_flight` files (twice the readers, by default) are being read
    at once and the next is submitted as one finishes; `check()` is called
    before every submission so a stop is honoured before more work starts.
    Without a pool (or if the pool breaks -- a reader process that crashed)
    each file is read here in plan order when its turn comes.

    The readers are replaced after `recycle_after` files each (a long-lived
    reader grows), between files, with nothing in flight. And when nothing
    has finished for `stall_seconds` while files were in flight, the
    readers are stopped, those files come back as failed readings, and the
    rest are read by fresh readers -- a safety net against a reader that
    hangs, not a budget a slow document is expected to meet.

    `item` is the plan entry `(path, relative, size, mtime, sha, row)`, so
    the caller can write whichever document completed."""
    from concurrent.futures import FIRST_COMPLETED, wait
    from concurrent.futures.process import BrokenProcessPool

    task = task if task is not None else extract

    def here(item):
        path, relative, _size, _mtime, sha, _row = item
        return lambda: task(str(path), relative, sha, ocr)

    if workers < 2 or len(plan) < 2:
        for item in plan:
            if check is not None:
                check()
            yield item, here(item)
        return
    settings = get_settings()
    limit = in_flight if in_flight else max(2, workers * 2)
    stall = stall_seconds if stall_seconds is not None else settings.sync_read_stall_seconds
    recycle = recycle_after if recycle_after is not None else settings.sync_reader_recycle_tasks
    size = min(workers, len(plan))
    queue = list(plan)
    pending: dict = {}
    broken = False
    pool = _make_pool(size)
    given = 0    # files handed to the current pool

    def spent() -> bool:
        return bool(recycle) and given >= recycle * size

    try:
        while queue or pending:
            if not broken and queue and not pending and spent():
                # This pool has read its share: a fresh one for the rest.
                _stop_pool(pool)
                pool = _make_pool(min(size, len(queue)))
                given = 0
            while not broken and queue and len(pending) < limit and not spent():
                if check is not None:
                    check()
                item = queue.pop(0)
                pending[pool.submit(task, str(item[0]), item[1], item[4], ocr)] = item
                given += 1
            if not pending:
                if broken:
                    break
                continue
            done, _still = wait(list(pending), return_when=FIRST_COMPLETED, timeout=stall if stall else None)
            if not done:
                names = ", ".join(item[0].name for item in pending.values())
                log.error("No document reading finished in %.0f s with %d in flight (%s): stopping the reader "
                          "processes and marking those files failed", stall, len(pending), names)
                _stop_pool(pool, kill=True)
                for future in list(pending):
                    stuck = pending.pop(future)
                    yield stuck, (lambda name=stuck[0].name: _raise(_stalled(name, stall)))
                pool = _make_pool(min(size, max(len(queue), 1)))
                given = 0
                continue
            for future in done:
                item = pending.pop(future)
                try:
                    result = future.result()
                except BrokenProcessPool:
                    if not broken:
                        log.warning("A document reader process stopped; reading the rest in the worker itself")
                    broken = True
                    yield item, here(item)
                    continue
                except Exception as exc:  # noqa: BLE001 -- this file's failure, raised where it is written
                    yield item, (lambda error=exc: _raise(error))
                    continue
                yield item, (lambda result=result: result)
            if broken:
                # The readings in flight went down with the pool: read them here.
                for future in list(pending):
                    lost = pending.pop(future)
                    yield lost, here(lost)
        while queue:   # only reached when the pool broke with files unstarted
            if check is not None:
                check()
            item = queue.pop(0)
            yield item, here(item)
    finally:
        # A stop or a failure leaves nothing queued: files not started are
        # dropped; the ones being read finish and are discarded.
        _stop_pool(pool)


# --- the sync ---------------------------------------------------------------------------


# --- what a sync measures ----------------------------------------------------------------
#
# Where the time goes, so a slow sync can be explained rather than guessed
# at: the seconds in each phase, how each document came out, and the ten
# slowest files. Aggregates only -- a per-page record of 350 documents is
# not the job table's to hold -- with the detail logged by the worker.

SLOWEST_KEPT = 10
LARGEST_KEPT = 10
# How documents are processed (Document Processing V2). Recorded with every
# processing job's telemetry so runs can be compared; it does not make
# documents be read again -- that is INDEX_VERSION's job, on purpose.
PROCESSOR_VERSION = "2.0-phase0"
# Stage keys a document's timing may carry besides the reader's own stages.
DOCUMENT_STAGE_KEYS = ("storage_wait_ms", "hash_ms", "ai_ms", "db_write_ms")


def _file_result(row: ProjectDocument) -> str:
    """How one document's processing came out, as the telemetry counts it:
    failed, unavailable (online-only in OneDrive), partial (read, not all
    of it) or processed."""
    if row.state == FAILED:
        return "failed"
    kinds = {document_control.describe_note(note)[0] for note in (row.extracted or {}).get("notes") or []}
    if "unavailable" in kinds:
        return "unavailable"
    if "failed" in kinds:
        return "failed"
    if "partial" in kinds:
        return "partial"
    return "processed"


class Telemetry:
    """The timings and counts of one sync or processing run: the seconds per
    phase, how each document came out, the slowest and largest files, and
    -- from each document's stage clock (document_control.StageClock) --
    where the reading time went, stage by stage, with the distribution of
    per-document times. Aggregates only; the per-document detail is logged."""

    def __init__(self):
        self.started = time.perf_counter()
        self.seconds = {"discovery": 0.0, "hashing": 0.0, "document_processing": 0.0, "ai": 0.0, "reconcile": 0.0}
        self.counts = {"processed": 0, "failed": 0, "unavailable": 0, "partial": 0, "unchanged_after_hash": 0}
        self.slowest: list[dict] = []
        self.largest: list[dict] = []
        self.stage_ms: dict[str, float] = {}
        self.events: dict[str, int] = {}
        self.durations_ms: list[float] = []
        self.roles: dict[str, int] = {}
        self.ocr_documents = 0
        self.ai_documents = 0

    def add(self, phase: str, seconds: float) -> None:
        self.seconds[phase] = self.seconds.get(phase, 0.0) + seconds

    def document(self, *, path: str, seconds: float, role: str, result: str, size: int | None = None,
                 timing: dict | None = None, ai: bool = False) -> None:
        self.counts[result] = self.counts.get(result, 0) + 1
        entry = {"path": path, "seconds": round(seconds, 1), "role": role, "result": result}
        self.slowest.append(entry)
        self.slowest.sort(key=lambda e: e["seconds"], reverse=True)
        del self.slowest[SLOWEST_KEPT:]
        if size:
            self.largest.append({"path": path, "mb": round(size / 1e6, 1), "seconds": round(seconds, 1), "role": role})
            self.largest.sort(key=lambda e: e["mb"], reverse=True)
            del self.largest[LARGEST_KEPT:]
        self.durations_ms.append(seconds * 1000)
        self.roles[role] = self.roles.get(role, 0) + 1
        if timing:
            for name, value in (timing.get("stages_ms") or {}).items():
                self.stage_ms[name] = self.stage_ms.get(name, 0.0) + float(value)
            for name in DOCUMENT_STAGE_KEYS:
                if timing.get(name) is not None:
                    key = name[:-3]
                    self.stage_ms[key] = self.stage_ms.get(key, 0.0) + float(timing[name])
            counts = timing.get("counts") or {}
            for name, value in counts.items():
                if name != "page_count":
                    self.events[name] = self.events.get(name, 0) + int(value)
            if counts.get("ocr_pages_attempted"):
                self.ocr_documents += 1
        if ai:
            self.ai_documents += 1
        log.debug("Processed %s in %.1f s (%s, %s)", path, seconds, role, result)

    def _percentiles(self) -> dict:
        if not self.durations_ms:
            return {}
        ordered = sorted(self.durations_ms)

        def at(share: float) -> float:
            return ordered[min(len(ordered) - 1, max(0, int(round(share * len(ordered))) - 1))]

        return {"median": round(ordered[len(ordered) // 2]), "p90": round(at(0.90)), "p95": round(at(0.95)),
                "p99": round(at(0.99)), "max": round(ordered[-1])}

    def result(self, **extra) -> dict:
        out = {f"{phase}_seconds": round(value, 2) for phase, value in self.seconds.items()}
        total = time.perf_counter() - self.started
        out["total_seconds"] = round(total, 2)
        out.update({f"{name}_count": value for name, value in self.counts.items()})
        out["slowest_files"] = list(self.slowest)
        if self.durations_ms:
            out["documents"] = len(self.durations_ms)
            out["documents_per_minute"] = round(len(self.durations_ms) / (total / 60), 1) if total > 0 else None
            out["percentiles_ms"] = self._percentiles()
            out["stage_ms"] = {name: round(value) for name, value in sorted(self.stage_ms.items())}
            stage_total = sum(self.stage_ms.values())
            out["stage_share_pct"] = ({name: round(100 * value / stage_total, 1) for name, value in sorted(self.stage_ms.items())}
                                      if stage_total else {})
            out["events"] = dict(sorted(self.events.items()))
            out["largest_files"] = list(self.largest)
            out["roles"] = dict(sorted(self.roles.items()))
            out["ocr_documents"] = self.ocr_documents
            out["ai_documents"] = self.ai_documents
            out["processor_version"] = PROCESSOR_VERSION
        out.update(extra)
        return out


# --- the sync ---------------------------------------------------------------------------


def sync(db: Session, project: Project, *, user: User | None = None, ctx=None, provider=None) -> dict:
    """The file sync: bring the index up to the folder, without reading a
    document. Stat every file; a new or changed file becomes a `pending`
    row (a changed one keeps its previous reading meanwhile); a file no
    longer there is marked removed and what was built from it stale; the
    project is synced. The pending documents are then read by a
    `process_documents` job (app.services.document_processing), queued
    here, in the document worker -- so this finishes in seconds and the
    engineer works while the reading goes on.

    Nothing is opened, OCRed, hashed or asked of the model here. The one
    exception is the DRF and the Design Sheets, which are watched: a change
    to their size or time is confirmed by hash, since what was read from
    them (the BOQ, Project Info) must be marked out of date at once.

    Documents removed from the folder are reconciled now: the submittal
    map and the drawing records do not wait for a reading."""
    from app.ai import submittal_reader
    from app.services import document_processing, project_state

    telemetry = Telemetry()
    root = Path(project.source_folder_path or "")
    if not project.source_folder_path or not root.is_dir():
        raise SyncError("The project's archive folder is not reachable")
    now = utc_now()

    # Discovery: a stat per file, nothing opened. Reported as it goes -- a
    # OneDrive tree answers slowly, and a silent minute reads as a hang.
    if ctx is not None:
        ctx.progress(0, 0, "Discovering files", phase="discovery")

    def discovered(count: int) -> None:
        if ctx is not None:
            ctx.progress(count, 0, f"Discovering files — {count} found", phase="discovery")

    started = time.perf_counter()
    files = listing(root, progress=discovered)
    fingerprint = listing_fingerprint(files)
    telemetry.add("discovery", time.perf_counter() - started)
    if not db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id,
                                            ProjectDocument.role.in_(INTAKE_ROLES)).count():
        # The first sync is the project's initial processing: the intake gate
        # indexes the DRF and the Design Sheets first, so they can be watched.
        from app.services import document_intake

        if ctx is not None:
            ctx.progress(0, max(len(files), 1), "Checking the DRF and the Design Sheets", phase="intake")
        document_intake.run(db, project)
        # Committed before any progress is reported: the job's progress is
        # written through a second session, and a SQLite file lets one
        # writer in at a time -- a flush here would lock it out.
        db.commit()
    rows = {row.path: row for row in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id)}
    intake_paths = {str(Path(p)) for _role, _system, p in _intake_documents(project)}
    counts = {"files": len(files), "new": 0, "changed": 0, "unchanged": 0, "already_pending": 0, "removed": 0,
              "pending": 0, "stale": 0}
    seen: set[str] = set()
    forms_removed = False
    hinted: list[ProjectDocument] = []    # Document Classification V2: the rows recorded by this sync

    # What changed: a stat each against the index. A new or changed file
    # is recorded and marked pending; nothing is opened.
    for index, (path, relative, size, mtime) in enumerate(files):
        key = str(path)
        seen.add(key)
        row = rows.get(key)
        if ctx is not None and index % 10 == 0:
            ctx.progress(index, len(files), f"Checking changes — {index + 1} of {len(files)} files", phase="checking")
        if key in intake_paths and row is None:
            continue   # the intake gate's document, not indexed by it yet: nothing to watch
        if row is not None and row.role in INTAKE_ROLES:
            # The DRF or a Design Sheet: watched, not read here. A change marks
            # what was read from it (the BOQ, Project Info) stale.
            started = time.perf_counter()
            _watch_intake(db, row, path, size, mtime, counts)
            telemetry.add("hashing", time.perf_counter() - started)
            hinted.append(row)    # classified from its intake association, never read here
            continue
        same_stat = row is not None and row.size == size and row.mtime is not None and abs(row.mtime - mtime) < 1e-6
        # A file OneDrive had not brought down is tried again every time:
        # making the folder available offline changes neither its size nor
        # its time, so it would otherwise stay unread for ever.
        if (same_stat and row.index_version == INDEX_VERSION and row.state in (FRESH, FAILED, STALE)
                and not _was_unavailable(row)):
            counts["unchanged"] += 1
            row.last_seen_at = now
            continue
        if row is None:
            role = ROLE_TRANSMITTAL if transmittals.is_transmittal(relative) else ROLE_DOCUMENT
            row = ProjectDocument(project_id=project.id, role=role, path=key, relative_path=relative, filename=path.name,
                                  first_seen_at=now, acknowledged=[], findings=[], state=PENDING)
            db.add(row)
            counts["new"] += 1
        elif same_stat and row.state in (PENDING, PROCESSING):
            counts["already_pending"] += 1    # found by an earlier sync, still to be read
        else:
            # Changed on the drive, or to be read again (older rules, or
            # online-only last time). Its previous reading stays on the row
            # until the new one is written.
            counts["changed"] += 1
        row.relative_path = relative
        row.size, row.mtime, row.last_seen_at = size, mtime, now
        row.state = PENDING
        counts["pending"] += 1
        hinted.append(row)
    db.commit()
    if hinted and get_settings().document_classification_v2:
        # The fast classification hint, from the metadata in hand: nothing
        # opened, hashed or asked; off by default; never fails the sync.
        # After the sync's own writes are committed, in a transaction of
        # its own: a hint that cannot be written (each is a savepoint) is
        # logged and skipped, and a failure here rolls back hints only,
        # never the index. The flag is read before the module is imported
        # (see app.workers.runtime for why that order matters).
        from app.services import document_classification

        try:
            counts["classification_hints"] = document_classification.hint_rows(db, project, hinted)
            db.commit()
        except Exception:  # noqa: BLE001 -- metadata only; the index stands
            db.rollback()
            log.exception("Document classification hints could not be written for project %s", project.id)
            counts["classification_hints"] = 0

    for key, row in rows.items():
        if key in seen or row.role in INTAKE_ROLES or row.state == REMOVED:
            continue
        row.state = REMOVED
        row.last_seen_at = now
        counts["removed"] += 1
        counts["stale"] += mark_stale(db, row, f"{row.filename} is no longer in the folder")
        if row.role == ROLE_SUBMITTAL:
            forms_removed = True

    first_sync = project.documents_synced_at is None
    project.documents_synced_at = now
    project.documents_listing_sha256 = fingerprint
    # What the folder changed reaches every page: the logs, the drawings and
    # the register's forms on file are read from this index, and the
    # project's actions are brought up to it -- in this commit.
    if first_sync or any(counts[k] for k in ("new", "changed", "removed")):
        project_state.record_change(db, project.id, "documents", "synced")
        project_state.reconcile_actions(db, project)
    project_state.prune_changes(db, project.id)
    db.commit()
    counts["synced_at"] = now.isoformat()
    counts["forms_changed"] = forms_removed

    # Documents removed from the folder: what was built from them is
    # brought up to date now, not after the reading. A form gone from the
    # folder leaves the map (its remaining forms come from stored readings:
    # no model call); a drawing gone leaves the drawing records.
    reconcile_started = time.perf_counter()
    if forms_removed and submittal_reader.available(project, provider) is None:
        if ctx is not None:
            ctx.progress(len(files), len(files), "Updating submittal records", phase="reconcile")
        # Flushed first: a form marked removed just above is still "fresh"
        # to a query until it is, and the map was handed a file that was no
        # longer there (EP-30880: a filed package deleted from the folder).
        db.flush()
        forms = list(db.query(ProjectDocument)
                     .filter(ProjectDocument.project_id == project.id, ProjectDocument.role == ROLE_SUBMITTAL,
                             ProjectDocument.state.notin_((REMOVED, PENDING, PROCESSING))))
        submittal_reader.check(db, project, user, ctx=None, provider=provider,
                               files=sorted({Path(r.path) for r in forms}),
                               known_shas={r.path: r.sha256 for r in forms if r.sha256})
        settle(db, project, "submittal")
    processing_job_id = None
    if counts["pending"]:
        job, created = document_processing.enqueue(db, project, user_id=user.id if user else None)
        processing_job_id = job.id
        log.info("Sync of project %s left %d document(s) pending: processing job %s (%s)", project.id,
                 counts["pending"], job.id, "queued" if created else "already active")
    if counts["removed"] or not counts["pending"]:
        # The shop drawing records brought up to what the folder now holds
        # (app.services.shop_drawings): the Drawings page reads the records,
        # not the index. With documents pending, the processing job does this
        # again once they are read. A failure here is the drawings' to report.
        if ctx is not None:
            ctx.progress(len(files), len(files), "Updating drawing records", phase="reconcile")
        try:
            counts["drawings"] = shop_drawings.reconcile(db, project, user=user)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("The shop drawing records could not be brought up to the index for project %s", project.id)
            counts["drawings"] = None
    telemetry.add("reconcile", time.perf_counter() - reconcile_started)
    counts["processing_job_id"] = processing_job_id
    counts["telemetry"] = telemetry.result(planned=counts["pending"])
    if ctx is not None:
        ctx.progress(len(files), len(files),
                     f"File discovery complete — {len(files)} files, {counts['pending']} to process"
                     if counts["pending"] else f"File discovery complete — {len(files)} files, nothing to process",
                     phase="done")
    log.info("Sync telemetry for project %s: %s", project.id, json.dumps(counts["telemetry"], default=str))
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
    try:
        files = listing(root)
    except TooManyFilesError:
        return True    # a sync would say so, clearly; the caller is only asking whether to run one
    return listing_fingerprint(files) != project.documents_listing_sha256


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

FILE_STATUSES = ("processed", "unchanged", "pending", "partial", "unavailable", "failed")


def _was_unavailable(row: ProjectDocument) -> bool:
    return any(document_control.describe_note(note)[0] == "unavailable"
               for note in (row.extracted or {}).get("notes") or [])


def file_status(row: ProjectDocument, window: tuple | None) -> tuple[str, str | None]:
    """(status, reason) of one indexed file, as File Sync shows it.

    pending      found by the file sync; its content is still to be read
                 (or is being read now) by document processing
    failed       reading it raised an error, or it is not a readable PDF
    unavailable  online-only in OneDrive: nothing could be read
    partial      read, but not all of it (pages not checked, not OCRed)
    processed    new or changed, and read since the last file sync began
                 (`window`: its start, and its end where processing is
                 over); a Design Sheet or the DRF that changed -- watched,
                 not read here -- counts too, since what was read from it
                 is now marked out of date
    unchanged    the same as when it was last read
    """
    if row.state in (PENDING, PROCESSING):
        return "pending", ("Being read now." if row.state == PROCESSING
                           else "Found by the file sync; waiting for document processing.")
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
    if (window and row.last_processed_at and window[0] <= row.last_processed_at
            and (window[1] is None or row.last_processed_at <= window[1])):
        return "processed", None
    return "unchanged", None


def _last_sync_job(db: Session, project: Project):
    from app.models import BackgroundJob

    return (db.query(BackgroundJob)
            .filter(BackgroundJob.project_id == project.id, BackgroundJob.kind == SYNC_JOB_KIND,
                    BackgroundJob.status == "succeeded")
            .order_by(BackgroundJob.finished_at.desc(), BackgroundJob.id.desc()).first())


def _processed_window(job, project: Project) -> tuple | None:
    """The span a file's `last_processed_at` falls in to count as processed
    by the current sync cycle: from the last sync's start. Open-ended,
    since the reading goes on after the sync in the processing job. The
    project's own sync time stands in where no sync job is on record (a
    sync run outside the worker)."""
    if job is not None and job.started_at is not None:
        return (job.started_at, None)
    if project.documents_synced_at is not None:
        return (project.documents_synced_at, None)
    return None


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
    window = _processed_window(_last_sync_job(db, project), project)
    rows = (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state != REMOVED)
            .order_by(ProjectDocument.relative_path).all())
    files = []
    classified = {}
    if get_settings().document_classification_v2:
        from app.models import DocumentClassification
        from app.services import document_classification

        for entry in (db.query(DocumentClassification)
                      .filter(DocumentClassification.project_id == project.id, DocumentClassification.superseded_at.is_(None))):
            classified[entry.document_id] = entry
    for row in rows:
        status, reason = file_status(row, window)
        files.append({"name": row.filename, "path": row.relative_path or row.filename, "status": status,
                      "reason": reason, "role": row.role,
                      # Document Classification V2: metadata beside the file when the feature is on; null otherwise.
                      "classification": document_classification.as_dict(classified.get(row.id), row, project)
                      if row.id in classified else None})   # noqa: F821 -- bound above when the feature is on
    return files


def _duration(job) -> int | None:
    if job is None or job.started_at is None or job.finished_at is None:
        return None
    return round((job.finished_at - job.started_at).total_seconds())


def processing_summary(db: Session, project: Project, counts: dict) -> dict:
    """Document processing as File Sync shows it: its state, how far the
    current (or last) job got, and how the documents stand now. `job` and
    `last_job` are the BackgroundJob rows, for the router to render."""
    from app.models import BackgroundJob
    from app.services import document_processing, jobs

    active = jobs.active_job(db, project.id, document_processing.JOB_KIND)
    last = (db.query(BackgroundJob)
            .filter(BackgroundJob.project_id == project.id, BackgroundJob.kind == document_processing.JOB_KIND,
                    BackgroundJob.status.in_(jobs.FINISHED))
            .order_by(BackgroundJob.finished_at.desc(), BackgroundJob.id.desc()).first())
    pending = counts.get("pending", 0)
    current = None
    if active is not None:
        status = active.status                      # "queued" | "running"
        progress = active.progress or {}
        total = progress.get("total") or pending
        completed = progress.get("done") or 0
        current = progress.get("current")
    elif last is not None:
        result = last.result or {}
        progress = last.progress or {}
        total = result.get("planned", progress.get("total") or 0)
        completed = total if last.status == "succeeded" else (progress.get("done") or 0)
        if last.status == "cancelled":
            status = "stopped"
        elif last.status == "failed":
            status = "failed"
        else:
            status = "complete" if pending == 0 else "idle"   # a later sync found more
    else:
        status, total, completed = "idle", 0, 0
    return {
        "status": status,
        "total": total,
        "completed": completed,
        "pending": pending,
        "processed": counts.get("processed", 0),
        "failed": counts.get("failed", 0),
        "unavailable": counts.get("unavailable", 0),
        "partial": counts.get("partial", 0),
        "current": current,
        "job": active,
        "last_job": last,
        "duration_s": _duration(last) if active is None else None,
        "finished_at": last.finished_at if last is not None and active is None else None,
        "worker_running": jobs.worker_running(db, lane="documents"),
    }


def sync_summary(db: Session, project: Project) -> dict:
    """What File Sync shows at the top: where the files come from, the last
    file sync -- when, who asked, how long it took, what it found -- how
    document processing stands, and how many files are in each status."""
    job = _last_sync_job(db, project)
    files = sync_files(db, project)
    counts = {status: 0 for status in FILE_STATUSES}
    for file in files:
        counts[file["status"]] += 1
    started_by = None
    if job is not None and job.created_by_id:
        user = db.get(User, job.created_by_id)
        started_by = user.full_name if user else None
    result = (job.result or {}) if job else {}
    last_sync = None
    if job is not None:
        last_sync = {key: result.get(key, 0) for key in ("files", "new", "changed", "unchanged", "removed", "pending")}
        last_sync["duration_s"] = _duration(job)
        last_sync["finished_at"] = job.finished_at
    return {
        "source": "OneDrive",
        "folder": project.source_folder_path,
        "folder_display": folder_display(project),
        "synced_at": project.documents_synced_at,
        "started_by": started_by,
        "automatic": job is not None and job.created_by_id is None,
        "duration_s": _duration(job),
        "removed": result.get("removed", 0),
        "total": len(files),
        "discovered": len(files),
        "last_sync": last_sync,
        "processing": processing_summary(db, project, counts),
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
