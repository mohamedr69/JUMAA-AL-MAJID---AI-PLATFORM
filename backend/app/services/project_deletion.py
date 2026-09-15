"""Deleting a project and everything that belongs to it, in an order the
database's foreign keys accept.

With foreign keys unenforced (SQLite's default), deleting a project left its
extraction runs, issues, AI proposals, compliance statements and audit rows
behind, pointing at nothing. The database now enforces them, so a project is
removed child-first. What survives by design: the activity record (it names
the project by label, not by key) and AI usage totals (kept for the budget,
their project link cleared).
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    AiProposal,
    AiUsage,
    BackgroundJob,
    BoqCandidate,
    BoqSnapshot,
    ComplianceAudit,
    ComplianceLearnedAnswer,
    ComplianceStatement,
    ExtractionIssue,
    ExtractionRun,
    Project,
    ProjectBoqItem,
    ProjectDocument,
    ResultCache,
)


def delete_project(db: Session, project: Project) -> None:
    """Remove the project and its rows. Not committed: the caller commits,
    together with the activity event that records the deletion."""
    project_id = project.id
    runs = select(ExtractionRun.id).where(ExtractionRun.project_id == project_id)
    issues = select(ExtractionIssue.id).where(ExtractionIssue.run_id.in_(runs))
    statements = select(ComplianceStatement.id).where(ComplianceStatement.project_id == project_id)

    # Lines point at runs, so they go before the runs do.
    db.execute(delete(ProjectBoqItem).where(ProjectBoqItem.project_id == project_id))
    db.execute(delete(AiProposal).where(AiProposal.issue_id.in_(issues)))
    db.execute(update(AiUsage).where(AiUsage.run_id.in_(runs)).values(run_id=None))
    db.execute(update(AiUsage).where(AiUsage.project_id == project_id).values(project_id=None))
    db.execute(delete(ExtractionIssue).where(ExtractionIssue.id.in_(issues)))
    db.execute(delete(ExtractionRun).where(ExtractionRun.project_id == project_id))
    db.execute(delete(ComplianceAudit).where(ComplianceAudit.statement_id.in_(statements)))
    db.execute(delete(ComplianceLearnedAnswer).where(ComplianceLearnedAnswer.project_id == project_id))
    db.execute(delete(ComplianceStatement).where(ComplianceStatement.project_id == project_id))
    for model in (BoqCandidate, BoqSnapshot, ProjectDocument, BackgroundJob, ResultCache):
        db.execute(delete(model).where(model.project_id == project_id))
    db.expire(project, ["boq_items"])
    db.delete(project)
