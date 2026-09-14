"""The compliance knowledge base, for administrators: its status, the
"Update knowledge base" action, and each import's report.

The source folder is a server setting (COMPLIANCE_KNOWLEDGE_SOURCE); its
path is never sent to the browser. Updating reads the source's workbook and
rewrites the knowledge tables in one transaction, in the background, with
no model involved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.knowledge import importer
from app.models import RoleEnum, User
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
