"""The floor-wise device count, read off drawings the engineer hands in.

  GET    /projects/{id}/floor-boq          the schedule as last extracted
  POST   /projects/{id}/floor-boq          read these drawings (DWG or DXF) and replace it
  DELETE /projects/{id}/floor-boq          clear it

This is its own thing: the drawings are uploaded, read and reported. It
reads nothing from the project folder, syncs nothing and changes no BOQ.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import ProjectFloorBoq, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, dwg_convert, floor_devices

router = APIRouter(prefix="/projects", tags=["floor boq"])

MAX_FILES = 40
MAX_BYTES = 200 * 1024 * 1024
DRAWING_SUFFIXES = (".dwg", ".dxf")


class FloorBoqOut(BaseModel):
    result: dict | None
    updated_at: datetime | None
    # Why a DWG cannot be converted on this server, when it cannot.
    converter: str | None


def _out(row: ProjectFloorBoq | None) -> FloorBoqOut:
    return FloorBoqOut(result=row.result if row else None, updated_at=row.updated_at if row else None,
                       converter=dwg_convert.unavailable())


@router.get("/{project_id}/floor-boq", response_model=FloorBoqOut)
def get_floor_boq(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FloorBoqOut:
    """What the drawings said, from the database: opening the tab reads no
    drawing again."""
    project = _get_project_or_404(db, project_id)
    return _out(db.query(ProjectFloorBoq).filter(ProjectFloorBoq.project_id == project.id).first())


@router.post("/{project_id}/floor-boq", response_model=FloorBoqOut)
def read_drawings(
    project_id: int,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> FloorBoqOut:
    """Read the drawings handed in and replace the schedule with what they
    say. A DWG is converted to DXF first; the devices of each floor are
    counted, the legend and anything outside the plan left out."""
    project = _get_project_or_404(db, project_id)
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose at least one drawing")
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_FILES} drawings at a time")

    staged = Path(tempfile.mkdtemp(prefix=f"floor-boq-{project.id}-"))
    try:
        drawings: list[Path] = []
        warnings: list[str] = []
        for upload in files:
            name = Path(upload.filename or "drawing").name
            if not name.lower().endswith(DRAWING_SUFFIXES):
                warnings.append(f"{name}: not a drawing (a DWG or a DXF is expected).")
                continue
            target = staged / name
            size = 0
            with open(target, "wb") as handle:
                while chunk := upload.file.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{name} is larger than 200 MB")
                    handle.write(chunk)
            drawings.append(target)
        if not drawings:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=warnings[0] if warnings else "No drawing was handed in")

        ready, names, failed = dwg_convert.ensure_dxf(drawings)
        extraction = floor_devices.extract(ready, names)
        extraction.warnings = warnings + failed + extraction.warnings
        result = extraction.as_dict()
    finally:
        shutil.rmtree(staged, ignore_errors=True)

    row = db.query(ProjectFloorBoq).filter(ProjectFloorBoq.project_id == project.id).first()
    if row is None:
        row = ProjectFloorBoq(project_id=project.id, result=result, created_by_id=current_user.id)
        db.add(row)
    else:
        row.result, row.created_by_id = result, current_user.id
    db.commit()
    db.refresh(row)
    activity.record(db, current_user, "floor_boq.read",
                    f"Read {len(drawings)} drawing{'s' if len(drawings) != 1 else ''} into the floor-wise BOQ: "
                    f"{len(result['floors'])} floor{'s' if len(result['floors']) != 1 else ''}",
                    project=project, entity_type="floor_boq",
                    detail={"files": [f["file"] for f in result["files"]], "devices": result["devices"]})
    return _out(row)


@router.delete("/{project_id}/floor-boq", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def clear_floor_boq(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    row = db.query(ProjectFloorBoq).filter(ProjectFloorBoq.project_id == project.id).first()
    if row is not None:
        db.delete(row)
        db.commit()
        activity.record(db, current_user, "floor_boq.cleared", "Cleared the floor-wise BOQ", project=project, entity_type="floor_boq")
