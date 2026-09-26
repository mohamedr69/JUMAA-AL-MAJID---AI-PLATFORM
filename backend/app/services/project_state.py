"""The project's state, worked out in one place from its own records.

A page is a view, never the owner of what it shows. The material
submittals are the register (project_submittals, one per system and brand,
and project_submittal_revisions, one current status per revision), read
through `app.routers.submittal.register_items`; the shop drawings and
samples are the document index (`document_sync.log_records`). From those,
and nothing else, this works out:

  * each system's material submittal status and shop drawing status, the
    summary Project Home shows ("FAS  Material submittal: Approved  Shop
    drawings: Under review");
  * whether a system's material is approved, which the drawings wait for;
  * the project's actions: held once in project_actions, opened when the
    records call for one and resolved when they no longer do -- "R1 is not
    filed" is resolved by filing R1, and is gone from every page at once;
  * the change log (project_changes, PROJECT_DATA_CHANGED): every writer
    adds its change in the transaction that makes it, and the pages ask
    for the ones after the last they saw and reload what they touch.

Nothing here commits: the caller's transaction carries the change, its
history, the actions and the change row together, or none of them.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import Project, ProjectAction, ProjectChange

KEEP_CHANGES = timedelta(days=30)

# A revision's status as the logs write it: the consultant's code.
APPROVED_CODES = ("A", "ANN")
RETURNED_CODES = ("RR", "REJ")
# A drawing record says it in words as well: "approved", "rejected".
DRAWING_APPROVED = (*APPROVED_CODES, "APPROVED")
DRAWING_RETURNED = (*RETURNED_CODES, "REJECTED")

LABELS = {
    "approved": "Approved", "under_review": "Under Review", "returned": "Returned",
    "not_submitted": "Not Submitted", "missing": "Not Filed",
}


# --- the change log ------------------------------------------------------------------------------


def record_change(db: Session, project_id: int, entity_type: str, change_type: str, *,
                  entity_id: int | None = None, system: str | None = None) -> None:
    """PROJECT_DATA_CHANGED, in the caller's transaction."""
    db.add(ProjectChange(project_id=project_id, entity_type=entity_type, change_type=change_type,
                         entity_id=entity_id, system_code=system, at=utc_now()))


def prune_changes(db: Session, project_id: int) -> None:
    """Changes are for pages that are open, not history: old ones go."""
    db.query(ProjectChange).filter(ProjectChange.project_id == project_id,
                                   ProjectChange.at < utc_now() - KEEP_CHANGES).delete(synchronize_session=False)


def changes_since(db: Session, project_id: int, since: int | None) -> tuple[int, list[ProjectChange]]:
    """The latest change id, and the changes after `since` (none when
    `since` is not given: a page opening takes the cursor and reads the
    records it shows anyway)."""
    latest = (db.query(ProjectChange.id).filter(ProjectChange.project_id == project_id)
              .order_by(ProjectChange.id.desc()).limit(1).scalar()) or 0
    if since is None or since >= latest:
        return latest, []
    rows = (db.query(ProjectChange).filter(ProjectChange.project_id == project_id, ProjectChange.id > since)
            .order_by(ProjectChange.id).limit(500).all())
    return latest, rows


# --- material submittals -------------------------------------------------------------------------


def _number(revision: str | None) -> int:
    from app.services.submittal_filing import _revision_number

    return _revision_number(revision or "")


def cell_code(status: str, reply_code: str | None) -> str:
    """A register status as the consultant's code: A, ANN, RR, REJ, UR, or
    NS for a revision not yet submitted."""
    if status == "approved":
        return "ANN" if reply_code == "B" else "A"
    if status == "rejected":
        return "RR" if reply_code == "C" else "REJ"
    if status == "under_review":
        return "UR"
    return "NS"


def _state_of(codes: list[str]) -> str:
    """One system's material submittal status from its submittals' (one per
    brand) latest codes: approved once any brand is approved -- that is
    the material the drawings are drawn to."""
    if not codes:
        return "missing"
    if any(c in APPROVED_CODES for c in codes):
        return "approved"
    if "UR" in codes:
        return "under_review"
    if any(c in RETURNED_CODES for c in codes):
        return "returned"
    return "not_submitted"


