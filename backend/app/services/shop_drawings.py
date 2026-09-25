"""The shop drawings: the records the Drawings page reads, and the sync
that writes them.

    document index (what the sync read off the folder)
        -> drawing_log.build: what the records say, per system
        -> reconcile: the records brought up to it
             project_shop_drawings / shop_drawing_revisions   the official history
             shop_drawing_candidates                          files found, not yet revisions
             drawing_issues                                   what needs an engineer
             shop_drawing_events                              what happened
    Drawings page  <- log / summary / detail, from the records alone

The rules, in the order that keeps history honest:

  * A floor has one shop drawing per system; the fire alarm's and the
    emergency lighting's share only the floor.
  * A file found is not a revision submitted. R1 on the drive while R0
    stands approved is a candidate ("R1 available"), never R1's status,
    until a reply proves it, or an engineer confirms it.
  * A revision's status is the consultant's answer to that revision. A
    later revision proves an earlier one was submitted, not what it came
    back as: an answer not on file says "reply not found".
  * Latest revision = the highest submitted revision, not the highest file.
  * What an engineer confirmed -- a status, a candidate's fate, a
    reference -- the sync and the AI leave alone; where the folder now
    says otherwise, that is an issue, not an overwrite.
  * A file that disappears from the folder changes no status: the
    revision stands, marked source missing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from app.core.timeutils import utc_now
from app.models import (Project, ProjectDocument, ProjectShopDrawing, ShopDrawingCandidate, ShopDrawingEvent,
                        ShopDrawingRevision)
from app.services import building_floors, document_control, drawing_issues, drawing_log, system_rules

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "not_submitted": "Not Submitted", "under_review": "Under Review", "approved": "Approved",
    "approved_as_noted": "Approved as Noted", "not_approved": "Not Approved",
    "reply_not_found": "Answered - Reply Not Found",
}
OFFICIAL_STATUSES = ("under_review", "approved", "approved_as_noted", "not_approved", "reply_not_found")
DECIDED = ("approved", "approved_as_noted", "not_approved")
MIN_REVISIONS = drawing_log.MIN_REVISIONS
# What a drawing reference says its system is, where it says one.
_REF_SYSTEM = (("FAS", re.compile(r"-(?:FA|FAS|VE|FT)-", re.I)), ("ELS", re.compile(r"-(?:LI|ELM|EML|ELS|CBS)-", re.I)),
               ("FRC", re.compile(r"-FRC-", re.I)))


def _rev(revision: str | None) -> int:
    return drawing_log._rev_number(revision)


def reference_system(reference: str) -> str | None:
    for code, pattern in _REF_SYSTEM:
        if pattern.search(reference or ""):
            return code
    return None


# --- which systems ------------------------------------------------------------------------------


def systems_for(project: Project, records: list) -> list[str]:
    """The project's systems that are drawn, in its own order, plus any a
    drawing on file names that the project does not list -- it is on the
    drive, and leaving it out reports no drawings over drawings that exist.
    Never a cable: it has no shop drawings."""
    integrated = system_rules.project_integrated(project)
    codes = [c for c in system_rules.project_codes(project) if system_rules.has_shop_drawings(c)]
    for record in records:
        if getattr(record, "category", None) != "drawings" or getattr(record, "source", None) == "drawing schedule":
            continue
        code = system_rules.effective_code(record.system_code, integrated=integrated)
        if code and code not in codes and system_rules.has_shop_drawings(code):
            codes.append(code)
    return codes


def project_systems(db: Session, project: Project) -> list[str]:
    """The systems the Drawings page offers: the project's drawn systems and
    any a shop drawing record already holds."""
    codes = [c for c in system_rules.project_codes(project) if system_rules.has_shop_drawings(c)]
    for (code,) in db.query(ProjectShopDrawing.system_code).filter(ProjectShopDrawing.project_id == project.id).distinct():
        if code not in codes:
            codes.append(code)
    return codes


# --- events -------------------------------------------------------------------------------------


def event(db: Session, project: Project, kind: str, text: str, *, system: str | None = None, drawing=None,
          floor_key: str | None = None, user=None, detail: dict | None = None) -> ShopDrawingEvent:
    row = ShopDrawingEvent(project_id=project.id, system_code=system, shop_drawing_id=drawing.id if drawing else None,
                           floor_key=floor_key or (drawing.floor_keys[0] if drawing and drawing.floor_keys else None),
                           kind=kind, text=text, detail=detail, user_id=user.id if user else None, at=utc_now())
    db.add(row)
    return row


# --- the sync's side: the records brought up to the index --------------------------------------


def _documents(db: Session, project: Project) -> tuple[list[ProjectDocument], dict[str, str]]:
    rows = db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id).all()
    by_path = {(r.relative_path or "").replace("\\", "/"): (r.sha256 or "") for r in rows if r.state != "removed"}
    return rows, by_path


def _raw_records(rows: list[ProjectDocument]) -> list[tuple[ProjectDocument, dict]]:
    out = []
    for row in rows:
        if row.state == "removed":
            continue
        for data in (row.extracted or {}).get("records") or []:
            out.append((row, data))
    return out


def reconcile(db: Session, project: Project, *, user=None, ai: bool = True, records: list | None = None) -> dict:
    """Bring the shop drawing records up to the document index: the floor
    registry, every system's drawings, revisions, candidates and issues.
    Reads the index (never the folder), writes the records, commits.
    Returns what changed, by system."""
    from app.services import document_sync, project_state

    now = utc_now()
    if records is None:
        records, _warnings = document_sync.log_records(db, project) if project.source_folder_path else ([], [])
    integrated = system_rules.project_integrated(project)
    # The systems the records name, and every system that already has shop
    # drawing records: a folder emptied of a system's drawings still has
    # that system's history to keep.
    systems = systems_for(project, records)
    for code in project_systems(db, project):
        if code not in systems:
            systems.append(code)
    rows, sha_by_path = _documents(db, project)
    shop_all = [r for r in records if getattr(r, "category", None) == "drawings"
                and getattr(r, "source", None) != "drawing schedule"]

    # The building's floors: from the IFC drawings in force, plus any a
    # shop drawing names that no plan has, with history kept whichever way.
    existing_drawings = (db.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project.id)
                         .options(selectinload(ProjectShopDrawing.revisions), selectinload(ProjectShopDrawing.candidates)).all())
    with_history = {key for d in existing_drawings for key in (d.floor_keys or []) if d.revisions}
    ifc_input = building_floors.in_force_as_log_input(db, project)
    aliases = drawing_log.floor_aliases(
        [(r.floor, r.name) for entry in shop_all for r in (entry, *getattr(entry, "superseded", ()))]
        + [(s.get("floor_name"), s.get("title")) for d in ifc_input for s in d.get("sheets") or [] if s.get("kind") == "plan"])
    ifc_keys = {e.key for e in building_floors.floors_from_ifc(ifc_input)}
    extra = []
    for record in shop_all:
        for key in (drawing_log.floor_identity(record.floor, record.name, aliases) if record.floor else set()):
            if key not in ifc_keys and key not in {e.key for e in extra}:
                extra.append(building_floors.floor_from_shop_drawing(key, record.floor))
    floors_changed = building_floors.refresh(db, project, floors_with_history=with_history, extra=extra)
    registry = building_floors.as_log_floors(building_floors.registry(db, project.id))
    floor_names = {f["key"]: f["display"] for f in registry}

    wanted_issues: dict[str, dict] = {}
    for key in floors_changed["missing"]:
        wanted_issues[f"floor_not_in_ifc:{key}"] = {
            "kind": "floor_not_in_ifc", "floor_key": key, "system_code": None,
            "text": f"{floor_names.get(key, key)} is not in the latest IFC drawings; its shop drawing history is kept."}

    changed: dict[str, dict] = {}
    for system in systems:
        changed[system] = _reconcile_system(db, project, system, records, shop_all, rows, sha_by_path, registry,
                                            ifc_input, integrated, wanted_issues, now, user=user)

    drawing_issues.reconcile(db, project, {k: v for k, v in wanted_issues.items() if v.get("system_code") is None
                                           or v["system_code"] in systems}, source=drawing_issues.SYSTEM)

    if ai:
        try:
            _ai_review(db, project, systems, rows, shop_all, integrated, registry)
        except Exception:  # noqa: BLE001 -- the AI never fails the sync
            logger.exception("The drawings AI review failed for project %s", project.id)

    project.drawings_reconciled_at = now
    project_state.record_change(db, project.id, "drawing", "reconciled")
    db.commit()
    return {"systems": changed, "floors": floors_changed}


def _reconcile_system(db: Session, project: Project, system: str, records: list, shop_all: list,
                      rows: list[ProjectDocument], sha_by_path: dict[str, str], registry: list[dict], ifc_input: list[dict],
                      integrated: bool, wanted_issues: dict, now: datetime, *, user=None) -> dict:
    counts = {"drawings": 0, "revisions": 0, "candidates": 0, "missing": 0}

    def in_system(code):
        return system_rules.effective_code(code, integrated=integrated) == system

    # Drawings whose reference names another system are not this system's:
    # flagged, and left out rather than logged under the wrong system. The
    # index folds a floor's records under the one that stands, so the
    # earlier revisions are looked at too.
    from dataclasses import replace as _replace

    mismatched = []
    usable = []
    for record in records:
        if getattr(record, "category", None) == "drawings" and getattr(record, "source", None) != "drawing schedule" \
                and in_system(record.system_code):
            earlier = tuple(getattr(record, "superseded", ()) or ())
            bad = [r for r in (record, *earlier) if reference_system(r.reference) not in (None, system)]
            mismatched.extend(bad)
            if record in bad:
                continue
            if bad:
                record = _replace(record, superseded=tuple(r for r in earlier if r not in bad))
        usable.append(record)
    # The index folds and drops duplicates before the records reach here, so
    # the stray is looked for on the raw records too.
    raw_mismatched = [(data.get("reference") or "", data.get("path") or "") for _row, data in _raw_records(rows)
                      if data.get("category") == "drawings" and data.get("source") != "drawing schedule"
                      and in_system(data.get("system_code")) and reference_system(data.get("reference") or "") not in (None, system)]
    for reference, path in [(r.reference, r.path) for r in mismatched] + raw_mismatched:
        wanted_issues[f"{system}:system_mismatch:{reference.upper()}"] = {
            "kind": "system_mismatch", "system_code": system, "floor_key": None,
            "text": f"{reference} is filed as a {system} drawing but its reference names "
                    f"{reference_system(reference)}: not logged under {system}.",
            "detail": {"reference": reference, "path": path, "named_system": reference_system(reference)}}

    derived = drawing_log.build(ifc_input, usable, in_system=in_system, floors=registry)
    existing = {d.drawing_reference.upper(): d for d in
                db.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project.id,
                                                    ProjectShopDrawing.system_code == system)
                .options(selectinload(ProjectShopDrawing.revisions), selectinload(ProjectShopDrawing.candidates)).all()}
    seen: set[str] = set()
    floor_names = {f["key"]: f["display"] for f in registry}

    # The log lists a submission that carries several floors -- "Basement
    # 4, 3, 2", drawn apart and sent together -- as a row per floor under
    # the one reference. The record is the drawing: one per reference, with
    # every floor it stands for; the page spreads it back over its floors.
    grouped: dict[str, list[dict]] = {}
    for row in (r for r in derived["rows"] if r["source"] == "shop_drawing"):
        grouped.setdefault(row["reference"].upper(), []).append(row)

    for reference_key, group in grouped.items():
        row = group[0]
        reference = row["reference"]
        keys = list(dict.fromkeys(k for r in group for k in r["floor_keys"]))
        typical = any(r.get("floors", 1) > 1 and bool(r.get("floor_named")) for r in group)
        label = row.get("floor_named") if len(group) == 1 else ", ".join(
            dict.fromkeys(str(r.get("floor_named") or r.get("floor") or "") for r in group))
        merged: dict[str, dict] = {}
        for r in group:
            for rev, cell in r["revisions"].items():
                held_cell = merged.get(rev)
                if held_cell is None or (held_cell.get("candidate") and not cell.get("candidate")):
                    merged[rev] = cell
        hints = list({(h["kind"], h.get("revision")): h for r in group for h in r.get("hints", [])}.values())
        latest_revision, latest_status = row["latest_revision"], row["latest_status"]

        drawing = existing.get(reference_key)
        if drawing is None:
            drawing = ProjectShopDrawing(project_id=project.id, system_code=system, drawing_reference=reference,
                                         floor_keys=keys, floor_label=label, typical=typical, created_at=now, updated_at=now)
            db.add(drawing)
            db.flush()
            existing[reference_key] = drawing
            counts["drawings"] += 1
            event(db, project, "drawing.found", f"{reference} found in the project folder"
                  + (f" ({row['floor']})" if row.get("floor") else ""), system=system, drawing=drawing)
        elif drawing.confirmed_by_id is None:
            drawing.floor_keys, drawing.floor_label, drawing.typical = keys, label, typical
        drawing.active = True
        seen.add(reference_key)
        held = {r.revision: r for r in drawing.revisions}
        present: set[str] = set()

        # The official revisions: each the consultant's answer to it.
        for rev, cell in merged.items():
            if cell.get("candidate"):
                continue
            present.add(rev)
            sha = sha_by_path.get((cell.get("path") or "").replace("\\", "/"), "") if cell.get("path") else None
            revision = held.get(rev)
            if revision is None:
                revision = ShopDrawingRevision(shop_drawing_id=drawing.id, revision=rev, number=_rev(rev), created_at=now,
                                               updated_at=now, source="sync")
                db.add(revision)
                drawing.revisions.append(revision)
                held[rev] = revision
                counts["revisions"] += 1
                event(db, project, "revision.found", f"{reference} {rev} on file: {STATUS_LABELS.get(cell['status'], cell['status'])}",
                      system=system, drawing=drawing, detail={"revision": rev, "status": cell["status"]})
            was = revision.status
            if revision.confirmed_by_id is not None:
                # The engineer's word stands; the folder disagreeing is an issue.
                if cell["status"] != revision.status and cell["status"] != "reply_not_found":
                    wanted_issues[f"{system}:status_conflict:{drawing.id}:{rev}"] = {
                        "kind": "status_conflict", "system_code": system, "shop_drawing_id": drawing.id,
                        "floor_key": drawing.floor_keys[0] if drawing.floor_keys else None,
                        "text": f"{reference} {rev}: the folder now reads {STATUS_LABELS.get(cell['status'], cell['status'])}, "
                                f"but an engineer confirmed {STATUS_LABELS.get(revision.status, revision.status)}. "
                                f"The confirmed status stands.",
                        "detail": {"folder_status": cell["status"], "confirmed_status": revision.status}}
            else:
                if cell["status"] != revision.status and not (
                        revision.source in ("engineer", "ai") and cell["status"] in ("reply_not_found", "under_review")
                        and revision.status in DECIDED):
                    revision.status = cell["status"]
                    if was != cell["status"] and cell["status"] in DECIDED and revision.id is not None:
                        event(db, project, "reply.received", f"{reference} {rev}: {STATUS_LABELS[cell['status']]}",
                              system=system, drawing=drawing, detail={"revision": rev, "status": cell["status"]})
                revision.note = cell.get("note")
                revision.reply_text = cell.get("remarks") or revision.reply_text
                revision.submitted = True
            if cell.get("path"):
                revision.drawing_path, revision.drawing_page = cell["path"], cell.get("page") or 1
                revision.drawing_sha256 = sha or revision.drawing_sha256
                if cell.get("modified") and revision.submitted_at is None:
                    revision.submitted_at = _when(cell["modified"])
                if revision.source_missing:
                    revision.source_missing = False
            revision.updated_at = now

        # A file found at a revision nothing proves was submitted: a candidate.
        for rev, cell in merged.items():
            candidate = cell.get("candidate")
            if not candidate:
                continue
            if rev in held and held[rev].submitted and (held[rev].source in ("engineer", "submission", "ai")
                                                       or held[rev].status in DECIDED):
                present.add(rev)      # confirmed, or answered since: an official revision now
                continue
            path = (candidate.get("path") or "").replace("\\", "/")
            sha = sha_by_path.get(path, "") or f"path:{path}"
            existing_candidate = next((c for c in drawing.candidates if c.revision == rev and c.file_sha256 == sha), None)
            if existing_candidate is None:
                existing_candidate = ShopDrawingCandidate(project_id=project.id, shop_drawing_id=drawing.id, revision=rev,
                                                          path=candidate.get("path"), page=candidate.get("page") or 1,
                                                          file_sha256=sha, detected_at=now, candidate_status="available",
                                                          evidence={"note": candidate.get("note")})
                db.add(existing_candidate)
                drawing.candidates.append(existing_candidate)
                db.flush()      # its id, for the issue that points at it
                counts["candidates"] += 1
                event(db, project, "revision.detected", f"{reference} {rev} found in the folder; not taken as submitted",
                      system=system, drawing=drawing, detail={"revision": rev, "path": candidate.get("path")})
                for other in drawing.candidates:
                    if other is not existing_candidate and other.revision == rev and other.candidate_status == "available":
                        other.candidate_status = "superseded"
            if existing_candidate.candidate_status == "available":
                wanted_issues[f"{system}:candidate:{drawing.id}:{rev}:{sha[:16]}"] = {
                    "kind": "revision_candidate", "severity": "info", "system_code": system, "shop_drawing_id": drawing.id,
                    "floor_key": drawing.floor_keys[0] if drawing.floor_keys else None,
                    "text": f"{reference}: {rev} is in the folder; the official latest is "
                            f"{latest_revision} ({STATUS_LABELS.get(latest_status, latest_status)}). "
                            f"No submission or consultant reply proves {rev} was submitted.",
                    "detail": {"revision": rev, "candidate_id": existing_candidate.id, "path": candidate.get("path")}}

        # Revisions on record that the folder no longer shows: the status stands.
        for rev, revision in held.items():
            if rev in present or not revision.submitted:
                continue
            if not revision.source_missing and revision.drawing_path:
                revision.source_missing = True
                counts["missing"] += 1
                event(db, project, "source.missing", f"{reference} {rev}: its file is no longer in the folder; "
                                                     f"{STATUS_LABELS.get(revision.status, revision.status)} stands",
                      system=system, drawing=drawing, detail={"revision": rev})
            if revision.source_missing:
                wanted_issues[f"{system}:source_missing:{drawing.id}:{rev}"] = {
                    "kind": "source_missing", "system_code": system, "shop_drawing_id": drawing.id,
                    "floor_key": drawing.floor_keys[0] if drawing.floor_keys else None,
                    "text": f"{reference} {rev}: the file is not in the project folder (or OneDrive has not brought it "
                            f"down). Its status, {STATUS_LABELS.get(revision.status, revision.status)}, is kept.",
                    "detail": {"revision": rev, "path": revision.drawing_path}}

        for hint in hints:
            if hint["kind"] == "revision_candidate":
                continue
            key = f"{system}:{hint['kind']}:{drawing.id}:{hint.get('revision') or ''}"
            wanted_issues[key] = {"kind": hint["kind"], "severity": hint.get("severity"), "system_code": system,
                                  "shop_drawing_id": drawing.id, "floor_key": drawing.floor_keys[0] if drawing.floor_keys else None,
                                  "text": f"{reference}: {hint['label']}. {hint.get('note') or ''}".strip(),
                                  "detail": {"revision": hint.get("revision")}}
        drawing.updated_at = now

    # Drawings on record the folder no longer has at all: kept, marked.
    for reference, drawing in existing.items():
        if reference in seen:
            continue
        for revision in drawing.revisions:
            if revision.submitted and revision.drawing_path and not revision.source_missing:
                revision.source_missing = True
                counts["missing"] += 1
            if revision.source_missing:
                wanted_issues[f"{system}:source_missing:{drawing.id}:{revision.revision}"] = {
                    "kind": "source_missing", "system_code": system, "shop_drawing_id": drawing.id,
                    "floor_key": drawing.floor_keys[0] if drawing.floor_keys else None,
                    "text": f"{drawing.drawing_reference} {revision.revision}: the file is not in the project folder. "
                            f"Its status, {STATUS_LABELS.get(revision.status, revision.status)}, is kept.",
                    "detail": {"revision": revision.revision, "path": revision.drawing_path}}

    # Two references for one floor, and two files for one revision.
    _conflicts(db, system, [r for r in shop_all if in_system(r.system_code) and reference_system(r.reference) in (None, system)],
               rows, existing, floor_names, wanted_issues, in_system)
    db.flush()
    return counts


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


_PLACEHOLDER = re.compile(r"^(?:Z+|X+)$", re.I)


def _reference_shape(reference: str) -> str:
    """A reference with its placeholder segments (ZZZ, XXX) and the segment
    a placeholder stands in for taken out: what is left tells two drawings
    apart, and leaves one drawing's two spellings the same."""
    parts = [p for p in re.split(r"[-\s_]+", reference.upper()) if p]
    serial = parts[-1] if parts and parts[-1].isdigit() else ""
    kept = [p for p in parts[:-1] if not _PLACEHOLDER.match(p) and not re.fullmatch(r"[BPL]?\d{1,3}[A-Z]?", p)]
    return "-".join(kept) + "|" + serial


