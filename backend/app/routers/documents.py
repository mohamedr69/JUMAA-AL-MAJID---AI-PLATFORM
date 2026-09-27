"""The project's document index (app.services.document_sync) and the
reading of its documents (app.services.document_processing).

  GET  /projects/{id}/documents/status           when the folder was last synced, what is stale, what failed,
                                                 what is still to be read
  GET  /projects/{id}/documents/sync-summary     File Sync: the last file sync, document processing, and how
                                                 many files are in each status
  GET  /projects/{id}/documents/sync-files       File Sync: the files, with their status and why (?status=)
  POST /projects/{id}/jobs/sync-documents        queue a file sync: the sync worker stats the folder and
                                                 records what is new, changed and removed -- seconds; the
                                                 documents are then read by a processing job it queues
  POST /projects/{id}/jobs/process-documents     queue the reading of the documents still pending (after a
                                                 stop, or to retry the failed ones with retry_failed)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import BackgroundJob, User
from app.routers import jobs as jobs_router
from app.routers.jobs import JobOut, _out
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import document_processing, document_sync, jobs

router = APIRouter(prefix="/projects", tags=["documents"])

JOB_KIND = document_sync.SYNC_JOB_KIND
PROCESS_KIND = document_processing.JOB_KIND


class StaleOut(BaseModel):
    dependent_type: str
    dependent_id: str
    reason: str
    source: str
    source_role: str
    source_state: str


class FailedOut(BaseModel):
    path: str
    error: str | None


class DocumentStatusOut(BaseModel):
    synced_at: datetime | None
    documents: int
    by_state: dict[str, int]
    failed: list[FailedOut]
    stale: list[StaleOut]
    syncing: bool
    job: JobOut | None
    folder: str | None
    folder_reachable: bool
    # Whether the background worker that runs syncs is alive. A queued sync
    # with no worker waits until one starts (start.bat starts it).
    worker_running: bool = True
    # Documents found by the sync whose content is still to be read, the
    # processing job reading them (queued or running), and whether the
    # document worker that runs it is alive.
    pending: int = 0
    processing: JobOut | None = None
    documents_worker_running: bool = True


@router.get("/{project_id}/documents/status", response_model=DocumentStatusOut)
def document_status(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentStatusOut:
    """What the index holds -- from the database, the folder untouched."""
    from pathlib import Path

    project = _get_project_or_404(db, project_id)
    running = jobs.active_job(db, project.id, JOB_KIND)
    processing = jobs.active_job(db, project.id, PROCESS_KIND)
    folder = Path(project.source_folder_path) if project.source_folder_path else None
    return DocumentStatusOut(**document_sync.status(db, project), syncing=running is not None,
                             job=_out(running) if running else None, folder=project.source_folder_path,
                             folder_reachable=bool(folder and folder.is_dir()),
                             worker_running=jobs.worker_running(db, lane="sync"),
                             pending=document_processing.pending_count(db, project),
                             processing=_out(processing) if processing else None,
                             documents_worker_running=jobs.worker_running(db, lane="documents"))


class LastSyncOut(BaseModel):
    files: int
    new: int
    changed: int
    unchanged: int
    removed: int
    pending: int
    duration_s: int | None
    finished_at: datetime | None


class ProcessingOut(BaseModel):
    # "idle" | "queued" | "running" | "complete" | "stopped" | "failed"
    status: str
    total: int
    completed: int
    pending: int
    processed: int
    failed: int
    unavailable: int
    partial: int
    current: str | None
    job: JobOut | None
    last_job: JobOut | None
    duration_s: int | None
    finished_at: datetime | None
    worker_running: bool


class SyncSummaryOut(BaseModel):
    source: str
    folder: str | None
    folder_display: str | None
    synced_at: datetime | None
    started_by: str | None
    automatic: bool
    duration_s: int | None
    removed: int
    total: int
    discovered: int
    last_sync: LastSyncOut | None
    processing: ProcessingOut
    counts: dict[str, int]
    job: JobOut | None
    worker_running: bool


class ClassificationOut(BaseModel):
    """Document Classification V2 (app.services.document_classification):
    what the file appears to contain, at what stage of evidence, and why.
    Metadata only; no relative or absolute path beyond the file's own."""

    id: int
    document_id: int
    primary_type: str
    stage: str
    evidence_strength: str
    component_types: list[str]
    evidence: list[str]
    evidence_sources: list[str]
    reason: str
    system_code: str | None
    discipline: str | None
    rules_version: str
    content_sha256: str | None
    context_fingerprint: str
    source: str
    engineer_confirmed: bool
    assessed_at: datetime
    stale: bool
    # "current" | "rules_changed" | "context_changed" | "source_changed": only "current" is the answer for the file as it is.
    freshness: str
    current: bool
    # "intake_association" | "metadata" | "content" | "none": what the answer rests on; only content supports a type.
    basis: str
    component_support: dict[str, str]
    source_state: str | None
    needs_review: bool
    review_reasons: list[str]
    # Source-quality findings (a printed project code that differs, a record whose reference is a date, ...).
    flags: list[str] = []
    # What the first pages were found to hold: kind, page, excerpt, method (text / ocr).
    evidence_pages: list[dict] = []