def items(db: Session, project: Project) -> list:
    """The register, as `GET /projects/{id}/submittals` lists it -- without
    the BOQ materials each submittal covers (matching those to datasheets
    is most of that page's time, and no status depends on it)."""
    from app.routers.submittal import register_items

    return register_items(db, project, materials=[])


def materials_by_system(project: Project, submittals: list) -> dict[str, dict]:
    """Each system's material submittal: its submittals (one per brand) at
    their latest revision, and where the system stands."""
    from app.services import system_rules

    out: dict[str, dict] = {}
    for item in submittals:
        code = system_rules.effective_code(item.system_code, project) if item.system_code else None
        if not code:
            continue
        entry = out.setdefault(code, {"submittals": []})
        entry["submittals"].append({
            "id": item.id if item.id > 0 else None, "reference": item.reference, "title": item.title,
            "brand": item.manufacturer, "revision": f"R{_number(item.revision)}",
            "status": item.status, "code": cell_code(item.status, item.reply_code),
            "path": item.document_path,
            "updated": next((r.updated_at for r in item.revisions if _number(r.revision) == _number(item.revision)
                             and r.updated_at), None) or item.updated_at,
        })
    for code, entry in out.items():
        state = _state_of([s["code"] for s in entry["submittals"]])
        approved = [s for s in entry["submittals"] if s["code"] in APPROVED_CODES]
        entry.update({"status": state, "label": LABELS[state], "approved": bool(approved),
                      "approved_as": approved[0] if approved else None})
    return out


def material_approval(db: Session, project: Project, system: str) -> dict:
    """Whether a system's material is approved -- a shop drawing is drawn to
    the approved material -- and what says so."""
    entry = materials_by_system(project, items(db, project)).get(system)
    if entry is None:
        return {"received": False, "remarks": f"Material approval missing: no material submittal is filed for {system}",
                "submittal": None}
    if entry["approved"]:
        s = entry["approved_as"]
        brand = f" ({s['brand']})" if s["brand"] else ""
        return {"received": True, "submittal": s,
                "remarks": f"Material approval received: {s['reference'] or s['title']}{brand} {s['revision']} {s['code']}"}
    where = "; ".join(f"{s['reference'] or s['title']} {s['revision']} {s['code']}" for s in entry["submittals"])
    return {"received": False, "submittal": None, "remarks": f"Material approval missing: {where}"}


# --- shop drawings and samples ---------------------------------------------------------------------


def _documents_by_system(project: Project, records: list) -> dict[str, dict]:
    """Each system's documents of one kind, one per document at its latest
    revision (the index has already put a drawing's history under it)."""
    from app.services import system_rules

    out: dict[str, dict] = {}
    for row in records:
        code = system_rules.effective_code(row.system_code, project) if row.system_code else None
        if not code:
            continue
        entry = out.setdefault(code, {"total": 0, "approved": 0, "under_review": 0, "returned": 0})
        status = (row.status or "UR").upper()
        # A revision found after an approval, with no reply of its own, was
        # not submitted: the drawing stands approved (drawing_log.after_approval).
        if status in ("UR", "SUPERSEDED"):
            earlier = [(r.status or "").upper() for r in getattr(row, "superseded", ())]
            if any(s in DRAWING_APPROVED for s in earlier):
                status = "A"
        entry["total"] += 1
        if status in DRAWING_APPROVED:
            entry["approved"] += 1
        elif status in DRAWING_RETURNED:
            entry["returned"] += 1
        else:
            entry["under_review"] += 1
    for entry in out.values():
        if entry["under_review"]:
            state = "under_review"
        elif entry["returned"]:
            state = "returned"
        else:
            state = "approved"
        entry.update({"status": state, "label": LABELS[state]})
    return out


def _shop_drawings_by_system(db: Session, project: Project) -> dict[str, dict]:
    """Each drawn system's shop drawings from their records: every floor of
    the building with where its drawing stands, as the Drawings page counts
    them."""
    from app.services import shop_drawings

    if shop_drawings.needs_reconcile(project):
        shop_drawings.reconcile(db, project, ai=False)
    out: dict[str, dict] = {}
    for s in shop_drawings.summary(db, project):
        total, approved = s["floors"], s["approved_total"]
        under_review, returned = s["under_review"] + s["reply_not_found"], s["not_approved"]
        if under_review or (approved and approved < total):
            state = "under_review"
        elif returned:
            state = "returned"
        elif approved and approved == total:
            state = "approved"
        else:
            state = "not_submitted"
        out[s["code"]] = {"total": total, "approved": approved, "under_review": under_review, "returned": returned,
                          "not_submitted": s["not_submitted"], "status": state, "label": LABELS[state]}
    return out


