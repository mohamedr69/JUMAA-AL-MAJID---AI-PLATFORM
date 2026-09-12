"""The project's design calculations -- so far Voice Evacuation amplifier
loading, imported from the engineer's amplifier workbook and then edited.
"""

import os
import re
from pathlib import Path

import threading
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import DesignRule, Project, ProjectDesign, User
from app.routers.design_rules import active_rules, rule_out, save_rule_version
from app.routers.projects import CREATOR_ROLES, XLSX_MEDIA_TYPE, _get_project_or_404
from app.schemas_design import (
    BatteryCalculationOut,
    BatteryDesign,
    BatterySetOut,
    PanelSettings,
    BatteryFillOut,
    BatteryLineOut,
    FilledCurrentOut,
    UnresolvedPartOut,
    DesignDocument,
    DesignRuleOut,
    VoiceEvacuationDesign,
    VoiceEvacuationImportIn,
    VoiceEvacuationOut,
    WorkbookCandidateOut,
    WorkbookSource,
)
from app.seed import (
    BATTERY_SELECTION_CATEGORY,
    BATTERY_SELECTION_KEY,
    BATTERY_SIZING_CATEGORY,
    BATTERY_SIZING_KEY,
    BATTERY_UNIT_CATEGORY,
    PART_CURRENT_CATEGORY,
    VE_LOAD_LIMIT_CATEGORY,
    VE_LOAD_LIMIT_KEY,
)
from app.services.battery_calculation import (
    MECHANICAL_RE,
    BatteryUnit,
    BoqLine,
    PartCurrent,
    Sizing,
    calculate_boq,
    calculate_panel,
    group_lines,
    part_key,
)
from app.services.battery_export import battery_workbook
from app.services.datasheet_currents import read_part_current
from app.services.datasheet_library import get_libraries, libraries_for
from app.services.ve_calculation import calculate
from app.services.ve_workbook_reader import WorkbookReadError, read_amplifier_workbook

router = APIRouter(prefix="/projects", tags=["design"])

# Makes a second overlapping fill (React StrictMode opens the page twice in
# dev) wait for the first instead of reading the same datasheets and racing
# it to write the same catalogue versions.
_fill_lock = threading.Lock()

WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}
# An amplifier calculation is named for what it is ("AMP TA CALC", "APS&BPS",
# "Amplifier Calculation") -- these float to the top of the list, the rest
# of the project's workbooks follow in case it is named some other way.
LIKELY_WORKBOOK_RE = re.compile(r"amp|aps|speaker|voice|evac|\bve\b|\bpa\b", re.IGNORECASE)
MAX_WORKBOOK_CANDIDATES = 300


def _project_folder(project: Project) -> Path:
    if not project.source_folder_path:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The project has no archive folder to read from")
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="The project's archive folder is not reachable")
    return folder


def _active_rule(db: Session, category: str, key: str) -> DesignRule | None:
    return (
        db.query(DesignRule)
        .filter(DesignRule.category == category, DesignRule.key == key, DesignRule.superseded_at.is_(None))
        .order_by(DesignRule.version.desc())
        .first()
    )


def _rule_out(rule: DesignRule | None) -> DesignRuleOut | None:
    if rule is None:
        return None
    return DesignRuleOut(
        id=rule.id, category=rule.category, key=rule.key, version=rule.version, data=rule.data, source=rule.source
    )


def _stored_design(project: Project) -> VoiceEvacuationDesign | None:
    if project.design is None:
        return None
    return DesignDocument.model_validate(project.design.document).voice_evacuation


def _out(db: Session, project: Project) -> VoiceEvacuationOut:
    design = _stored_design(project)
    if design is None:
        return VoiceEvacuationOut(
            design=None, result=None, rule=_rule_out(_active_rule(db, VE_LOAD_LIMIT_CATEGORY, VE_LOAD_LIMIT_KEY))
        )
    rule = db.get(DesignRule, design.max_load_rule_id) if design.max_load_rule_id else None
    return VoiceEvacuationOut(
        design=design,
        result=calculate(design),
        rule=_rule_out(rule),
        updated_at=project.design.updated_at,
        updated_by=project.design.updated_by.full_name if project.design.updated_by else None,
    )


def _store(db: Session, project: Project, design: VoiceEvacuationDesign, user: User) -> None:
    document = DesignDocument.model_validate(project.design.document) if project.design else DesignDocument()
    document.voice_evacuation = design
    # A new dict every time: the JSON column only notices reassignment.
    payload = document.model_dump(mode="json")
    if project.design is None:
        project.design = ProjectDesign(document=payload, updated_by_id=user.id)
    else:
        project.design.document = payload
        project.design.updated_by_id = user.id
        project.design.updated_at = utc_now()
    db.commit()
    db.refresh(project)