def _conflicts(db: Session, system: str, shop: list, rows: list[ProjectDocument], existing: dict, floor_names: dict,
               wanted_issues: dict, in_system) -> None:
    """A floor drawn under two references (not a typical run and a floor of
    its own), and a revision on two different files."""
    by_floor: dict[str, dict[str, object]] = {}
    for entry in shop:
        # The index folds a floor's earlier revisions under the one that
        # stands, whatever number they were issued under: each is looked at.
        for record in (entry, *(getattr(entry, "superseded", ()) or ())):
            if not record.floor or drawing_log._is_typical(record):
                continue
            keys = drawing_log.floors_named(record.floor)
            if len(keys) != 1:
                continue
            by_floor.setdefault(next(iter(keys)), {}).setdefault(record.reference.upper(), record)
    for key, refs in by_floor.items():
        # A sheet re-issued with its placeholder segment filled in ("...-ZZZ-
        # ZZZ-010009" then "...-ZZZ-L22-010009") is the same drawing under
        # two spellings of one number, which the index already folds; only
        # references that differ by more than a placeholder conflict.
        if len({_reference_shape(r) for r in refs}) < 2:
            continue
        held = [existing[r] for r in refs if r in existing]
        confirmed = [d for d in held if d.confirmed_by_id is not None]
        if confirmed:
            continue     # an engineer said which stands
        names = ", ".join(sorted(refs))
        wanted_issues[f"{system}:reference_conflict:{key}"] = {
            "kind": "reference_conflict", "system_code": system, "floor_key": key,
            "shop_drawing_id": held[0].id if held else None,
            "text": f"{floor_names.get(key, key)} has two drawing references: {names}. Neither replaces the other "
                    f"until an engineer says which stands.",
            "detail": {"references": [{"reference": r, "title": getattr(rec, 'name', None),
                                       "revisions": sorted({x.revision for x in (rec, *getattr(rec, 'superseded', ()))})}
                                      for r, rec in refs.items()]}}
    # Two different submissions of one revision, filed side by side. The
    # archive files the same revision in several places on purpose -- the
    # package under the revision's folder, the bare sheet beside it, the
    # stamped copy under Received, a mirror under 03- Drawings/SD, and a
    # resubmission carries the sheet it answers -- so only two files in
    # the *same folder*, with different content and names that are not
    # one another's (the sheet and its package share a stem), are a
    # conflict. The same content anywhere is a copy.
    from app.services.submittal_scanner import APPROVAL_FOLDER_RE

    files: dict[tuple, dict[str, str]] = {}
    for row, data in _raw_records(rows):
        if data.get("category") != "drawings" or data.get("source") == "drawing schedule" or not in_system(data.get("system_code")):
            continue
        relative = (row.relative_path or row.filename or "").replace("\\", "/")
        folder = relative.rsplit("/", 1)[0] if "/" in relative else ""
        if APPROVAL_FOLDER_RE.search(folder):
            continue
        key = ((data.get("reference") or "").upper(), data.get("revision") or "R0", folder)
        files.setdefault(key, {})[relative] = row.sha256 or ""
    for (reference, revision, folder), paths in files.items():
        if len(paths) < 2 or len({sha for sha in paths.values() if sha}) < 2:
            continue
        stems = {p: re.sub(r"\.[^.]+$", "", p.rsplit("/", 1)[-1]).upper() for p in paths}
        distinct: list[str] = []
        for path in sorted(paths, key=lambda p: len(stems[p])):
            if not any(stems[path].startswith(stems[other]) or stems[other].startswith(stems[path]) for other in distinct):
                distinct.append(path)
        if len(distinct) < 2 or len({paths[p] for p in distinct if paths[p]}) < 2:
            continue
        drawing = existing.get(reference)
        wanted_issues[f"{system}:revision_conflict:{reference}:{revision}"] = {
            "kind": "revision_conflict", "system_code": system, "shop_drawing_id": drawing.id if drawing else None,
            "floor_key": drawing.floor_keys[0] if drawing and drawing.floor_keys else None,
            "text": f"{reference} {revision} is two different files in {folder or 'the folder'}: "
                    f"{', '.join(p.rsplit('/', 1)[-1] for p in distinct)}. Which is the submitted one?",
            "detail": {"files": [{"path": p, "sha256": paths[p]} for p in distinct]}}


