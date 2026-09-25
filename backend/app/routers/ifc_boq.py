"""The BOQ page's "As per IFC Drawings" tab: fire alarm devices counted off
an issued-for-construction drawing (app/ifc).

  GET    /ifc/capabilities                            DXF always; DWG when a converter is on this PC
  GET    /ifc/device-types                            what a symbol can be verified as
  POST   /ifc/device-types                            add one, for good
  PATCH  /ifc/device-types/{id}                       rename, re-enable
  DELETE /ifc/device-types/{id}                       only when no verified symbol uses it
  POST   /ifc/symbols/unverify                        forget verified symbols: they are asked again
  POST   /ifc/reprocess                               re-read every stored drawing (admin)

  GET    /projects/{id}/ifc-drawings                  the project's drawings
  POST   /projects/{id}/ifc-drawings                  upload a DWG or DXF, read within the request
  POST   /projects/{id}/ifc-drawings/jobs             ... read as a job: a percentage and the time left
  GET    /projects/{id}/ifc-drawings/{did}            the drawing, resolved against the library
  DELETE /projects/{id}/ifc-drawings/{did}
  PUT    /projects/{id}/ifc-drawings/{did}/floors     how many floors a sheet stands for
  POST   /projects/{id}/ifc-drawings/{did}/verify     map symbols to a device, or not a device
  POST   /projects/{id}/ifc-drawings/{did}/review     save answers: devices / not a device / skip
  GET    /projects/{id}/ifc-drawings/{did}/export     the BOQ as Excel, once every symbol is answered

Verify first, then quantities: no quantity is given, and nothing is
exported, while a symbol on the floor plans is still to be answered. Every
answer goes to the symbol library the moment it is given, so the next
drawing -- on any project -- recognises it.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.ifc import storage
from app.ifc.dxf import convert
from app.ifc.dxf.extract import extract
from app.ifc.dxf.loose import LOOSE_NAME
from app.ifc import hints
from app.ifc.comparison import compare
from app.ifc.library_file import save_library
from app.ifc.progress import ReadTimer
from app.ifc.resolve import MODEL, resolved_drawing
from app.models import IfcBlockAlias, IfcDeviceType, IfcSymbol, ProjectIfcDrawing, RoleEnum, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, project_folders

router = APIRouter(tags=["BOQ as per IFC"])

MAX_BYTES = 500 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")
CATEGORIES = ("fire_alarm", "emergency_light", "other")


# --- the library: device types and symbols ----------------------------------------------------


class DeviceTypeOut(BaseModel):
    id: int
    code: str
    name: str
    category: str
    unit: str
    sort_order: int
    is_active: bool
    symbol_count: int = 0
    # smoke, heat, call point...: an answer in another family than the symbol's own words is flagged
    family: str | None = None


class DeviceTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    category: str
    unit: str = Field(default="Nos", max_length=10)
    sort_order: int = 500


class DeviceTypeUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    unit: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


def _category(value: str | None) -> None:
    if value is not None and value not in CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"category is one of {', '.join(CATEGORIES)}")


def _type_out(dt: IfcDeviceType, count: int) -> DeviceTypeOut:
    return DeviceTypeOut(id=dt.id, code=dt.code, name=dt.name, category=dt.category, unit=dt.unit,
                         sort_order=dt.sort_order, is_active=dt.is_active, symbol_count=count,
                         family=hints.family_of_type(dt.code, dt.name))


@router.get("/ifc/capabilities")
def capabilities(_current_user: User = Depends(get_current_user)):
    """What the upload accepts on this PC: DWG needs a converter."""
    conv = convert.find_converter()
    return {"dxf": True, "dwg": conv is not None, "dwg_converter": conv.name if conv else None}


@router.get("/ifc/device-types", response_model=list[DeviceTypeOut])
def list_device_types(_current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = dict(
        db.query(IfcSymbol.device_type_id, func.count(IfcSymbol.id))
        .filter(IfcSymbol.device_type_id.isnot(None))
        .group_by(IfcSymbol.device_type_id)
        .all()
    )
    rows = db.query(IfcDeviceType).order_by(IfcDeviceType.category, IfcDeviceType.sort_order, IfcDeviceType.name).all()
    return [_type_out(dt, counts.get(dt.id, 0)) for dt in rows]


@router.post("/ifc/device-types", response_model=DeviceTypeOut, status_code=201)
def create_device_type(body: DeviceTypeCreate, current_user: User = Depends(require_role(*CREATOR_ROLES)),
                       db: Session = Depends(get_db)):
    _category(body.category)
    code = body.code.strip().upper()
    if not code or not body.name.strip():
        raise HTTPException(422, "A device type needs a code and a name")
    if db.query(IfcDeviceType).filter(func.upper(IfcDeviceType.code) == code).first():
        raise HTTPException(409, f"A device type with code {code} already exists")
    dt = IfcDeviceType(code=code, name=body.name.strip(), category=body.category, unit=body.unit.strip() or "Nos",
                       sort_order=body.sort_order)
    db.add(dt)
    db.commit()
    save_library(db)
    activity.record(db, current_user, "ifc.device_type_added", f"Added the IFC device type {code} ({dt.name})",
                    entity_type="ifc_device_type", entity_id=dt.id)
    return _type_out(dt, 0)


@router.patch("/ifc/device-types/{type_id}", response_model=DeviceTypeOut)
def update_device_type(type_id: int, body: DeviceTypeUpdate,
                       _current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    dt = db.get(IfcDeviceType, type_id)
    if not dt:
        raise HTTPException(404, "Device type not found")
    _category(body.category)
    if body.code is not None:
        code = body.code.strip().upper()
        clash = db.query(IfcDeviceType).filter(func.upper(IfcDeviceType.code) == code, IfcDeviceType.id != type_id).first()
        if clash:
            raise HTTPException(409, f"A device type with code {code} already exists")
        dt.code = code
    if body.name is not None:
        dt.name = body.name.strip()
    if body.category is not None:
        dt.category = body.category
    if body.unit is not None:
        dt.unit = body.unit
    if body.sort_order is not None:
        dt.sort_order = body.sort_order
    if body.is_active is not None:
        dt.is_active = body.is_active
    db.commit()
    save_library(db)
    count = db.query(IfcSymbol).filter(IfcSymbol.device_type_id == dt.id).count()
    return _type_out(dt, count)


@router.delete("/ifc/device-types/{type_id}", status_code=204)
def delete_device_type(type_id: int,
                       _current_user: User = Depends(require_role(RoleEnum.admin, RoleEnum.design_manager)),
                       db: Session = Depends(get_db)):
    dt = db.get(IfcDeviceType, type_id)
    if not dt:
        raise HTTPException(404, "Device type not found")
    used = db.query(IfcSymbol).filter(IfcSymbol.device_type_id == dt.id).count()
    if used:
        raise HTTPException(409, f"{used} verified symbol(s) use this device type; reassign them first")
    db.delete(dt)
    db.commit()
    save_library(db)


class UnverifyRequest(BaseModel):
    signatures: list[str] = Field(min_length=1)


@router.post("/ifc/symbols/unverify")
def unverify(body: UnverifyRequest, current_user: User = Depends(require_role(*CREATOR_ROLES)),
             db: Session = Depends(get_db)):
    """Forget verified symbols (by signature) so they come back for review."""
    n = 0
    for sig in body.signatures:
        s = db.query(IfcSymbol).filter(IfcSymbol.signature == sig).first()
        if s is not None:
            db.delete(s)
            n += 1
    db.commit()
    save_library(db)
    if n:
        activity.record(db, current_user, "ifc.symbols_unverified",
                        f"Took {n} symbol{'s' if n != 1 else ''} out of the IFC symbol library",
                        entity_type="ifc_symbol", detail={"signatures": body.signatures[:50]})
    return {"deleted": n}


@router.post("/ifc/reprocess")
def reprocess_drawings(_current_user: User = Depends(require_role(RoleEnum.admin)), db: Session = Depends(get_db)):
    """Re-read every stored drawing with the current extraction rules,
    carrying the engineers' decisions over to symbols whose letters changed."""
    from app.ifc.reprocess import reprocess_all

    rep = reprocess_all(db)
    save_library(db)
    return {
        "drawings": rep.drawings,
        "carried_over": rep.carried_over,
        "weatherproof": rep.weatherproof,
        "left_for_review": rep.left_for_review,
        "errors": rep.errors,
        "totals": rep.totals,
    }


