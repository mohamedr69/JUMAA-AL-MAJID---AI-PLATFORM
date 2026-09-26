"""The building's floors, as the IFC drawings in force name them: the one
thing the Drawings Log takes from BOQ > As per IFC Drawings.

    IFC drawings in force
        -> their floor-plan sheets (the title's floor, the floors it stands for)
        -> normalised floor keys (a typical sheet for 3 to 14 is twelve floors)
        -> the project's aliases (which physical floor a name is, on this building)
        -> project_building_floors

Three things are kept apart: a floor's canonical identity ("L2"), the
names the documents give it ("L02", "LEVEL 02", "1ST MECHANICAL FLOOR
PLAN"), and the project's own aliases between them. "L2", "L02", "LEVEL
2" and "2ND FLOOR" are one floor everywhere (`app.ifc.comparison.floor_key`).
A special floor name -- 1st Mechanical, Structural, Non Accessible,
Transfer, Technical -- is never mapped to a level for every building:
which level it is here is this project's, and is settled in this order:

  1. the canonical key itself;
  2. the deterministic spellings above;
  3. an alias this project already holds (`project_floor_aliases`);
  4. a title on this project's own drawings naming both the level and the
     floor ("L02- 1ST MECHANICAL FLOOR PLAN"): deterministic evidence,
     merged and kept as an alias, source "evidence";
  5. the building's sequence -- a named sheet sitting where a level the
     shop drawings name has no IFC sheet -- is only a suspicion: a
     "possible duplicate floor" for the engineer, with the AI's opinion
     beside it where it has one, never merged on its own;
  6. the engineer: Merge, or Keep Separate, kept for good.

Refreshed when an IFC drawing is read, archived, deleted or given a floor
count -- not on the Drawings page's requests, which read the registry.
The IFC drawing's own sheet names, numbers and revisions are not a shop
drawing's anything, and are kept here only to say where a floor came from.

A floor the latest IFC no longer has is not removed: with shop drawing
history it stays, flagged "not in the latest IFC" (app.services.
drawing_issues); with none it is made inactive. A floor a shop drawing
names that no IFC plan has is added from the shop drawing, so the log
has a row for it. A floor merged into another keeps its row, inactive,
pointing at the floor it is now.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.ifc.comparison import floor_key
from app.ifc.resolve import floor_sheets, name_floor
from app.models import Project, ProjectBuildingFloor, ProjectFloorAlias, ProjectIfcDrawing
from app.services import drawing_log

log = logging.getLogger(__name__)

MERGE, SEPARATE = "merge", "separate"
EVIDENCE, ENGINEER, AI = "evidence", "engineer", "ai"


@dataclass
class FloorEntry:
    key: str
    display_name: str
    elevation: float
    ifc_sheet: str | None = None
    source: str = "ifc"
    # A sheet names the level itself ("L02 PLAN"), rather than reaching it through an alias.
    direct: bool = True
    secondary: str | None = None


@dataclass
class Suspect:
    """Two names that may be one physical floor: the named sheet and the level."""
    alias_key: str
    canonical_key: str
    alias_label: str
    canonical_label: str
    evidence: str
    context: dict = field(default_factory=dict)


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


def ifc_pairs(drawings: list[dict]) -> list[tuple]:
    """(floor, title) of every plan sheet, for reading aliases off the titles."""
    return [(sheet.get("floor_name"), sheet.get("title")) for d in drawings for sheet in d.get("sheets") or []
            if sheet.get("kind") == "plan"]


# --- the project's aliases -------------------------------------------------------------------------


def alias_label(key: str) -> str:
    """A floor name's identity, in words: MECHANICAL#1 is "1st Mechanical
    Floor", LIFT MACHINE is "Lift Machine"."""
    if "#" in key:
        return drawing_log._named_label(key)
    return drawing_log.floor_label(key)


def alias_rows(db: Session, project_id: int) -> list[ProjectFloorAlias]:
    return (db.query(ProjectFloorAlias).filter(ProjectFloorAlias.project_id == project_id)
            .order_by(ProjectFloorAlias.id).all())


def alias_map(rows: list[ProjectFloorAlias]) -> dict[str, str]:
    """{alias key: canonical key} of the aliases in force."""
    return {r.alias_key: r.canonical_key for r in rows if r.decision == MERGE}


def separate_pairs(rows: list[ProjectFloorAlias]) -> set[tuple[str, str]]:
    return {(r.alias_key, r.canonical_key) for r in rows if r.decision == SEPARATE}