def summary(db: Session, project: Project, submittals: list | None = None) -> dict:
    """Project Home's per-system board: every system of the project with its
    material submittal, shop drawings and samples, from the records."""
    from app.services import document_sync, system_rules

    submittals = items(db, project) if submittals is None else submittals
    materials = materials_by_system(project, submittals)
    records: list = []
    if project.source_folder_path:
        records, _warnings = document_sync.log_records(db, project)
    drawn = system_rules.drawings_in_scope(project)
    # The shop drawings are their own records (app.services.shop_drawings),
    # the same the Drawings page and the Logs register show: never counted
    # off the document index, where a schedule entry or a file copy is a row.
    drawings = _shop_drawings_by_system(db, project) if drawn else {}
    samples = _documents_by_system(project, [r for r in records if r.category == "samples"])
    codes = list(dict.fromkeys([*system_rules.project_codes(project), *materials, *drawings, *samples]))
    empty = {"total": 0, "approved": 0, "under_review": 0, "returned": 0, "status": "not_submitted",
             "label": LABELS["not_submitted"]}
    systems = []
    for code in codes:
        material = materials.get(code) or {"submittals": [], "status": "missing", "label": LABELS["missing"],
                                           "approved": False, "approved_as": None}
        systems.append({
            "code": code, "name": system_rules.system_display_name(project, code),
            "material": material,
            # A system we do not draw -- or cable, which has no drawings --
            # has no shop drawing status at all, not "not submitted".
            "shop_drawings": (drawings.get(code) or empty) if drawn and system_rules.has_shop_drawings(code) else None,
            "samples": samples.get(code) or empty,
        })
    return {"systems": systems, "synced_at": project.documents_synced_at}


# --- actions -------------------------------------------------------------------------------------


def _wanted(project: Project, submittals: list) -> dict[str, dict]:
    """The actions the records call for, by key."""
    from app.services import system_rules

    wanted: dict[str, dict] = {}
    per_system: dict[str, int] = {}
    for item in submittals:
        if item.system_code:
            per_system[item.system_code] = per_system.get(item.system_code, 0) + 1
    for item in submittals:
        code = cell_code(item.status, item.reply_code)
        if code not in RETURNED_CODES:
            continue
        n = _number(item.revision)
        ident = f"{item.id}" if item.id > 0 else f"ref:{(item.reference or item.title).upper()}"
        brand = f" ({item.manufacturer})" if item.manufacturer and per_system.get(item.system_code or "", 0) > 1 else ""
        how = "revise and resubmit" if code == "RR" else "rejected"
        wanted[f"submittal:{ident}:R{n + 1:02d}"] = {
            "kind": "material_submittal_revision", "system_code": item.system_code,
            "entity_type": "submittal", "entity_id": item.id if item.id > 0 else None,
            "text": f"Material submittal required: {item.reference or item.title}{brand} R{n} was returned {how}; "
                    f"R{n + 1} is not filed",
            "severity": "warning", "link": "submittal",
        }
    # A system with no material submittal at all -- once the folder has been
    # read; before that, "none filed" is not known.
    if project.documents_synced_at is not None or submittals:
        held = {system_rules.effective_code(i.system_code, project) for i in submittals if i.system_code}
        for code in system_rules.project_codes(project):
            if code and code not in held:
                wanted[f"system:{code}:no-submittal"] = {
                    "kind": "material_submittal_missing", "system_code": code, "entity_type": "system",
                    "entity_id": None, "text": f"Material submittal required: no material submittal is filed for {code}",
                    "severity": "warning", "link": "submittal",
                }
    return wanted


def _resolution(action: ProjectAction, submittals: list) -> str:
    """Why an action is no longer open, in words."""
    if action.key.startswith("system:"):
        return f"A material submittal is filed for {action.system_code}"
    needed = _number(action.key.rsplit(":", 1)[-1])
    ident = action.key.split(":", 2)[1] if not action.key.startswith("submittal:ref:") else None
    item = next((i for i in submittals
                 if (ident and str(i.id) == ident)
                 or (not ident and f"submittal:ref:{(i.reference or i.title).upper()}:" in action.key)), None)
    if item is None:
        return "The submittal is no longer on file"
    if _number(item.revision) >= needed:
        return f"R{needed} filed ({item.reference or item.title})"
    return f"No longer required: {item.reference or item.title} R{_number(item.revision)} is " \
           f"{cell_code(item.status, item.reply_code)}"


