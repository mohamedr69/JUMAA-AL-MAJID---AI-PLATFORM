"""Re-reading the Design Sheets into a reviewable candidate, snapshots of the
BOQ before anything replaces part of it, and the source image of a line.

  POST /projects/{id}/boq/candidates                  read again, compare, write nothing
  GET  /projects/{id}/boq/candidates                  the re-reads, newest first
  GET  /projects/{id}/boq/candidates/{cid}            one, with every change
  GET  /projects/{id}/boq/candidates/{cid}/changes/{change}/evidence.png
  POST /projects/{id}/boq/candidates/{cid}/apply      the engineer's decisions
  POST /projects/{id}/boq/candidates/{cid}/discard
  GET  /projects/{id}/boq/snapshots                   the BOQ as it was before each change
  GET  /projects/{id}/boq/snapshots/{sid}
  POST /projects/{id}/boq/snapshots/{sid}/restore     put a snapshot back (itself snapshotted first)
  GET  /projects/{id}/boq/items/{item}/evidence.png   where a line was read from
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai import evidence as evidence_builder
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import BoqCandidate, BoqSnapshot, ExtractionRun, ProjectBoqItem, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, boq_candidates, boq_provenance, concurrency, design_sheet_extractor

router = APIRouter(prefix="/projects", tags=["boq review"])

ROW_IMAGE_WIDTH_PX = 1100


class CandidateSummaryOut(BaseModel):
    id: int
    status: str
    base_boq_version: int
    parser_version: str
    summary: dict
    created_at: datetime
    decided_at: datetime | None
    decisions_needed: int


class CandidateOut(CandidateSummaryOut):
    changes: list[dict]
    boq_version: int
    stale: bool


class ApplyIn(BaseModel):
    decisions: dict[str, str]


class SnapshotSummaryOut(BaseModel):
    id: int
    boq_version: int
    reason: str
    lines: int
    created_at: datetime
    created_by_name: str | None


class SnapshotOut(SnapshotSummaryOut):
    items: list[dict]


def _summary(candidate: BoqCandidate) -> CandidateSummaryOut:
    return CandidateSummaryOut(
        id=candidate.id, status=candidate.status, base_boq_version=candidate.base_boq_version,
        parser_version=candidate.parser_version, summary=candidate.summary or {}, created_at=candidate.created_at,
        decided_at=candidate.decided_at, decisions_needed=len(boq_candidates.decisions_needed(candidate)),
    )


def _candidate_or_404(db: Session, project_id: int, candidate_id: int) -> BoqCandidate:
    candidate = db.get(BoqCandidate, candidate_id)
    if candidate is None or candidate.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such re-read on this project")
    return candidate


def _full(candidate: BoqCandidate, boq_version: int) -> CandidateOut:
    return CandidateOut(**_summary(candidate).model_dump(), changes=candidate.changes, boq_version=boq_version,
                        stale=candidate.status == "pending" and candidate.base_boq_version != boq_version)


@router.post("/{project_id}/boq/candidates", response_model=CandidateOut, status_code=status.HTTP_201_CREATED)
def build_candidate(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> CandidateOut:
    """Read every Design Sheet again with the current parser and lay the
    result against the BOQ. Slow (OCR); writes nothing to the BOQ."""
    project = _get_project_or_404(db, project_id)
    try:
        candidate = boq_candidates.build(db, project, current_user)
    except boq_candidates.CandidateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    s = candidate.summary
    activity.record(db, current_user, "boq.reread", f"Re-read the Design Sheets: {s['new_lines']} lines read, "
                    f"{s['changed']} changed, {s['added']} added, {s['removed']} removed",
                    project=project, entity_type="boq_candidate", entity_id=candidate.id,
                    detail={k: s[k] for k in ("old_lines", "new_lines", "changed", "added", "removed", "probable")})
    return _full(candidate, project.boq_version)


@router.get("/{project_id}/boq/candidates", response_model=list[CandidateSummaryOut])
def list_candidates(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CandidateSummaryOut]:
    _get_project_or_404(db, project_id)
    rows = db.query(BoqCandidate).filter(BoqCandidate.project_id == project_id).order_by(BoqCandidate.id.desc()).limit(20)
    return [_summary(c) for c in rows]


@router.get("/{project_id}/boq/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(
    project_id: int,
    candidate_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CandidateOut:
    project = _get_project_or_404(db, project_id)
    return _full(_candidate_or_404(db, project_id, candidate_id), project.boq_version)


@router.get("/{project_id}/boq/candidates/{candidate_id}/changes/{change_id}/evidence.png")
def candidate_change_evidence(
    project_id: int,
    candidate_id: int,
    change_id: str,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The row on the sheet the change was read from."""
    candidate = _candidate_or_404(db, project_id, candidate_id)
    change = next((c for c in candidate.changes if c.get("id") == change_id), None)
    record = (change or {}).get("after") or (change or {}).get("before")
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such change")
    return _render(db, record.get("extraction_run_id"), record.get("source_page"), record.get("source_region"))


