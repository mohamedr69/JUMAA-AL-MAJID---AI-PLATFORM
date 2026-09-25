"""The BOQ page's "As per IFC Drawings" tab: fire alarm devices counted off
an issued-for-construction drawing (app/ifc).

  GET    /ifc/capabilities                            DXF always; DWG when a converter is on this PC; the upload limit
  GET    /ifc/device-types                            what a symbol can be verified as
  POST   /ifc/device-types                            add one, for good
  PATCH  /ifc/device-types/{id}                       rename, re-enable
  DELETE /ifc/device-types/{id}                       only when no verified symbol uses it
  POST   /ifc/symbols/unverify                        forget verified symbols: they are asked again
  POST   /ifc/reprocess                               re-read every stored drawing, in the request (admin; no AI)
  POST   /ifc/reprocess/jobs                          ... as a job the IFC worker runs (admin)
  GET    /ifc/ai-metrics                              what the AI symbol review costs and how often it is right (admin)

  GET    /projects/{id}/ifc-drawings                  the project's drawings
  POST   /projects/{id}/ifc-drawings                  upload a DWG or DXF, read within the request (tests, scripts)
  POST   /projects/{id}/ifc-drawings/jobs             ... queued for the IFC worker: HTTP 202 and the job to follow
  POST   /projects/{id}/ifc-drawings/zip/jobs         a zip of the building, a floor at a time, as one job
  GET    /projects/{id}/ifc-drawings/{did}            the drawing, resolved against the library
  DELETE /projects/{id}/ifc-drawings/{did}            a mistaken import removed; a revision archived
  PUT    /projects/{id}/ifc-drawings/{did}/floors     how many floors a sheet stands for
  POST   /projects/{id}/ifc-drawings/{did}/verify     map symbols to a device, or not a device
  POST   /projects/{id}/ifc-drawings/{did}/review     save answers: devices / not a device / skip
  GET    /projects/{id}/ifc-drawings/{did}/export     the BOQ as Excel, once every symbol is answered

Verify first, then quantities: no quantity is given, and nothing is
exported, while a symbol on the floor plans is still to be answered. Every
answer goes to the symbol library the moment it is given, so the next
drawing -- on any project -- recognises it.

The router checks and answers; the reading is in app/ifc/services and runs
in the IFC worker (app.workers.ifc_worker), never in the request that
uploaded the file.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.database import SessionLocal, get_db
from app.deps import get_current_user, require_role
from app.ifc import hints, storage
from app.ifc.comparison import compare
from app.ifc.dxf import convert
from app.ifc.library_file import save_library
from app.ifc.resolve import resolved_drawing
from app.ifc.services import library, processing, revisions, runners, upload, zip_import
from app.ifc.services.processing import ReadError
from app.ifc.services.revisions import revision_number  # noqa: F401 -- part of this module's interface
from app.models import IfcDeviceType, IfcSymbol, IfcSymbolReview, ProjectIfcDrawing, RoleEnum, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, jobs, shop_drawings

router = APIRouter(tags=["BOQ as per IFC"])

# The one upload limit (IFC_MAX_UPLOAD_MB), and the zip's, as the rest of the platform imports them.
MAX_BYTES = upload.MAX_BYTES
MAX_MEMBERS = zip_import.MAX_MEMBERS
MAX_UNPACKED_BYTES = zip_import.MAX_UNPACKED_BYTES
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


def _required_text(value: str | None, what: str) -> str | None:
    """Stripped; a value of nothing but spaces is no value."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError(f"{what} cannot be blank")
    return value


class DeviceTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    category: str
    unit: str = Field(default="Nos", max_length=10)
    sort_order: int = 500

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        return _required_text(v, "A device type's code").upper()

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _required_text(v, "A device type's name")

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        return v.strip() or "Nos"


class DeviceTypeUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    unit: str | None = Field(default=None, max_length=10)
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        v = _required_text(v, "A device type's code")
        return v.upper() if v is not None else None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _required_text(v, "A device type's name")

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str | None) -> str | None:
        return _required_text(v, "A device type's unit")


