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
from app.services import activity, drawing_log, project_folders, required_drawings, system_rules

router = APIRouter(tags=["drawings"])

SHOP_DRAWINGS_FA = f"{project_folders.DRAWINGS}/SD/FA"


def _log(db: Session, project) -> dict:
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
    out = drawing_log.build(in_force, records,
                            in_system=lambda code: system_rules.effective_code(code, integrated=integrated) == "FAS")
    if project.source_folder_path and project.documents_synced_at is None:
        warnings = ["The project folder has not been synced yet: sync the documents to read the shop drawings."] + warnings
    out.update({
        "project": {"id": project.id, "ep_number": project.ep_number, "name": project.project_name},
        "system": "FAS",
        "ifc": [{"id": d["id"], "filename": d["filename"], "revision": d["revision"]} for d in in_force],
        "synced_at": project.documents_synced_at.isoformat() if project.documents_synced_at else None,
        "folder": SHOP_DRAWINGS_FA if project.source_folder_path else None,
        "warnings": warnings,
    })
    return out


@router.get("/projects/{project_id}/drawings/log")
def drawings_log(project_id: int, _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Each floor plan of the IFC drawings in force, with its shop drawing's
    status at every revision, read from the submissions in the project folder."""
    return _log(db, _get_project_or_404(db, project_id))


@router.get("/projects/{project_id}/drawings/log/export.xlsx")
def export_drawings_log(project_id: int, _current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    project = _get_project_or_404(db, project_id)
    log = _log(db, project)
    wb = Workbook()
    ws = wb.active
    ws.title = "Drawings Log"
    ws.append([f"EP-{project.ep_number} {project.project_name or ''} - Drawings Log (FAS)"])
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
    target = (root / (body.path or SHOP_DRAWINGS_FA)).resolve()
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
    return out


@router.get("/projects/{project_id}/drawings/required")
def required(project_id: int, system: str = "FAS", _current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Each item the contractor must hand over before a system's shop
    drawings start, received when its folder in the project folder holds a file."""
    return _required(db, _get_project_or_404(db, project_id), system.upper())


@router.get("/projects/{project_id}/drawings/required/export.xlsx")
def export_required(project_id: int, system: str = "FAS", _current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    project = _get_project_or_404(db, project_id)
    data = _required(db, project, system.upper())
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