class SyncFileOut(BaseModel):
    name: str
    path: str
    status: str
    reason: str | None
    role: str
    # Present only with DOCUMENT_CLASSIFICATION_V2 on and an assessment stored; null otherwise.
    classification: ClassificationOut | None = None


class ExtractedSummaryOut(BaseModel):
    """What the readers stored on the row, beside the classification: the
    first record's reference, revision and status, the categories read, the
    parser that read them, the notes, the processing error."""

    reference: str | None
    revision: str | None
    status: str | None
    categories: list[str]
    records: int
    parser_version: str | None
    parser_current: bool
    form_is_submittal: bool | None
    notes: list[str]
    error: str | None
    evidence_pages_read: int | None
    page_count: int | None


class DocumentClassificationRowOut(BaseModel):
    document_id: int
    name: str
    path: str
    role: str
    state: str
    classification: ClassificationOut | None
    extracted: ExtractedSummaryOut | None = None


class ClassificationMetricsOut(BaseModel):
    documents: int
    eligible: int
    assessed: int
    unassessed: int
    by_type: dict[str, int]
    by_stage: dict[str, int]
    by_basis: dict[str, int]
    by_freshness: dict[str, int]
    unknown: int
    ambiguous: int
    agree_with_role: int
    conflict_with_role: int
    mixed_component_files: int
    path_only: int
    metadata_only: int
    content_supported: int
    stale: int
    current: int
    needs_review: int
    flagged: int = 0
    duplicate_current: int
    rules_version: str


@router.get("/{project_id}/documents/sync-summary", response_model=SyncSummaryOut)
def sync_summary(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncSummaryOut:
    """File Sync's header and counts, from the index: the folder is not touched."""
    project = _get_project_or_404(db, project_id)
    running = jobs.active_job(db, project.id, JOB_KIND)
    summary = document_sync.sync_summary(db, project)
    processing = summary.pop("processing")
    processing["job"] = _out(processing["job"]) if processing["job"] else None
    processing["last_job"] = _out(processing["last_job"]) if processing["last_job"] else None
    return SyncSummaryOut(**summary, processing=ProcessingOut(**processing), job=_out(running) if running else None,
                          worker_running=jobs.worker_running(db, lane="sync"))


@router.get("/{project_id}/documents/sync-files", response_model=list[SyncFileOut])
def sync_files(
    project_id: int,
    file_status: str | None = Query(default=None, alias="status"),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncFileOut]:
    """The project's files with their File Sync status and the reason for it;
    one status's only when `status` is given (the page loads a category when
    it is opened, not before)."""
    if file_status is not None and file_status not in document_sync.FILE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"status must be one of {', '.join(document_sync.FILE_STATUSES)}")
    project = _get_project_or_404(db, project_id)
    files = document_sync.sync_files(db, project)
    return [SyncFileOut(**f) for f in files if file_status is None or f["status"] == file_status]


@router.get("/{project_id}/documents/classification", response_model=list[DocumentClassificationRowOut])
def document_classification(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DocumentClassificationRowOut]:
    """Every indexed file with its current classification assessment, when
    one is stored (Document Classification V2). Informational only."""
    from app.models import DocumentClassification, ProjectDocument
    from app.services import document_classification as classification

    project = _get_project_or_404(db, project_id)
    rows = (db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == project.id, ProjectDocument.state != document_sync.REMOVED)
            .order_by(ProjectDocument.relative_path).all())
    entries = {e.document_id: e for e in db.query(DocumentClassification)
               .filter(DocumentClassification.project_id == project.id, DocumentClassification.superseded_at.is_(None))}
    return [DocumentClassificationRowOut(document_id=row.id, name=row.filename, path=row.relative_path or row.filename,
                                         role=row.role, state=row.state,
                                         classification=classification.as_dict(entries.get(row.id), row, project),
                                         extracted=_extracted_summary(row))
            for row in rows]


def _extracted_summary(row) -> ExtractedSummaryOut:
    from app.services import document_control

    extracted = row.extracted or {}
    records = [r for r in (extracted.get("records") or []) if isinstance(r, dict)]
    form = extracted.get("form") if isinstance(extracted.get("form"), dict) else None
    evidence = extracted.get("evidence") if isinstance(extracted.get("evidence"), dict) else None
    return ExtractedSummaryOut(
        reference=row.reference, revision=row.revision, status=row.status,
        categories=sorted({str(r.get("category")) for r in records if r.get("category")}), records=len(records),
        parser_version=extracted.get("parser_version"),
        parser_current=extracted.get("parser_version") == document_control.PARSER_VERSION if row.extracted is not None else True,
        form_is_submittal=form.get("is_submittal") if form else None,
        notes=[str(n) for n in (extracted.get("notes") or [])][:5], error=row.error,
        evidence_pages_read=evidence.get("pages_read") if evidence else None,
        page_count=evidence.get("page_count") if evidence else None)