def _ai_review(db: Session, project: Project, systems: list[str], rows: list[ProjectDocument], shop_all: list,
               integrated: bool, registry: list[dict]) -> None:
    """What the rules could not settle, for the AI: replies that name no
    drawing of the log, and floors with two references."""
    from app.services import drawing_ai_review

    ok, _why = drawing_ai_review.enabled()
    if not ok:
        return
    report = drawing_ai_review.Report()
    wanted: dict[str, dict] = {}
    known = {(r.reference.upper(), system_rules.effective_code(r.system_code, integrated=integrated))
             for r in shop_all}
    for system in systems:
        drawings = (db.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project.id,
                                                        ProjectShopDrawing.system_code == system, ProjectShopDrawing.active.is_(True))
                    .options(selectinload(ProjectShopDrawing.revisions)).all())
        if not drawings:
            continue
        compact = [{"id": d.id, "reference": d.drawing_reference, "floor": d.floor_label or "", "floor_key": (d.floor_keys or [None])[0],
                    "floor_keys": d.floor_keys or [], "revisions": [r.revision for r in d.revisions if r.submitted]} for d in drawings]
        unmatched = []
        for row, data in _raw_records(rows):
            if data.get("category") != "reply" or not data.get("reference"):
                continue
            if any((data["reference"].upper(), code) in known for code in (system, None)) or \
                    any((data["reference"].upper(), s) in known for s in systems):
                continue
            named = reference_system(data["reference"])
            if named and named != system:
                continue
            unmatched.append({"sha256": row.sha256 or f"path:{row.relative_path}", "path": row.relative_path,
                              "name": row.filename, "reference": data["reference"], "revision": data.get("revision") or "R0",
                              "status": data.get("status"), "words": data.get("reply_text"),
                              "floor_key": None})
        conflicts = []
        for issue in drawing_issues.open_issues(db, project.id, system):
            if issue.kind == "reference_conflict" and issue.system_code == system:
                conflicts.append({"floor_key": issue.floor_key, "floor": next((f["display"] for f in registry if f["key"] == issue.floor_key), issue.floor_key),
                                  "references": (issue.detail or {}).get("references") or []})
        findings = drawing_ai_review.review_replies(db, project.id, system, unmatched, compact, report)
        findings += drawing_ai_review.review_reference_conflicts(db, project.id, system, conflicts, report)
        for finding in findings:
            if finding.accepted and finding.apply:
                if _apply_reply(db, project, system, finding):
                    continue
            wanted[finding.key] = {"kind": finding.kind, "system_code": system, "shop_drawing_id": finding.shop_drawing_id,
                                   "floor_key": finding.floor_key, "text": finding.text, "ai": finding.ai,
                                   "severity": drawing_issues.INFO if finding.kind == "ai_suggestion" else None}
    drawing_issues.reconcile(db, project, wanted, source=drawing_issues.AI)
    if report.calls or report.cache_hits:
        logger.info("drawings.ai project=%s calls=%d cache_hits=%d errors=%d skipped=%d", project.id, report.calls,
                 report.cache_hits, report.errors, report.skipped)