def _category(value: str | None) -> None:
    if value is not None and value not in CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"category is one of {', '.join(CATEGORIES)}")


def _type_out(dt: IfcDeviceType, count: int) -> DeviceTypeOut:
    return DeviceTypeOut(id=dt.id, code=dt.code, name=dt.name, category=dt.category, unit=dt.unit,
                         sort_order=dt.sort_order, is_active=dt.is_active, symbol_count=count,
                         family=hints.family_of_type(dt.code, dt.name))


@router.get("/ifc/capabilities")
def capabilities(_current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What the upload accepts on this PC: DWG needs a converter. The limit
    is the server's own, so the page checks a file against the same number."""
    from app.ifc.services import ai_symbol_review

    conv = convert.find_converter()
    ai_on, ai_why = ai_symbol_review.enabled()
    return {"dxf": True, "dwg": conv is not None, "dwg_converter": conv.name if conv else None,
            "max_upload_mb": upload.limit_mb(), "max_zip_members": zip_import.MAX_MEMBERS,
            "worker_running": jobs.worker_running(db, lane="ifc"),
            "ai_symbol_review": ai_on, "ai_symbol_review_note": ai_why}


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
    code = body.code
    if db.query(IfcDeviceType).filter(func.upper(IfcDeviceType.code) == code).first():
        raise HTTPException(409, f"A device type with code {code} already exists")
    dt = IfcDeviceType(code=code, name=body.name, category=body.category, unit=body.unit, sort_order=body.sort_order)
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
        clash = (db.query(IfcDeviceType)
                 .filter(func.upper(IfcDeviceType.code) == body.code, IfcDeviceType.id != type_id).first())
        if clash:
            raise HTTPException(409, f"A device type with code {body.code} already exists")
        dt.code = body.code
    if body.name is not None:
        dt.name = body.name
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
    """Forget verified symbols (by signature) so they come back for review.
    What the AI said about them is forgotten too: it is not reused."""
    n = 0
    for sig in body.signatures:
        s = db.query(IfcSymbol).filter(IfcSymbol.signature == sig).first()
        if s is not None:
            db.delete(s)
            n += 1
    library.invalidate(db, body.signatures)
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
    carrying the engineers' decisions over to symbols whose letters changed.
    Answered in the request, so without the AI: /ifc/reprocess/jobs runs it
    in the IFC worker, with it."""
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


@router.post("/ifc/reprocess/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_reprocess(current_user: User = Depends(require_role(RoleEnum.admin)), db: Session = Depends(get_db)):
    """Re-read every stored drawing as a job of the IFC worker."""
    job, created = jobs.enqueue(db, kind=runners.REPROCESS, project_id=None, user_id=current_user.id,
                                dedup_key="ifc_reprocess:all", params={"user_id": current_user.id},
                                progress={"stage": "waiting_for_worker"})
    return _started(db, job, created)


@router.get("/ifc/ai-metrics")
def ai_metrics(days: int = 30, _current_user: User = Depends(require_role(RoleEnum.admin)),
               db: Session = Depends(get_db)):
    """What the AI symbol review has cost and how often engineers kept its
    answers: for tuning, not for the engineers' pages."""
    from datetime import timedelta

    from app.ai import metrics
    from app.ifc.services import ai_symbol_review

    since = utc_now() - timedelta(days=days)
    rows = db.query(IfcSymbolReview).filter(IfcSymbolReview.created_at >= since).all()
    stages: dict[str, dict] = {}
    for r in rows:
        s = stages.setdefault(r.stage, {"reviews": 0, "accepted": 0, "uncertain": 0, "rejected": 0, "errors": 0,
                                        "input_tokens": 0, "output_tokens": 0, "approved": 0, "corrected": 0})
        s["reviews"] += 1
        s[{"accepted": "accepted", "uncertain": "uncertain", "rejected": "rejected"}.get(r.validation, "errors")] += 1
        s["input_tokens"] += r.input_tokens or 0
        s["output_tokens"] += r.output_tokens or 0
        if r.outcome in ("approved", "corrected"):
            s[r.outcome] += 1
    signatures = {r.signature for r in rows if r.error is None}
    tokens = sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in rows)
    usage = [u for u in metrics.usage_metrics(db, since=since)
             if u["task"] in (ai_symbol_review.TASK_METADATA, ai_symbol_review.TASK_VISUAL)]
    return {"since": since, "stages": stages, "unique_signatures": len(signatures),
            "tokens_per_new_signature": round(tokens / len(signatures), 1) if signatures else None,
            "usage": usage, "prompt_version": ai_symbol_review.PROMPT_VERSION}


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
    # The drawing's identity, whatever its file is called ("FA-101").
    reference: str | None = None
    # "verified" once every symbol on the plans is answered; else "review_required"
    boq_status: str = "review_required"
    total_occurrences: int = 0
    floor_name: str | None = None


