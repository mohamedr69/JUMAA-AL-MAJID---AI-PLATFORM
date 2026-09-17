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
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import DocumentDependency, Project, ProjectDocument, User
from app.services import document_control, spec_finder, submittal_scanner

INDEX_VERSION = "index-2026-09-17.1"
MAX_FILES = 2000
# The intake gate's rows (the DRF and the Design Sheets): indexed by it, watched here.
INTAKE_ROLES = ("drf", "design_sheet")
ROLE_SUBMITTAL = "submittal_form"
ROLE_SPEC = "spec"
ROLE_DOCUMENT = "document"
FRESH, STALE, PROCESSING, FAILED, REMOVED = "fresh", "stale", "processing", "failed", "removed"


class SyncError(Exception):
    pass


# --- the folder, cheaply -------------------------------------------------------------


def listing(root: Path) -> list[tuple[Path, str, int, float]]:
    """(path, relative path, size, mtime) of every PDF under the folder -- a
    stat each, nothing opened."""
    found = []
    for n, path in enumerate(sorted(root.rglob("*.pdf"))):
        if n >= MAX_FILES:
            break
        try:
            stat = os.stat(document_control._os_path(path))
        except OSError:
            continue
        found.append((path, path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime))
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
    any other document of the project's (a drawing, a reply, a catalogue)."""
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


def process(db: Session, project: Project, row: ProjectDocument, path: Path, root: Path, *, run=None,
            user_id: int | None, ocr: bool) -> None:
    """Read what this document holds and keep it on its row: the
    document-control records (reference, revision, decision, title block)
    for every document, and for a material submittal form the model's
    reading as well. Raises on failure; the caller keeps the old result."""
    stat = os.stat(document_control._os_path(path))
    records, notes = document_control._read_pdf(str(path), stat.st_mtime_ns, stat.st_size, ocr)
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
            row.status = submittal_reader._code(reading)
            row.system_code = submittal_reader._system_code(reading, row.relative_path or "") or row.system_code
    row.extracted = extracted
    row.last_processed_at = utc_now()
    row.index_version = INDEX_VERSION


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
        if (row is not None and row.size == size and row.mtime is not None and abs(row.mtime - mtime) < 1e-6
                and row.index_version == INDEX_VERSION and row.state in (FRESH, FAILED, STALE)):
            counts["unchanged"] += 1
            row.last_seen_at = now
            continue
        sha = sha256_of(path)
        if row is not None and sha and row.sha256 == sha and row.index_version == INDEX_VERSION:
            # Touched, not changed (a re-sync, a copy): the same content.
            row.size, row.mtime, row.last_seen_at = size, mtime, now
            counts["unchanged"] += 1
            continue
        role = classify(path, relative)
        if row is None:
            row = ProjectDocument(project_id=project.id, role=role, path=key, relative_path=relative, filename=path.name,
                                  first_seen_at=now, acknowledged=[], findings=[])
            db.add(row)
            counts["new"] += 1
        else:
            counts["changed"] += 1
            row.role = role
            row.relative_path = relative
        row.sha256, row.size, row.mtime, row.last_seen_at = sha, size, mtime, now
        row.state = PROCESSING
        db.commit()   # the row says "processing" on disk, and the write lock is released for the progress report
        if ctx is not None:
            ctx.progress(index, len(files), f"Reading {path.name} ({'new' if counts['new'] else 'changed'})")
        try:
            calls_before = ai_run.calls if ai_run else 0
            process(db, project, row, path, root, run=ai_run if role == ROLE_SUBMITTAL else None, user_id=user.id if user else None, ocr=ocr)
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


def log_records(db: Session, project: Project) -> tuple[list, list[str]]:
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
            records.append(document_control.ControlledDocument(**data))
        warnings.extend(extracted.get("notes") or [])
    return document_control.combine(records), list(dict.fromkeys(warnings))