def _apply_reply(db: Session, project: Project, system: str, finding) -> bool:
    """A validated AI reply match, applied: the revision's status, where no
    engineer confirmed one. Returns whether it was applied."""
    drawing = db.get(ProjectShopDrawing, finding.apply["shop_drawing_id"])
    if drawing is None:
        return False
    rev = finding.apply["revision"]
    revision = next((r for r in drawing.revisions if r.revision == rev), None)
    status = finding.apply["status"]
    if status not in DECIDED:
        return False
    if revision is not None and revision.confirmed_by_id is not None:
        return False      # the engineer's word stands: the finding is shown instead
    if revision is None:
        candidate = next((c for c in drawing.candidates if c.revision == rev and c.candidate_status == "available"), None)
        if candidate is None:
            return False
        revision = ShopDrawingRevision(shop_drawing_id=drawing.id, revision=rev, number=_rev(rev), submitted=True,
                                       drawing_path=candidate.path, drawing_page=candidate.page,
                                       drawing_sha256=candidate.file_sha256, created_at=utc_now(), updated_at=utc_now())
        db.add(revision)
        candidate.candidate_status, candidate.decided_at = "confirmed", utc_now()
        candidate.evidence = {**(candidate.evidence or {}), "ai": finding.ai}
    revision.status, revision.source, revision.updated_at = status, "ai", utc_now()
    revision.reply_text = finding.apply.get("reply_text") or revision.reply_text
    revision.note = f"Reply matched by the AI ({finding.ai.get('confidence', 0):.0%}, {finding.ai.get('reason_code')})"
    event(db, project, "reply.received", f"{drawing.drawing_reference} {rev}: {STATUS_LABELS[status]} (reply matched by the AI)",
          system=system, drawing=drawing, detail={"revision": rev, "status": status, "ai": finding.ai})
    db.flush()
    return True


