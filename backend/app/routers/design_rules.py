"""The design-rule catalogue the calculations draw on: part current draws
and battery units, entered from datasheets.

Shared by every project. Entries are versioned, never edited in place:
saving a part that already has an entry supersedes it with the next
version, so what a figure was computed from can always be traced.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import DesignRule, PartDatasheetLink, RoleEnum, User
from app.routers.projects import CREATOR_ROLES
from app.schemas_design import (
    BatteryUnitIn,
    DatasheetFileOut,
    DatasheetLibraryOut,
    DatasheetMatchOut,
    DatasheetRowOut,
    DatasheetSuggestionOut,
    DatasheetSystemOut,
    DesignRuleOut,
    EquipmentAuditOut,
    EquipmentCurrentIn,
    EquipmentCurrentOut,
    PartCurrentIn,
    SystemManufacturerOut,
)
from app.seed import BATTERY_UNIT_CATEGORY, PART_CURRENT_CATEGORY
from app.services import company_library
from app.services.battery_calculation import part_key
from app.services import datasheet_documents
from app.services.datasheet_library import SYSTEM_LIBRARIES, get_libraries, libraries_for

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
    # A figure an engineer typed in is a settled one: into the table, so the
    # part is known on every project from now on.
    from app.services import equipment_currents

    equipment_currents.upsert(db, part_no=payload.part_no.strip(), description=payload.description,
                              standby_ma=payload.standby_ma, alarm_ma=payload.alarm_ma, kind=equipment_currents.DEVICE,
                              source=payload.source.strip(), confirmed_by=current_user.full_name, user=current_user)
    return rule_out(rule)


@router.get("/equipment-currents", response_model=list[EquipmentCurrentOut])
def list_equipment_currents(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EquipmentCurrentOut]:
    """The equipment current table: every part that is settled -- no load,
    built into a module, or a device with its figures -- and the ones an
    engineer said draw current but gave no figure for yet."""
    from app.services import equipment_currents

    return [EquipmentCurrentOut(**equipment_currents.as_dict(r)) for r in equipment_currents.all_rows(db)]


@router.post("/equipment-currents", response_model=EquipmentCurrentOut)
def save_equipment_current(
    payload: EquipmentCurrentIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> EquipmentCurrentOut:
    """Add or correct one row. A settled row is also written to the
    catalogue as a confirmed entry, so every project with the part uses it
    at once and no engineer is asked to confirm it."""
    from app.services import equipment_currents

    if not payload.no_load and (payload.standby_ma is None) != (payload.alarm_ma is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Give both the standby and the alarm current, or neither")
    if payload.included_in and not payload.no_load:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A part built into another module draws no current of its own")
    try:
        row = equipment_currents.upsert(
            db, part_no=payload.part_no.strip(), description=payload.description, no_load=payload.no_load,
            standby_ma=payload.standby_ma, alarm_ma=payload.alarm_ma, included_in=(payload.included_in or "").strip() or None,
            source=payload.source.strip(), confirmed_by=current_user.full_name, user=current_user, aliases=payload.aliases,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if equipment_currents.settled(row):
        data, source = equipment_currents.rule_data(row, row.part_no, row.description)
        save_rule_version(db, PART_CURRENT_CATEGORY, row.key, data, source, current_user)
    return EquipmentCurrentOut(**equipment_currents.as_dict(row))


@router.post("/equipment-currents/audit", response_model=EquipmentAuditOut)
def audit_equipment_currents(
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> EquipmentAuditOut:
    """Walk the table against the datasheet library: link every row whose
    part has a sheet and no link yet, then report the rows with no sheet at
    all, links that no longer resolve, figures from a sheet that is not the
    part's own, sheets that changed since, and figures the sheet no longer
    gives. Reads the sheets; changes nothing but the links."""
    from app.services import equipment_currents

    linked = equipment_currents.link_datasheets(db)
    findings = equipment_currents.audit(db)
    return EquipmentAuditOut(rows=len(equipment_currents.all_rows(db)), findings=findings, linked=linked)


@router.delete("/equipment-currents/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_equipment_current(
    row_id: int,
    _current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> None:
    """Take a row out of the table. The catalogue keeps what projects
    already computed from; the part is simply no longer settled here."""
    from app.models import EquipmentCurrent

    row = db.get(EquipmentCurrent, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such row")
    db.delete(row)
    db.commit()


def _libraries() -> dict:
    return get_libraries()


@router.get("/datasheet-libraries", response_model=list[DatasheetLibraryOut])
def list_datasheet_libraries(_current_user: User = Depends(get_current_user)) -> list[DatasheetLibraryOut]:
    """Every manufacturer the platform can look a part up in.

    A brand is a folder under `library/datasheets/`, so this is what the
    library holds rather than what anyone configured. A brand named in
    `DATASHEET_LIBRARIES` whose folder is nowhere is listed as unavailable,
    since a silently absent library reads as a part with no datasheet.
    """
    settings = get_settings()
    available = _libraries()
    rows = [
        DatasheetLibraryOut(
            name=name,
            folder=str(library.folder),
            available=True,
            source=company_library.source_of(library.folder),
            datasheets=library.count(),
        )
        for name, library in sorted(available.items())
    ]
    configured = {**settings.datasheet_libraries, **settings.archive_datasheet_libraries}
    rows += [
        DatasheetLibraryOut(name=name.upper(), folder=location, available=False, source="missing")
        for name, location in sorted(configured.items())
        if name.upper() not in available
    ]
    return rows


@router.get("/datasheet-systems", response_model=list[DatasheetSystemOut])
def list_datasheet_systems(
    _current_user: User = Depends(get_current_user),
) -> list[DatasheetSystemOut]:
    """The library grouped by the system being designed.

    An engineer opens this looking for a fire alarm detector or an exit
    sign, not for a brand folder, so the brands are shown under the system
    they supply (`SYSTEM_LIBRARIES`). The count is the sheets the library
    would list, not every PDF in the folder: the submittal builder and the
    EST4 manuals share that folder without being datasheets, and a card
    promising 106 where the table then shows 75 is a card that lies.
    """
    available = _libraries()
    counts = {name: len(library.listing()) for name, library in available.items()}
    systems = []
    for system in SYSTEM_LIBRARIES:
        brands = [
            SystemManufacturerOut(
                name=name,
                datasheets=counts.get(name.upper(), 0),
                available=name.upper() in available,
            )
            for name in system["manufacturers"]
        ]
        systems.append(DatasheetSystemOut(
            code=system["code"],
            label=system["label"],
            description=system["description"],
            datasheets=sum(b.datasheets for b in brands),
            manufacturers=brands,
        ))
    return systems


@router.post("/datasheet-libraries/reindex", response_model=list[DatasheetLibraryOut])
def reindex_datasheet_libraries(
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
) -> list[DatasheetLibraryOut]:
    """Re-read the libraries now.

    A library's file listing is trusted for `LIBRARY_RESCAN_SECONDS` so that
    looking up a BOQ's fifty parts does not walk the folder fifty times.
    This is the way to see a datasheet added a moment ago without waiting
    for that interval.
    """
    for library in _libraries().values():
        library.reindex()
    return list_datasheet_libraries(_current_user)


@router.get("/datasheets/all", response_model=list[DatasheetFileOut])
def list_all_datasheets(
    manufacturer: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DatasheetFileOut]:
    """Every datasheet the platform holds, filed as the library files them.

    The library is shared, so this is the same list on every project: a
    project page shows it to say what can be looked up, and links to each
    file through `/datasheets/file`. The submittal builder and the EST4
    manuals live in the same synced folder and are not datasheets, so they
    are left out.
    """
    files = []
    references: dict[tuple[str, str], str] = {}
    for library in libraries_for(manufacturer, _libraries()):
        files += library.listing()
        references.update(
            {(library.name, path): row.reference_no for path, row in datasheet_documents.stored(db, library.name).items()}
        )
    return [
        DatasheetFileOut(
            library=f.library,
            path=f.path,
            folder=f.folder,
            filename=f.filename,
            document_no=f.document_no,
            pages=f.pages,
            size=f.size,
            reads_as_datasheet=f.reads_as_datasheet,
            unreadable=f.unreadable,
            modified=f.modified,
            reference_no=datasheet_documents.resolve(references.get((f.library, f.path)), f.document_no),
        )
        for f in files
    ]


# A suggestion's description is a label, not the datasheet: the ordering
# tables run several sentences into one cell.
SUGGESTION_DESCRIPTION_CHARS = 120


@router.get("/datasheets/index", response_model=list[DatasheetSuggestionOut])
def datasheet_index(
    manufacturer: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DatasheetSuggestionOut]:
    """What the datasheet search can be asked for, for its dropdown.

    The library's own files, and the part numbers recorded against them.
    Neither alone is what an engineer types: a part whose sheet is named
    for it has no link (the search finds it by file name), and a variant
    on another model's sheet is only ever found through its link. The
    list is small -- a brand's library is tens of files and hundreds of
    parts -- so it is sent once per manufacturer and filtered as the
    engineer types, which is what makes the dropdown answer immediately.
    """
    libraries = libraries_for(manufacturer, _libraries())
    names = [library.name for library in libraries]

    references: dict[tuple[str, str], str] = {}
    for library in libraries:
        references.update(
            {(library.name, path): row.reference_no for path, row in datasheet_documents.stored(db, library.name).items()}
        )

    # A sheet is offered under its document number when it has one, with
    # its file name beside it -- that is what an engineer now sees in the
    # list, so it is what they will type.
    out = []
    for library in libraries:
        for f in library.listing():
            reference = datasheet_documents.resolve(references.get((f.library, f.path)), f.document_no)
            out.append(DatasheetSuggestionOut(
                kind="document",
                label=reference or Path(f.filename).stem,
                description=Path(f.filename).stem if reference else (f.folder or None),
                library=f.library,
                path=f.path,
                document_no=f.document_no,
                reference_no=reference,
            ))

    if names:
        links = (
            db.query(PartDatasheetLink)
            .filter(PartDatasheetLink.library.in_(names))
            .order_by(PartDatasheetLink.part_no)
            .all()
        )
        out += [
            DatasheetSuggestionOut(
                kind="part",
                label=link.part_no,
                description=(link.note or "")[:SUGGESTION_DESCRIPTION_CHARS].strip() or None,
                library=link.library,
                path=link.path,
                reference_no=references.get((link.library, link.path)),
            )
            for link in links
        ]
    return out


@router.get("/datasheets", response_model=list[DatasheetMatchOut])
def find_datasheets(
    part_no: str,
    manufacturer: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DatasheetMatchOut]:
    """The datasheets in the manufacturer's library that document the part,
    best first, with the rows of each that give a current. A sheet recorded
    for the part in the equipment table comes first."""
    from app.services import equipment_currents

    libraries = _libraries()
    matches = []
    row = equipment_currents.datasheet_for(db, part_no)
    mapped = equipment_currents.mapped_match(row, libraries) if row is not None else None
    if mapped is not None:
        matches.append(mapped[1])
    for library in libraries_for(manufacturer, libraries):
        matches += [m for m in library.find(part_no)
                    if not any(m.path == x.path and m.library == x.library for x in matches)]
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


# Wide enough to stay sharp on the card, small enough that a page of
# rows does not pull megabytes of preview.
THUMBNAIL_WIDTH = 240


@router.get("/datasheets/thumbnail")
def datasheet_thumbnail(
    library: str,
    path: str,
    _current_user: User = Depends(get_current_user),
) -> Response:
    """The first page of a datasheet as a PNG, for the preview on the card.

    Rendered on the way out rather than kept: the libraries are tens of
    files, a page renders in milliseconds, and a stored thumbnail is one
    more thing to invalidate when the synced folder changes underneath.
    The browser is told to keep it for an hour, which is what stops a
    scroll re-rendering the same page.
    """
    import pymupdf

    found = _libraries().get(library.upper())
    file = found.resolve(path) if found else None
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such datasheet in the library")
    try:
        with pymupdf.open(file) as document:
            page = document.load_page(0)
            scale = THUMBNAIL_WIDTH / page.rect.width if page.rect.width else 1
            png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png")
    except Exception:
        # An unreadable PDF is already flagged in the listing; the card
        # simply shows no preview rather than failing the page.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This datasheet could not be rendered")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


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