# --- a project's drawings ------------------------------------------------------------------------


class DrawingSummary(BaseModel):
    id: int
    filename: str
    uploaded_at: datetime
    units: str
    seconds: float
    symbol_groups: int
    review_required: int = 0
    totals: dict[str, int]
    archive_path: str | None = None
    revision: str = "R0"
    supersedes_id: int | None = None
    superseded_by: int | None = None
    current: bool = True
    # The fire alarm building quantities by device code, once verified: what
    # a revision changed is read against the revision before it.
    devices: dict[str, int] = {}


_REVISION = re.compile(r"R(?:EV)?\.?\s*0*(\d{1,2})", re.I)


def revision_number(revision: str | None) -> int:
    m = _REVISION.fullmatch((revision or "").strip())
    return int(m.group(1)) if m else -1


def _superseded(db: Session, project_id: int) -> dict[int, ProjectIfcDrawing]:
    """{drawing id: the drawing that supersedes it}."""
    rows = (db.query(ProjectIfcDrawing)
            .filter(ProjectIfcDrawing.project_id == project_id, ProjectIfcDrawing.supersedes_id.isnot(None)).all())
    return {d.supersedes_id: d for d in rows}


def _in_force_named(db: Session, project_id: int, filename: str) -> ProjectIfcDrawing | None:
    """The drawing of this name that nothing has revised yet, if any. What
    makes a file in an archive a revision rather than a new floor."""
    superseded = _superseded(db, project_id)
    rows = (db.query(ProjectIfcDrawing)
            .filter(ProjectIfcDrawing.project_id == project_id,
                    func.lower(ProjectIfcDrawing.filename) == filename.lower())
            .order_by(ProjectIfcDrawing.id.desc()).all())
    return next((d for d in rows if superseded.get(d.id) is None), None)


