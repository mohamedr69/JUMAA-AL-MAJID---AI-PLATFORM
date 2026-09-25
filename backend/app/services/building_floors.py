"""The building's floors, as the IFC drawings in force name them: the one
thing the Drawings Log takes from BOQ > As per IFC Drawings.

    IFC drawings in force
        -> their floor-plan sheets (the title's floor, the floors it stands for)
        -> normalised floor keys (a typical sheet for 3 to 14 is twelve floors)
        -> project_building_floors

Refreshed when an IFC drawing is read, archived, deleted or given a floor
count -- not on the Drawings page's requests, which read the registry.
The IFC drawing's own sheet names, numbers and revisions are not a shop
drawing's anything, and are kept here only to say where a floor came from.

A floor the latest IFC no longer has is not removed: with shop drawing
history it stays, flagged "not in the latest IFC" (app.services.
drawing_issues); with none it is made inactive. A floor a shop drawing
names that no IFC plan has is added from the shop drawing, so the log
has a row for it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.ifc.comparison import floor_key
from app.ifc.resolve import floor_sheets, name_floor
from app.models import Project, ProjectBuildingFloor, ProjectIfcDrawing
from app.services import drawing_log

log = logging.getLogger(__name__)


@dataclass
class FloorEntry:
    key: str
    display_name: str
    elevation: float
    ifc_sheet: str | None = None
    source: str = "ifc"


def ifc_sheets(drawing: ProjectIfcDrawing) -> list[dict]:
    """The drawing's floor-plan sheets with the floor each names and the
    floors it stands for -- the floor overrides in force -- without
    resolving a single symbol."""
    sheets, _single = floor_sheets(drawing)
    for sheet in sheets:
        name_floor(sheet)
    return sheets


def in_force_as_log_input(db: Session, project: Project) -> list[dict]:
    """The IFC drawings in force in the shape `drawing_log` reads floors
    from: id, filename, revision and sheets. Cheap: the sheets alone."""
    from app.ifc.services import revisions

    return [{"id": d.id, "filename": d.filename, "revision": d.revision or "R0", "sheets": ifc_sheets(d)}
            for d in revisions.in_force(db, project.id)]


def floors_from_ifc(drawings: list[dict], shop_pairs: list[tuple] | None = None) -> list[FloorEntry]:
    """Every floor the IFC drawings name, one entry per floor, in building
    order. `shop_pairs`: (floor, title) of the shop drawings, so a floor
    named in words ("1ST MECHANICAL") is tied to its level the same way
    the log ties it."""
    aliases = drawing_log.floor_aliases(
        list(shop_pairs or [])
        + [(sheet.get("floor_name"), sheet.get("title")) for d in drawings for sheet in d.get("sheets") or []
           if sheet.get("kind") == "plan"])
    rows = drawing_log._rows(drawings, aliases)
    heights = drawing_log.floor_heights(rows)
    named_as = {level: name for name, level in aliases.items()}
    entries: dict[str, FloorEntry] = {}
    for row in rows:
        keys = sorted(row.keys, key=drawing_log._floor_order)
        for key in keys:
            if key in entries:
                continue
            if len(keys) == 1:
                display = drawing_log.floor_label(row.floor_name)
                if key in named_as:
                    extra = drawing_log._named_label(named_as[key])
                    if extra.upper() not in display.upper():
                        display = f"{display} - {extra}"
            else:
                # A typical sheet's floors are each their own: "Level 3", not the run's title.
                display = drawing_log._spelled(key)
            elevation = drawing_log._elevation(key)
            if elevation is None:
                elevation = heights.get(key)
            if elevation is None:
                elevation = drawing_log._height([key], heights)[1]
            entries[key] = FloorEntry(key=key, display_name=display[:160], elevation=float(elevation),
                                      ifc_sheet=f"{row.drawing} {row.ifc_revision} · {row.sheet} · {row.title}"[:300])
    return sorted(entries.values(), key=lambda e: (e.elevation, drawing_log._floor_order(e.key)))


def floor_from_shop_drawing(key: str, label: str | None) -> FloorEntry:
    """A floor a shop drawing names that no IFC plan has."""
    display = drawing_log.floor_label(label) if label and len(drawing_log.floors_named(label)) == 1 else drawing_log._spelled(key)
    elevation = drawing_log._elevation(key)
    if elevation is None:
        elevation = drawing_log._height([key], {})[1]
    return FloorEntry(key=key, display_name=display[:160], elevation=float(elevation), source="shop_drawing")


def registry(db: Session, project_id: int, *, include_inactive: bool = False) -> list[ProjectBuildingFloor]:
    query = db.query(ProjectBuildingFloor).filter(ProjectBuildingFloor.project_id == project_id)
    if not include_inactive:
        query = query.filter(ProjectBuildingFloor.active.is_(True))
    return query.order_by(ProjectBuildingFloor.sort_order, ProjectBuildingFloor.id).all()


def refresh(db: Session, project: Project, *, floors_with_history: set[str] | None = None,
            extra: list[FloorEntry] | None = None) -> dict:
    """Bring the registry up to the IFC drawings in force. Returns
    {"added": [...], "missing": [keys the latest IFC no longer has but
    which keep their history], "inactive": [...]}. The caller commits.

    `floors_with_history`: the floors that have shop drawing records --
    those are never made inactive. `extra`: floors from the shop drawings
    that no IFC plan names."""
    drawings = in_force_as_log_input(db, project)
    wanted = {e.key: e for e in floors_from_ifc(drawings)}
    for entry in extra or ():
        wanted.setdefault(entry.key, entry)
    existing = {f.floor_key: f for f in registry(db, project.id, include_inactive=True)}
    now = utc_now()
    added, missing, inactive = [], [], []
    history = floors_with_history or set()
    for key, entry in wanted.items():
        row = existing.get(key)
        if row is None:
            row = ProjectBuildingFloor(project_id=project.id, floor_key=key, display_name=entry.display_name,
                                       elevation=entry.elevation, source=entry.source, ifc_sheet=entry.ifc_sheet,
                                       first_detected_at=now, last_detected_at=now, active=True)
            db.add(row)
            added.append(key)
        else:
            if entry.source == "ifc" or row.source != "ifc":
                row.display_name, row.elevation, row.ifc_sheet = entry.display_name, entry.elevation, entry.ifc_sheet
                row.source = entry.source if row.source != "engineer" else row.source
            row.last_detected_at = now
            if not row.active:
                row.active = True
                added.append(key)
    ifc_keys = {e.key for e in floors_from_ifc(drawings)}
    for key, row in existing.items():
        if key in wanted or row.source != "ifc":
            continue
        if not drawings:
            continue    # no IFC in force at all: nothing to compare against, the registry stands
        if key in history:
            missing.append(key)
        elif row.active:
            row.active = False
            inactive.append(key)
    db.flush()
    rows = registry(db, project.id, include_inactive=True)
    for order, row in enumerate(sorted(rows, key=lambda r: (r.elevation, drawing_log._floor_order(r.floor_key)))):
        row.sort_order = order
    db.flush()
    if added or inactive:
        log.info("Building floors of project %s: %d added, %d inactive, %d kept without an IFC plan",
                 project.id, len(added), len(inactive), len(missing))
    return {"added": added, "missing": [k for k in missing if k not in ifc_keys], "inactive": inactive}


def as_log_floors(rows: list[ProjectBuildingFloor]) -> list[dict]:
    """The registry in the shape `drawing_log.build` takes as `floors`."""
    return [{"key": r.floor_key, "display": r.display_name, "elevation": r.elevation, "order": r.sort_order,
             "source": r.source, "ifc_sheet": r.ifc_sheet} for r in rows if r.active]


def key_of(label: str) -> str:
    """One floor's canonical key from how a document names it."""
    keys = drawing_log.floors_named(label)
    return next(iter(keys)) if len(keys) == 1 else floor_key(label)[0]
