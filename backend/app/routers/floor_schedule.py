"""The floor-wise BOQ read off the schedule an engineer keeps in Excel.

  GET    /projects/{id}/floor-schedule              what the workbook said
  POST   /projects/{id}/floor-schedule              read this workbook and replace it
  PATCH  .../floor-schedule/items/{row}             set one floor's quantity by hand
  PATCH  .../floor-schedule/items/{row}/material    settle which part the line is
  GET    /projects/{id}/floor-schedule/materials    the parts a line may be settled as
  GET    /projects/{id}/floor-schedule/export.pdf   the schedule as a document
  GET    /projects/{id}/floor-schedule/check        against the design sheet BOQ
  DELETE /projects/{id}/floor-schedule              clear it

This is the BOQ page's "BOQ Floor Wise" tab: the quantities are already
written down in the engineer's own workbook, and the work is reading the
sheet honestly.

The workbook is also kept. A schedule an engineer works from belongs with
the project's own documents, so it is filed under `03- Design` in the
project's OneDrive folder, beside the submittals and the drawings -- the
one thing on this route that writes to the archive, and only ever a new
file in a folder of its own. No BOQ is changed.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.extraction.identity import part_key
from app.models import ProjectBoqItem, ProjectFloorSchedule, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, document_sync, floor_schedule, project_folders
from app.services.schedule_materials import device_materials, rank_for_line, settle_unambiguous

router = APIRouter(prefix="/projects", tags=["floor schedule"])

MAX_BYTES = 50 * 1024 * 1024
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


class FloorScheduleOut(BaseModel):
    result: dict | None
    file_name: str | None
    sheet_name: str | None
    # Where the workbook was filed in the project's own folder on OneDrive
    # ("03- Design/..."). Null when that folder is not reachable on this
    # PC: the schedule is read and shown all the same, it is simply not
    # filed, and `filed_note` says why.
    archive_path: str | None
    # The workbook in the project folder the tab keeps itself in step with.
    source_path: str | None
    filed_note: str | None
    updated_at: datetime | None


def _out(row: ProjectFloorSchedule | None, note: str | None = None) -> FloorScheduleOut:
    return FloorScheduleOut(
        result=row.result if row else None,
        file_name=row.file_name if row else None,
        sheet_name=row.sheet_name if row else None,
        archive_path=row.archive_path if row else None,
        source_path=row.source_path if row else None,
        filed_note=note,
        updated_at=row.updated_at if row else None,
    )


def sync_from_folder(db: Session, project, user: User | None = None) -> tuple[ProjectFloorSchedule | None, str | None]:
    """The floor-wise BOQ from the project's folder, its lines settled as
    far as the project's own materials settle them."""
    stored, note = _read_from_folder(db, project, user)
    if stored is not None and settle_unambiguous(db, project, stored):
        db.commit()
        db.refresh(stored)
    return stored, note


def _read_from_folder(db: Session, project, user: User | None = None) -> tuple[ProjectFloorSchedule | None, str | None]:
    """Read the floor-wise BOQ out of the project's own folder, if it has
    moved on since last time.

    The schedule an engineer keeps is a workbook in `03- Design`, and it
    is edited there for the rest of the job. Asking for it to be uploaded
    again every time it changes would be asking them to do the platform's
    filing; instead the newest workbook in that folder is the source, and
    it is read again whenever its contents differ from what was read
    before. Nothing is uploaded twice, and a project already set up needs
    no upload at all.

    Returns (the row, a note about what happened) -- the note is None when
    nothing needed doing.
    """
    stored = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    candidates = project_folders.design_documents(project)
    if not candidates:
        return stored, None

    for path in candidates:
        digest = document_sync.sha256_of(path)
        if stored is not None and stored.source_sha256 and digest == stored.source_sha256:
            return stored, None                      # already in step
        schedule = floor_schedule.read(path)
        if not schedule.items:
            continue                                  # not a floor-wise schedule; try the next
        result = floor_schedule.apply_edits(schedule.as_dict(), (stored.edits if stored else {}) or {})
        relative = path.name
        try:
            relative = path.relative_to(Path(project.source_folder_path)).as_posix()
        except (TypeError, ValueError):
            pass
        first = stored is None
        if stored is None:
            stored = ProjectFloorSchedule(project_id=project.id, result=result)
            db.add(stored)
        stored.result = result
        stored.file_name, stored.sheet_name = path.name, schedule.sheet
        stored.archive_path = stored.source_path = relative
        stored.source_sha256 = digest
        if user is not None:
            stored.created_by_id = user.id
        db.commit()
        db.refresh(stored)
        return stored, (
            f"Read {path.name} from the project's 03- Design folder."
            if first else
            f"{path.name} has changed in 03- Design and was read again."
        )
    return stored, None