def _revision_plan(db: Session, project, revision: str | None,
                   supersedes_id: int | None) -> tuple[str, ProjectIfcDrawing | None]:
    """The revision an upload is issued as, and the drawing it replaces.
    A revised drawing replaces the one in force, at a later revision than it;
    a first drawing is whatever revision it was issued at (R0 unless said)."""
    previous = None
    if supersedes_id is not None:
        previous = _drawing(db, project, supersedes_id)
        later = _superseded(db, project.id).get(previous.id)
        if later is not None:
            raise HTTPException(409, f"{previous.filename} {previous.revision} is already revised by "
                                     f"{later.filename} {later.revision}: import the revision of that one.")
    if revision is None or not revision.strip():
        n = revision_number(previous.revision) + 1 if previous is not None else 0
    else:
        n = revision_number(revision)
        if n < 0:
            raise HTTPException(422, f"'{revision}' is not a revision: choose R0, R1, R2 ...")
    if previous is not None and n <= revision_number(previous.revision):
        raise HTTPException(422, f"{previous.filename} is at {previous.revision}: its revised drawing must be a "
                                 f"later revision than that.")
    return f"R{n}", previous


def _drawing(db: Session, project, drawing_id: int) -> ProjectIfcDrawing:
    d = db.get(ProjectIfcDrawing, drawing_id)
    if d is None or d.project_id != project.id:
        raise HTTPException(404, "Drawing not found")
    return d


def _resolved(db: Session, d: ProjectIfcDrawing, **kwargs) -> dict:
    out = resolved_drawing(db, d, **kwargs)
    out["archive_path"] = d.archive_path
    out["filed_note"] = (d.meta or {}).get("filed_note")
    out["revision"] = d.revision or "R0"
    out["supersedes_id"] = d.supersedes_id
    later = _superseded(db, d.project_id).get(d.id)
    out["superseded_by"] = {"id": later.id, "revision": later.revision} if later is not None else None
    out["carried_over"] = (d.meta or {}).get("carried_over")
    return out


@router.get("/projects/{project_id}/ifc-drawings", response_model=list[DrawingSummary])
def list_drawings(project_id: int, _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    out = []
    rows = (db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.project_id == project.id)
            .order_by(ProjectIfcDrawing.uploaded_at.desc(), ProjectIfcDrawing.id.desc()).all())
    later = _superseded(db, project.id)
    for d in rows:
        r = resolved_drawing(db, d, with_occurrences=False)
        ready = r["review"]["required"] == 0
        building = r["floor_boq"]["fire_alarm"]["building"] if ready else []
        out.append(DrawingSummary(
            id=d.id, filename=d.filename, uploaded_at=d.uploaded_at, units=d.units,
            seconds=d.seconds, symbol_groups=len(d.groups or []), totals=r["totals"],
            review_required=r["review"]["required"], archive_path=d.archive_path,
            revision=d.revision or "R0", supersedes_id=d.supersedes_id,
            superseded_by=later[d.id].id if d.id in later else None, current=d.id not in later,
            devices={b["device_type"]["code"]: b["qty"] for b in building},
        ))
    return out


class ReadError(Exception):
    """The drawing could not be read: its message is for the engineer."""


async def _received(file: UploadFile) -> tuple[str, str, bytes]:
    """The uploaded file's name, kind and bytes, refused when it is no drawing."""
    name = Path(file.filename or "drawing.dxf").name
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ("dxf", "dwg"):
        raise HTTPException(415, "Upload a DWG or DXF drawing")
    size = 0
    chunks = []
    while chunk := await file.read(1 << 20):
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{name} is larger than 250 MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if ext == "dwg" and not data.startswith(b"AC10"):
        raise HTTPException(422, "This file is not a DWG drawing (it does not start with an AutoCAD DWG header)")
    return name, ext, data