def floors_changed(db: Session, project: Project) -> dict:
    """The IFC drawings in force changed: the building's floors brought up
    to them, and the floors flagged that lost their IFC plan. Nothing of
    any shop drawing's changes. Commits."""
    from app.services import project_state

    with_history = {key for d in db.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project.id)
                    .options(selectinload(ProjectShopDrawing.revisions)) for key in (d.floor_keys or []) if d.revisions}
    result = building_floors.refresh(db, project, floors_with_history=with_history)
    names = {f.floor_key: f.display_name for f in building_floors.registry(db, project.id, include_inactive=True)}
    wanted = {f"floor_not_in_ifc:{key}": {
        "kind": "floor_not_in_ifc", "floor_key": key, "system_code": None,
        "text": f"{names.get(key, key)} is not in the latest IFC drawings; its shop drawing history is kept."}
        for key in result["missing"]}
    existing = {i.key: i for i in db.query(drawing_issues.DrawingIssue).filter(
        drawing_issues.DrawingIssue.project_id == project.id, drawing_issues.DrawingIssue.kind == "floor_not_in_ifc")}
    now = utc_now()
    for key, want in wanted.items():
        issue = existing.get(key)
        if issue is None:
            db.add(drawing_issues.DrawingIssue(project_id=project.id, key=key, source=drawing_issues.SYSTEM, severity="warning",
                                               created_at=now, updated_at=now, **want))
        elif issue.resolved_at is not None and issue.resolved_by_id is None:
            issue.resolved_at, issue.resolution, issue.updated_at = None, None, now
    for key, issue in existing.items():
        if key not in wanted and issue.resolved_at is None:
            issue.resolved_at, issue.resolution = now, "The floor is in the latest IFC drawings again"
    project_state.record_change(db, project.id, "drawing", "floors")
    db.commit()
    return result


