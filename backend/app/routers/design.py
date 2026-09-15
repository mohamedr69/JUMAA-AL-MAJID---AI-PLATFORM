"""The project's design calculations -- so far Voice Evacuation amplifier
loading, imported from the engineer's amplifier workbook and then edited.
"""

import os
import re
from pathlib import Path

import threading
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
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
from app.services import activity, calc_integrity, concurrency
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
    included_in_module,
    part_key,
)
from app.services.battery_export import battery_workbook
from app.services.battery_pdf import battery_calculation_pdf, battery_systems_title, panel_manufacturer
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


def _design_version(project: Project) -> int:
    """The version a design save must name; 0 before anything is saved."""
    return project.design.version if project.design is not None else 0


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
        design_version=_design_version(project),
    )


def _store(db: Session, project: Project, design: VoiceEvacuationDesign, user: User, *, imported: bool = False) -> None:
    document = DesignDocument.model_validate(project.design.document) if project.design else DesignDocument()
    document.voice_evacuation = design
    # A new dict every time: the JSON column only notices reassignment.
    payload = document.model_dump(mode="json")
    if project.design is None:
        project.design = ProjectDesign(document=payload, updated_by_id=user.id, version=1)
    else:
        project.design.document = payload
        project.design.updated_by_id = user.id
        project.design.updated_at = utc_now()
    db.commit()
    activity.record(db, user, "design.ve_imported" if imported else "design.ve_saved",
                    "Imported the amplifier workbook" if imported else "Saved the amplifier calculation",
                    project=project, entity_type="design", entity_id=project.id,
                    detail={"zones": len(design.zones), "channels": len(design.channels)})
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
    _store(db, project, design, current_user, imported=True)
    return _out(db, project)