def _read_drawing(db: Session, project, user: User, name: str, ext: str, data: bytes, *,
                  timer: ReadTimer | None = None, check=None, revision: str = "R0",
                  supersedes: ProjectIfcDrawing | None = None) -> ProjectIfcDrawing:
    """Read a DWG or DXF into the project: a DWG is converted to DXF first,
    then both go through the same extraction; the drawing as uploaded is
    filed in the project's folder, 03- Drawings/IFC/Electrical/FA, once it
    has been read. `timer` hears each stage; `check` is called between
    stages and raises to stop the read, which then leaves nothing behind.
    A revised drawing (`supersedes`) keeps what was decided on the revision
    before it -- a sheet's number of floors, the symbols skipped -- where
    the sheet and the symbol are still on it."""
    def stage(name_: str) -> None:
        if check is not None:
            check()
        if timer is not None:
            timer.begin(name_)

    # The working copy goes under a short name of its own: an IFC sheet's
    # name ("257-Sheet - FA-105 - TYP(1ST TO 14TH) FLOORS FIRE ALARM
    # LAYOUT.dwg") under the uploads folder can pass Windows' 260-character
    # limit, which neither AutoCAD nor ezdxf will open. The name as uploaded
    # is the drawing's `filename`, which is what the floors are read from.
    stage("save")
    folder = storage.project_folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex[:12]
    source = folder / f"{stem}.{ext}"
    source.write_bytes(data)
    dxf_path = folder / f"{stem}.dxf" if ext == "dwg" else source

    def discard() -> None:
        source.unlink(missing_ok=True)
        if dxf_path != source:
            dxf_path.unlink(missing_ok=True)

    try:
        conversion: dict | None = None
        if ext == "dwg":
            stage("convert")
            try:
                res = convert.convert_dwg_to_dxf(source, dxf_path)
            except convert.ConversionError as exc:
                raise ReadError(f"Could not convert the DWG to DXF: {exc}") from exc
            conversion = {"source_format": "dwg", "converter": res.converter, "seconds": round(res.seconds, 1),
                          "dwg_path": storage.relative(source)}
            if timer is not None:
                timer.dxf_size(dxf_path.stat().st_size / 1e6)

        stage("read")

        def progress(part: str, fraction: float) -> None:
            if timer is None:
                return
            if part != timer.stage:
                stage(part)
            timer.at(fraction)

        try:
            result = extract(str(dxf_path), progress=progress, plan=timer.plan if timer is not None else None)
        except ReadError:
            raise
        except Exception as exc:  # ezdxf raises many types for a broken file
            if check is not None:
                check()      # a stop asked for is a stop, not a broken drawing
            raise ReadError(f"Could not read this drawing: {exc}") from exc
        d = result.to_dict()
        meta = {"containers": d["containers"], "skipped_empty_blocks": d["skipped_empty_blocks"],
                "layouts": d["layouts"], "sheets": d["sheets"], "architecture": d["architecture"],
                "loose_symbols": d["loose_symbols"], "conversion": conversion}
        if supersedes is not None:
            before = supersedes.meta or {}
            sheets = {s["name"] for s in d["sheets"] or []} | {MODEL}  # a drawing with no sheets is one plan, "Model"
            signatures = {g["signature"] for g in d["groups"]}
            overrides = {k: v for k, v in (before.get("floor_overrides") or {}).items() if k in sheets}
            skipped = sorted(sig for sig in before.get("review_skipped") or [] if sig in signatures)
            if overrides:
                meta["floor_overrides"] = overrides
            if skipped:
                meta["review_skipped"] = skipped
            meta["carried_over"] = {"from": supersedes.revision, "floor_overrides": len(overrides),
                                    "review_skipped": len(skipped)}

        # Filed only once it has been read, so a file that is not a drawing is
        # not left in the project's folder.
        stage("file")
    except BaseException:
        discard()
        raise
    filed = note = None
    try:
        filed = project_folders.file_ifc_drawing(project, name, data, stamp=datetime.now().strftime("%Y-%m-%d %H%M"))
        if filed is None:
            note = "The drawing was read but not filed: this project's folder is not reachable on this PC."
    except OSError as exc:
        note = f"The drawing was read but could not be filed in {project_folders.IFC_FIRE_ALARM} ({exc})."
    meta["filed_note"] = note

    if supersedes is not None and _superseded(db, project.id).get(supersedes.id) is not None:
        discard()
        raise ReadError(f"{supersedes.filename} {supersedes.revision} was revised by someone else while this was read.")
    drawing = ProjectIfcDrawing(
        project_id=project.id, filename=name, stored_path=storage.relative(dxf_path), archive_path=filed,
        units=d["units"], dxf_version=d["dxf_version"], seconds=d["seconds"], meta=meta, groups=d["groups"],
        created_by_id=user.id, revision=revision, supersedes_id=supersedes.id if supersedes is not None else None,
    )
    db.add(drawing)
    db.commit()
    if timer is not None:
        timer.learned(dwg_mb=len(data) / 1e6 if ext == "dwg" else None, dxf_mb=dxf_path.stat().st_size / 1e6)
    activity.record(db, user, "ifc.drawing_uploaded",
                    f"Read the IFC drawing {name} {revision}"
                    + (f" (revises {supersedes.revision})" if supersedes is not None else "")
                    + f": {len(d['groups'])} distinct symbols"
                    + (f", {d['loose_symbols']} of them drawn without a block" if d["loose_symbols"] else ""),
                    project=project, entity_type="ifc_drawing", entity_id=drawing.id,
                    detail={"file": name, "filed": filed, "converted": bool(conversion), "revision": revision,
                            "supersedes_id": supersedes.id if supersedes is not None else None})
    return drawing


