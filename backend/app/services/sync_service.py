"""Running a project's document sync: what the worker does with a
`sync_documents` job (app.workers.sync_worker).

The sync itself is `document_sync.sync`; this is the job around it -- the
project and the person who asked, and the activity line recorded when it
finishes. The API never calls this: it only queues the job
(app.services.jobs.enqueue).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import BackgroundJob, Project, User
from app.services import activity, document_sync
from app.services.jobs import JobContext

log = logging.getLogger(__name__)


def run_project_sync(session: Session, job: BackgroundJob, ctx: JobContext) -> dict:
    """Sync the job's project folder into the index, reporting through
    `ctx` (progress, cancellation, the worker's shutdown). Returns the
    counts the job keeps as its result."""
    project = session.get(Project, job.project_id) if job.project_id else None
    if project is None:
        raise document_sync.SyncError("The project no longer exists")
    actor = session.get(User, job.created_by_id) if job.created_by_id else None
    log.info("Starting sync job %s for project %s (%s)", job.id, project.id, project.ep_number or "no EP number")
    result = document_sync.sync(session, project, user=actor, ctx=ctx)
    summary = (f"Synced the project folder: {result['files']} file{'s' if result['files'] != 1 else ''}, "
               f"{result['new']} new, {result['changed']} changed, {result['unchanged']} unchanged, "
               f"{result['removed']} removed; {result['read_by_ai']} read by the AI")
    if actor is not None:
        activity.record(session, actor, "documents.synced", summary, project=project, entity_type="project",
                        entity_id=project.id, detail={k: v for k, v in result.items() if isinstance(v, (int, bool))})
    log.info("Sync job %s: %s", job.id, summary)
    return result