def _superseded(db: Session, project_id: int) -> dict[int, ProjectIfcDrawing]:
    """{drawing id: the live drawing that supersedes it} (app.ifc.services.revisions)."""
    return revisions.superseded(db, project_id)


def _drawing(db: Session, project, drawing_id: int) -> ProjectIfcDrawing:
    d = db.get(ProjectIfcDrawing, drawing_id)
    if d is None or d.project_id != project.id or d.deleted_at is not None:
        raise HTTPException(404, "Drawing not found")
    return d


def _resolved(db: Session, d: ProjectIfcDrawing, **kwargs) -> dict:
    out = resolved_drawing(db, d, **kwargs)
    out["archive_path"] = d.archive_path
    out["filed_note"] = (d.meta or {}).get("filed_note")
    out["revision"] = d.revision or "R0"
    out["supersedes_id"] = d.supersedes_id
    out["reference"] = d.drawing_reference
    later = _superseded(db, d.project_id).get(d.id)
    out["superseded_by"] = {"id": later.id, "revision": later.revision} if later is not None else None
    out["carried_over"] = (d.meta or {}).get("carried_over")
    return out


@router.get("/projects/{project_id}/ifc-drawings", response_model=list[DrawingSummary])
def list_drawings(project_id: int, _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    out = []
    rows = revisions.live(db, project.id).order_by(ProjectIfcDrawing.uploaded_at.desc(), ProjectIfcDrawing.id.desc()).all()
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
            reference=d.drawing_reference, boq_status=r["analysis"]["boq_status"],
            total_occurrences=r["analysis"]["total_occurrences"], floor_name=r["floor_info"]["floor_name"],
        ))
    return out


def _identity_http(exc: revisions.IdentityError) -> HTTPException:
    return HTTPException(exc.status, detail=exc.detail)


def _read_drawing(db: Session, project, user: User, name: str, ext: str, data: bytes, *,
                  timer=None, check=None, revision: str = "R0",
                  supersedes: ProjectIfcDrawing | None = None) -> ProjectIfcDrawing:
    """A drawing's bytes read into the project (scripts and tests; the pages
    upload through the worker). The bytes are staged as an upload is."""
    import hashlib
    import uuid

    folder = upload.staging_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.{ext}"
    path.write_bytes(data)
    try:
        ref = supersedes.drawing_reference if supersedes is not None and supersedes.drawing_reference else \
            revisions.reference(name)
        plan = revisions.Plan(revision=revision, supersedes=supersedes, reference=ref)
        return processing.read_drawing(db, project, user, source=path, name=name, ext=ext,
                                       sha256=hashlib.sha256(data).hexdigest(), plan=plan, timer=timer, check=check,
                                       use_ai=False)
    finally:
        upload.discard(path)


