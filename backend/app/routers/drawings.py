"""The Drawings page: the Drawings Log (app/services/drawing_log.py).

GET  /projects/{id}/drawings/log              the log: IFC floors x shop-drawing revisions
GET  /projects/{id}/drawings/required?system= what the contractor must hand over (FAS, ELS)
GET  /projects/{id}/drawings/required/export.xlsx?system=  the same, as a workbook
POST /projects/{id}/drawings/required/request the request email for items, logged
GET  /projects/{id}/drawings/log/export.xlsx  the same, as a workbook
POST /projects/{id}/drawings/open-folder      open a folder of the project in Explorer, on this PC
"""
from __future__ import annotations

import io
import os
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_role
from app.ifc.resolve import resolved_drawing
from app.models import ActivityEvent, ProjectIfcDrawing, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404
from app.services import activity, drawing_log, project_folders, project_state, required_drawings, system_rules

router = APIRouter(tags=["drawings"])

def shop_drawings_folder(system: str | None) -> str:
    """Where a system's shop drawings are filed.

    Each system has its own folder -- FA, ELS, FRC -- and this was fixed to
    the fire alarm's, so the page sent an engineer working on emergency
    lighting to the fire alarm folder.
    """
    name = project_folders.system_folder(system)
    return f"{project_folders.DRAWINGS}/SD/{name}" if name else f"{project_folders.DRAWINGS}/SD"


def _first_system(project) -> str:
    """The project's own first system, for a caller that named none."""
    codes = list(system_rules.project_codes(project))
    return codes[0] if codes else "FAS"


def _systems_with_drawings(project, records) -> list[str]:
    """The project's systems, in its own order, plus any a drawing on file
    names that the project does not list.

    The project decides, not the page: a job with fire alarm and emergency
    lighting has a log for each, and one with only fire alarm has one. A
    drawing found under a system the DRF never mentioned is still shown --
    it is on the drive, and leaving it out reports no drawings over
    drawings that exist.
    """
    integrated = system_rules.project_integrated(project)
    codes = list(system_rules.project_codes(project))
    for record in records:
        if getattr(record, "category", None) != "drawings" or getattr(record, "source", None) == "drawing schedule":
            continue
        code = system_rules.effective_code(record.system_code, integrated=integrated)
        if code and code not in codes:
            codes.append(code)
    return codes


def _log(db: Session, project, system: str | None = None) -> dict:
    from app.routers.ifc_boq import _superseded
    from app.services import document_sync

    later = _superseded(db, project.id)
    in_force = []
    for d in (db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.project_id == project.id)
              .order_by(ProjectIfcDrawing.uploaded_at, ProjectIfcDrawing.id).all()):
        if d.id in later:
            continue
        r = resolved_drawing(db, d, with_occurrences=False)
        in_force.append({**r, "id": d.id, "filename": d.filename, "revision": d.revision or "R0"})

    records, warnings = ([], [])
    if project.source_folder_path:
        records, warnings = document_sync.log_records(db, project)
    integrated = system_rules.project_integrated(project)
    systems = _systems_with_drawings(project, records)
    # The system asked for, or the project's first. Fixed to FAS before, so
    # a project's emergency lighting drawings were read, indexed and then
    # never shown.
    wanted = (system or "").strip().upper() or (systems[0] if systems else "FAS")
    out = drawing_log.build(in_force, records,
                            in_system=lambda code: system_rules.effective_code(code, integrated=integrated) == wanted)
    if project.source_folder_path and project.documents_synced_at is None:
        warnings = ["The project folder has not been synced yet: sync the documents to read the shop drawings."] + warnings
    out.update({
        "project": {"id": project.id, "ep_number": project.ep_number, "name": project.project_name},
        "system": wanted,
        "systems": systems,
        "ifc": [{"id": d["id"], "filename": d["filename"], "revision": d["revision"]} for d in in_force],
        "synced_at": project.documents_synced_at.isoformat() if project.documents_synced_at else None,
        "folder": shop_drawings_folder(wanted) if project.source_folder_path else None,
        "warnings": warnings,
    })
    return out