@router.post("/projects/{project_id}/ifc-drawings", status_code=201)
async def upload_drawing(project_id: int, file: UploadFile = File(...), revision: str | None = Form(None),
                         supersedes_id: int | None = Form(None),
                         current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """Read a DWG or DXF within the request, and answer with the drawing.
    The tab reads through /ifc-drawings/jobs instead, to show how far it has got."""
    project = _get_project_or_404(db, project_id)
    name, ext, data = await _received(file)
    rev, previous = _revision_plan(db, project, revision, supersedes_id)
    try:
        drawing = await run_in_threadpool(lambda: _read_drawing(db, project, current_user, name, ext, data,
                                                                revision=rev, supersedes=previous))
    except ReadError as exc:
        raise HTTPException(422, str(exc))
    return _resolved(db, drawing)


@router.post("/projects/{project_id}/ifc-drawings/jobs", status_code=status.HTTP_202_ACCEPTED)
async def start_reading_drawing(project_id: int, file: UploadFile = File(...),
                                revision: str | None = Form(None), supersedes_id: int | None = Form(None),
                                current_user: User = Depends(require_role(*CREATOR_ROLES)),
                                db: Session = Depends(get_db)):
    """Read a DWG or DXF as a job the tab follows (GET /jobs/{id}): its
    progress is a percentage with the stage it is at and the seconds it
    estimates are left ("stage", "eta_seconds"); its result names the
    drawing. POST /jobs/{id}/cancel stops it between stages."""
    from app.models import Project
    from app.routers import jobs as jobs_router
    from app.services import jobs

    project = _get_project_or_404(db, project_id)
    name, ext, data = await _received(file)
    rev, previous = _revision_plan(db, project, revision, supersedes_id)
    previous_id = previous.id if previous is not None else None
    converter = convert.find_converter() if ext == "dwg" else None
    if ext == "dwg" and converter is None:
        raise HTTPException(422, "Could not convert the DWG to DXF: no DWG converter on this PC. Install AutoCAD, or "
                                 "the free ODA File Converter, or upload a DXF instead.")
    project_id_, user_id = project.id, current_user.id

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        timer = ReadTimer.for_upload(
            lambda percent, message, stage, eta: ctx.progress(percent, 100, message, stage=stage, eta_seconds=eta,
                                                               file=name),
            is_dwg=ext == "dwg", size_mb=len(data) / 1e6, converter=converter.name if converter else None,
        )
        timer.begin("save")
        timer.start()
        try:
            drawing = _read_drawing(session, session.get(Project, project_id_), session.get(User, user_id), name, ext,
                                    data, timer=timer, check=ctx.check, revision=rev,
                                    supersedes=session.get(ProjectIfcDrawing, previous_id) if previous_id else None)
        finally:
            timer.stop()
        ctx.progress(100, 100, "Read", stage="done", eta_seconds=0, file=name)
        return {"drawing_id": drawing.id, "filename": name, "revision": rev}

    job = jobs.start(db, kind="ifc_read", project_id=project_id_, user_id=user_id, work=work,
                     run_inline=jobs_router.RUN_INLINE)
    return jobs_router._out(job)


# --- a zip of the whole building -------------------------------------------------
#
# A project's drawings arrive as one archive, a file per floor. Each file
# becomes a drawing of its own, read one after another, so the tab can say
# which floor it is on and a single broken file does not lose the rest.

MAX_MEMBERS = 200
# Checked against the *uncompressed* sizes the archive declares, before
# anything is written: a zip bomb is small until it is opened.
MAX_UNPACKED_BYTES = MAX_BYTES


def _by_floor_then_name(pair: tuple[str, bytes]) -> list:
    """Level 2 before Level 10: the digits in a name count as a number."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", pair[0].lower())]


def _drawings_in_a_zip(data: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    """The DWG and DXF files an archive holds, and what was left out.

    Nothing is written to disk: the members are read into memory one at a
    time, the same way a single upload is. A member is refused rather
    than trusted -- an archive is a list of paths someone else wrote, and
    a path that climbs out of the folder it is unpacked into is the
    oldest trick there is.
    """
    skipped: list[str] = []
    found: list[tuple[str, bytes]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(422, "This file is not a zip archive")
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_MEMBERS:
            raise HTTPException(422, f"The archive holds {len(members)} files; {MAX_MEMBERS} is the most that can be "
                                     f"read at once")
        wanted = []
        unpacked = 0
        for member in members:
            # One normalised spelling, checked once: a path rooted with a
            # backslash is the same path as one rooted with a slash.
            path = member.filename.replace("\\", "/")
            name = PurePosixPath(path).name
            parts = PurePosixPath(path).parts
            ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
            if (path.startswith("/") or ".." in parts or ":" in path
                    or any(part.startswith("__MACOSX") for part in parts) or name.startswith(".")):
                skipped.append(member.filename)
                continue
            if ext not in ("dwg", "dxf"):
                skipped.append(member.filename)
                continue
            unpacked += member.file_size
            if unpacked > MAX_UNPACKED_BYTES:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"The drawings in this archive come to more than "
                                           f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB unpacked")
            wanted.append((member, name, ext))

        for member, name, ext in wanted:
            with archive.open(member) as handle:
                content = handle.read(member.file_size + 1)
            if ext == "dwg" and not content.startswith(b"AC10"):
                # Named .dwg and is not one: the same check a single
                # upload gets, so a renamed file is caught here too.
                skipped.append(member.filename)
                continue
            found.append((name, content))
    if not found:
        raise HTTPException(422, "This archive holds no DWG or DXF drawings")
    # Only the file name is kept, so two floors filed under different
    # folders but the same name would land on one drawing -- the second
    # superseding the first inside a single import, quietly. Say so
    # instead: the archive is the one that needs fixing.
    seen: set[str] = set()
    for name, _ in found:
        if name.lower() in seen:
            raise HTTPException(422, f"Two files in the archive are both called {name}. Give each floor a name of "
                                     f"its own -- the folders they sit in are not kept.")
        seen.add(name.lower())
    # Read in the order the floors are named, so the tab walks the
    # building rather than the archive's own ordering, counting the
    # numbers in a name so that Level 2 comes before Level 10.
    found.sort(key=_by_floor_then_name)
    return found, skipped


async def _received_zip(file: UploadFile) -> tuple[str, bytes]:
    name = Path(file.filename or "drawings.zip").name
    if not name.lower().endswith(".zip"):
        raise HTTPException(415, "Upload a zip archive of DWG or DXF drawings")
    size = 0
    chunks = []
    while chunk := await file.read(1 << 20):
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{name} is larger than 250 MB")
        chunks.append(chunk)
    return name, b"".join(chunks)


@router.post("/projects/{project_id}/ifc-drawings/zip/jobs", status_code=status.HTTP_202_ACCEPTED)
async def start_reading_zip(project_id: int, file: UploadFile = File(...),
                            current_user: User = Depends(require_role(*CREATOR_ROLES)),
                            db: Session = Depends(get_db)):
    """Read a zip of the building's drawings, a floor at a time.

    Each file in the archive becomes a drawing of its own. A file whose
    name matches a drawing already in force revises that one; every other
    file is a new drawing at R0 -- an archive is the building, not a
    revision of one drawing, so there is no single revision to issue it
    at.

    Floors still come from the sheets inside each drawing (and the floors
    endpoint corrects them). Nothing here reads a floor off a file name.
    """
    from app.models import Project
    from app.routers import jobs as jobs_router
    from app.services import jobs

    project = _get_project_or_404(db, project_id)
    archive_name, data = await _received_zip(file)
    drawings, skipped = _drawings_in_a_zip(data)

    # Once, before the loop: twelve conversions that cannot work should
    # fail now and not after reading nothing.
    if any(name.lower().endswith(".dwg") for name, _ in drawings) and convert.find_converter() is None:
        raise HTTPException(422, "Could not convert the DWGs to DXF: no DWG converter on this PC. Install AutoCAD, or "
                                 "the free ODA File Converter, or put DXF files in the archive instead.")
    project_id_, user_id = project.id, current_user.id
    total = len(drawings)

    def work(session: Session, ctx: jobs.JobContext) -> dict:
        read: list[dict] = []
        failed: list[dict] = []
        for index, (name, content) in enumerate(drawings):
            ctx.check()
            ctx.progress(index, total, f"Reading {name}", stage="read", file=name)
            ext = name.lower().rsplit(".", 1)[-1]
            # Per file: one that revises a drawing already in force takes
            # that drawing's next revision; the rest are new at R0.
            previous = _in_force_named(session, project_id_, name)
            revision = f"R{revision_number(previous.revision) + 1}" if previous is not None else "R0"
            try:
                drawing = _read_drawing(session, session.get(Project, project_id_), session.get(User, user_id),
                                        name, ext, content, revision=revision, supersedes=previous)
                read.append({"drawing_id": drawing.id, "filename": name, "revision": revision})
            except ReadError as exc:
                # One unreadable file does not lose the other eleven.
                failed.append({"filename": name, "reason": str(exc)})
        ctx.progress(total, total, "Read", stage="done", eta_seconds=0)
        return {"archive": archive_name, "read": read, "failed": failed, "skipped": skipped,
                "drawings": len(read)}

    job = jobs.start(db, kind="ifc_read_zip", project_id=project_id_, user_id=user_id, work=work,
                     run_inline=jobs_router.RUN_INLINE)
    return jobs_router._out(job)


@router.get("/projects/{project_id}/ifc-drawings/{drawing_id}")
def get_drawing(project_id: int, drawing_id: int, _current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    return _resolved(db, _drawing(db, project, drawing_id))


@router.delete("/projects/{project_id}/ifc-drawings/{drawing_id}", status_code=204)
def delete_drawing(project_id: int, drawing_id: int, current_user: User = Depends(require_role(*CREATOR_ROLES)),
                   db: Session = Depends(get_db)):
    """The drawing and the platform's working copy of it. The copy filed in
    the project's folder stays -- it is the project's document -- and so do
    the symbols verified on it, in the library."""
    project = _get_project_or_404(db, project_id)
    d = _drawing(db, project, drawing_id)
    conv = (d.meta or {}).get("conversion") or {}
    for p in (d.stored_path, conv.get("dwg_path")):
        if p:
            try:
                storage.absolute(p).unlink(missing_ok=True)
            except OSError:
                pass
    name = d.filename
    for later in db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.supersedes_id == d.id).all():
        later.supersedes_id = d.supersedes_id
    db.flush()
    db.delete(d)
    db.commit()
    activity.record(db, current_user, "ifc.drawing_deleted", f"Removed the IFC drawing {name}",
                    project=project, entity_type="ifc_drawing", entity_id=drawing_id)


class FloorOverride(BaseModel):
    sheet: str
    multiplier: int | None = Field(default=None, ge=0, le=500)


@router.put("/projects/{project_id}/ifc-drawings/{drawing_id}/floors")
def set_floor_multiplier(project_id: int, drawing_id: int, body: FloorOverride,
                         _current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """How many floors a sheet stands for, when the title's reading is wrong.
    multiplier null goes back to the title's reading."""
    project = _get_project_or_404(db, project_id)
    d = _drawing(db, project, drawing_id)
    meta = dict(d.meta or {})
    ov = dict(meta.get("floor_overrides") or {})
    if body.multiplier is None:
        ov.pop(body.sheet, None)
    else:
        ov[body.sheet] = body.multiplier
    meta["floor_overrides"] = ov
    d.meta = meta
    db.commit()
    return _resolved(db, d)


class VerifyRequest(BaseModel):
    """Map one or more symbol groups of the drawing to a device type, or mark
    them as not a device. Block names are remembered as aliases."""
    signatures: list[str] = Field(min_length=1)
    device_type_id: int | None = None
    ignore: bool = False
    notes: str = ""


@router.post("/projects/{project_id}/ifc-drawings/{drawing_id}/verify")
def verify(project_id: int, drawing_id: int, body: VerifyRequest,
           current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """Map the given symbol groups of a drawing to a device type, or mark
    them as not a device. Returns the drawing re-resolved."""
    project = _get_project_or_404(db, project_id)
    drawing = _drawing(db, project, drawing_id)
    if not body.ignore:
        if body.device_type_id is None:
            raise HTTPException(422, "Choose a device type, or mark the symbol as not a device")
        if db.get(IfcDeviceType, body.device_type_id) is None:
            raise HTTPException(404, "Device type not found")
    groups = {g["signature"]: g for g in drawing.groups or []}
    missing = [s for s in body.signatures if s not in groups]
    if missing:
        raise HTTPException(404, f"Symbol(s) not in this drawing: {', '.join(missing[:5])}")
    for sig in body.signatures:
        _remember(db, drawing, groups[sig], None if body.ignore else body.device_type_id, body.notes,
                  user=current_user)
    db.commit()
    save_library(db)
    return _resolved(db, drawing)


def _remember(db: Session, drawing: ProjectIfcDrawing, g: dict, device_type_id: int | None, notes: str = "",
              *, user: User | None = None) -> IfcSymbol:
    """Put one symbol group's exact drawing in the library: as the device
    type, or (device_type_id None) as not a device. Its block names become
    aliases."""
    sig = g["signature"]
    s = db.query(IfcSymbol).filter(IfcSymbol.signature == sig).first()
    if s is None:
        s = IfcSymbol(signature=sig, source_drawing=drawing.filename, created_by_id=user.id if user else None)
        db.add(s)
    s.label = g.get("label", "")
    s.inner_label = g.get("inner_label", "")
    s.raster_hex = g.get("raster_hex", "")
    s.svg = g.get("svg", "")
    s.entity_counts = g.get("entity_counts", {})
    s.block_names = sorted(set(list(s.block_names or []) + list(g.get("block_names", {}).keys())))
    s.is_ignored = device_type_id is None
    s.device_type_id = device_type_id
    if notes:
        s.notes = notes
    db.flush()
    for name in g.get("block_names", {}):
        if name == LOOSE_NAME:
            continue  # every symbol drawn without a block shares it: not a name to know one by
        key = name.upper()
        alias = db.query(IfcBlockAlias).filter(IfcBlockAlias.block_name == key).first()
        if alias is None:
            db.add(IfcBlockAlias(block_name=key, symbol_id=s.id))
        else:
            alias.symbol_id = s.id
    return s


class ReviewAnswer(BaseModel):
    """The engineer's answer for one symbol: a device type, not a device, or
    skip (leave it out of the BOQ without teaching the library). No answer
    at all takes back an earlier skip."""
    signature: str
    device_type_id: int | None = None
    ignore: bool = False
    skip: bool = False


class ReviewSave(BaseModel):
    answers: list[ReviewAnswer] = Field(min_length=1)


@router.post("/projects/{project_id}/ifc-drawings/{drawing_id}/review")
def save_review(project_id: int, drawing_id: int, body: ReviewSave,
                current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """Save the engineer's answers for a drawing's symbols in one go: devices
    and 'not a device' go to the library (and its file), skips are
    remembered on the drawing only. Returns the drawing re-resolved: once
    every symbol on the floor plans is answered, its quantities are given."""
    project = _get_project_or_404(db, project_id)
    drawing = _drawing(db, project, drawing_id)
    groups = {g["signature"]: g for g in drawing.groups or []}
    missing = [a.signature for a in body.answers if a.signature not in groups]
    if missing:
        raise HTTPException(404, f"Symbol(s) not in this drawing: {', '.join(missing[:5])}")
    for a in body.answers:
        if (a.device_type_id is not None) + a.ignore + a.skip > 1:
            raise HTTPException(422, "Each answer is one of: a device type, not a device, or skip")
    type_ids = {a.device_type_id for a in body.answers if a.device_type_id is not None}
    known = {t.id for t in db.query(IfcDeviceType).filter(IfcDeviceType.id.in_(type_ids)).all()} if type_ids else set()
    if type_ids - known:
        raise HTTPException(404, "Device type not found")

    meta = dict(drawing.meta or {})
    skipped = set(meta.get("review_skipped") or [])
    for a in body.answers:
        if a.skip:
            skipped.add(a.signature)
            continue
        skipped.discard(a.signature)
        if a.device_type_id is not None or a.ignore:
            _remember(db, drawing, groups[a.signature], a.device_type_id, user=current_user)
    meta["review_skipped"] = sorted(skipped)
    drawing.meta = meta
    db.commit()
    save_library(db)
    out = _resolved(db, drawing)
    if out["review"]["ready"] and not (drawing.meta or {}).get("verified_at"):
        meta = dict(drawing.meta or {})
        meta["verified_at"] = datetime.now().isoformat(timespec="seconds")
        drawing.meta = meta
        db.commit()
        activity.record(db, current_user, "ifc.drawing_verified",
                        f"Verified every symbol on {drawing.filename}: {out['totals']['fire_alarm']} fire alarm devices",
                        project=project, entity_type="ifc_drawing", entity_id=drawing.id)
    return out


@router.get("/projects/{project_id}/ifc-drawings/{drawing_id}/export")
def export_drawing(project_id: int, drawing_id: int, _current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    from app.ifc.export import workbook

    project = _get_project_or_404(db, project_id)
    d = _drawing(db, project, drawing_id)
    r = resolved_drawing(db, d)
    if not r["review"]["ready"]:
        n = r["review"]["required"]
        raise HTTPException(409, f"Verify the drawing's symbols first: {n} symbol{'s' if n != 1 else ''} on the floor plans "
                                 "still need an answer. Quantities are given once they are saved to the library.")
    buf = workbook(d, r)
    base = re.sub(r"\.(dxf|dwg)$", "", d.filename, flags=re.I)
    fname = f"EP-{project.ep_number} " + _SAFE.sub("_", base)[:80] + " - BOQ as per IFC.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/projects/{project_id}/ifc-comparison")
def comparison(project_id: int, _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """BOQ Floor Wise beside BOQ as per IFC Drawings, fire alarm, device by
    device and floor by floor (`app.ifc.comparison`). The IFC side is every
    drawing in force -- its latest revision -- whose symbols are all
    answered; one still being answered is named and left out."""
    from app.routers.floor_schedule import sync_from_folder

    project = _get_project_or_404(db, project_id)
    schedule_row, note = sync_from_folder(db, project)
    schedule = schedule_row.result if schedule_row is not None and schedule_row.result else None
    later = _superseded(db, project.id)
    in_force, pending = [], []
    for d in (db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.project_id == project.id)
              .order_by(ProjectIfcDrawing.uploaded_at, ProjectIfcDrawing.id).all()):
        if d.id in later:
            continue
        r = resolved_drawing(db, d, with_occurrences=False)
        summary = {"id": d.id, "filename": d.filename, "revision": d.revision or "R0",
                   "uploaded_at": d.uploaded_at.isoformat(), "review_required": r["review"]["required"]}
        if r["review"]["required"]:
            pending.append(summary)
        else:
            in_force.append({**r, **summary})
    out = compare(schedule, in_force)
    out["schedule"] = ({"file": schedule_row.file_name, "sheet": schedule_row.sheet_name,
                        "stated_grand_total": schedule.get("stated_grand_total")}
                       if schedule is not None else None)
    out["schedule_note"] = note
    out["drawings"] = [{k: d[k] for k in ("id", "filename", "revision", "uploaded_at")} for d in in_force]
    out["pending"] = pending
    return out