@router.post("/projects/{project_id}/ifc-drawings", status_code=201)
async def upload_drawing(project_id: int, file: UploadFile = File(...), revision: str | None = Form(None),
                         supersedes_id: int | None = Form(None), confirm_new: bool = Form(True),
                         current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """Read a DWG or DXF within the request, and answer with the drawing:
    for scripts and tests -- the original tool's contract, kept as it was
    (a file not said to revise a drawing is a new drawing, `confirm_new`;
    the same file may be read again). The tab reads through
    /ifc-drawings/jobs, which the IFC worker runs, and which refuses both."""
    project = _get_project_or_404(db, project_id)
    staged = await upload.stream_to_staging(file, allowed=("dxf", "dwg"), default_name="drawing.dxf")
    try:
        try:
            plan = revisions.plan(db, project.id, filename=staged.name, sha256=staged.sha256, revision=revision,
                                  supersedes_id=supersedes_id, confirm_new=confirm_new, refuse_duplicate=False)
        except revisions.IdentityError as exc:
            raise _identity_http(exc)
        project_id_, user_id, supersedes = project.id, current_user.id, plan.supersedes.id if plan.supersedes else None

        def work() -> int:
            # A session of the thread's own: the request's is never shared with another thread.
            session = SessionLocal()
            try:
                p = revisions.Plan(revision=plan.revision, reference=plan.reference,
                                   supersedes=session.get(ProjectIfcDrawing, supersedes) if supersedes else None)
                drawing = processing.read_drawing(session, session.get(type(project), project_id_),
                                                  session.get(User, user_id), source=staged.path, name=staged.name,
                                                  ext=staged.ext, sha256=staged.sha256, plan=p, use_ai=False)
                return drawing.id
            finally:
                session.close()

        try:
            drawing_id = await run_in_threadpool(work)
        except ReadError as exc:
            raise HTTPException(422, str(exc))
        except revisions.RevisionConflict as exc:
            raise HTTPException(409, str(exc))
    finally:
        upload.discard(staged.path)
    db.expire_all()
    return _resolved(db, db.get(ProjectIfcDrawing, drawing_id))


def _started(db: Session, job, created: bool):
    """The job as the page follows it; `already_active` when this request
    found the same work already queued or running."""
    from app.routers import jobs as jobs_router

    out = jobs_router._out(job)
    out.already_active = not created
    return out


def _queue_note(db: Session) -> dict:
    """What a queued read tells the page while it waits."""
    if jobs.worker_running(db, lane="ifc"):
        return {"message": "Queued: waiting for the IFC worker", "stage": "waiting_for_worker"}
    return {"message": "Queued: the IFC worker is not running on the server. Start it (start.bat) and the read "
                       "begins.", "stage": "waiting_for_worker"}


def _run_inline(job_id: int) -> None:
    """Tests: the worker's own code runs the job before the response returns."""
    from app.workers.ifc_worker import IfcWorker

    IfcWorker(background_reading=False).run_claimed(job_id)


@router.post("/projects/{project_id}/ifc-drawings/jobs", status_code=status.HTTP_202_ACCEPTED)
async def start_reading_drawing(project_id: int, file: UploadFile = File(...),
                                revision: str | None = Form(None), supersedes_id: int | None = Form(None),
                                confirm_new: bool = Form(False),
                                current_user: User = Depends(require_role(*CREATOR_ROLES)),
                                db: Session = Depends(get_db)):
    """Queue a DWG or DXF for the IFC worker and answer at once (HTTP 202)
    with the job the tab follows (GET /jobs/{id}): its progress is a
    percentage with the stage it is at and the seconds it estimates are
    left ("stage", "eta_seconds"); its result names the drawing.
    POST /jobs/{id}/cancel stops it between stages.

    Refused before a job is queued: a file that is not a drawing, one over
    the limit, a DWG with no converter on this PC, the same file already
    imported, and -- unless `confirm_new` -- a file that may be a revision
    of a drawing in force that it was not said to revise (409
    "revision_confirmation_required"). The same file sent twice for the
    same drawing and revision while the first is still queued or running
    returns that job (`already_active`)."""
    from app.routers import jobs as jobs_router

    project = _get_project_or_404(db, project_id)
    staged = await upload.stream_to_staging(file, allowed=("dxf", "dwg"), default_name="drawing.dxf")
    queued = False
    try:
        if staged.ext == "dwg" and convert.find_converter() is None:
            raise HTTPException(422, "The DWG converter is not available on this server. Install AutoCAD, or the "
                                     "free ODA File Converter, or upload a DXF instead.")
        key_revision = (revision or "").strip() or "auto"
        key = runners.dedup_key(runners.READ, project.id, staged.sha256, str(supersedes_id or "new"), key_revision)
        existing = jobs.active_by_key(db, key)
        if existing is not None:
            return _started(db, existing, False)
        try:
            plan = revisions.plan(db, project.id, filename=staged.name, sha256=staged.sha256, revision=revision,
                                  supersedes_id=supersedes_id, confirm_new=confirm_new)
        except revisions.IdentityError as exc:
            raise _identity_http(exc)
        params = {"staged_path": str(staged.path), "name": staged.name, "ext": staged.ext, "size": staged.size,
                  "sha256": staged.sha256, "revision": plan.revision, "reference": plan.reference,
                  "supersedes_id": plan.supersedes.id if plan.supersedes is not None else None,
                  "user_id": current_user.id}
        job, created = jobs.enqueue(db, kind=runners.READ, project_id=project.id, user_id=current_user.id,
                                    dedup_key=key, params=params,
                                    progress={**_queue_note(db), "file": staged.name}, message="")
        queued = created
        if not created:
            return _started(db, job, False)
    finally:
        if not queued:
            upload.discard(staged.path)
    if jobs_router.RUN_INLINE:
        _run_inline(job.id)
        db.expire_all()
        job = db.get(type(job), job.id)
    return _started(db, job, True)


@router.post("/projects/{project_id}/ifc-drawings/zip/jobs", status_code=status.HTTP_202_ACCEPTED)
async def start_reading_zip(project_id: int, file: UploadFile = File(...),
                            current_user: User = Depends(require_role(*CREATOR_ROLES)),
                            db: Session = Depends(get_db)):
    """Queue a zip of the building's drawings for the IFC worker, read a
    floor at a time (app.ifc.services.zip_import).

    Each drawing in the archive becomes a drawing of its own, or the
    revision of one in force when its name states a later revision of the
    same drawing; one that matches a drawing in force without saying so is
    listed for the engineer to confirm, never imported on a guess. The same
    file as a drawing already imported is left unchanged. One unreadable
    floor is reported and the rest are read.

    Floors still come from the sheets inside each drawing (and the floors
    endpoint corrects them). Nothing here reads a floor off a file name."""
    from app.routers import jobs as jobs_router

    project = _get_project_or_404(db, project_id)
    staged = await upload.stream_to_staging(file, allowed=("zip",), default_name="drawings.zip")
    queued = False
    try:
        key = runners.dedup_key(runners.READ_ZIP, project.id, staged.sha256)
        existing = jobs.active_by_key(db, key)
        if existing is not None:
            return _started(db, existing, False)
        found, skipped = await run_in_threadpool(zip_import.members, staged.path)
        # Once, before the job: twelve conversions that cannot work should
        # fail now and not after reading nothing.
        if any(upload.extension(name) == "dwg" for name, _ in found) and convert.find_converter() is None:
            raise HTTPException(422, "The DWG converter is not available on this server. Install AutoCAD, or the free "
                                     "ODA File Converter, or put DXF files in the archive instead.")
        params = {"staged_path": str(staged.path), "name": staged.name, "size": staged.size, "sha256": staged.sha256,
                  "skipped": skipped, "members": [name for name, _ in found], "user_id": current_user.id}
        job, created = jobs.enqueue(db, kind=runners.READ_ZIP, project_id=project.id, user_id=current_user.id,
                                    dedup_key=key, params=params, progress=_queue_note(db), message="")
        queued = created
        if not created:
            return _started(db, job, False)
    finally:
        if not queued:
            upload.discard(staged.path)
    if jobs_router.RUN_INLINE:
        _run_inline(job.id)
        db.expire_all()
        job = db.get(type(job), job.id)
    return _started(db, job, True)


def _drawings_in_a_zip(source) -> tuple[list[tuple[str, object]], list[str]]:
    """The drawings an archive holds, in floor order, and what was left out
    (app.ifc.services.zip_import.members)."""
    return zip_import.members(source)


@router.get("/projects/{project_id}/ifc-drawings/{drawing_id}")
def get_drawing(project_id: int, drawing_id: int, _current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    return _resolved(db, _drawing(db, project, drawing_id))


@router.delete("/projects/{project_id}/ifc-drawings/{drawing_id}", status_code=204)
def delete_drawing(project_id: int, drawing_id: int, current_user: User = Depends(require_role(*CREATOR_ROLES)),
                   db: Session = Depends(get_db)):
    """A drawing imported by mistake -- nothing revises it and it revises
    nothing -- is removed with the platform's working copy of it. The
    revision in force of a chain is archived instead: kept for the history,
    out of every list and BOQ, and the revision before it is in force
    again. A revision a later one depends on is not deleted. The copy
    filed in the project's folder always stays (it is the project's
    document), and so do the symbols verified on it, in the library."""
    project = _get_project_or_404(db, project_id)
    d = _drawing(db, project, drawing_id)
    try:
        rule = revisions.delete_rule(db, d)
    except revisions.IdentityError as exc:
        raise _identity_http(exc)
    name, revision = d.filename, d.revision
    if rule == "archive":
        d.deleted_at, d.deleted_by_id = utc_now(), current_user.id
        db.commit()
        activity.record(db, current_user, "ifc.drawing_archived",
                        f"Withdrew the IFC drawing {name} {revision}: the revision before it is in force again",
                        project=project, entity_type="ifc_drawing", entity_id=drawing_id)
        shop_drawings.floors_changed(db, project)
        return
    conv = (d.meta or {}).get("conversion") or {}
    paths = [p for p in (d.stored_path, conv.get("dwg_path")) if p]
    db.delete(d)
    db.commit()
    for p in paths:
        try:
            storage.absolute(p).unlink(missing_ok=True)
        except OSError:
            pass
    activity.record(db, current_user, "ifc.drawing_deleted", f"Removed the IFC drawing {name}",
                    project=project, entity_type="ifc_drawing", entity_id=drawing_id)
    shop_drawings.floors_changed(db, project)


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
    shop_drawings.floors_changed(db, project)     # a plan's floors changed: the Drawings page's registry follows
    return _resolved(db, d)


class VerifyRequest(BaseModel):
    """Map one or more symbol groups of the drawing to a device type, or mark
    them as not a device -- one or the other, never both. Block names are
    remembered as aliases."""
    signatures: list[str] = Field(min_length=1)
    device_type_id: int | None = None
    ignore: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _one_decision(self) -> "VerifyRequest":
        if self.ignore and self.device_type_id is not None:
            raise ValueError("Choose a device type or mark the symbol as not a device, not both")
        return self


@router.post("/projects/{project_id}/ifc-drawings/{drawing_id}/verify")
def verify(project_id: int, drawing_id: int, body: VerifyRequest,
           current_user: User = Depends(require_role(*CREATOR_ROLES)), db: Session = Depends(get_db)):
    """Map the given symbol groups of a drawing to a device type, or mark
    them as not a device. The engineer's answer: it overrides the rules' and
    the AI's, for good. Returns the drawing re-resolved."""
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
    """An engineer's answer for one symbol group, into the library
    (app.ifc.services.library) and onto the AI's review of it, if it had one."""
    s = library.remember(db, g, device_type_id, source=library.ENGINEER, drawing_name=drawing.filename,
                         user=user, notes=notes)
    library.record_outcome(db, g["signature"], device_type_id, user=user)
    return s


class ReviewAnswer(BaseModel):
    """The engineer's answer for one symbol: a device type, not a device, or
    skip (leave it out of the BOQ without teaching the library). No answer
    at all takes back an earlier skip."""
    signature: str
    device_type_id: int | None = None
    ignore: bool = False
    skip: bool = False

    @model_validator(mode="after")
    def _one_decision(self) -> "ReviewAnswer":
        if (self.device_type_id is not None) + self.ignore + self.skip > 1:
            raise ValueError("Each answer is one of: a device type, not a device, or skip")
        return self


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
    in_force, pending = [], []
    for d in revisions.in_force(db, project.id):
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
