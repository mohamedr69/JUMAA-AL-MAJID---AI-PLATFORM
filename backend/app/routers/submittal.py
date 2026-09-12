"""The material submittal register: the submittal documents of a project,
what they cover, which revision went out and where each stands.

The BOQ says what the project buys, so it is what a submittal is built from:
the register suggests the submittals the BOQ implies (one per system, with
the brand the DRF gives it), and each submittal carries the count of
materials of its system and how many of them have a datasheet in the
manufacturer's library -- what would go into the package.

The history is kept as events rather than by overwriting, so a submittal
rejected before it was approved still says so after the next revision.
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status as http_status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, ProjectSubmittal, ProjectSubmittalEvent, SubmittalStatus, User
from app.routers.projects import CREATOR_ROLES, DELETER_ROLES, XLSX_MEDIA_TYPE, _get_project_or_404
from app.schemas_design import (
    MaterialItemOut,
    MaterialSubmittalOut,
    ScannedFormOut,
    StorageFolderOut,
    SubmittalEventOut,
    SubmittalIn,
    SubmittalOut,
    SubmittalPatch,
    SubmittalRegisterOut,
    SubmittalScanOut,
    SubmittalSuggestionOut,
)
from app.services.battery_calculation import part_key
from app.services.datasheet_library import get_libraries, libraries_for
from app.services.submittal_export import submittal_register_workbook
from app.services.submittal_scanner import scan_folder

router = APIRouter(prefix="/projects", tags=["submittal"])

# One scan at a time: it reads every PDF in the archive folder and OCRs the
# forms it finds, and two at once would do that twice for nothing.
_scan_lock = threading.Lock()

RECENT_ACTIVITY = 12
MAX_STORAGE_FOLDERS = 12

# What a system's submittal is called, when the register suggests one.
SYSTEM_TITLES = {
    "FAS": "Fire Alarm System",
    "EML": "Emergency Light Monitoring System",
    "ELS": "Emergency Lighting System",
    "VES": "Voice Evacuation System",
    "CBS": "Central Battery System",
    "FT": "Fire Telephone System",
}


def _materials(project: Project) -> list[MaterialItemOut]:
    """The BOQ's parts, one per part number per system, each with the
    datasheet found for it in its manufacturer's library."""
    settings = get_settings()
    libraries = get_libraries(settings.datasheet_libraries, settings.projects_root)

    items: dict[tuple[str | None, str], MaterialItemOut] = {}
    for line in project.boq_items:
        key = part_key(line.catalog_no or "")
        if not key:
            continue  # a heading or a description-only line: not a material
        item = items.get((line.system_code, key))
        if item is None:
            item = MaterialItemOut(
                system_code=line.system_code,
                part_no=(line.catalog_no or "").strip(),
                description=line.description,
                manufacturer=line.manufacturer,
                quantity=0,
                groups=[],
            )
            items[(line.system_code, key)] = item
        if line.group_heading and line.group_heading not in item.groups:
            item.groups.append(line.group_heading)
        try:
            item.quantity = (item.quantity or 0) + float(line.quantity)
        except (TypeError, ValueError):
            # "Lot" and the like: the quantity is the BOQ's business.
            item.quantity = None if item.quantity in (0, None) else item.quantity

    for item in items.values():
        for library in libraries_for(item.manufacturer, libraries):
            match = next(iter(library.find(item.part_no)), None)
            if match:
                item.datasheet_library = match.library
                item.datasheet_path = match.path
                item.datasheet_filename = match.filename
                item.document_no = match.document_no
                item.datasheet_named_for_part = match.matched_on in ("filename", "family")
                break
    return sorted(items.values(), key=lambda i: ((i.system_code or "~"), i.part_no))