@router.get("/{project_id}/floor-schedule", response_model=FloorScheduleOut)
def get_floor_schedule(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FloorScheduleOut:
    """The floor-wise BOQ, kept in step with the workbook in the project's
    own folder.

    The workbook in `03- Design` is the source: it is read the first time
    the tab is opened and again whenever it changes, so nothing is
    uploaded twice and a project whose schedule is already filed needs no
    upload at all.
    """
    project = _get_project_or_404(db, project_id)
    row, note = sync_from_folder(db, project)
    return _out(row, note)


@router.post("/{project_id}/floor-schedule", response_model=FloorScheduleOut)
def read_schedule(
    project_id: int,
    file: UploadFile = File(...),
    sheet: str | None = None,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> FloorScheduleOut:
    """Read the schedule handed in and replace what the tab shows.

    A column standing for a range of floors -- "1 to 13" -- becomes a row
    per floor, which is the whole point of the tab.
    """
    project = _get_project_or_404(db, project_id)
    name = Path(file.filename or "schedule.xlsx").name
    if not name.lower().endswith(WORKBOOK_SUFFIXES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"{name}: an Excel workbook (.xlsx) is expected")

    staged = Path(tempfile.mkdtemp(prefix=f"floor-schedule-{project.id}-"))
    try:
        target = staged / name
        size = 0
        with open(target, "wb") as handle:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                        detail=f"{name} is larger than 50 MB")
                handle.write(chunk)
        schedule = floor_schedule.read(target, sheet_name=sheet)
        content = target.read_bytes()
    finally:
        shutil.rmtree(staged, ignore_errors=True)

    if not schedule.items and schedule.warnings:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=schedule.warnings[0])

    # The workbook is kept in the project's own folder, not only read: the
    # schedule an engineer works from belongs with the project's documents,
    # where the next person looks for it. It is filed only once the read
    # has succeeded, so a file that turned out not to be a schedule is not
    # left in the archive.
    filed = note = None
    try:
        filed = project_folders.file_design_document(project, name, content)
        if filed is None:
            note = ("The workbook was read but not filed: this project's folder is not reachable on this PC, "
                    "so there is no 03- Design folder to put it in.")
    except OSError as exc:
        note = f"The workbook was read but could not be filed in 03- Design ({exc})."

    result = schedule.as_dict()
    row = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if row is None:
        row = ProjectFloorSchedule(project_id=project.id, result=result, file_name=name,
                                   sheet_name=schedule.sheet, archive_path=filed,
                                   created_by_id=current_user.id)
        db.add(row)
    else:
        row.result, row.file_name, row.sheet_name = result, name, schedule.sheet
        row.archive_path = filed
        row.created_by_id = current_user.id
    # What was handed in is now the source the tab keeps in step with.
    row.source_path = filed
    row.source_sha256 = hashlib.sha256(content).hexdigest()
    settle_unambiguous(db, project, row)
    db.commit()
    db.refresh(row)
    activity.record(db, current_user, "floor_schedule.read",
                    f"Read {name} into the floor-wise BOQ: {len(result['items'])} "
                    f"item{'s' if len(result['items']) != 1 else ''} over {len(result['floors'])} "
                    f"floor{'s' if len(result['floors']) != 1 else ''}",
                    project=project, entity_type="floor_schedule",
                    detail={"file": name, "sheet": schedule.sheet, "floors": result["floors"],
                            "filed": filed})
    return _out(row, note)


class MaterialOut(BaseModel):
    part_no: str
    description: str
    manufacturer: str | None
    system_code: str | None
    source: str


class MaterialOptionsOut(BaseModel):
    """The parts a system's lines may be settled as, and the order each
    line should offer them in."""

    materials: list[MaterialOut]
    by_line: dict[int, list[str]]