@router.get("/{project_id}/documents/classification/metrics", response_model=ClassificationMetricsOut)
def document_classification_metrics(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClassificationMetricsOut:
    from app.services import document_classification as classification

    project = _get_project_or_404(db, project_id)
    return ClassificationMetricsOut(**classification.metrics(db, project))


CLASSIFY_KIND = "classify_documents"


@router.post("/{project_id}/jobs/classify-documents", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_classification_backfill(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    """Classify every indexed document of the project from what is already
    stored -- no file is read, no model is asked -- as a job the page can
    follow and stop. Rows whose assessment is already current are skipped,
    so a stopped backfill carries on where it left off. Only with
    DOCUMENT_CLASSIFICATION_V2 on."""
    from app.services import document_classification as classification

    if not classification.enabled():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Document classification is off on this server (DOCUMENT_CLASSIFICATION_V2)")
    project = _get_project_or_404(db, project_id)
    running = jobs.active_job(db, project.id, CLASSIFY_KIND)
    if running is not None:
        return _out(running)
    project_id_, user_id = project.id, current_user.id

    def work(session: Session, ctx) -> dict:
        from app.models import Project

        target = session.get(Project, project_id_)
        return classification.backfill(session, target, ctx=ctx)

    job = jobs.start(db, kind=CLASSIFY_KIND, project_id=project.id, user_id=user_id, work=work,
                     run_inline=jobs_router.RUN_INLINE)
    return _out(job)


def _run_inline(db: Session, job: BackgroundJob, worker) -> BackgroundJob:
    """Tests: run a queued worker job here, through the worker's own code,
    so they need not poll."""
    worker.run_claimed(job.id)
    db.expire_all()
    return db.get(BackgroundJob, job.id)


@router.post("/{project_id}/jobs/sync-documents", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    """Queue a file sync and answer at once. The sync worker runs it: every
    file's size and time is checked against the index, new and changed
    files are recorded as pending, removed ones marked, and the project is
    synced -- in seconds, nothing opened. The pending documents are then
    read by a processing job the sync queues for the document worker
    (`result.processing_job_id`). Nothing heavy happens in this request.

    Asked again while the project's sync is queued or running -- a second
    click, another tab, a colleague -- it answers with that same job,
    `already_active` set, and starts nothing. The database refuses a second
    active sync of a project (app.services.jobs.enqueue), so this holds for
    requests that arrive together too."""
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no archive folder to sync")
    job, created = jobs.enqueue(db, kind=JOB_KIND, project_id=project.id, user_id=current_user.id)
    if created and jobs_router.RUN_INLINE:
        from app.workers.document_worker import DocumentWorker
        from app.workers.sync_worker import Worker

        job = _run_inline(db, job, Worker())
        # And the processing the sync queued, so the tests see the whole of it.
        follow = (job.result or {}).get("processing_job_id")
        if follow:
            _run_inline(db, db.get(BackgroundJob, follow), DocumentWorker())
            db.expire_all()
            job = db.get(BackgroundJob, job.id)
    out = _out(job)
    out.already_active = not created
    return out


class ProcessRequest(BaseModel):
    # Put the documents whose last reading failed back in the queue too.
    retry_failed: bool = False


@router.post("/{project_id}/jobs/process-documents", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def start_processing(
    project_id: int,
    body: ProcessRequest | None = None,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> JobOut:
    """Queue the reading of the project's pending documents -- the ones a
    stopped processing run left, or a sync found while no worker was there
    to read them -- and answer at once. With `retry_failed`, the documents
    whose last reading failed are queued again too. One processing job per
    project: asked again while one is queued or running, it answers with
    that job, `already_active` set. Nothing pending is a 409."""
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no archive folder to process")
    if body is not None and body.retry_failed:
        document_processing.retry_failed(db, project)
    existing = jobs.active_job(db, project.id, PROCESS_KIND)
    if existing is None and document_processing.pending_count(db, project) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "code": "nothing_to_process",
            "message": "Every document the file sync found has been processed. Sync Files finds new ones."})
    job, created = document_processing.enqueue(db, project, user_id=current_user.id)
    if created and jobs_router.RUN_INLINE:
        from app.workers.document_worker import DocumentWorker

        job = _run_inline(db, job, DocumentWorker())
    out = _out(job)
    out.already_active = not created
    return out
