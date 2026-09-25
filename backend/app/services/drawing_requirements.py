"""Drawings > Actions Required: what the contractor must hand over before a
system's shop drawings start, and where each item stands.

What is *received* comes from the folder (`required_drawings.status`) and
the material submittal register (`project_state.material_approval`); what
the folder cannot say -- when an item was asked of the contractor, and by
whom -- is kept here (drawing_requirement_states), not read back off the
activity log. Generating the request email records nothing: the request
is noted when the engineer says it was sent.

The lists are the system's own (`required_drawings.ITEMS`): a fire alarm
IFC filed for the fire alarm does not receive an emergency lighting item
unless the list says that folder is shared.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import DrawingRequirementState, Project
from app.services import project_state, required_drawings, system_rules


def systems_of(project: Project) -> list[dict]:
    """The project's systems that have a list: fire alarm, emergency lighting."""
    codes = [c for c in system_rules.project_codes(project) if c in required_drawings.SYSTEMS] or ["FAS"]
    return [{"code": c, "name": required_drawings.SYSTEMS[c]} for c in codes]


def check_system(project: Project, system: str | None) -> str:
    """The system asked for, in its canonical spelling, or 404."""
    code = system_rules.canonical(system) if system else None
    if code is None:
        code = systems_of(project)[0]["code"]
    if code not in required_drawings.SYSTEMS:
        raise HTTPException(404, f"No required documents are listed for {system}")
    return code


def check_keys(system: str, keys: list[str]) -> list[str]:
    """The item keys as one list of this system's, or 422 for a key that is
    not, so a fire alarm request cannot carry an emergency lighting item."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise HTTPException(422, "Choose the items to request")
    bad = [k for k in wanted if k not in required_drawings.BY_KEY]
    if bad:
        raise HTTPException(422, f"Unknown item(s): {', '.join(bad[:5])}")
    other = [k for k in wanted if required_drawings.BY_KEY[k].system != system]
    if other:
        raise HTTPException(422, f"Not {system} item(s): {', '.join(other[:5])}. Request each system's items from its own list.")
    return wanted


def states(db: Session, project_id: int, system: str) -> dict[str, DrawingRequirementState]:
    rows = db.query(DrawingRequirementState).filter(DrawingRequirementState.project_id == project_id,
                                                    DrawingRequirementState.system_code == system).all()
    return {r.requirement_key: r for r in rows}


def mark_sent(db: Session, project: Project, system: str, keys: list[str], user, *, at: datetime | None = None) -> list[str]:
    """The request was sent: note when and by whom against each item. The caller commits."""
    now = at or utc_now()
    held = states(db, project.id, system)
    for key in keys:
        row = held.get(key)
        if row is None:
            row = DrawingRequirementState(project_id=project.id, system_code=system, requirement_key=key)
            db.add(row)
        row.requested_at, row.requested_by_id = now, user.id if user else None
        row.request_count = (row.request_count or 0) + 1
        row.updated_at = now
    db.flush()
    return keys


def status(db: Session, project: Project, system: str) -> dict:
    """Every required item of the system with where it stands: the
    material approval first (from the register, in approval words), then
    the contractor's documents (from the folder, in received words), each
    with when it was requested."""
    held = states(db, project.id, system)
    requests = {key: row.requested_at for key, row in held.items() if row.requested_at}
    out = required_drawings.status(project, requests, system)
    out["systems"] = systems_of(project)
    for item in (i for g in out["groups"] for i in g["items"]):
        item["kind"] = "document"
        item["status"] = "received" if item["received"] else "not_received"
        item["status_label"] = "Received" if item["received"] else "Not Received"
        row = held.get(item["key"])
        item["requested_by_id"] = row.requested_by_id if row else None
        item["request_count"] = row.request_count if row else 0
    # The fire alarm IFC drawing the BOQ as per IFC reads, at its revision in force.
    from app.ifc.services import revisions

    in_force = revisions.in_force(db, project.id)
    for item in (i for g in out["groups"] for i in g["items"]):
        if item["key"] in ("fa_ifc", "els_fa_ifc") and in_force:
            item["ifc"] = [{"filename": d.filename, "revision": d.revision or "R0"} for d in in_force]
            item["remarks"] = ("IFC set " + ", ".join(d.revision or "R0" for d in in_force)
                               + " (BOQ as per IFC) · " + item["remarks"])
    approval = project_state.material_approval(db, project, system)
    submittal = approval["submittal"]
    out["groups"].insert(0, {"key": "approvals", "name": "Approvals", "items": [{
        "key": "material_approval", "kind": "approval", "group": "approvals",
        "name": "Material Submittal Approval",
        "purpose": f"The approved {required_drawings.SYSTEMS[system].lower()} material the shop drawings are drawn to",
        "folder": "", "format": "Approval", "received": approval["received"],
        "status": "approved" if approval["received"] else "not_approved",
        "status_label": "Approved" if approval["received"] else "Not Approved",
        "received_date": submittal["updated"].isoformat(timespec="seconds") if submittal and submittal["updated"] else None,
        "files": [], "file_count": 0, "folder_exists": True, "requested_at": None, "requested_by_id": None,
        "request_count": 0, "remarks": approval["remarks"],
        "submittal": {k: v for k, v in submittal.items() if k != "updated"} if submittal else None,
    }]})
    out["total"] += 1
    out["received"] += 1 if approval["received"] else 0
    out["not_received"] += 0 if approval["received"] else 1
    out["ready"] = out["received"]
    out["readiness"] = f"{out['received']} / {out['total']} Ready"
    return out