def reconcile_actions(db: Session, project: Project, submittals: list | None = None) -> bool:
    """Open the actions the records call for and resolve the ones they no
    longer do, in the caller's transaction. Returns whether any changed."""
    db.flush()
    db.expire(project, ["submittals"])
    submittals = items(db, project) if submittals is None else submittals
    wanted = _wanted(project, submittals)
    existing = {a.key: a for a in db.query(ProjectAction).filter(ProjectAction.project_id == project.id)}
    now = utc_now()
    changed = False
    for key, want in wanted.items():
        action = existing.get(key)
        if action is None:
            action = ProjectAction(project_id=project.id, key=key, created_at=now, **want)
            db.add(action)
            record_change(db, project.id, "action", "opened", system=want["system_code"])
            changed = True
        elif action.resolved_at is not None or action.text != want["text"]:
            if action.resolved_at is not None:
                record_change(db, project.id, "action", "reopened", entity_id=action.id, system=want["system_code"])
            for field, value in want.items():
                setattr(action, field, value)
            action.resolved_at, action.resolution = None, None
            changed = True
    for key, action in existing.items():
        if key not in wanted and action.resolved_at is None:
            action.resolved_at, action.resolution = now, _resolution(action, submittals)
            record_change(db, project.id, "action", "resolved", entity_id=action.id, system=action.system_code)
            changed = True
    # The session does not flush on its own: what was opened here is to be
    # seen by the next query in this same transaction.
    db.flush()
    return changed


def open_actions(db: Session, project_id: int) -> list[ProjectAction]:
    return (db.query(ProjectAction).filter(ProjectAction.project_id == project_id, ProjectAction.resolved_at.is_(None))
            .order_by(ProjectAction.system_code, ProjectAction.created_at, ProjectAction.id).all())


def submittals_changed(db: Session, project: Project, changes: list[tuple[str, int | None, str | None]]) -> None:
    """After the register changed: a change row each, and the actions
    brought up to it -- all in the caller's transaction.
    `changes`: (change_type, submittal id, system)."""
    for change_type, entity_id, system in changes:
        record_change(db, project.id, "submittal", change_type, entity_id=entity_id, system=system)
    reconcile_actions(db, project)


# --- the Material Submittal Log ------------------------------------------------------------------


def _log_path(project: Project, path: str | None) -> str:
    """A revision's file as the log opens it: relative to the project folder
    (GET /projects/{id}/logs/file). A file elsewhere is not opened from the log."""
    from pathlib import Path

    if not path:
        return ""
    if not project.source_folder_path:
        return ""
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(Path(project.source_folder_path).resolve()).as_posix()
    except (ValueError, OSError):
        return ""


def material_log(db: Session, project: Project, submittals: list | None = None) -> list[dict]:
    """The Material Submittal Log: the register, one row per submittal at its
    latest revision with the earlier revisions under it -- the same records
    the Material Submittals page edits, never a second reading of the folder."""
    submittals = items(db, project) if submittals is None else submittals
    out = []
    for item in submittals:
        name = item.title
        if item.manufacturer and item.manufacturer.upper() not in (item.title or "").upper():
            name = f"{item.title} — {item.manufacturer}"
        group = item.reference or item.title
        rows = []
        for rev in sorted(item.revisions, key=lambda r: _number(r.revision), reverse=True):
            rows.append({
                # No reference for a submittal entered by hand before it was
                # filed: the log names it by its title, and offers no
                # deletion by a reference it does not have.
                "group_reference": group, "reference": rev.reference or item.reference,
                "revision": f"R{_number(rev.revision)}", "status": cell_code(rev.status, rev.reply_code),
                "reply_text": rev.note, "source": "register", "system_code": item.system_code, "name": name,
                "path": _log_path(project, rev.document_path), "modified": rev.updated_at or item.updated_at,
                "page": 1, "superseded": [],
            })
        if not rows:
            continue
        latest, *earlier = rows
        out.append({**latest, "superseded": earlier})
    return out
