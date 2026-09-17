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
from app.services import activity, device_symbols, dwg_convert, floor_devices

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
        # What the platform already knows about these symbols, before the
        # drawings are read; what their legends teach is kept afterwards.
        library = device_symbols.load_library(db)
        extraction = floor_devices.extract(ready, names, library=library)
        extraction.warnings = warnings + failed + extraction.warnings
        result = extraction.as_dict()
        device_symbols.remember(db, extraction.learned, source="legend", user_id=current_user.id)
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


class ConfirmSymbolIn(BaseModel):
    """An engineer settling what a symbol is."""

    block: str
    device: str


@router.post("/{project_id}/floor-boq/symbols/confirm", response_model=FloorBoqOut)
def confirm_symbol(
    project_id: int,
    payload: ConfirmSymbolIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> FloorBoqOut:
    """Settle what a symbol is: the schedule is counted again with it, and
    the platform keeps the symbol's geometry so the next project knows it
    without being told."""
    project = _get_project_or_404(db, project_id)
    row = db.query(ProjectFloorBoq).filter(ProjectFloorBoq.project_id == project.id).first()
    if row is None or not row.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No drawing has been read yet")
    device = payload.device.strip()
    if not device:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name the device the symbol stands for")

    result = dict(row.result)
    symbols = [dict(symbol) for symbol in result.get("symbols", [])]
    wanted = [symbol for symbol in symbols if symbol["block"] == payload.block]
    if not wanted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No symbol {payload.block!r} in this schedule")
    for symbol in wanted:
        symbol.update(device=device, confidence=100.0, state="accepted", method="engineer", conflict=None,
                      evidence=[{"source": "engineer", "device": device, "weight": 100,
                                 "detail": f"confirmed by {current_user.full_name}"}])
    result["symbols"] = symbols
    result = _recount(result)
    row.result, row.created_by_id = result, current_user.id
    db.commit()
    db.refresh(row)

    device_symbols.remember(
        db,
        [{"device": device, "block": symbol["block"], "fingerprint": symbol.get("fingerprint"),
          "shape": symbol.get("shape"), "file": symbol.get("file")} for symbol in wanted],
        source="engineer", user_id=current_user.id,
    )
    activity.record(db, current_user, "floor_boq.symbol_confirmed",
                    f"Confirmed the symbol {payload.block} as {device}", project=project, entity_type="floor_boq",
                    detail={"block": payload.block, "device": device})
    return _out(row)


def _recount(result: dict) -> dict:
    """The schedule counted again from the devices themselves, after a
    symbol has been settled. The instances carry the block they were drawn
    from, so nothing is read again."""
    by_block = {(symbol["file"], symbol["block"]): symbol["device"] for symbol in result.get("symbols", [])}
    instances = [dict(instance) for instance in result.get("instances", [])]
    counted = sum(floor.get("total", 0) for floor in result.get("floors", []))
    if not instances or len(instances) < counted:
        # Only the first 20,000 devices are kept one by one, so on a very
        # large job the instances are not the whole schedule. The symbol is
        # still settled and kept; the floors are counted again when the
        # drawings are next read.
        result.setdefault("warnings", []).append(
            "The symbol was confirmed. This job has more devices than are stored one by one, "
            "so the floor totals are counted again the next time the drawings are read."
        )
        return result
    for instance in instances:
        instance["device"] = by_block.get((instance["file"], instance["block"]), instance["device"])
    floors: dict[str, dict] = {}
    for row in result.get("floors", []):
        floors[row["floor"]] = {**row, "devices": {}, "total": 0}
    for instance in instances:
        floor = floors.get(instance["floor"])
        if floor is None:
            continue
        floor["devices"][instance["device"]] = floor["devices"].get(instance["device"], 0) + 1
        floor["total"] += 1
    result["instances"] = instances
    result["floors"] = [floors[row["floor"]] for row in result.get("floors", []) if row["floor"] in floors]
    result["devices"] = sorted({instance["device"] for instance in instances})
    return result


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