def needs_reconcile(project: Project) -> bool:
    """The records are behind the index: the sync ran since they were
    last brought up to it, or they never were."""
    if project.drawings_reconciled_at is None:
        return project.documents_synced_at is not None
    return bool(project.documents_synced_at and project.documents_synced_at > project.drawings_reconciled_at)


# --- the page's side: the records as the log shows them ------------------------------------------


def _cell(revision: ShopDrawingRevision, reference: str) -> dict:
    return {
        "revision": revision.revision, "status": revision.status, "label": STATUS_LABELS.get(revision.status, revision.status),
        "reference": reference, "path": revision.drawing_path, "page": revision.drawing_page, "name": None,
        "floor_named": None, "remarks": revision.reply_text,
        "modified": revision.submitted_at.isoformat() if revision.submitted_at else None,
        "note": revision.note, "source": revision.source, "confirmed": revision.confirmed_by_id is not None,
        "source_missing": revision.source_missing,
        "submitted_at": revision.submitted_at.isoformat() if revision.submitted_at else None,
        "reply_at": revision.reply_at.isoformat() if revision.reply_at else None,
    }


def _candidate_out(c: ShopDrawingCandidate) -> dict:
    return {"id": c.id, "revision": c.revision, "path": c.path, "page": c.page, "file_sha256": c.file_sha256,
            "detected_at": c.detected_at.isoformat() if c.detected_at else None, "status": c.candidate_status,
            "evidence": c.evidence or {}, "decided_at": c.decided_at.isoformat() if c.decided_at else None,
            "label": f"{c.revision} available"}


def row_of(drawing: ProjectShopDrawing, revisions: list[str], issues: list, floor_names: dict[str, str]) -> dict:
    official = sorted((r for r in drawing.revisions if r.submitted), key=lambda r: r.number)
    cells = {rev: _cell(r, drawing.drawing_reference) for rev, r in ((r.revision, r) for r in official)}
    candidates = [c for c in drawing.candidates if c.candidate_status == "available"]
    for c in candidates:
        if c.revision not in cells:
            cells[c.revision] = {**drawing_log.NOT_SUBMITTED, "revision": c.revision, "label": drawing_log.BLANK,
                                 "path": c.path, "page": c.page, "candidate": _candidate_out(c)}
    latest = official[-1] if official else None
    keys = list(drawing.floor_keys or [])
    if drawing.floor_label:
        floor = drawing_log.floor_label(drawing.floor_label)
        if len(keys) == 1 and keys[0] in floor_names and floor_names[keys[0]].upper() not in floor.upper() \
                and " - " in floor_names[keys[0]]:
            floor = floor_names[keys[0]]
    elif len(keys) == 1:
        floor = floor_names.get(keys[0], drawing_log._spelled(keys[0]))
    else:
        floor = "Floor not named on the drawing"
    hints = [{"kind": "revision_candidate", "revision": c.revision, "label": f"{c.revision} available", "severity": "info",
              "candidate_id": c.id, "path": c.path, "note": (c.evidence or {}).get("note")} for c in candidates]
    for issue in issues:
        if issue.kind not in ("revision_candidate",):
            hints.append({"kind": issue.kind, "label": drawing_issues.label_of(issue.kind), "severity": issue.severity,
                          "source": issue.source, "issue_id": issue.id, "note": issue.text})
    return {
        "key": f"sd:{drawing.id}", "id": drawing.id, "source": "shop_drawing", "reference": drawing.drawing_reference,
        "floor": floor, "floor_named": drawing.floor_label, "floor_keys": keys, "floors": len(keys) if drawing.typical else max(1, len(keys)),
        "typical": drawing.typical, "confirmed": drawing.confirmed_by_id is not None, "remarks": drawing.remarks or "",
        "revision": latest.revision if latest else None, "status": latest.status if latest else "not_submitted",
        "label": STATUS_LABELS.get(latest.status if latest else "not_submitted"),
        "path": latest.drawing_path if latest else None, "page": latest.drawing_page if latest else 1, "name": None,
        "revisions": {rev: cells[rev] for rev in sorted(cells, key=_rev)},
        "cells": {rev: cells.get(rev) or dict(drawing_log.NOT_SUBMITTED) for rev in revisions},
        "latest_revision": latest.revision if latest else None,
        "latest_status": latest.status if latest else "not_submitted",
        "latest_note": (", ".join(f"{c.revision} available" for c in candidates) or None),
        "latest_path": latest.drawing_path if latest else None, "latest_page": latest.drawing_page if latest else 1,
        "candidates": [_candidate_out(c) for c in drawing.candidates],
        "hints": hints, "issues": len(issues),
    }


