"""Processing the documents the index found: the slow half of File Sync, as
a job of its own (`process_documents`, run by app.workers.document_worker).

File Sync used to be one job that did two different things: find out what
files the project folder holds (a stat per file, seconds) and read what
each new or changed document says (open the PDF, read the title block and
the approval boxes, OCR the stamps, ask the model about a submittal form:
minutes per form, hours per project). The page said "Syncing" for the
whole of it, and a project's first sync sat at "70 of 356" while the
engineer waited for the index to know what existed.

Now `document_sync.sync` only indexes: every file is a row within seconds,
new and changed rows are marked `pending`, and the project is synced. This
module then works through the pending rows in the background, in a worker
of its own, one project at a time, writing each document the moment its
reading finishes -- and the engineer uses the platform meanwhile. Nothing
about *how* a document is read changes: `document_sync.process` and
`document_control._read_pdf` are the same readers with the same rules.

    discovered  -> pending -> processing -> fresh | failed        a new file
    fresh       -> pending -> processing -> fresh | failed        a changed file
                             (the previous reading stays until the new one succeeds)

Order. Three hundred documents are not read alphabetically: what the logs
and registers are built from -- submittal forms, consultant replies, shop
drawings, transmittals -- comes first, plain documents next, and large
specifications and catalogues last (`priority`). Order only: every pending
document is read, whatever its priority.

Change verification. A pending row is hashed before it is read: a file
touched but not changed (a re-sync, a copy, a OneDrive hydration) is found
to be the content already read and is put back to `fresh` without opening
it, OCRing it or asking the model.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.models import BackgroundJob, Project, ProjectDocument, User
from app.services import document_control, document_sync, shop_drawings, spec_finder, submittal_scanner, transmittals
from app.services.document_sync import (
    FAILED, FRESH, INDEX_VERSION, INTAKE_ROLES, PENDING, PROCESSING, REMOVED, ROLE_SPEC, ROLE_SUBMITTAL,
    ROLE_TRANSMITTAL, SyncError, Telemetry,
)

log = logging.getLogger(__name__)

# The job kind (app.services.jobs.LANES, lane "documents").
JOB_KIND = "process_documents"
# The states a row is in when its current content still needs reading. A
# row left `processing` by a worker that stopped is a leftover, since one
# processing job runs per project at a time; it is read again.
NEEDS_PROCESSING = (PENDING, PROCESSING)


# --- order -------------------------------------------------------------------------------

# Words in a file or folder name that place it among the project-control
# documents (0), the drawings and submissions (1) or the reference material
# (3). Folder and file name only -- what the document *is* is settled by
# reading it, and a wrong guess here costs order, never a document.
_CONTROL_RE = re.compile(r"reply|repl(?:y|ies)|comment|transmittal|received|recieved|returned|approv"
                         r"|material\s+submittal|\bmas\b|\bmar\b|\bsar\b", re.I)
_SUBMISSION_RE = re.compile(r"\bms\b|\bsdw\b|submittal|submission|sample|shop|drawing|dwg|layout", re.I)
_REFERENCE_RE = re.compile(r"catalog|datasheet|data sheet|manual|brochure|technical|specification|\bspec\b|tender|division",
                           re.I)
LARGE_FILE_BYTES = 50 * 1024 * 1024


def priority(relative: str, role: str | None = None, size: int | None = None) -> int:
    """Where a document goes in the processing order: 0 first, 3 last.

    0  what the registers wait on: material submittal forms (by role, or
       named as one), consultant replies, transmittals, anything in a
       received / approval folder
    1  shop drawings and other submissions
    2  other project documents
    3  reference material -- specifications, catalogues, manuals -- and
       any file over LARGE_FILE_BYTES, which nothing is waiting on and
       which takes the longest to read

    Built from the path helpers the readers already use; it invents no
    rule of its own about what a file is."""
    path = (relative or "").replace("\\", "/")
    folders, _slash, name = path.rpartition("/")
    if role in (ROLE_SUBMITTAL, ROLE_TRANSMITTAL) or transmittals.in_transmittal_folder(path):
        return 0
    if submittal_scanner.APPROVAL_FOLDER_RE.search(folders) or _CONTROL_RE.search(name):
        return 0
    if role == ROLE_SPEC or spec_finder.looks_like_a_spec(path, name) or _REFERENCE_RE.search(path):
        return 3
    if size is not None and size > LARGE_FILE_BYTES:
        return 3
    if document_control.FOLDER_REV.search(path) or _SUBMISSION_RE.search(path):
        return 1
    return 2


def pending_rows(db: Session, project: Project) -> list[ProjectDocument]:
    """The project's documents whose current content is not read yet, in
    processing order: by priority, then by path."""
    rows = (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state.in_(NEEDS_PROCESSING),
                    ProjectDocument.role.notin_(INTAKE_ROLES)).all())
    rows.sort(key=lambda r: (priority(r.relative_path or r.filename, r.role, r.size),
                             (r.relative_path or r.filename).lower()))
    return rows


def pending_count(db: Session, project: Project) -> int:
    return (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state.in_(NEEDS_PROCESSING),
                    ProjectDocument.role.notin_(INTAKE_ROLES)).count())


# --- reading one file, in a reader process ---------------------------------------------------


def read_task(path: str, relative: str, previous_sha: str | None, ocr: bool,
              known_shas: frozenset | None = None) -> dict:
    """Hash the file, and read it only when its content is not
    `previous_sha` -- the content the row's reading was made from. Runs in
    a reader process (document_sync.read_in_completion_order); returns
    plain values and never touches the database.

    Returns {"missing": True} when the file is gone; {"unchanged": True}
    with the hash, size and time when the content is the one already read;
    {"duplicate_of": sha} when the content is that of another document
    already read in this project (`known_shas`: the caller copies that
    reading rather than reading the same bytes again -- the file keeps its
    own row and path); else the role, records and notes of the reading, as
    `extract` gives them."""
    started = time.perf_counter()
    target = Path(path)
    try:
        stat = os.stat(document_control._os_path(target))
    except OSError as exc:
        return {"missing": True, "error": str(exc), "seconds": time.perf_counter() - started}
    # Storage readiness, measured apart from everything after it: the first
    # read of an online-only OneDrive file is where the download happens,
    # and charging that to "hashing" or "PDF parsing" pointed at the wrong
    # problem. One small read, then the hash reads a file that is local.
    storage_started = time.perf_counter()
    try:
        with open(document_control._os_path(target), "rb") as handle:
            handle.read(64 * 1024)
    except OSError:
        pass   # the hash and the reader report what is wrong, as they did before
    storage_wait_ms = (time.perf_counter() - storage_started) * 1000
    hashing_started = time.perf_counter()
    sha = document_sync.sha256_of(target)
    hash_ms = (time.perf_counter() - hashing_started) * 1000
    timing = {"storage_wait_ms": round(storage_wait_ms, 1), "hash_ms": round(hash_ms, 1)}
    if previous_sha and sha and sha == previous_sha:
        return {"unchanged": True, "sha": sha, "size": stat.st_size, "mtime": stat.st_mtime,
                "hashing_seconds": hash_ms / 1000, "timing": timing, "seconds": time.perf_counter() - started}
    if known_shas and sha and sha in known_shas:
        timing["counts"] = {"duplicate_reused": 1}
        return {"duplicate_of": sha, "sha": sha, "size": stat.st_size, "mtime": stat.st_mtime,
                "hashing_seconds": hash_ms / 1000, "timing": timing, "seconds": time.perf_counter() - started}
    role, records, notes, read_timing = document_sync.extract(path, relative, sha, ocr)
    evidence = None
    if isinstance(read_timing, dict):
        evidence = read_timing.pop("evidence", None)
        timing.update(read_timing)
    return {"unchanged": False, "sha": sha, "size": stat.st_size, "mtime": stat.st_mtime, "role": role,
            "records": records, "notes": notes, "evidence": evidence, "hashing_seconds": hash_ms / 1000,
            "read_seconds": document_sync.timing_seconds(read_timing), "timing": timing,
            "seconds": time.perf_counter() - started}


def _previous_sha(row: ProjectDocument) -> str | None:
    """The content hash a row's reading stands for, when that reading can be
    kept if the file's content proves the same; None when the file must be
    read whatever its hash: no reading yet, the last one failed, it was
    online-only, the rules have changed since, or a stopped worker left the
    row mid-read (its hash written, its reading not)."""
    if row.state == PROCESSING or row.error or row.extracted is None or row.index_version != INDEX_VERSION:
        return None
    if document_sync._was_unavailable(row):
        return None
    if not parser_current(row):
        return None   # read under an earlier parser: the same bytes are read again under this one
    return row.sha256


def parser_current(row: ProjectDocument) -> bool:
    """Whether the row's reading was made by the parser as it is now
    (document_control.PARSER_VERSION). A reading that was not is not kept
    for its hash: a known defect of the earlier parser would otherwise
    stand for ever behind an unchanged file."""
    return (row.extracted or {}).get("parser_version") == document_control.PARSER_VERSION


# --- the job -----------------------------------------------------------------------------


def run(db: Session, project: Project, *, user: User | None = None, ctx=None, provider=None) -> dict:
    """Read every pending document of the project, each written as its
    reading finishes; then bring what is built from the documents -- the
    submittal map, the shop drawing records, the project's actions -- up
    to the readings, once. Returns the counts the job keeps as its result."""
    from app.ai import submittal_reader
    from app.services import project_state

    telemetry = Telemetry()
    root = Path(project.source_folder_path or "")
    if not project.source_folder_path or not root.is_dir():
        raise SyncError("The project's archive folder is not reachable")
    now = utc_now()
    ocr = submittal_scanner.ocr_available()
    rows = pending_rows(db, project)
    total = len(rows)
    counts = {"planned": total, "processed": 0, "failed": 0, "unavailable": 0, "partial": 0,
              "unchanged_after_hash": 0, "missing": 0, "read_by_ai": 0, "stale": 0}
    if ctx is not None:
        ctx.progress(0, total, f"Checking changes — {total} file{'s' if total != 1 else ''}", phase="checking")
    ai_run = None
    can_read_forms = submittal_reader.available(project, provider) is None
    if can_read_forms:
        ai_run = submittal_reader._Run(db=db, project=project, provider=provider or submittal_reader.get_provider(),
                                       budget=submittal_reader._budget(db, project.id))
    plan = [(Path(row.path), row.relative_path or row.filename, row.size, row.mtime, _previous_sha(row), row)
            for row in rows]
    workers = max(0, get_settings().sync_file_workers)
    # Content already read in this project, by hash: a second copy of the
    # same bytes (ABC-R0.pdf and Archive/ABC-R0.pdf) takes that reading
    # rather than being read again. Its row and path stay its own.
    known = {r.sha256: r for r in db.query(ProjectDocument)
             .filter(ProjectDocument.project_id == project.id, ProjectDocument.state == FRESH,
                     ProjectDocument.index_version == INDEX_VERSION, ProjectDocument.sha256.isnot(None),
                     ProjectDocument.extracted.isnot(None), ProjectDocument.role.notin_(INTAKE_ROLES))
             if r.error is None and not document_sync._was_unavailable(r) and parser_current(r)}
    task = functools.partial(read_task, known_shas=frozenset(known)) if known else read_task
    forms_changed = False
    anything_read = False
    done = 0
    # Forms the model has not read yet: their deterministic reading is
    # written now, and the model is asked once every other document is
    # done (the AI stage below), so a 150-second call on one form never
    # holds up the shop drawings and replies behind it.
    deferred_forms: list[tuple[ProjectDocument, Path, str]] = []
    processing_started = time.perf_counter()
    for (path, relative, _size, _mtime, _previous, row), reading in document_sync.read_in_completion_order(
            plan, ocr, workers, check=ctx.check if ctx is not None else None, task=task):
        if ctx is not None:
            ctx.check()   # a stop asked for while this file was being read
        write_started = time.perf_counter()
        try:
            result = reading()
        except Exception as exc:  # noqa: BLE001 -- this file's failure, recorded on its row
            result = {"error": exc}
        # Another process (a sync) may have touched the row meanwhile: a
        # file removed from the folder is not written back as fresh.
        db.refresh(row)
        if row.state == REMOVED:
            done += 1
            continue
        if result.get("missing"):
            # Gone between the sync and now: as the next sync would record it.
            row.state, row.last_seen_at = REMOVED, now
            counts["missing"] += 1
            counts["stale"] += document_sync.mark_stale(db, row, f"{row.filename} is no longer in the folder")
            if row.role == ROLE_SUBMITTAL:
                forms_changed = True
            db.commit()
            done += 1
            continue
        if "error" in result:
            # The reader itself failed on this file (or was stopped as
            # stalled): the previous reading stays; this attempt is recorded.
            row.state, row.error = FAILED, f"{type(result['error']).__name__}: {result['error']}"[:1000]
            row.last_seen_at = now
            counts["failed"] += 1
            db.commit()
            done += 1
            telemetry.document(path=relative, seconds=time.perf_counter() - write_started, role=row.role,
                               result="failed", size=row.size)
            _log_document(relative, row.role, "failed", row.size, {"error": str(result["error"])[:200]})
            _report(ctx, done, total, path.name)
            continue
        timing = dict(result.get("timing") or {})
        if result["unchanged"]:
            # Touched, not changed: the content already read. Nothing opened.
            row.sha256, row.size, row.mtime, row.last_seen_at = result["sha"], result["size"], result["mtime"], now
            row.state, row.error = FRESH, None
            counts["unchanged_after_hash"] += 1
            telemetry.add("hashing", result.get("hashing_seconds", 0.0))
            commit_started = time.perf_counter()
            db.commit()
            timing["db_write_ms"] = round((time.perf_counter() - commit_started) * 1000, 1)
            done += 1
            telemetry.document(path=relative, seconds=time.perf_counter() - write_started + result.get("seconds", 0.0),
                               role=row.role, result="unchanged_after_hash", size=result["size"], timing=timing)
            _log_document(relative, row.role, "unchanged_after_hash", result["size"], timing)
            _report(ctx, done, total, path.name)
            continue

        telemetry.add("hashing", result.get("hashing_seconds", 0.0))
        source = known.get(result.get("duplicate_of")) if result.get("duplicate_of") else None
        if source is not None and source.id == row.id:
            source = None    # its own earlier reading: read it as new
        role = source.role if source is not None else result["role"]
        row.role, row.relative_path = role, relative
        row.sha256, row.size, row.mtime, row.last_seen_at = result["sha"], result["size"], result["mtime"], now
        row.state = PROCESSING
        commit_started = time.perf_counter()
        db.commit()   # the row says "processing" on disk, and the write lock is released for the progress report
        db_write_ms = (time.perf_counter() - commit_started) * 1000
        called_ai = False
        try:
            if ctx is not None:
                ctx.progress(done, total, f"Processing documents — {done} of {total} · reading {path.name}",
                             phase="processing", current=path.name)
            ai_started = time.perf_counter()
            deferred = False
            try:
                calls_before = ai_run.calls if ai_run else 0
                if source is not None:
                    _copy_reading(row, source, relative)
                    counts["duplicates_reused"] = counts.get("duplicates_reused", 0) + 1
                else:
                    # A form whose reading the model has not stored yet is
                    # read deterministically now and by the model later; one
                    # already read (the same content, this or another
                    # project) takes that reading here, with no call.
                    deferred = (role == ROLE_SUBMITTAL and ai_run is not None
                                and submittal_reader.stored(db, row.sha256 or "") is None)
                    document_sync.process(db, project, row, path, root,
                                          run=ai_run if (role == ROLE_SUBMITTAL and not deferred) else None,
                                          user_id=user.id if user else None, ocr=ocr,
                                          read=(result["records"], result["notes"]) if result["records"] is not None else None,
                                          evidence=result.get("evidence"))
                if ai_run and ai_run.calls > calls_before:
                    counts["read_by_ai"] += 1
                    called_ai = True
                    ai_seconds = time.perf_counter() - ai_started
                    telemetry.add("ai", ai_seconds)
                    timing["ai_ms"] = round(ai_seconds * 1000, 1)
                if _moved_under_us(path, result):
                    # The file changed while it was being read: what was
                    # read is kept, and the row waits for the next run.
                    row.state, row.error = PENDING, None
                    deferred = False
                elif deferred:
                    row.state, row.error = PROCESSING, None    # fresh once the model has read it
                    deferred_forms.append((row, path, relative))
                else:
                    row.state, row.error = FRESH, None
            except Exception as exc:  # noqa: BLE001 -- the previous result stays; this document is marked
                row.state, row.error = FAILED, f"{type(exc).__name__}: {exc}"[:1000]
                counts["failed"] += 1
        except BaseException:
            # Stopped (cancelled, or the worker shutting down) between the
            # row saying "processing" and its reading being written: it is
            # pending again, not left mid-read.
            db.rollback()
            db.refresh(row)
            if row.state == PROCESSING:
                row.state = PENDING
                db.commit()
            raise
        anything_read = True
        forms_changed = _after_reading(db, project, row, role, relative, now, counts) or forms_changed
        commit_started = time.perf_counter()
        db.commit()
        db_write_ms += (time.perf_counter() - commit_started) * 1000
        _classify(db, project, row, counts)
        timing["db_write_ms"] = round(db_write_ms, 1)
        done += 1
        if deferred:
            outcome = "processed"    # counted when the model has read it (the AI stage)
        else:
            outcome = document_sync._file_result(row) if row.state != PENDING else "processed"
            counts[outcome] = counts.get(outcome, 0) + 1
        telemetry.document(path=relative, seconds=result.get("seconds", 0.0) + (time.perf_counter() - write_started),
                           role=role, result=outcome, size=result["size"], timing=timing, ai=called_ai)
        _log_document(relative, role, outcome, result["size"], timing, ai=called_ai)
        _report(ctx, done, total, path.name)
    telemetry.add("document_processing", time.perf_counter() - processing_started)

    # The AI stage: the forms the model has not read, one at a time, after
    # every other document is written. A stop here leaves them pending;
    # their deterministic reading is redone next time from the caches.
    if deferred_forms:
        ai_stage_started = time.perf_counter()
        try:
            for number, (row, path, relative) in enumerate(deferred_forms, 1):
                if ctx is not None:
                    ctx.progress(total, total, f"Reading forms with the AI — {number} of {len(deferred_forms)} · {path.name}",
                                 phase="ai", current=path.name)
                    ctx.check()
                db.refresh(row)
                if row.state != PROCESSING:
                    continue     # a sync removed it or marked it pending again meanwhile
                form_started = time.perf_counter()
                try:
                    reading = document_sync.read_form_or_raise(db, ai_run, path, row.sha256 or "",
                                                               user_id=user.id if user else None)
                    document_sync.apply_form_reading(db, project, row, path, root, reading)
                    row.state, row.error = FRESH, None
                    counts["read_by_ai"] += 1
                except Exception as exc:  # noqa: BLE001 -- the deterministic reading stays; the form is marked
                    row.state, row.error = FAILED, f"{type(exc).__name__}: {exc}"[:1000]
                    counts["failed"] += 1
                row.last_processed_at = utc_now()
                form_seconds = time.perf_counter() - form_started
                telemetry.add("ai", form_seconds)
                if row.state == FRESH:
                    if row.reference:
                        document_sync.depend(db, row, "submittal", row.reference, f"read from {relative}")
                        document_sync.depend(db, row, "log", row.reference, f"register row from {relative}")
                    counts["stale"] -= document_sync.settle_from(db, row, "log")
                    outcome = document_sync._file_result(row)
                    counts[outcome] = counts.get(outcome, 0) + 1
                db.commit()
                _classify(db, project, row, counts)
                _log_document(relative, row.role, "processed" if row.state == FRESH else "failed", row.size,
                              {"ai_ms": round(form_seconds * 1000, 1)}, ai=True)
        except BaseException:
            # Stopped (cancelled, or the worker shutting down): the forms
            # the model has not read go back to pending, not left mid-read.
            db.rollback()
            for row, _path, _relative in deferred_forms:
                db.refresh(row)
                if row.state == PROCESSING:
                    row.state = PENDING
            db.commit()
            raise
        telemetry.add("ai_stage", time.perf_counter() - ai_stage_started)

    # What is built from the documents, brought up to the readings once.
    reconcile_started = time.perf_counter()
    if forms_changed and can_read_forms:
        if ctx is not None:
            ctx.progress(total, total, "Updating submittal records", phase="reconcile")
        db.flush()
        map_started = time.perf_counter()
        forms = list(db.query(ProjectDocument)
                     .filter(ProjectDocument.project_id == project.id, ProjectDocument.role == ROLE_SUBMITTAL,
                             ProjectDocument.state != REMOVED))
        submittal_reader.check(db, project, user, ctx=None, provider=provider,
                               files=sorted({Path(r.path) for r in forms}),
                               known_shas={r.path: r.sha256 for r in forms if r.sha256})
        document_sync.settle(db, project, "submittal")
        telemetry.add("reconcile_map", time.perf_counter() - map_started)
    if anything_read or counts["missing"] or forms_changed:
        actions_started = time.perf_counter()
        project_state.record_change(db, project.id, "documents", "processed")
        project_state.reconcile_actions(db, project)
        telemetry.add("reconcile_actions", time.perf_counter() - actions_started)
    project_state.prune_changes(db, project.id)
    db.commit()
    if ctx is not None:
        ctx.progress(total, total, "Updating drawing records", phase="reconcile")
    drawings_started = time.perf_counter()
    try:
        counts["drawings"] = shop_drawings.reconcile(db, project, user=user)
    except Exception:  # noqa: BLE001 -- the drawings' to report, not the processing's
        db.rollback()
        log.exception("The shop drawing records could not be brought up to the index for project %s", project.id)
        counts["drawings"] = None
    telemetry.add("reconcile_drawings", time.perf_counter() - drawings_started)
    telemetry.add("reconcile", time.perf_counter() - reconcile_started)
    counts["forms_changed"] = forms_changed
    counts["remaining"] = pending_count(db, project)   # a sync meanwhile may have found more
    counts["finished_at"] = utc_now().isoformat()
    counts["telemetry"] = telemetry.result(workers=workers, planned=total)
    log.info("Processing telemetry for project %s: %s", project.id, counts["telemetry"])
    if ctx is not None:
        ctx.progress(total, total, f"Processing complete — {counts['processed']} processed, {counts['failed']} failed",
                     phase="done")
    return counts


def _classify(db: Session, project: Project, row: ProjectDocument, counts: dict) -> None:
    """Document Classification V2: the assessment of a row just written,
    from what it holds -- after the row's own commit, in a transaction of
    its own, so nothing it does can touch the reading. Off by default;
    a failure is logged and the row stays as written."""
    # The flag first, from the settings, so a process with the feature off
    # never imports the module (and a worker started before the module's
    # models existed never trips over them while the feature is off).
    if not get_settings().document_classification_v2:
        return
    from app.services import document_classification

    started = time.perf_counter()
    try:
        if document_classification.assess_row(db, project, row) is not None:
            counts["classified"] = counts.get("classified", 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001 -- metadata only; the reading stands
        db.rollback()
        log.exception("Document classification could not be written for document %s", row.id)
    counts["classification_ms"] = round(counts.get("classification_ms", 0.0) + (time.perf_counter() - started) * 1000, 1)


def _after_reading(db: Session, project: Project, row: ProjectDocument, role: str, relative: str, now, counts: dict) -> bool:
    """What follows a document's reading: what was built from it is marked
    stale, what is built from it now is recorded, and its own log entry is
    settled. Returns whether it was a form (the map is redrawn)."""
    counts["stale"] += document_sync.mark_stale(db, row, f"{Path(relative).name} changed on {now:%Y-%m-%d %H:%M}")
    forms_changed = False
    if role == ROLE_SUBMITTAL:
        forms_changed = True
        if row.reference:
            document_sync.depend(db, row, "submittal", row.reference, f"read from {relative}")
    elif role == ROLE_SPEC:
        # A specification arrived or changed: the compliance page's stored
        # locations are no longer the whole story; it searches again.
        project.spec_locations, project.specs_found_at = None, None
        document_sync.depend(db, row, "compliance", str(project.id), f"specification {relative}")
    if row.reference and row.extracted and row.extracted.get("records"):
        document_sync.depend(db, row, "log", row.reference, f"register row from {relative}")
    # A document just read again is already in the log as it now is.
    if row.state == FRESH:
        counts["stale"] -= document_sync.settle_from(db, row, "log")
    return forms_changed


def _copy_reading(row: ProjectDocument, source: ProjectDocument, relative: str) -> None:
    """Give `row` the reading of `source`, another file with the same
    content: the records with this file's own path, the form reading (by
    content, so it is the same reading), the reference, revision and
    status. Two rows, one reading -- never one row for two files."""
    import copy

    extracted = copy.deepcopy(source.extracted or {})
    for record in extracted.get("records") or []:
        record["path"] = relative
    row.extracted = extracted
    row.reference, row.revision, row.status = source.reference, source.revision, source.status
    row.system_code = row.system_code or source.system_code
    row.reading_id = source.reading_id
    row.last_processed_at = utc_now()
    row.index_version = INDEX_VERSION


def _log_document(relative: str, role: str, outcome: str, size: int | None, timing: dict, *, ai: bool = False) -> None:
    """One structured line per document: where its time went and what was
    done to it. The detail lives in the worker's log; the job keeps the
    aggregates (document_sync.Telemetry)."""
    import json

    counts = timing.get("counts") or {}
    log.info("document %s", json.dumps({
        "path": relative, "role": role, "result": outcome, "size": size, "pages": counts.get("page_count"),
        "ai": ai, "stages_ms": timing.get("stages_ms") or {},
        **{key: timing.get(key) for key in document_sync.DOCUMENT_STAGE_KEYS if timing.get(key) is not None},
        "total_ms": timing.get("total_ms"),
        "counts": {name: value for name, value in counts.items() if name != "page_count"},
    }, default=str))


def _moved_under_us(path: Path, result: dict) -> bool:
    """Whether the file changed while its content was being read."""
    try:
        stat = os.stat(document_control._os_path(path))
    except OSError:
        return False
    return stat.st_size != result["size"] or abs(stat.st_mtime - result["mtime"]) > 1e-6


def _report(ctx, done: int, total: int, name: str) -> None:
    if ctx is not None:
        ctx.progress(done, total, f"Processing documents — {done} of {total}", phase="processing", current=name)


# --- the job around it -------------------------------------------------------------------


def run_job(session: Session, job: BackgroundJob, ctx) -> dict:
    """What the document worker does with a `process_documents` job."""
    from app.services import activity

    project = session.get(Project, job.project_id) if job.project_id else None
    if project is None:
        raise SyncError("The project no longer exists")
    actor = session.get(User, job.created_by_id) if job.created_by_id else None
    log.info("Starting document processing job %s for project %s (%s)", job.id, project.id,
             project.ep_number or "no EP number")
    result = run(session, project, user=actor, ctx=ctx)
    summary = (f"Processed the project documents: {result['processed']} processed, {result['failed']} failed, "
               f"{result['unavailable']} unavailable, {result['unchanged_after_hash']} unchanged; "
               f"{result['read_by_ai']} read by the AI")
    if actor is not None:
        activity.record(session, actor, "documents.processed", summary, project=project, entity_type="project",
                        entity_id=project.id, detail={k: v for k, v in result.items() if isinstance(v, (int, bool))})
    log.info("Document processing job %s: %s", job.id, summary)
    return result


def enqueue(db: Session, project: Project, *, user_id: int | None) -> tuple[BackgroundJob, bool]:
    """Queue the project's processing job, or return the one already
    queued or running: at most one per project (app.services.jobs.enqueue,
    and the database's own index)."""
    from app.services import jobs

    return jobs.enqueue(db, kind=JOB_KIND, project_id=project.id, user_id=user_id,
                        message="Queued: waiting for the document processing worker")


def retry_failed(db: Session, project: Project) -> int:
    """Put the documents whose last reading failed back in the queue.
    Returns how many."""
    rows = (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state == FAILED,
                    ProjectDocument.role.notin_(INTAKE_ROLES)).all())
    for row in rows:
        row.state = PENDING
    db.commit()
    return len(rows)