def resolve_aliases(db: Session, project: Project, drawings: list[dict], shop_pairs: list[tuple] | None = None) -> dict[str, str]:
    """The project's alias map, in force: an engineer's word first, then
    what the project already holds, then what this project's own titles
    say now -- a title naming both a level and a floor ("L02- 1ST
    MECHANICAL FLOOR PLAN") ties them, and that is kept as an alias so
    every later IFC revision, sync and restart reuses it. Not committed."""
    rows = alias_rows(db, project.id)
    held = {r.alias_key: r for r in rows}
    evidence = drawing_log.floor_alias_evidence(list(shop_pairs or []) + ifc_pairs(drawings))
    now = utc_now()
    for key, (canonical, titles) in evidence.items():
        row = held.get(key)
        if row is None:
            row = ProjectFloorAlias(project_id=project.id, alias_key=key, canonical_key=canonical, alias_label=alias_label(key),
                                    decision=MERGE, source=EVIDENCE, evidence={"titles": titles[:5]}, created_at=now, updated_at=now)
            db.add(row)
            held[key] = row
            log.info("Floor alias of project %s from the drawings' titles: %s is %s (%s)", project.id, key, canonical, titles[:1])
        elif row.source == EVIDENCE and row.decision == MERGE:
            # Read off the titles: follows them. An engineer's row never does.
            if row.canonical_key != canonical:
                row.canonical_key, row.updated_at = canonical, now
            row.evidence = {"titles": titles[:5]}
    db.flush()
    return alias_map(list(held.values()))


def project_aliases(db: Session, project: Project, drawings: list[dict] | None = None) -> dict[str, str]:
    """The alias map with no records in hand (the IFC worker's path): what
    the project holds, plus what the IFC titles say."""
    return resolve_aliases(db, project, drawings if drawings is not None else in_force_as_log_input(db, project))


# --- the floors ------------------------------------------------------------------------------------


def _secondaries(aliases: dict[str, str], labels: dict[str, str] | None = None) -> dict[str, str]:
    """{canonical key: the alias names shown under it}."""
    out: dict[str, list[str]] = {}
    for key, canonical in aliases.items():
        name = (labels or {}).get(key) or alias_label(key)
        if name not in out.setdefault(canonical, []):
            out[canonical].append(name)
    return {k: " / ".join(v)[:200] for k, v in out.items()}


def floors_from_ifc(drawings: list[dict], aliases: dict[str, str] | None = None,
                    alias_labels: dict[str, str] | None = None) -> list[FloorEntry]:
    """Every floor the IFC drawings name, one entry per floor, in building
    order, each name brought to the floor it is by `aliases` (the project's:
    `resolve_aliases`)."""
    aliases = aliases or {}
    rows = drawing_log._rows(drawings, aliases)
    heights = drawing_log.floor_heights(rows)
    secondary = _secondaries(aliases, alias_labels)
    entries: dict[str, FloorEntry] = {}
    for row in rows:
        keys = sorted(row.keys, key=drawing_log._floor_order)
        # Whether this sheet names the level itself, or a floor an alias brings to it.
        raw = drawing_log.floor_identity(row.floor_name, row.title, {})
        for key in keys:
            direct = key in raw
            if len(keys) == 1:
                display = drawing_log.floor_label(row.floor_name) if direct else drawing_log._spelled(key)
            else:
                # A typical sheet's floors are each their own: "Level 3", not the run's title.
                display = drawing_log._spelled(key)
            elevation = drawing_log._elevation(key)
            if elevation is None:
                elevation = heights.get(key)
            if elevation is None:
                elevation = drawing_log._height([key], heights)[1]
            entry = entries.get(key)
            if entry is None:
                entries[key] = FloorEntry(key=key, display_name=display[:160], elevation=float(elevation), direct=direct,
                                          secondary=secondary.get(key),
                                          ifc_sheet=f"{row.drawing} {row.ifc_revision} · {row.sheet} · {row.title}"[:300])
            elif direct and not entry.direct:
                # The level's own sheet names it better than the alias's did.
                entry.display_name, entry.direct = display[:160], True
                entry.ifc_sheet = f"{row.drawing} {row.ifc_revision} · {row.sheet} · {row.title}"[:300]
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
            extra: list[FloorEntry] | None = None, aliases: dict[str, str] | None = None,
            drawings: list[dict] | None = None) -> dict:
    """Bring the registry up to the IFC drawings in force. Returns
    {"added": [...], "missing": [keys the latest IFC no longer has but
    which keep their history], "inactive": [...], "merged": [...]}. The
    caller commits.

    `floors_with_history`: the floors that have shop drawing records --
    those are never made inactive. `extra`: floors from the shop drawings
    that no IFC plan names. `aliases`: the project's alias map
    (`resolve_aliases`); left out, it is read from what the project holds
    and the IFC titles."""
    drawings = in_force_as_log_input(db, project) if drawings is None else drawings
    if aliases is None:
        aliases = project_aliases(db, project, drawings)
    labels = {r.alias_key: r.alias_label for r in alias_rows(db, project.id)}
    wanted = {e.key: e for e in floors_from_ifc(drawings, aliases, labels)}
    for entry in extra or ():
        held = wanted.get(entry.key)
        if held is None:
            wanted[entry.key] = entry
        elif not held.direct and entry.direct:
            # The level's own name, as the shop drawings write it ("L02"),
            # over a spelling made up for a level only an alias reaches.
            held.display_name, held.direct = entry.display_name, True
    secondary = _secondaries(aliases, labels)
    for key, entry in wanted.items():
        entry.secondary = secondary.get(key)
    existing = {f.floor_key: f for f in registry(db, project.id, include_inactive=True)}
    now = utc_now()
    added, missing, inactive, merged = [], [], [], []
    history = floors_with_history or set()
    for key, entry in wanted.items():
        row = existing.get(key)
        if row is None:
            row = ProjectBuildingFloor(project_id=project.id, floor_key=key, display_name=entry.display_name,
                                       elevation=entry.elevation, source=entry.source, ifc_sheet=entry.ifc_sheet,
                                       secondary_name=entry.secondary, first_detected_at=now, last_detected_at=now, active=True)
            db.add(row)
            added.append(key)
        else:
            if entry.source == "ifc" or row.source != "ifc":
                # A name the project has already given the floor ("L02", off
                # a shop drawing) stands over a spelling made up for a level
                # only an alias reaches ("Level 2"): the IFC worker, with no
                # shop drawing in hand, never takes it back.
                if entry.direct or not row.display_name:
                    row.display_name = entry.display_name
                row.elevation, row.ifc_sheet = entry.elevation, entry.ifc_sheet
                row.source = entry.source if row.source != "engineer" else row.source
            row.secondary_name = entry.secondary
            row.last_detected_at = now
            row.merged_into = None
            if not row.active:
                row.active = True
                added.append(key)
    ifc_keys = {e.key for e in floors_from_ifc(drawings, aliases, labels)}
    for key, row in existing.items():
        if key in wanted:
            continue
        canonical = aliases.get(key)
        if canonical is not None:
            # A name the project now knows as another floor: its row stays,
            # inactive, pointing at the floor it is.
            if row.active or row.merged_into != canonical:
                merged.append(key)
            row.active, row.merged_into = False, canonical
            continue
        if row.source != "ifc":
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
        # The alias goes under the canonical name, never inside it: a row
        # written as "L41 - 3rd Mechanical Floor" before the aliases were
        # kept apart reads "L41" with "3rd Mechanical Floor" beneath.
        under = row.secondary_name or secondary.get(row.floor_key)
        if under and row.display_name.upper().endswith(f" - {under}".upper()):
            row.display_name = row.display_name[:-len(under) - 3].rstrip()
            row.secondary_name = row.secondary_name or under
    db.flush()
    if added or inactive or merged:
        log.info("Building floors of project %s: %d added, %d inactive, %d merged, %d kept without an IFC plan",
                 project.id, len(added), len(inactive), len(merged), len(missing))
    return {"added": added, "missing": [k for k in missing if k not in ifc_keys], "inactive": inactive, "merged": merged}