def rows_of(drawing: ProjectShopDrawing, revisions: list[str], issues: list, floor_names: dict[str, str]) -> list[dict]:
    """The drawing as the log lists it: one row, or -- for a submission
    that carries several floors drawn apart ("Basement 4, 3, 2") -- a row
    per floor, each with the same reference and revisions. A typical sheet
    is one drawing for a run of floors and stays one row."""
    whole = row_of(drawing, revisions, issues, floor_names)
    keys = list(drawing.floor_keys or [])
    if drawing.typical or len(keys) <= 1:
        return [whole]
    return [{**whole, "key": f"sd:{drawing.id}:{key}", "floor": floor_names.get(key, drawing_log._spelled(key)),
             "floor_keys": [key], "floors": 1} for key in keys]


def log(db: Session, project: Project, system: str) -> dict:
    """The Drawings Log of one system, from the records: every floor of the
    building, with its shop drawing at every official revision."""
    registry = building_floors.registry(db, project.id)
    floor_names = {f.floor_key: f.display_name for f in registry}
    drawings = (db.query(ProjectShopDrawing)
                .filter(ProjectShopDrawing.project_id == project.id, ProjectShopDrawing.system_code == system,
                        ProjectShopDrawing.active.is_(True))
                .options(selectinload(ProjectShopDrawing.revisions), selectinload(ProjectShopDrawing.candidates)).all())
    issues = drawing_issues.open_issues(db, project.id, system)
    by_drawing: dict[int, list] = {}
    for issue in issues:
        if issue.shop_drawing_id:
            by_drawing.setdefault(issue.shop_drawing_id, []).append(issue)
    top = max([r.number for d in drawings for r in d.revisions if r.submitted]
              + [_rev(c.revision) for d in drawings for c in d.candidates if c.candidate_status == "available"], default=-1)
    revisions = [f"R{n}" for n in range(max(MIN_REVISIONS, top + 1))]
    rows = [row for d in drawings for row in rows_of(d, revisions, by_drawing.get(d.id, []), floor_names)]
    covered = {key for row in rows for key in row["floor_keys"]}
    for floor in registry:
        if floor.floor_key in covered:
            continue
        rows.append({
            "key": f"floor:{floor.floor_key}", "id": None, "source": "ifc_floor", "reference": None,
            "floor": floor.display_name, "floor_named": None, "floor_keys": [floor.floor_key], "floors": 1, "typical": False,
            "confirmed": False, "remarks": "", "revision": None, "status": "not_submitted", "label": "Not Submitted",
            "path": None, "page": 1, "name": None, "revisions": {},
            "cells": {rev: dict(drawing_log.NOT_SUBMITTED) for rev in revisions},
            "latest_revision": None, "latest_status": "not_submitted", "latest_note": None, "latest_path": None,
            "latest_page": 1, "candidates": [], "hints": [], "issues": 0,
        })
    heights = {f.floor_key: f.elevation for f in registry}
    rows.sort(key=lambda row: drawing_log._height(row["floor_keys"], heights))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["latest_status"]] = counts.get(row["latest_status"], 0) + 1
    return {
        "revisions": revisions, "rows": rows, "counts": counts,
        "submissions": sum(1 for d in drawings if any(r.submitted for r in d.revisions)),
        "review_items": len(issues),
        "candidates": sum(1 for d in drawings for c in d.candidates if c.candidate_status == "available"),
        "floors": [{"key": f.floor_key, "name": f.display_name, "source": f.source, "ifc_sheet": f.ifc_sheet} for f in registry],
    }


def summary(db: Session, project: Project) -> list[dict]:
    """Every system's numbers, for the system cards and the design manager:
    floors, each status at the latest revision, review items."""
    out = []
    for system in project_systems(db, project):
        built = log(db, project, system)
        counts = built["counts"]
        approved = counts.get("approved", 0) + counts.get("approved_as_noted", 0)
        out.append({
            "code": system, "name": system_rules.CODE_NAMES.get(system, system),
            "floors": len(built["rows"]), "approved": counts.get("approved", 0),
            "approved_as_noted": counts.get("approved_as_noted", 0), "under_review": counts.get("under_review", 0),
            "not_approved": counts.get("not_approved", 0), "not_submitted": counts.get("not_submitted", 0),
            "reply_not_found": counts.get("reply_not_found", 0), "approved_total": approved,
            "review_items": built["review_items"], "candidates": built["candidates"], "counts": counts,
        })
    return out


def detail(db: Session, project: Project, drawing: ProjectShopDrawing) -> dict:
    registry = building_floors.registry(db, project.id, include_inactive=True)
    floor_names = {f.floor_key: f.display_name for f in registry}
    floors = {f.floor_key: f for f in registry}
    issues = [i for i in drawing_issues.open_issues(db, project.id, drawing.system_code) if i.shop_drawing_id == drawing.id]
    top = max([r.number for r in drawing.revisions if r.submitted] + [_rev(c.revision) for c in drawing.candidates], default=-1)
    row = row_of(drawing, [f"R{n}" for n in range(max(MIN_REVISIONS, top + 1))], issues, floor_names)
    events = (db.query(ShopDrawingEvent).filter(ShopDrawingEvent.project_id == project.id,
                                                ShopDrawingEvent.shop_drawing_id == drawing.id)
              .order_by(ShopDrawingEvent.at.desc(), ShopDrawingEvent.id.desc()).limit(100).all())
    return {
        **row,
        "system": drawing.system_code, "system_name": system_rules.CODE_NAMES.get(drawing.system_code, drawing.system_code),
        "floor_source": {key: {"source": floors[key].source, "ifc_sheet": floors[key].ifc_sheet, "active": floors[key].active}
                         for key in drawing.floor_keys or [] if key in floors},
        "revision_history": [_cell(r, drawing.drawing_reference) for r in sorted(drawing.revisions, key=lambda r: r.number)],
        "issues_list": [drawing_issues.out(i) for i in issues],
        "events": [event_out(e) for e in events],
    }


def event_out(e: ShopDrawingEvent) -> dict:
    return {"id": e.id, "kind": e.kind, "text": e.text, "system": e.system_code, "shop_drawing_id": e.shop_drawing_id,
            "floor_key": e.floor_key, "detail": e.detail or {}, "user_id": e.user_id, "at": e.at.isoformat() if e.at else None}


def events(db: Session, project: Project, system: str | None = None, limit: int = 200) -> list[dict]:
    query = db.query(ShopDrawingEvent).filter(ShopDrawingEvent.project_id == project.id)
    if system:
        query = query.filter((ShopDrawingEvent.system_code == system) | ShopDrawingEvent.system_code.is_(None))
    return [event_out(e) for e in query.order_by(ShopDrawingEvent.at.desc(), ShopDrawingEvent.id.desc()).limit(limit).all()]


