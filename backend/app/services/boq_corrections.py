"""What engineers correct on the rows the Design Sheet read produced, kept
as evaluation data (app.models.BoqCorrection).

A review row accepted (with the model's value or another), a review row
rejected as not an item, a machine-read line whose sheet values were
edited: each is one record carrying what the machine read of the row --
the first reading, the verification's readings, the page geometry's
evidence, the reason the row was for the engineer -- and what the
engineer made it. Nothing is trained on this now; it is what a later
evaluation, or a fine-tuning, measures against, and it is why a false
auto-accept can be counted after the fact.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import BoqCorrection, ExtractionIssue, ProjectBoqItem


def _processor_version() -> str:
    from app.ai import sheet_reader
    from app.services import design_sheet_extractor

    return f"{sheet_reader.PROMPT_VERSION}+{sheet_reader.VERIFY_PROMPT_VERSION}+{design_sheet_extractor.PARSER_VERSION}"


def record_issue_decision(db: Session, issue: ExtractionIssue, user_id: int | None, *, kind: str,
                          final_quantity: str | None) -> BoqCorrection:
    """A review row accepted ("review_accepted") or rejected
    ("review_rejected"), with everything the read had of it. Flushed, not
    committed: the caller's transaction carries it with the decision."""
    detail = issue.detail or {}
    primary = detail.get("primary") or {}
    row = BoqCorrection(
        project_id=issue.run.project_id, document_sha256=issue.run.document_sha256, page=issue.page,
        bbox=list(issue.region) if issue.region else None, row_id=detail.get("row_id"), kind=kind,
        reason_code=detail.get("reason_code"),
        primary_part_number=primary.get("catalog_no") or detail.get("catalog_no"),
        primary_quantity=primary.get("quantity") or detail.get("raw_quantity"),
        primary_description=primary.get("description") or detail.get("description"),
        verification=detail.get("verification"), evidence=detail.get("evidence"),
        final_part_number=detail.get("catalog_no") if kind == "review_accepted" else None,
        final_quantity=final_quantity if kind == "review_accepted" else None,
        final_description=detail.get("description") if kind == "review_accepted" else None,
        final_group=detail.get("group_heading") if kind == "review_accepted" else None,
        processor_version=_processor_version(), user_id=user_id, created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def record_line_edit(db: Session, previous: ProjectBoqItem, item: ProjectBoqItem, user_id: int | None) -> BoqCorrection | None:
    """A machine-read line whose sheet values an engineer changed in the
    BOQ table: what the read had made of it against what they typed. None
    for a line the read did not produce."""
    if previous.origin not in ("extracted", "ai_accepted", "review_accepted"):
        return None
    raw = previous.raw_values or {}
    first = raw.get("ai_reading") or {}
    row = BoqCorrection(
        project_id=previous.project_id, document_sha256=previous.source_document_sha256, page=previous.source_page,
        bbox=list(previous.source_region) if previous.source_region else None, row_id=raw.get("row_id"), kind="line_edited",
        reason_code=None,
        primary_part_number=first.get("catalog_no") or (previous.extracted_values or {}).get("catalog_no") or previous.catalog_no,
        primary_quantity=first.get("quantity") or (previous.extracted_values or {}).get("quantity") or previous.quantity,
        primary_description=first.get("description") or previous.description,
        verification={"ai_check": previous.ai_check} if previous.ai_check else None, evidence=raw.get("evidence"),
        final_part_number=item.catalog_no, final_quantity=item.quantity, final_description=item.description,
        final_group=item.group_heading, processor_version=previous.parser_version or _processor_version(),
        user_id=user_id, created_at=utc_now(),
    )
    db.add(row)
    return row
