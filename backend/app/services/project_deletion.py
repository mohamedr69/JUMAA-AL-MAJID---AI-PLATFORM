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
    AiVerification,
    BackgroundJob,
    BatteryPanelResult,
    BoqCandidate,
    BoqSnapshot,
    ComplianceAudit,
    ComplianceLearnedAnswer,
    ComplianceStatement,
    DocumentDependency,
    DocumentReading,
    DrawingIssue,
    DrawingRequirementState,
    ExtractionIssue,
    ExtractionRun,
    Project,
    ProjectAction,
    ProjectAmplifierDesign,
    ProjectBoqItem,
    ProjectBuildingFloor,
    ProjectChange,
    ProjectDocument,
    ProjectFloorSchedule,
    ProjectFrcCables,
    ProjectIfcDrawing,
    ProjectProposedMaterial,
    ProjectShopDrawing,
    ResultCache,
    ShopDrawingCandidate,
    ShopDrawingEvent,
    ShopDrawingRevision,
    SubmittalReply,
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
    # Everything else that hangs off the project by key and has no
    # cascade of its own. A table missing from here does not fail
    # quietly: the foreign keys are enforced, so deleting the project
    # raises and the project cannot be removed at all -- which is what
    # `test_a_project_carrying_every_kind_of_row_can_still_be_deleted`
    # is here to catch when the next per-project table is added.
    # Documents go after the rows that point at them.
    drawings = select(ProjectShopDrawing.id).where(ProjectShopDrawing.project_id == project_id)
    db.execute(delete(ShopDrawingRevision).where(ShopDrawingRevision.shop_drawing_id.in_(drawings)))
    db.execute(delete(ShopDrawingCandidate).where(ShopDrawingCandidate.project_id == project_id))
    for model in (AiVerification, DocumentDependency, DocumentReading, BatteryPanelResult, SubmittalReply,
                  ProjectShopDrawing, ProjectBuildingFloor, DrawingIssue, DrawingRequirementState, ShopDrawingEvent,
                  ProjectFrcCables, ProjectProposedMaterial, ProjectFloorSchedule, ProjectAmplifierDesign, ProjectIfcDrawing,
                  BoqCandidate, BoqSnapshot, ProjectDocument, BackgroundJob, ResultCache, ProjectAction, ProjectChange):
        db.execute(delete(model).where(model.project_id == project_id))
    db.expire(project, ["boq_items"])
    db.delete(project)
    # The platform's working copies of its IFC drawings. What was filed in
    # the project's own folder is the project's, and stays.
    import shutil

    from app.ifc import storage

    shutil.rmtree(storage.project_folder(project), ignore_errors=True)