@router.get("/{project_id}/floor-schedule/materials/by-line", response_model=MaterialOptionsOut)
def schedule_material_options(
    project_id: int,
    system: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaterialOptionsOut:
    """The same parts as `/materials`, with each line's own order.

    A line for a smoke detector should open its dropdown on the smoke
    detector rather than on whatever part sorts first, and a line for a
    ceiling speaker on the ceiling speaker. The ordering is worked out
    here, once for every line of the system, so the tab asks once rather
    than once per line. Nothing is hidden from any line: a part the
    wording did not predict is further down the same list.
    """
    project = _get_project_or_404(db, project_id)
    materials = device_materials(db, project, system)
    stored = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    items = (stored.result or {}).get("items", []) if stored is not None else []
    wanted = (system or "").strip().upper()
    by_line = {
        item["row"]: [material["part_no"] for material in rank_for_line(item, materials)]
        for item in items
        if item.get("row") is not None and (not wanted or (item.get("system") or "").strip().upper() == wanted)
    }
    return MaterialOptionsOut(materials=[MaterialOut(**material) for material in materials], by_line=by_line)


@router.get("/{project_id}/floor-schedule/materials", response_model=list[MaterialOut])
def schedule_materials(
    project_id: int,
    system: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MaterialOut]:
    """The parts a line of the schedule may be settled as: this project's
    own proposed materials for the system, devices only.

    A back box, a loop card or a battery is ordered with a device or for a
    panel and never counted on a floor, so none is offered -- settling a
    floor's smoke detectors as a back box would be worse than offering
    nothing (`app.services.schedule_materials`).
    """
    project = _get_project_or_404(db, project_id)
    return [MaterialOut(**material) for material in device_materials(db, project, system)]


class QuantityIn(BaseModel):
    """One cell of the schedule, set by hand: a floor, or all the floors of
    a typical column (each set to the quantity)."""

    floor: str | None = None
    floors: list[str] = []
    # Nothing puts the cell back to blank -- the item is not on that floor
    # -- rather than to a BOQ line of zero.
    quantity: float | None = None


@router.patch("/{project_id}/floor-schedule/items/{row}", response_model=FloorScheduleOut)
def set_quantity(
    project_id: int,
    row: int,
    payload: QuantityIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> FloorScheduleOut:
    """Change how many of one item sit on one floor.

    The line is addressed by the row it came from in the workbook, which
    does not move when the page re-sorts or the systems are split.
    Everything derived -- the line's total, the floor's, the system's and
    the whole schedule's -- is worked out again.
    """
    project = _get_project_or_404(db, project_id)
    stored = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if stored is None or not stored.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No schedule has been read yet")
    item = next((line for line in (stored.result or {}).get("items", []) if line.get("row") == row), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no line at row {row}")
    targets = payload.floors or ([payload.floor] if payload.floor else [])
    if not targets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Say which floor, or floors, to set")
    try:
        stored.result = floor_schedule.set_quantity(
            dict(stored.result), row=row, floor=targets, quantity=payload.quantity,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc.args[0])) from exc
    # Kept apart from the reading, so re-reading a changed workbook does
    # not throw the correction away.
    edits = {key: dict(value) for key, value in (stored.edits or {}).items()}
    for target in targets:
        edits.setdefault(item["description"], {})[target] = payload.quantity or 0
    stored.edits = edits
    stored.created_by_id = current_user.id
    db.commit()
    db.refresh(stored)
    return _out(stored)


class MaterialChoiceIn(BaseModel):
    """The proposed material an engineer settles a line as."""

    part_no: str | None = None


@router.patch("/{project_id}/floor-schedule/items/{row}/material", response_model=FloorScheduleOut)
def choose_material(
    project_id: int,
    row: int,
    payload: MaterialChoiceIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> FloorScheduleOut:
    """Settle which of the project's proposed materials a line is.

    The schedule says what the device is ("Smoke Detector"); this says
    which part it is ordered as, from the materials already proposed for
    this project. Nothing is invented: only a part on that list can be
    chosen, and only from the line's own system.
    """
    project = _get_project_or_404(db, project_id)
    stored = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if stored is None or not stored.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No schedule has been read yet")

    result = dict(stored.result)
    items = [dict(item) for item in result.get("items", [])]
    wanted = next((item for item in items if item.get("row") == row), None)
    if wanted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no line at row {row}")

    chosen = (payload.part_no or "").strip()
    if chosen:
        offered = {
            material["part_no"]: material
            for material in device_materials(db, project, wanted.get("system"))
        }
        if chosen not in offered:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"{chosen} is not a device proposed for this project's "
                       f"{wanted.get('system') or 'schedule'}",
            )
        wanted["material"] = {
            "part_no": chosen,
            "description": offered[chosen].get("description"),
            "manufacturer": offered[chosen].get("manufacturer"),
        }
    else:
        wanted.pop("material", None)
    result["items"] = items
    stored.result = result
    stored.created_by_id = current_user.id
    db.commit()
    db.refresh(stored)
    return _out(stored)


@router.get("/{project_id}/floor-schedule/export.pdf")
def export_schedule(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The floor-wise BOQ as a document: a section per system, the floors
    split across as many landscape pages as they need."""
    from app.services import schedule_export

    project = _get_project_or_404(db, project_id)
    row = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if row is None or not row.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No schedule has been read yet")
    doc = schedule_export.build(project, row.result)
    pdf = doc.tobytes()
    doc.close()
    name = f"EP-{project.ep_number} - BOQ Floor Wise.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"{name}\"; "
                                                    f"filename*=UTF-8''{quote(name)}"})


class ScheduleCheckOut(BaseModel):
    """The schedule's own totals against the design sheet BOQ."""

    rows: list[dict]
    matched: int
    # Items the schedule has and the design sheet does not, and the other
    # way about: what an engineer looks at first.
    only_in_schedule: list[dict]
    only_in_boq: list[dict]


@router.get("/{project_id}/floor-schedule/check", response_model=ScheduleCheckOut)
def check_against_boq(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleCheckOut:
    """What the floors add up to, beside what the design sheet BOQ says.

    The two are read from different documents and should agree. Where they
    do not, the difference is the question -- most often a typical column
    read one floor short, or an item the schedule carries and the BOQ does
    not.
    """
    project = _get_project_or_404(db, project_id)
    row = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if row is None or not row.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No schedule has been read yet")

    def quantity(value) -> float | None:
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    boq: dict[str, dict] = {}
    for item in db.query(ProjectBoqItem).filter(ProjectBoqItem.project_id == project.id).all():
        key = part_key(item.catalog_no or "") or (item.description or "").strip().upper()
        if not key:
            continue
        entry = boq.setdefault(key, {"catalog_no": item.catalog_no, "description": item.description,
                                     "quantity": 0.0, "counted": False})
        amount = quantity(item.quantity)
        if amount is not None:
            entry["quantity"] += amount
            entry["counted"] = True

    rows, matched, only_schedule = [], 0, []
    seen: set[str] = set()
    for item in row.result.get("items", []):
        key = part_key(item.get("catalog_no") or "") or (item.get("description") or "").strip().upper()
        against = boq.get(key)
        if against is None:
            only_schedule.append({"description": item["description"], "catalog_no": item.get("catalog_no"),
                                  "total": item["total"]})
            continue
        seen.add(key)
        matched += 1
        rows.append({
            "description": item["description"],
            "catalog_no": item.get("catalog_no") or against["catalog_no"],
            "schedule_total": item["total"],
            "boq_quantity": against["quantity"] if against["counted"] else None,
            "difference": (item["total"] - against["quantity"]) if against["counted"] else None,
        })
    only_boq = [
        {"catalog_no": entry["catalog_no"], "description": entry["description"], "quantity": entry["quantity"]}
        for key, entry in boq.items() if key not in seen
    ]
    rows.sort(key=lambda entry: (entry["difference"] is None, -abs(entry["difference"] or 0)))
    return ScheduleCheckOut(rows=rows, matched=matched, only_in_schedule=only_schedule, only_in_boq=only_boq)


@router.delete("/{project_id}/floor-schedule", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def clear_floor_schedule(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    row = db.query(ProjectFloorSchedule).filter(ProjectFloorSchedule.project_id == project.id).first()
    if row is not None:
        db.delete(row)
        db.commit()
        activity.record(db, current_user, "floor_schedule.cleared", "Cleared the floor-wise BOQ schedule",
                        project=project, entity_type="floor_schedule")
