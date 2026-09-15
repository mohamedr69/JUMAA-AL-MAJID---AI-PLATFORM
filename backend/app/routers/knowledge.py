"""The compliance knowledge base, for administrators: its status, the
"Update knowledge base" action, and each import's report.

The source folder is a server setting (COMPLIANCE_KNOWLEDGE_SOURCE); its
path is never sent to the browser. Updating reads the source's workbook and
rewrites the knowledge tables in one transaction, in the background, with
no model involved.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.knowledge import eligibility, importer
from app.models import RoleEnum, User
from app.services import activity
from app.schemas_design import KnowledgeImportReportOut, KnowledgeStatusOut

router = APIRouter(prefix="/admin/knowledge", tags=["knowledge"])


@router.get("", response_model=KnowledgeStatusOut)
def knowledge_status(
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> KnowledgeStatusOut:
    return KnowledgeStatusOut(**importer.status(db))


@router.post("/import", response_model=KnowledgeStatusOut, status_code=status.HTTP_202_ACCEPTED)
def update_knowledge_base(
    force: bool = False,
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> KnowledgeStatusOut:
    """Read the source collection and refresh the knowledge base, in the
    background. `force` re-imports a workbook whose content has not changed."""
    if importer.source_root() is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="No knowledge source is configured on this server (COMPLIANCE_KNOWLEDGE_SOURCE).")
    if not importer.start_import(user_id=current_user.id, force=force):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An update is already running")
    return KnowledgeStatusOut(**importer.status(db))


class MappingReviewIn(BaseModel):
    mapping_ids: list[str] = Field(min_length=1, max_length=200)
    verdict: Literal["verified", "rejected"]
    note: str | None = Field(default=None, max_length=500)


@router.get("/eligibility")
def eligibility_summary(
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> dict:
    """Why the knowledge base's responses are blocked, counted every way
    that helps decide what to review first."""
    return eligibility.summary(db)


@router.get("/eligibility/review-queue")
def eligibility_review_queue(
    method: str = "A_table",
    confidence: str = "medium",
    manufacturer: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> dict:
    """Unreviewed source mappings of one method and confidence, with the
    clause, the source page and the responses each carries."""
    return eligibility.review_queue(db, method=method, confidence=confidence, manufacturer=manufacturer,
                                    limit=limit, offset=offset)


@router.post("/eligibility/reviews")
def review_mappings(
    payload: MappingReviewIn,
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> dict:
    """Record verdicts on a batch of mappings and recompute the eligibility
    of every response they carry, under the unchanged policy."""
    result = eligibility.review(db, payload.mapping_ids, payload.verdict, current_user, payload.note)
    activity.record(db, current_user, "knowledge.mappings_reviewed",
                    f"Marked {result['reviewed']} knowledge-base source pairing{'s' if result['reviewed'] != 1 else ''} "
                    f"{payload.verdict}: {result['now_eligible']} response(s) now eligible",
                    entity_type="knowledge_mapping", detail={**result, "verdict": payload.verdict})
    return result


@router.get("/imports/{import_id}", response_model=KnowledgeImportReportOut)
def import_report(
    import_id: int,
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> KnowledgeImportReportOut:
    report = importer.import_report(db, import_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such import")
    return KnowledgeImportReportOut(**report)
