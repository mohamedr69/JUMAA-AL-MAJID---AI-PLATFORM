"""The design-rule catalogue the calculations draw on: part current draws
and battery units, entered from datasheets.

Shared by every project. Entries are versioned, never edited in place:
saving a part that already has an entry supersedes it with the next
version, so what a figure was computed from can always be traced.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import DesignRule, User
from app.routers.projects import CREATOR_ROLES
from app.schemas_design import (
    BatteryUnitIn,
    DatasheetLibraryOut,
    DatasheetMatchOut,
    DatasheetRowOut,
    DesignRuleOut,
    PartCurrentIn,
)
from app.seed import BATTERY_UNIT_CATEGORY, PART_CURRENT_CATEGORY
from app.services.battery_calculation import part_key
from app.services.datasheet_library import get_libraries, libraries_for

router = APIRouter(prefix="/design-rules", tags=["design rules"])


def rule_out(rule: DesignRule) -> DesignRuleOut:
    return DesignRuleOut(
        id=rule.id, category=rule.category, key=rule.key, version=rule.version, data=rule.data, source=rule.source
    )


def active_rules(db: Session, category: str) -> list[DesignRule]:
    return (
        db.query(DesignRule)
        .filter(DesignRule.category == category, DesignRule.superseded_at.is_(None))
        .order_by(DesignRule.key)
        .all()
    )


def save_rule_version(db: Session, category: str, key: str, data: dict, source: str, user: User) -> DesignRule:
    """Add `data` as the rule's next version, superseding the current one --
    or return the current one unchanged if it already says the same."""
    if not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The part number has no letters or digits")
    current = (
        db.query(DesignRule)
        .filter(DesignRule.category == category, DesignRule.key == key, DesignRule.superseded_at.is_(None))
        .one_or_none()
    )
    def substance(values: dict) -> dict:
        # "4-cpu" and "4-CPU" are one part: its spelling is not a new version.
        return {k: v for k, v in values.items() if k != "part_no"}

    if current is not None and substance(current.data) == substance(data) and current.source == source:
        return current
    latest = (
        db.query(DesignRule.version)
        .filter(DesignRule.category == category, DesignRule.key == key)
        .order_by(DesignRule.version.desc())
        .first()
    )
    if current is not None:
        current.superseded_at = utc_now()
    rule = DesignRule(
        category=category,
        key=key,
        version=(latest[0] + 1) if latest else 1,
        data=data,
        source=source,
        created_by_id=user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/part-currents", response_model=list[DesignRuleOut])
def list_part_currents(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DesignRuleOut]:
    return [rule_out(r) for r in active_rules(db, PART_CURRENT_CATEGORY)]


@router.post("/part-currents", response_model=DesignRuleOut)
def save_part_current(
    payload: PartCurrentIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DesignRuleOut:
    data = {
        "part_no": payload.part_no.strip(),
        "standby_ma": payload.standby_ma,
        "alarm_ma": payload.alarm_ma,
        "description": payload.description,
    }
    rule = save_rule_version(db, PART_CURRENT_CATEGORY, part_key(payload.part_no), data, payload.source.strip(), current_user)
    return rule_out(rule)


def _libraries() -> dict:
    settings = get_settings()
    return get_libraries(settings.datasheet_libraries, settings.projects_root)


@router.get("/datasheet-libraries", response_model=list[DatasheetLibraryOut])
def list_datasheet_libraries(_current_user: User = Depends(get_current_user)) -> list[DatasheetLibraryOut]:
    settings = get_settings()
    available = _libraries()
    return [
        DatasheetLibraryOut(
            name=name.upper(),
            folder=str(available[name.upper()].folder) if name.upper() in available else location,
            available=name.upper() in available,
        )
        for name, location in settings.datasheet_libraries.items()
    ]


@router.get("/datasheets", response_model=list[DatasheetMatchOut])
def find_datasheets(
    part_no: str,
    manufacturer: str | None = None,
    _current_user: User = Depends(get_current_user),
) -> list[DatasheetMatchOut]:
    """The datasheets in the manufacturer's library that document the part,
    best first, with the rows of each that give a current."""
    matches = []
    for library in libraries_for(manufacturer, _libraries()):
        matches += library.find(part_no)
    return [
        DatasheetMatchOut(
            library=m.library,
            path=m.path,
            filename=m.filename,
            document_no=m.document_no,
            matched_on=m.matched_on,
            pages=m.pages,
            current_rows=[DatasheetRowOut(page=p, text=t) for p, t in m.current_rows],
            source=m.source,
        )
        for m in matches
    ]


@router.get("/datasheets/file")
def open_datasheet(
    library: str,
    path: str,
    _current_user: User = Depends(get_current_user),
) -> FileResponse:
    found = _libraries().get(library.upper())
    file = found.resolve(path) if found else None
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such datasheet in the library")
    # Inline, so the browser's PDF viewer opens it (and honours #page=N).
    return FileResponse(file, media_type="application/pdf", content_disposition_type="inline", filename=file.name)


@router.get("/battery-units", response_model=list[DesignRuleOut])
def list_battery_units(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DesignRuleOut]:
    return [rule_out(r) for r in active_rules(db, BATTERY_UNIT_CATEGORY)]


@router.post("/battery-units", response_model=DesignRuleOut)
def save_battery_unit(
    payload: BatteryUnitIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> DesignRuleOut:
    data = {
        "part_no": payload.part_no.strip(),
        "capacity_ah": payload.capacity_ah,
        "voltage": payload.voltage,
        "description": payload.description,
        "brand": payload.brand.strip().upper() if payload.brand else None,
    }
    rule = save_rule_version(db, BATTERY_UNIT_CATEGORY, part_key(payload.part_no), data, payload.source.strip(), current_user)
    return rule_out(rule)