@router.get("/{project_id}/design/ve", response_model=VoiceEvacuationOut)
def get_voice_evacuation(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VoiceEvacuationOut:
    return _out(db, _get_project_or_404(db, project_id))


@router.get("/{project_id}/design/ve/workbooks", response_model=list[WorkbookCandidateOut])
def list_amplifier_workbooks(
    project_id: int,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> list[WorkbookCandidateOut]:
    folder = _project_folder(_get_project_or_404(db, project_id))
    found: list[WorkbookCandidateOut] = []
    for directory, _dirs, files in os.walk(folder):
        for filename in files:
            # "~$..." is Excel's lock file for a workbook someone has open.
            if Path(filename).suffix.lower() not in WORKBOOK_SUFFIXES or filename.startswith("~$"):
                continue
            relative = (Path(directory) / filename).relative_to(folder).as_posix()
            found.append(
                WorkbookCandidateOut(path=relative, filename=filename, likely=bool(LIKELY_WORKBOOK_RE.search(filename)))
            )
            if len(found) >= MAX_WORKBOOK_CANDIDATES:
                break
        if len(found) >= MAX_WORKBOOK_CANDIDATES:
            break
    return sorted(found, key=lambda c: (not c.likely, c.path.lower()))


@router.post("/{project_id}/design/ve/import", response_model=VoiceEvacuationOut)
def import_voice_evacuation(
    project_id: int,
    payload: VoiceEvacuationImportIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> VoiceEvacuationOut:
    """Read the workbook and replace the project's VE design with it."""
    project = _get_project_or_404(db, project_id)
    folder = _project_folder(project).resolve()
    path = (folder / payload.path).resolve()
    # Only the project's own archive folder is readable through this.
    if not path.is_relative_to(folder) or path.suffix.lower() not in WORKBOOK_SUFFIXES or not path.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Not a workbook in this project's folder")

    rule = _active_rule(db, VE_LOAD_LIMIT_CATEGORY, VE_LOAD_LIMIT_KEY)
    if rule is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No amplifier load limit is configured")

    try:
        read = read_amplifier_workbook(path, payload.sheet)
    except WorkbookReadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    design = VoiceEvacuationDesign(
        source=WorkbookSource(
            path=path.relative_to(folder).as_posix(),
            sheet=read.sheet,
            sheets=read.sheets,
            imported_at=utc_now(),
            warnings=read.warnings,
        ),
        speaker_types=read.speaker_types,
        zones=read.zones,
        channels=read.channels,
        racks=read.racks,
        max_load_fraction=rule.data["fraction"],
        max_load_rule_id=rule.id,
    )
    _store(db, project, design, current_user)
    return _out(db, project)


def _datasheet_libraries() -> dict:
    settings = get_settings()
    return get_libraries(settings.datasheet_libraries, settings.projects_root)


def _missing_parts(panels) -> list[BatteryLineOut]:
    """One line per part with no current, in BOQ order."""
    seen: dict[str, BatteryLineOut] = {}
    for panel in panels:
        for line in panel.lines:
            if line.missing_current and line.part_no and part_key(line.part_no) not in seen:
                seen[part_key(line.part_no)] = line
    return list(seen.values())


def _read_from_datasheets(line: BatteryLineOut, libraries: dict, panel_voltage: float):
    """(reading, match) from the first of the part's datasheets that gives
    its current, or (None, the best match or None)."""
    best = None
    for library in libraries_for(line.manufacturer, libraries):
        for match in library.find(line.part_no or ""):
            best = best or match
            reading = read_part_current(
                library.folder / match.path,
                line.part_no or "",
                doc_named_for_part=match.matched_on in ("filename", "family"),
                panel_voltage=panel_voltage,
            )
            if reading:
                return reading, match
    return None, best


def _unresolved_reason(line: BatteryLineOut, libraries: dict) -> str:
    if not libraries:
        return "No datasheet library is available"
    matches = [m for lib in libraries_for(line.manufacturer, libraries) for m in lib.find(line.part_no or "")]
    if not matches:
        return "No datasheet in the library mentions this part"
    return f"{matches[0].filename} mentions it, but gives no current for this model"


@router.get("/{project_id}/design/battery", response_model=BatteryCalculationOut)
def get_battery_calculation(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatteryCalculationOut:
    """Size each panel's standby battery from the saved BOQ and the datasheet
    catalogue as they stand now (see app.services.battery_calculation)."""
    project = _get_project_or_404(db, project_id)
    result = _battery_calculation(db, project)
    libraries = _datasheet_libraries()
    result.unresolved = [
        UnresolvedPartOut(part_no=line.part_no or "", description=line.description, reason=_unresolved_reason(line, libraries))
        for line in _missing_parts(result.panels)
    ]
    return result


@router.post("/{project_id}/design/battery/fill-currents", response_model=BatteryFillOut)
def fill_battery_currents(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BatteryFillOut:
    """Read every missing part's current off its datasheet and record it in
    the catalogue, with the datasheet as its source; a part with no
    datasheet current whose BOQ description is mechanical (chassis, plate,
    cabinet, door, bracket) is recorded as drawing none. Only parts without
    a current are touched, so it is safe to call on every open."""
    project = _get_project_or_404(db, project_id)
    with _fill_lock:
        result = _battery_calculation(db, project)
        rule = result.rule
        panel_voltage = rule.data["panel_voltage"] if rule else 24
        libraries = _datasheet_libraries()
        filled: list[FilledCurrentOut] = []
        for line in _missing_parts(result.panels):
            reading, match = _read_from_datasheets(line, libraries, panel_voltage)
            if reading and match:
                pages = ", ".join(str(p) for p in reading.pages)
                source = f"{match.source.rsplit(', p.', 1)[0]}, p.{pages}: read automatically"
                if reading.notes:
                    source += " (" + "; ".join(reading.notes) + ")"
                data = {
                    "part_no": line.part_no, "standby_ma": reading.standby_ma, "alarm_ma": reading.alarm_ma,
                    "description": line.description, "auto": True,
                    "datasheet": {"library": match.library, "path": match.path, "pages": reading.pages},
                }
            elif MECHANICAL_RE.search(line.description):
                source = f"No electrical load: mechanical part (BOQ: \"{line.description[:80]}\"), set automatically"
                data = {
                    "part_no": line.part_no, "standby_ma": 0, "alarm_ma": 0,
                    "description": line.description, "auto": True, "no_load": True,
                }
            else:
                continue
            try:
                save_rule_version(db, PART_CURRENT_CATEGORY, part_key(line.part_no or ""), data, source[:1000], current_user)
            except IntegrityError:
                db.rollback()
                continue
            filled.append(FilledCurrentOut(part_no=line.part_no or "", standby_ma=data["standby_ma"], alarm_ma=data["alarm_ma"], source=source))
    return BatteryFillOut(filled=filled)


def _battery_calculation(db: Session, project: Project) -> BatteryCalculationOut:
    rule = _active_rule(db, BATTERY_SIZING_CATEGORY, BATTERY_SIZING_KEY)
    unit_rules = active_rules(db, BATTERY_UNIT_CATEGORY)
    selection = _active_rule(db, BATTERY_SELECTION_CATEGORY, BATTERY_SELECTION_KEY)
    brand = (selection.data.get("brand") or "").upper() if selection else ""
    batteries = _selectable_batteries(unit_rules, brand)
    lines = [
        BoqLine(
            system_code=item.system_code,
            group_heading=item.group_heading,
            catalog_no=item.catalog_no,
            description=item.description,
            quantity=item.quantity,
            manufacturer=item.manufacturer,
        )
        for item in project.boq_items
    ]

    design = _stored_battery_design(project)
    panels, groups = [], []
    if rule is not None:
        currents = {
            r.key: PartCurrent(
                r.data["standby_ma"], r.data["alarm_ma"], rule_id=r.id, rule_version=r.version,
                source=r.source, datasheet=r.data.get("datasheet"),
            )
            for r in active_rules(db, PART_CURRENT_CATEGORY)
        }
        defaults = {k: rule.data[k] for k in SIZING_FIELDS}
        types, groups = calculate_boq(lines, Sizing(**defaults), currents, batteries)
        members = {(system, heading): group for system, heading, group in group_lines(lines)}
        # One card per physical panel, numbered in BOQ order: a group quoting
        # two identical panels gives FACP-02 and FACP-03.
        for panel_type in types:
            for instance in range(1, panel_type.count + 1):
                key = f"{panel_type.system_code or ''}|{panel_type.heading}|{instance}"
                settings = design.panels.get(key) or PanelSettings()
                overridden = [f for f in SIZING_FIELDS if getattr(settings, f) is not None]
                sizing = {f: getattr(settings, f) if f in overridden else defaults[f] for f in SIZING_FIELDS}
                panel = calculate_panel(
                    panel_type.heading, panel_type.system_code, members[(panel_type.system_code, panel_type.heading)],
                    Sizing(**sizing), currents, batteries, extras=settings.extra_components,
                )
                panel.key, panel.instance = key, instance
                panel.name = settings.name or f"FACP-{len(panels) + 1:02d}"
                panel.location = settings.location
                panel.settings, panel.overridden = sizing, overridden
                panels.append(panel)

    unlisted: dict[str, dict] = {}
    for panel in panels:
        for quoted in panel.quoted:
            key = part_key(quoted.part_no or "")
            if key and key not in batteries and key not in unlisted:
                unlisted[key] = {"part_no": quoted.part_no, "capacity_ah": quoted.capacity_ah, "voltage": quoted.voltage}

    return BatteryCalculationOut(
        rule=_rule_out(rule),
        panels=panels,
        groups=groups,
        battery_units=[rule_out(r) for r in unit_rules],
        unlisted_batteries=list(unlisted.values()),
        design=design,
        selection_rule=_rule_out(selection),
        selectable=[
            BatterySetOut(
                part_no=u.part_no, capacity_ah=u.capacity_ah, voltage=u.voltage, units=1, strings=0, brand=u.brand,
                datasheet_library=(u.datasheet or {}).get("library"), datasheet_path=(u.datasheet or {}).get("path"),
            )
            for u in sorted(batteries.values(), key=lambda u: u.capacity_ah)
        ],
    )


SIZING_FIELDS = ("standby_hours", "alarm_minutes", "spare_factor", "panel_voltage")


def _selectable_batteries(unit_rules: list[DesignRule], brand: str) -> dict[str, BatteryUnit]:
    """The batteries a selection may choose from: every battery datasheet of
    the selection brand in the datasheet libraries (read off the datasheet
    itself -- "ES 65-12 / 12V - 65Ah"), plus catalogue entries of that brand.
    With no brand rule, every catalogued battery."""
    found: dict[str, BatteryUnit] = {}
    for library in _datasheet_libraries().values():
        for sheet, path in library.batteries():
            if brand and (sheet.brand or "").upper() != brand:
                continue
            found.setdefault(part_key(sheet.model), BatteryUnit(
                part_no=sheet.model, capacity_ah=sheet.capacity_ah, voltage=sheet.voltage, brand=sheet.brand,
                datasheet={"library": library.name, "path": path},
            ))
    for rule in unit_rules:
        if brand and (rule.data.get("brand") or "").upper() != brand:
            continue
        found[rule.key] = BatteryUnit(
            part_no=rule.data["part_no"], capacity_ah=rule.data["capacity_ah"], voltage=rule.data["voltage"],
            brand=rule.data.get("brand"),
        )
    return found


def _stored_battery_design(project: Project) -> BatteryDesign:
    if project.design is None:
        return BatteryDesign()
    return DesignDocument.model_validate(project.design.document).battery or BatteryDesign()


@router.put("/{project_id}/design/battery", response_model=BatteryCalculationOut)
def save_battery_design(
    project_id: int,
    payload: BatteryDesign,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BatteryCalculationOut:
    """Save the panels' names, locations, calculation settings and added
    components, and return the recalculation."""
    project = _get_project_or_404(db, project_id)
    document = DesignDocument.model_validate(project.design.document) if project.design else DesignDocument()
    document.battery = payload
    content = document.model_dump(mode="json")
    if project.design is None:
        project.design = ProjectDesign(document=content, updated_by_id=current_user.id)
    else:
        project.design.document = content
        project.design.updated_by_id = current_user.id
        project.design.updated_at = utc_now()
    db.commit()
    db.refresh(project)
    return get_battery_calculation(project_id, current_user, db)


@router.get("/{project_id}/design/battery/export.xlsx")
def export_battery_calculation(
    project_id: int,
    panel: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The battery calculation as a workbook, one sheet per panel (or just
    `panel`), with live formulas like the engineers' own sheets."""
    project = _get_project_or_404(db, project_id)
    result = _battery_calculation(db, project)
    panels = [p for p in result.panels if panel is None or p.key == panel]
    if not panels:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such panel")
    content = battery_workbook(project, panels, exported_by=current_user.full_name, exported_at=utc_now())
    name = f"EP-{project.ep_number} Battery Calculation" + (f" {panels[0].name}" if panel else "") + ".xlsx"
    return Response(
        content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"},
    )


@router.put("/{project_id}/design/ve", response_model=VoiceEvacuationOut)
def update_voice_evacuation(
    project_id: int,
    payload: VoiceEvacuationDesign,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> VoiceEvacuationOut:
    """Save the engineer's edits. Where it came from and the limit it is held
    to are the server's to keep: they are taken from the stored design, not
    from the request, so an edit cannot loosen the limit."""
    project = _get_project_or_404(db, project_id)
    stored = _stored_design(project)
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Import an amplifier workbook first")
    design = payload.model_copy(
        update={
            "source": stored.source,
            "max_load_fraction": stored.max_load_fraction,
            "max_load_rule_id": stored.max_load_rule_id,
        }
    )
    _store(db, project, design, current_user)
    return _out(db, project)