def _datasheet_libraries() -> dict:
    settings = get_settings()
    return get_libraries()


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
    result.design_version = _design_version(project)
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
        rejected = {r.key for r in active_rules(db, PART_CURRENT_CATEGORY) if r.data.get("rejected_no_load")}
        for line in _missing_parts(result.panels):
            host = included_in_module(line.part_no)
            if part_key(line.part_no or "") in rejected and not host:
                # An engineer rejected setting this part to no current: only a
                # datasheet figure or a typed one may fill it now.
                reading, match = _read_from_datasheets(line, libraries, panel_voltage)
                if not (reading and match):
                    continue
            # Asked before the datasheet, not after: a part built into another
            # module has no current of its own, so a figure read off the host
            # module's sheet would be that module's current counted twice.
            reading, match = (None, None) if host else _read_from_datasheets(line, libraries, panel_voltage)
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
            elif host:
                source = (
                    f"No current of its own: built into {host}, so it is already counted in "
                    f"{host}'s figure (platform owner)"
                )
                data = {
                    "part_no": line.part_no, "standby_ma": 0, "alarm_ma": 0,
                    "description": line.description, "auto": True, "no_load": True,
                    "included_in": host,
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
    currents: dict = {}
    if rule is not None:
        currents = {
            r.key: PartCurrent(
                r.data["standby_ma"], r.data["alarm_ma"], rule_id=r.id, rule_version=r.version,
                source=r.source, datasheet=r.data.get("datasheet"),
            )
            for r in active_rules(db, PART_CURRENT_CATEGORY)
            # A rejected automatic setting carries no figure: the part has no current.
            if not r.data.get("rejected_no_load")
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

    # Automatic "draws nothing" classifications of this project's parts that
    # no engineer has confirmed: shown for confirmation, and the calculation
    # is not complete until they are.
    named = {part_key(line.catalog_no or "") for line in lines if line.catalog_no}
    needs_confirmation = [
        {"part_no": r.data.get("part_no") or r.key, "description": r.data.get("description"), "reason": r.source,
         "rule_id": r.id, "rule_version": r.version}
        for r in active_rules(db, PART_CURRENT_CATEGORY)
        if r.key in named and r.data.get("auto") and r.data.get("no_load") and not r.data.get("confirmed_by")
    ]
    reasons: list[str] = []
    if rule is None:
        reasons.append("No battery sizing rule is configured.")
    lower = [p.name or p.heading for p in panels if p.lower_bound]
    if lower:
        reasons.append(f"{len(lower)} panel{'s have' if len(lower) != 1 else ' has'} parts without a current, so the "
                       f"load is a lower bound: {', '.join(lower[:6])}.")
    if needs_confirmation:
        reasons.append(f"{len(needs_confirmation)} part{'s were' if len(needs_confirmation) != 1 else ' was'} set to draw "
                       "no current automatically and must be confirmed by an engineer.")

    out = BatteryCalculationOut(
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
        complete=not reasons,
        incomplete_reasons=reasons,
        needs_confirmation=needs_confirmation,
    )
    out.input_hash = calc_integrity.stable_hash(
        calc_integrity.battery_inputs(lines, design, rule, selection, currents, batteries))
    out.result_hash = calc_integrity.stable_hash(calc_integrity.battery_result(panels))
    return out


@router.post("/{project_id}/design/battery/confirm-no-load", response_model=BatteryCalculationOut)
def confirm_no_load(
    project_id: int,
    payload: dict,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BatteryCalculationOut:
    """An engineer confirms that a part the platform set to draw no current
    really draws none. Recorded as a new catalogue version naming them, so
    every project with the part sees it confirmed -- or, with
    `{"part_no", "confirm": false}`, withdrawn to have no current at all,
    which makes the load a lower bound again until a figure is entered."""
    project = _get_project_or_404(db, project_id)
    part_no = str(payload.get("part_no") or "").strip()
    if not part_no:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name the part")
    key = part_key(part_no)
    current = next((r for r in active_rules(db, PART_CURRENT_CATEGORY) if r.key == key), None)
    if current is None or not current.data.get("no_load"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That part is not set to draw no current")
    if payload.get("confirm", True):
        data = {**current.data, "confirmed_by": current_user.full_name, "confirmed_by_id": current_user.id,
                "confirmed_at": utc_now().isoformat()}
        source = f"{current.source or ''} -- confirmed by {current_user.full_name}"
        summary = f"Confirmed {part_no} draws no current"
    else:
        # Rejected: a version that carries no figure, so the part is missing a
        # current again -- and that the automatic fill will not set back.
        data = {"part_no": current.data.get("part_no") or part_no, "description": current.data.get("description"),
                "rejected_no_load": True, "rejected_by": current_user.full_name, "rejected_at": utc_now().isoformat()}
        save_rule_version(db, PART_CURRENT_CATEGORY, key, data,
                          f"No-current setting rejected by {current_user.full_name}: enter the part's current", current_user)
        activity.record(db, current_user, "design.no_load_rejected", f"Rejected the no-current setting for {part_no}",
                        project=project, entity_type="design_rule", entity_id=current.id)
        return get_battery_calculation(project_id, current_user, db)
    save_rule_version(db, PART_CURRENT_CATEGORY, key, data, source[:1000], current_user)
    activity.record(db, current_user, "design.no_load_confirmed", summary, project=project,
                    entity_type="design_rule", entity_id=current.id)
    return get_battery_calculation(project_id, current_user, db)


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
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> BatteryCalculationOut:
    """Save the panels' names, locations, calculation settings and added
    components, and return the recalculation. Refused with 409 when
    `If-Match` names a design version someone else's save has moved past."""
    project = _get_project_or_404(db, project_id)
    concurrency.require_current(if_match, _design_version(project), "The design inputs")
    document = DesignDocument.model_validate(project.design.document) if project.design else DesignDocument()
    document.battery = payload
    content = document.model_dump(mode="json")
    if project.design is None:
        project.design = ProjectDesign(document=content, updated_by_id=current_user.id, version=1)
    else:
        project.design.document = content
        project.design.updated_by_id = current_user.id
        project.design.updated_at = utc_now()
    db.commit()
    activity.record(db, current_user, "design.battery_saved", "Saved the battery calculation",
                    project=project, entity_type="design", entity_id=project.id)
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


@router.get("/{project_id}/design/battery/export.pdf")
def export_battery_calculation_pdf(
    project_id: int,
    panel: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The battery calculation as the sheet the team issues.

    Laid out to FACP_Battery_Calculation_Clean.pdf in the submittal builder,
    one page per panel. The workbook export stays for working in Excel; this
    is the one that goes to a consultant.
    """
    project = _get_project_or_404(db, project_id)
    result = _battery_calculation(db, project)
    panels = [p for p in result.panels if panel is None or p.key == panel]
    if not panels:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such panel")

    content = battery_calculation_pdf(
        project,
        panels,
        systems=battery_systems_title(project, panels).upper(),
        manufacturers={p.key: panel_manufacturer(project, p) for p in panels},
    )
    name = f"EP-{project.ep_number} Battery Calculation" + (f" {panels[0].name}" if panel else "") + ".pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"},
    )


@router.put("/{project_id}/design/ve", response_model=VoiceEvacuationOut)
def update_voice_evacuation(
    project_id: int,
    payload: VoiceEvacuationDesign,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> VoiceEvacuationOut:
    """Save the engineer's edits. Where it came from and the limit it is held
    to are the server's to keep: they are taken from the stored design, not
    from the request, so an edit cannot loosen the limit."""
    project = _get_project_or_404(db, project_id)
    concurrency.require_current(if_match, _design_version(project), "The design inputs")
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