def suspects(drawings: list[dict], shop_only_keys: set[str], aliases: dict[str, str],
             separate: set[tuple[str, str]]) -> list[Suspect]:
    """Two names that may be one physical floor, from the building's
    sequence: a sheet named by what it is ("1ST MECHANICAL FLOOR"), with
    no alias, sitting in the IFC's sheet order between level n and level
    m -- where a level between them exists only from the shop drawings,
    with no IFC sheet of its own. A suspicion for the engineer, never a
    merge: which level a mechanical floor is depends on the building."""
    rows = drawing_log._rows(drawings, aliases)
    ordered = sorted(rows, key=lambda r: r.order)
    levels = [(i, max(h for h in (drawing_log._elevation(k) for k in r.keys) if h is not None))
              for i, r in enumerate(ordered)
              if any(drawing_log._elevation(k) is not None for k in r.keys)]
    out: list[Suspect] = []
    for i, row in enumerate(ordered):
        keys = list(row.keys)
        if len(keys) != 1 or drawing_log._elevation(keys[0]) is not None or keys[0] in aliases:
            continue
        below = max((h for j, h in levels if j < i), default=None)
        above = min((h for j, h in levels if j > i), default=None)
        if below is None or above is None:
            continue
        for level in sorted(shop_only_keys, key=drawing_log._floor_order):
            height = drawing_log._elevation(level)
            if height is None or not (below < height < above) or (keys[0], level) in separate:
                continue
            named = drawing_log.floor_label(row.floor_name)
            neighbours = [drawing_log.floor_label(r.floor_name) for r in ordered[max(0, i - 1):i + 2] if r is not row]
            out.append(Suspect(
                alias_key=keys[0], canonical_key=level, alias_label=named, canonical_label=drawing_log._spelled(level),
                evidence=f"{named} sits between {' and '.join(neighbours) or 'the levels'} on the IFC drawing; "
                         f"{drawing_log._spelled(level)} is named only by the shop drawings, with no IFC sheet of its own.",
                context={"ifc_sheet": row.sheet, "ifc_title": row.title, "neighbours": neighbours}))
    return out


def as_log_floors(rows: list[ProjectBuildingFloor]) -> list[dict]:
    """The registry in the shape `drawing_log.build` takes as `floors`."""
    return [{"key": r.floor_key, "display": r.display_name, "secondary": r.secondary_name, "elevation": r.elevation,
             "order": r.sort_order, "source": r.source, "ifc_sheet": r.ifc_sheet} for r in rows if r.active]


def key_of(label: str) -> str:
    """One floor's canonical key from how a document names it."""
    keys = drawing_log.floors_named(label)
    return next(iter(keys)) if len(keys) == 1 else floor_key(label)[0]