@router.post("/{project_id}/boq/candidates/{candidate_id}/apply")
def apply_candidate(
    project_id: int,
    candidate_id: int,
    payload: ApplyIn,
    response: Response,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    project = _get_project_or_404(db, project_id)
    candidate = _candidate_or_404(db, project_id, candidate_id)
    try:
        counts = boq_candidates.apply(db, project, candidate, payload.decisions, current_user)
    except boq_candidates.CandidateStale as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": concurrency.STALE_WRITE, "message": str(exc),
                                                              "current_version": project.boq_version,
                                                              "your_version": candidate.base_boq_version}) from exc
    except boq_candidates.CandidateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    activity.record(db, current_user, "boq.reread_applied",
                    f"Applied a re-read of the Design Sheets: {counts['changed']} changed, {counts['added']} added, "
                    f"{counts['removed']} removed, {counts['kept']} kept as they were",
                    project=project, entity_type="boq_candidate", entity_id=candidate.id, detail=counts)
    concurrency.set_version_header(response, project.boq_version)
    return {"applied": counts, "boq_version": project.boq_version}


@router.post("/{project_id}/boq/candidates/{candidate_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
def discard_candidate(
    project_id: int,
    candidate_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    project = _get_project_or_404(db, project_id)
    candidate = _candidate_or_404(db, project_id, candidate_id)
    try:
        boq_candidates.discard(db, candidate, current_user)
    except boq_candidates.CandidateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    activity.record(db, current_user, "boq.reread_discarded", "Discarded a re-read of the Design Sheets",
                    project=project, entity_type="boq_candidate", entity_id=candidate.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- snapshots ------------------------------------------------------------------------------


def _snapshot_summary(shot: BoqSnapshot, db: Session) -> SnapshotSummaryOut:
    user = db.get(User, shot.created_by_id) if shot.created_by_id else None
    return SnapshotSummaryOut(id=shot.id, boq_version=shot.boq_version, reason=shot.reason, lines=len(shot.items),
                              created_at=shot.created_at, created_by_name=user.full_name if user else None)


def _snapshot_or_404(db: Session, project_id: int, snapshot_id: int) -> BoqSnapshot:
    shot = db.get(BoqSnapshot, snapshot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such snapshot on this project")
    return shot


@router.get("/{project_id}/boq/snapshots", response_model=list[SnapshotSummaryOut])
def list_snapshots(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SnapshotSummaryOut]:
    _get_project_or_404(db, project_id)
    rows = db.query(BoqSnapshot).filter(BoqSnapshot.project_id == project_id).order_by(BoqSnapshot.id.desc()).limit(50)
    return [_snapshot_summary(s, db) for s in rows]


@router.get("/{project_id}/boq/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(
    project_id: int,
    snapshot_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SnapshotOut:
    shot = _snapshot_or_404(db, project_id, snapshot_id)
    return SnapshotOut(**_snapshot_summary(shot, db).model_dump(), items=shot.items)


@router.post("/{project_id}/boq/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    project_id: int,
    snapshot_id: int,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    """Put the BOQ back as a snapshot kept it -- lines and their source
    records. The BOQ as it is now is snapshotted first, so a restore is
    itself undoable."""
    project = _get_project_or_404(db, project_id)
    concurrency.require_current(if_match, project.boq_version, "The BOQ")
    shot = _snapshot_or_404(db, project_id, snapshot_id)
    boq_provenance.snapshot(db, project, f"before restoring snapshot #{shot.id}", current_user)
    project.boq_items = [boq_candidates._item_from(record, position) for position, record in enumerate(shot.items)]
    project.boq_version += 1
    db.commit()
    activity.record(db, current_user, "boq.snapshot_restored", f"Restored the BOQ from snapshot #{shot.id} ({shot.reason})",
                    project=project, entity_type="boq_snapshot", entity_id=shot.id, detail={"lines": len(shot.items)})
    concurrency.set_version_header(response, project.boq_version)
    return {"lines": len(shot.items), "boq_version": project.boq_version}


# --- a line's source image -------------------------------------------------------------------


def _render(db: Session, run_id: int | None, page: int | None, region) -> Response:
    if not run_id or not page or not region:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This line has no recorded source region")
    run = db.get(ExtractionRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The read this line came from is no longer on record")
    path = Path(run.document_path)
    current = boq_provenance.pipeline_sha(path)
    if run.document_sha256 and current and current != run.document_sha256:
        # The file has changed since it was read: the crop would show a
        # different document than the one the value came from.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The Design Sheet has changed since this line was read")
    try:
        png = evidence_builder.render_region(path, int(page), tuple(int(v) for v in region),
                                             design_sheet_extractor.RENDER_DPI, max_width=ROW_IMAGE_WIDTH_PX)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"The document could not be rendered: {exc}") from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/{project_id}/boq/items/{item_id}/evidence.png")
def boq_item_evidence(
    project_id: int,
    item_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    item = db.get(ProjectBoqItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such line on this project")
    return _render(db, item.extraction_run_id, item.source_page, item.source_region)