# --- the engineer's side ----------------------------------------------------------------------


def confirm_candidate(db: Session, project: Project, candidate: ShopDrawingCandidate, user, *,
                      submission_reference: str | None = None) -> ShopDrawingRevision:
    """The engineer says the candidate was submitted: it becomes the
    official revision, under review until the consultant answers."""
    drawing = candidate.drawing
    revision = next((r for r in drawing.revisions if r.revision == candidate.revision), None)
    now = utc_now()
    if revision is None:
        revision = ShopDrawingRevision(shop_drawing_id=drawing.id, revision=candidate.revision, number=_rev(candidate.revision),
                                       created_at=now, updated_at=now)
        db.add(revision)
    revision.submitted, revision.status, revision.source = True, "under_review", "engineer"
    revision.drawing_path, revision.drawing_page, revision.drawing_sha256 = candidate.path, candidate.page, candidate.file_sha256
    revision.submission_reference = submission_reference or revision.submission_reference
    revision.submitted_at = revision.submitted_at or now
    revision.note = f"Submission confirmed by an engineer on {now:%d %b %Y}" + (f" ({submission_reference})" if submission_reference else "")
    revision.updated_at = now
    candidate.candidate_status, candidate.decided_by_id, candidate.decided_at = "confirmed", user.id if user else None, now
    event(db, project, "revision.confirmed", f"{drawing.drawing_reference} {candidate.revision} confirmed as submitted",
          system=drawing.system_code, drawing=drawing, user=user, detail={"revision": candidate.revision,
                                                                          "submission_reference": submission_reference})
    _settle_candidate_issues(db, project, candidate, user, "Confirmed as submitted")
    db.flush()
    return revision


def ignore_candidate(db: Session, project: Project, candidate: ShopDrawingCandidate, user, reason: str = "") -> None:
    """Not a submission: remembered against the file, so the next sync
    does not ask again unless the file changes."""
    drawing = candidate.drawing
    candidate.candidate_status, candidate.decided_by_id, candidate.decided_at = "ignored", user.id if user else None, utc_now()
    candidate.evidence = {**(candidate.evidence or {}), "ignored_reason": reason}
    event(db, project, "revision.ignored", f"{drawing.drawing_reference} {candidate.revision} ignored"
          + (f": {reason}" if reason else ""), system=drawing.system_code, drawing=drawing, user=user,
          detail={"revision": candidate.revision})
    _settle_candidate_issues(db, project, candidate, user, "Ignored by an engineer")
    db.flush()


def _settle_candidate_issues(db: Session, project: Project, candidate: ShopDrawingCandidate, user, resolution: str) -> None:
    for issue in drawing_issues.open_issues(db, project.id, candidate.drawing.system_code):
        if issue.shop_drawing_id == candidate.shop_drawing_id and (issue.detail or {}).get("candidate_id") == candidate.id:
            drawing_issues.resolve(db, issue, user, resolution)


def set_status(db: Session, project: Project, drawing: ProjectShopDrawing, revision_name: str, status: str, user,
               *, note: str = "", submitted: bool = True) -> ShopDrawingRevision:
    """An engineer's word on a revision's status: authoritative from now on."""
    if status not in OFFICIAL_STATUSES:
        raise ValueError(f"'{status}' is not a status")
    revision = next((r for r in drawing.revisions if r.revision == revision_name), None)
    now = utc_now()
    if revision is None:
        revision = ShopDrawingRevision(shop_drawing_id=drawing.id, revision=revision_name, number=_rev(revision_name),
                                       created_at=now, updated_at=now)
        db.add(revision)
    was = revision.status
    revision.status, revision.submitted, revision.source = status, submitted, "engineer"
    revision.confirmed_by_id, revision.confirmed_at, revision.updated_at = user.id if user else None, now, now
    revision.note = note or f"Set by an engineer on {now:%d %b %Y}"
    for candidate in drawing.candidates:
        if candidate.revision == revision_name and candidate.candidate_status == "available":
            candidate.candidate_status, candidate.decided_by_id, candidate.decided_at = "confirmed", user.id if user else None, now
            _settle_candidate_issues(db, project, candidate, user, "Status set by an engineer")
    event(db, project, "status.changed", f"{drawing.drawing_reference} {revision_name}: {STATUS_LABELS[status]}"
          + (f" (was {STATUS_LABELS.get(was, was)})" if was and was != status else ""),
          system=drawing.system_code, drawing=drawing, user=user, detail={"revision": revision_name, "status": status, "was": was})
    db.flush()
    return revision


def set_drawing(db: Session, project: Project, drawing: ProjectShopDrawing, user, *, drawing_reference: str | None = None,
                floor_keys: list[str] | None = None, remarks: str | None = None) -> ProjectShopDrawing:
    """An engineer corrects the reference, the floors or the remarks."""
    now = utc_now()
    changes = {}
    if drawing_reference and drawing_reference.strip() and drawing_reference.strip() != drawing.drawing_reference:
        changes["reference"] = (drawing.drawing_reference, drawing_reference.strip())
        drawing.drawing_reference = drawing_reference.strip()
    if floor_keys is not None and list(floor_keys) != list(drawing.floor_keys or []):
        changes["floors"] = (list(drawing.floor_keys or []), list(floor_keys))
        drawing.floor_keys = list(floor_keys)
    if remarks is not None:
        drawing.remarks = remarks
    if changes:
        drawing.confirmed_by_id, drawing.confirmed_at = user.id if user else None, now
        event(db, project, "reference.corrected", f"{drawing.drawing_reference}: " + "; ".join(
            f"{k} {a} -> {b}" for k, (a, b) in changes.items()), system=drawing.system_code, drawing=drawing, user=user,
              detail={k: {"from": a, "to": b} for k, (a, b) in changes.items()})
    drawing.updated_at = now
    db.flush()
    return drawing


def find_reply_document(rows: list[ProjectDocument], path: str | None) -> ProjectDocument | None:
    return next((r for r in rows if (r.relative_path or "") == (path or "")), None)


def is_shop_drawing_record(record) -> bool:
    return document_control.is_shop_drawing(record)