@router.get("/projects/{project_id}/drawings/log")
def drawings_log(project_id: int, system: str | None = None,
                 _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Each floor plan of the IFC drawings in force, with its shop drawing's
    status at every revision, read from the submissions in the project folder.

    `system` picks which of the project's systems to show; left out, its
    first. The systems it has are in the answer.
    """
    return _log(db, _get_project_or_404(db, project_id), system)


@router.get("/projects/{project_id}/drawings/log/export.xlsx")
def export_drawings_log(project_id: int, system: str | None = None,
                        _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    project = _get_project_or_404(db, project_id)
    # The system on show, not always the fire alarm.
    log = _log(db, project, system)
    wb = Workbook()
    ws = wb.active
    ws.title = "Drawings Log"
    ws.append([f"EP-{project.ep_number} {project.project_name or ''} - Drawings Log ({log['system']})"])
    ws.append([f"Floors from {', '.join(f'{d['filename']} {d['revision']}' for d in log['ifc']) or 'no IFC drawing'}; "
               f"statuses from the shop drawings in the project folder, as of {datetime.now():%Y-%m-%d %H:%M}"])
    ws.append([])
    header = ["#", "Floor", "IFC sheet", "No. of floors", *log["revisions"], "Latest revision", "Remarks / Notes"]
    ws.append(header)
    fills = {"approved": "C6EFCE", "approved_as_noted": "DDEBF7", "under_review": "FFEB9C", "not_approved": "FFC7CE",
             "not_submitted": "EDEDED"}
    for i, row in enumerate(log["rows"], 1):
        ws.append([i, row["floor"], row["sheet"], row["floors"], *(row["cells"][r]["label"] for r in log["revisions"]),
                   row["latest_revision"] or "-", row["remarks"] or "-"])
        for j, rev in enumerate(log["revisions"]):
            c = ws.cell(row=ws.max_row, column=5 + j)
            c.fill = PatternFill("solid", fgColor=fills[row["cells"][rev]["status"]])
    for c in ws[4]:
        c.font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=13)
    for col, width in zip("ABCD", (5, 34, 12, 12)):
        ws.column_dimensions[col].width = width
    for j in range(len(log["revisions"])):
        ws.column_dimensions[chr(ord("E") + j)].width = 18
    ws.column_dimensions[chr(ord("E") + len(log["revisions"]))].width = 15
    ws.column_dimensions[chr(ord("F") + len(log["revisions"]))].width = 60
    for r in ws.iter_rows(min_row=5):
        for c in r:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"EP-{project.ep_number} Drawings Log FAS.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


class OpenFolder(BaseModel):
    # relative to the project folder: a file (shown selected) or a folder; none for the shop drawings folder
    path: str | None = None


LOCAL = {"127.0.0.1", "::1", "localhost"}


@router.post("/projects/{project_id}/drawings/open-folder", status_code=204)
def open_folder(project_id: int, body: OpenFolder, request: Request,
                _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Open the folder in Windows Explorer on the PC the platform runs on --
    so only for a page opened on that PC: from anywhere else it would open
    on someone else's screen."""
    if (request.client.host if request.client else "") not in LOCAL:
        raise HTTPException(403, "Folders open on the PC the platform runs on: open the page there, or use the path shown.")
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(404, "This project has no folder")
    root = Path(project.source_folder_path).resolve()
    # The folder of the system asked for, not always the fire alarm's.
    target = (root / (body.path or shop_drawings_folder(getattr(body, "system", None)))).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(403, "Outside the project folder")
    while not target.exists() and target != root:  # the nearest folder that is there
        target = target.parent
    if os.name != "nt":
        raise HTTPException(501, "Opening a folder needs Windows")
    args = ["explorer", f"/select,{target}"] if target.is_file() else ["explorer", str(target)]
    subprocess.Popen(args)  # noqa: S603 -- a path inside the project folder, checked above


# --- Actions Required: the contractor's drawings, by folder -------------------------------------

REQUESTED = "drawings.requested"


def _requests(db: Session, project_id: int) -> dict[str, datetime]:
    """When each item was last requested from the contractor."""
    out: dict[str, datetime] = {}
    for e in (db.query(ActivityEvent)
              .filter(ActivityEvent.project_id == project_id, ActivityEvent.action == REQUESTED)
              .order_by(ActivityEvent.at).all()):
        for key in (e.detail or {}).get("keys", []):
            out[key] = e.at
    return out


def _required(db: Session, project, system: str) -> dict:
    from app.routers.ifc_boq import _superseded

    if system not in required_drawings.SYSTEMS:
        raise HTTPException(404, f"No required documents are listed for {system}")
    # The systems the project has that have a list: fire alarm, emergency lighting.
    systems = [c for c in system_rules.project_codes(project) if c in required_drawings.SYSTEMS] or ["FAS"]
    out = required_drawings.status(project, _requests(db, project.id), system)
    out["systems"] = [{"code": c, "name": required_drawings.SYSTEMS[c]} for c in systems]
    # The fire alarm IFC drawing the BOQ as per IFC reads, at its revision in force.
    later = _superseded(db, project.id)
    in_force = [d for d in db.query(ProjectIfcDrawing).filter(ProjectIfcDrawing.project_id == project.id).all()
                if d.id not in later]
    for item in (i for g in out["groups"] for i in g["items"]):
        if item["key"] in ("fa_ifc", "els_fa_ifc") and in_force:
            item["ifc"] = [{"filename": d.filename, "revision": d.revision or "R0"} for d in in_force]
            item["remarks"] = ("IFC set " + ", ".join(d.revision or "R0" for d in in_force)
                               + " (BOQ as per IFC) · " + item["remarks"])
    # And what is not a file from the contractor: the system's material,
    # approved. A shop drawing is drawn to the approved material, so this
    # waits on the material submittal register (app.services.project_state)
    # -- "missing" turns "received" when the consultant's approval is
    # recorded there, with nothing ticked here.
    approval = project_state.material_approval(db, project, system)
    submittal = approval["submittal"]
    out["groups"].insert(0, {"key": "approvals", "name": "Approvals", "items": [{
        "key": "material_approval", "kind": "approval", "group": "approvals",
        "name": "Material Submittal Approval",
        "purpose": f"The approved {required_drawings.SYSTEMS[system].lower()} material the shop drawings are drawn to",
        "folder": "", "format": "Approval", "received": approval["received"],
        "received_date": submittal["updated"].isoformat(timespec="seconds") if submittal and submittal["updated"] else None,
        "files": [], "file_count": 0, "folder_exists": True, "requested_at": None, "remarks": approval["remarks"],
        "submittal": {k: v for k, v in submittal.items() if k != "updated"} if submittal else None,
    }]})
    out["total"] += 1
    out["received"] += 1 if approval["received"] else 0
    out["not_received"] += 0 if approval["received"] else 1
    return out


@router.get("/projects/{project_id}/drawings/required")
def required(project_id: int, system: str | None = None, _current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Each item the contractor must hand over before a system's shop
    drawings start, received when its folder in the project folder holds a file.

    `system` left out means the project's first, not the fire alarm's: a
    project without one would otherwise be asked about a system it has not
    got.
    """
    project = _get_project_or_404(db, project_id)
    return _required(db, project, (system or _first_system(project)).upper())


@router.get("/projects/{project_id}/drawings/required/export.xlsx")
def export_required(project_id: int, system: str | None = None,
                    _current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    project = _get_project_or_404(db, project_id)
    data = _required(db, project, (system or _first_system(project)).upper())
    wb = Workbook()
    ws = wb.active
    ws.title = f"Actions Required {data['system']}"
    ws.append([f"EP-{project.ep_number} {project.project_name or ''} - Actions Required - {data['system']}"])
    ws.append([f"Received when its folder in the project folder holds a file; as of {datetime.now():%Y-%m-%d %H:%M}"])
    ws.append([])
    ws.append(["#", "Document Category", "Description / Scope", "Format", "Status", "Requested Date", "Received Date",
               "Remarks", "Folder"])
    n = 0
    for g in data["groups"]:
        ws.append(["", g["name"]])
        ws.cell(row=ws.max_row, column=2).font = Font(bold=True)
        for i in g["items"]:
            n += 1
            ws.append([n, i["name"], i["purpose"], i["format"], "Received" if i["received"] else "Not Received",
                       (i["requested_at"] or "")[:10] or "-", (i["received_date"] or "")[:10] or "-", i["remarks"],
                       i["folder"]])
            ws.cell(row=ws.max_row, column=5).fill = PatternFill("solid", fgColor="C6EFCE" if i["received"] else "FFC7CE")
    for c in ws[4]:
        c.font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=13)
    for col, width in zip("ABCDEFGHI", (5, 32, 50, 12, 14, 15, 15, 40, 40)):
        ws.column_dimensions[col].width = width
    for r in ws.iter_rows(min_row=5):
        for c in r:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"EP-{project.ep_number} Actions Required {data['system']}.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


class RequestItems(BaseModel):
    keys: list[str]


@router.post("/projects/{project_id}/drawings/required/request")
def request_items(project_id: int, body: RequestItems,
                  current_user: User = Depends(require_role(*CREATOR_ROLES)),
                  db: Session = Depends(get_db)):
    """The email asking the contractor for the items, and the request noted
    against each, so its remarks say when it was asked for."""
    project = _get_project_or_404(db, project_id)
    keys = [k for k in dict.fromkeys(body.keys) if k in required_drawings.BY_KEY]
    if not keys:
        raise HTTPException(422, "Choose the items to request")
    text = required_drawings.request_text(project, keys)
    activity.record(db, current_user, REQUESTED, f"Requested from the contractor: {', '.join(text['items'])}",
                    project=project, entity_type="project", entity_id=project.id, detail={"keys": keys})
    return text