@router.get("/{project_id}/submittal/materials", response_model=MaterialSubmittalOut)
def list_materials(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaterialSubmittalOut:
    project = _get_project_or_404(db, project_id)
    settings = get_settings()
    items = _materials(project)
    return MaterialSubmittalOut(
        items=items,
        systems=sorted({i.system_code or "" for i in items}),
        with_datasheet=sum(1 for i in items if i.datasheet_path),
        libraries=sorted(get_libraries(settings.datasheet_libraries, settings.projects_root)),
    )


def _out(submittal: ProjectSubmittal, materials: dict[str | None, tuple[int, int]]) -> SubmittalOut:
    count, with_datasheet = materials.get(submittal.system_code, (0, 0))
    return SubmittalOut(
        id=submittal.id,
        title=submittal.title,
        reference=submittal.reference,
        reply_code=submittal.reply_code,
        system_code=submittal.system_code,
        manufacturer=submittal.manufacturer,
        revision=submittal.revision,
        status=submittal.status.value,
        document_path=submittal.document_path,
        note=submittal.note,
        created_by=submittal.created_by.full_name if submittal.created_by else None,
        created_at=submittal.created_at,
        updated_at=submittal.updated_at,
        materials=count,
        materials_with_datasheet=with_datasheet,
    )


def _by_system(items: list[MaterialItemOut]) -> dict[str | None, tuple[int, int]]:
    counts: dict[str | None, tuple[int, int]] = {}
    for item in items:
        count, with_sheet = counts.get(item.system_code, (0, 0))
        counts[item.system_code] = (count + 1, with_sheet + (1 if item.datasheet_path else 0))
    return counts


def _storage(project: Project) -> list[StorageFolderOut]:
    """The project folder's own subfolders, with what is in each -- where the
    submittals, catalogues and certificates live."""
    if not project.source_folder_path:
        return []
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        return []
    found: list[StorageFolderOut] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            items = sum(1 for _ in entry.iterdir())
            modified = datetime.fromtimestamp(entry.stat().st_mtime)
        except OSError:
            items, modified = 0, None
        found.append(StorageFolderOut(name=entry.name, path=str(entry), items=items, modified=modified))
        if len(found) >= MAX_STORAGE_FOLDERS:
            break
    return found


@router.get("/{project_id}/submittals", response_model=SubmittalRegisterOut)
def list_submittals(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmittalRegisterOut:
    project = _get_project_or_404(db, project_id)
    materials = _materials(project)
    by_system = _by_system(materials)
    items = [_out(s, by_system) for s in project.submittals]

    counts = {status.value: 0 for status in SubmittalStatus}
    for submittal in project.submittals:
        counts[submittal.status.value] += 1
    counts["total"] = len(items)

    activity = sorted(
        (
            SubmittalEventOut(
                kind=event.kind,
                detail=event.detail,
                by=event.by.full_name if event.by else None,
                at=event.at,
                submittal_id=submittal.id,
                submittal_title=submittal.title,
            )
            for submittal in project.submittals
            for event in submittal.events
        ),
        key=lambda e: e.at,
        reverse=True,
    )[:RECENT_ACTIVITY]

    # A system the BOQ has materials for but the register has no submittal for.
    covered = {s.system_code for s in project.submittals}
    brands = {s.name.upper(): s.brand for s in project.systems if s.brand}
    suggestions = [
        SubmittalSuggestionOut(
            title=SYSTEM_TITLES.get(system or "", f"{system or 'Other'} Materials"),
            system_code=system,
            manufacturer=next(
                (brand for name, brand in brands.items() if system and system.upper() in name.replace(" ", "")), None
            )
            or next(iter({i.manufacturer for i in materials if i.system_code == system and i.manufacturer}), None),
            materials=count,
            materials_with_datasheet=with_sheet,
        )
        for system, (count, with_sheet) in sorted(by_system.items(), key=lambda kv: (kv[0] or "~"))
        if system not in covered
    ]

    return SubmittalRegisterOut(
        items=items,
        counts=counts,
        systems=sorted({i.system_code or "" for i in materials} | {s.system_code or "" for s in project.submittals}),
        activity=activity,
        suggestions=suggestions,
        storage=_storage(project),
    )


def _log(submittal: ProjectSubmittal, kind: str, detail: str, user: User) -> None:
    submittal.events.append(ProjectSubmittalEvent(kind=kind, detail=detail, by_id=user.id, at=utc_now()))


@router.post("/{project_id}/submittals", response_model=SubmittalOut, status_code=http_status.HTTP_201_CREATED)
def create_submittal(
    project_id: int,
    payload: SubmittalIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalOut:
    project = _get_project_or_404(db, project_id)
    submittal = ProjectSubmittal(
        project_id=project.id,
        title=payload.title.strip(),
        system_code=payload.system_code,
        manufacturer=payload.manufacturer,
        revision=payload.revision.strip(),
        status=SubmittalStatus(payload.status),
        document_path=payload.document_path,
        note=payload.note,
        created_by_id=current_user.id,
    )
    _log(submittal, "created", f"Created {submittal.revision}", current_user)
    db.add(submittal)
    db.commit()
    db.refresh(project)
    return _out(submittal, _by_system(_materials(project)))


def _get_submittal(project: Project, submittal_id: int) -> ProjectSubmittal:
    found = next((s for s in project.submittals if s.id == submittal_id), None)
    if found is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Submittal not found")
    return found


@router.patch("/{project_id}/submittals/{submittal_id}", response_model=SubmittalOut)
def update_submittal(
    project_id: int,
    submittal_id: int,
    payload: SubmittalPatch,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalOut:
    project = _get_project_or_404(db, project_id)
    submittal = _get_submittal(project, submittal_id)
    changes = payload.model_dump(exclude_unset=True)

    # A revision and a status can change together (a new revision approved);
    # the status is logged last, so the newest event is where it stands.
    if "revision" in changes and changes["revision"] != submittal.revision:
        _log(submittal, "revision", f"Revision {changes['revision']} (was {submittal.revision})", current_user)
    if "status" in changes and changes["status"] != submittal.status.value:
        was = submittal.status.value.replace("_", " ")
        submittal.status = SubmittalStatus(changes.pop("status"))
        _log(submittal, "status", f"{submittal.status.value.replace('_', ' ').title()} (was {was})", current_user)
    changes.pop("status", None)
    for field, value in changes.items():
        setattr(submittal, field, value.strip() if isinstance(value, str) and field in ("title", "revision") else value)
    submittal.updated_at = utc_now()
    db.commit()
    db.refresh(project)
    return _out(submittal, _by_system(_materials(project)))


@router.delete("/{project_id}/submittals/{submittal_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_submittal(
    project_id: int,
    submittal_id: int,
    _current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    project = _get_project_or_404(db, project_id)
    db.delete(_get_submittal(project, submittal_id))
    db.commit()


@router.post("/{project_id}/submittals/scan", response_model=SubmittalScanOut)
def scan_submittals(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalScanOut:
    """Read the project folder's material submittals and bring the register
    up to date with them: the consultant's reply where the form carries one,
    under review where it does not (see app.services.submittal_scanner)."""
    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="The project has no archive folder to scan")
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="The project's archive folder is not reachable")

    with _scan_lock:
        forms, warnings = scan_folder(folder)
        by_reference = {(s.reference or "").upper(): s for s in project.submittals if s.reference}
        created = updated = unchanged = 0
        for form in forms:
            submittal = by_reference.get(form.reference.upper())
            if submittal is None:
                submittal = ProjectSubmittal(
                    project_id=project.id,
                    title=form.title,
                    reference=form.reference,
                    system_code=form.system_code,
                    manufacturer=form.supplier,
                    revision=form.revision,
                    status=SubmittalStatus(form.status),
                    reply_code=form.reply_code,
                    document_path=form.relative,
                    note=form.reply_text,
                )
                _log(submittal, "scanned", _scan_detail(form), current_user)
                db.add(submittal)
                by_reference[form.reference.upper()] = submittal
                created += 1
                continue

            changes = []
            if submittal.status.value != form.status or submittal.reply_code != form.reply_code:
                changes.append(_scan_detail(form))
                submittal.status = SubmittalStatus(form.status)
                submittal.reply_code = form.reply_code
            if submittal.revision != form.revision:
                changes.append(f"Revision {form.revision} (was {submittal.revision})")
                submittal.revision = form.revision
            submittal.document_path = form.relative
            submittal.manufacturer = submittal.manufacturer or form.supplier
            submittal.system_code = submittal.system_code or form.system_code
            if changes:
                submittal.updated_at = utc_now()
                for detail in changes:
                    _log(submittal, "scanned", detail, current_user)
                updated += 1
            else:
                unchanged += 1
        db.commit()
        db.refresh(project)

    return SubmittalScanOut(
        found=len(forms),
        created=created,
        updated=updated,
        unchanged=unchanged,
        warnings=warnings,
        forms=[
            ScannedFormOut(
                reference=f.reference,
                revision=f.revision,
                title=f.title,
                system_code=f.system_code,
                supplier=f.supplier,
                reply_code=f.reply_code,
                reply_text=f.reply_text,
                status=f.status,
                path=f.relative,
                read_by_ocr=f.ocr_used,
            )
            for f in forms
        ],
        updated_register=list_submittals(project_id, current_user, db),
    )


def _scan_detail(form) -> str:
    """What the scan read, as the register's history records it."""
    if form.reply_code:
        label = {"A": "Approved (A)", "B": "Approved as noted (B)", "C": "Revise and resubmit (C)"}[form.reply_code]
        return f"{label} — read from the consultant's reply on {Path(form.relative).name}"
    return f"Under review — no consultant reply on {Path(form.relative).name}"


@router.get("/{project_id}/submittals/export.xlsx")
def export_submittals(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The register as a workbook: the submittals, then the materials of each
    system with the datasheet found for them."""
    project = _get_project_or_404(db, project_id)
    materials = _materials(project)
    items = [_out(s, _by_system(materials)) for s in project.submittals]
    content = submittal_register_workbook(
        project, items, materials, exported_by=current_user.full_name, exported_at=utc_now()
    )
    name = f"EP-{project.ep_number} Material Submittals.xlsx"
    return Response(
        content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"},
    )


# Kept for the "open the project folder" hint on the page: the path as the
# archive spells it, which the engineer pastes into Explorer.
@router.get("/{project_id}/submittals/folder")
def submittal_folder(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = _get_project_or_404(db, project_id)
    return {"path": project.source_folder_path, "exists": bool(project.source_folder_path and os.path.isdir(project.source_folder_path))}
