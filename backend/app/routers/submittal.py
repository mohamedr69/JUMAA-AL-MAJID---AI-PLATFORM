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
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status as http_status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, ProjectSubmittal, ProjectSubmittalEvent, SubmittalStatus, User
from app.routers.projects import CREATOR_ROLES, DELETER_ROLES, XLSX_MEDIA_TYPE, _get_project_or_404
from app.schemas_design import (
    ChecklistReadOut,
    MaterialItemOut,
    MaterialSubmittalOut,
    PackageBuildIn,
    PackageDocumentOut,
    PackagePlanOut,
    PackageSectionOut,
    SubmittalMapOut,
    StorageFolderOut,
    SubmittalEventOut,
    SubmittalIn,
    SubmittalOut,
    SubmittalPatch,
    SubmittalRegisterOut,
    SubmittalSuggestionOut,
)
from app.services import activity, system_rules
from app.services.battery_calculation import part_key
from app.services import company_library
from app.services.datasheet_library import get_libraries, libraries_for
from app.services.spec_finder import find_specs
from app.services.submittal_export import submittal_register_workbook
from app.services.submittal_package import (
    BATTERY_SECTION,
    DATASHEET_SECTION,
    SECTION_NAMES,
    SECTIONS,
    SPEC_SECTION,
    PackageBuildError,
    PackagePlan,
    build_package,
    plan_package,
    read_checklist,
)
from app.ai import submittal_reader
from app.services import jobs

settings = get_settings()

router = APIRouter(prefix="/projects", tags=["submittal"])

# One scan at a time: it reads every PDF in the archive folder and OCRs the
# forms it finds, and two at once would do that twice for nothing.
_scan_lock = threading.Lock()

RECENT_ACTIVITY = 12
MAX_STORAGE_FOLDERS = 12

# What a system's submittal is called, when the register suggests one.
SYSTEM_TITLES = {
    "FAS": "Fire Alarm System",
    "ELS": "Emergency Lighting System",
    "VES": "Voice Evacuation System",
    "FT": "Fire Telephone System",
    "PAVA": "Public Address & Voice Alarm System",
}

# The DRF rows a Fire Alarm submittal covers: the one design sheet quotes
# the panel, the voice evacuation and the fire telephone together. Not the
# project's other systems -- a cover that listed Central Battery System on a
# fire alarm package was the platform's doing, not the engineer's.
FAS_FAMILY_ROWS = ("Fire Alarm", "Voice Evacuation", "Fire Telephone")


def _materials(project: Project) -> list[MaterialItemOut]:
    """The BOQ's parts, one per part number per system, each with the
    datasheet found for it in its manufacturer's library."""
    settings = get_settings()
    libraries = get_libraries()

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
        libraries=sorted(get_libraries()),
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

    # Sorted over the rows, not the output models, so the row id is available
    # as a tie-break: two events logged in one request (a revision and a
    # status change together) share a timestamp exactly -- the clock is
    # coarser than one request -- and a stable sort then leaves them
    # oldest-first inside that timestamp, showing "Approved" under the
    # "Revision R01" it actually followed.
    newest_first = sorted(
        ((submittal, event) for submittal in project.submittals for event in submittal.events),
        key=lambda pair: (pair[1].at, pair[1].id),
        reverse=True,
    )[:RECENT_ACTIVITY]
    activity = [
        SubmittalEventOut(
            kind=event.kind,
            detail=event.detail,
            by=event.by.full_name if event.by else None,
            at=event.at,
            submittal_id=submittal.id,
            submittal_title=submittal.title,
        )
        for submittal, event in newest_first
    ]

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
        system_code=system_rules.effective_code(payload.system_code, project),
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
    activity.record(db, current_user, "submittal.created", f"Created submittal \"{submittal.title}\" {submittal.revision}",
                    project=project, entity_type="submittal", entity_id=submittal.id,
                    detail={"system": submittal.system_code, "status": submittal.status.value})
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
    changed = {
        name: f"{getattr(submittal, name).value if name == 'status' else getattr(submittal, name)} -> {value}"
        for name, value in changes.items()
        if (getattr(submittal, name).value if name == "status" else getattr(submittal, name)) != value
    }

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
    if changed:
        activity.record(db, current_user, "submittal.updated",
                        f"Changed submittal \"{submittal.title}\": {', '.join(changed)}",
                        project=project, entity_type="submittal", entity_id=submittal.id, detail=changed)
    db.refresh(project)
    return _out(submittal, _by_system(_materials(project)))


@router.delete("/{project_id}/submittals/{submittal_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_submittal(
    project_id: int,
    submittal_id: int,
    current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    project = _get_project_or_404(db, project_id)
    submittal = _get_submittal(project, submittal_id)
    activity.record(db, current_user, "submittal.deleted", f"Deleted submittal \"{submittal.title}\" {submittal.revision}",
                    project=project, entity_type="submittal", entity_id=submittal.id, commit=False)
    db.delete(submittal)
    db.commit()


@router.post("/{project_id}/submittals/scan", status_code=http_status.HTTP_202_ACCEPTED)
def scan_submittals(
    project_id: int,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Start the AI check of the project folder's material submittals
    (app.ai.submittal_reader): the model reads every form it has not read
    before, the map is drawn and stored, and the register is brought up to
    it. Runs as a job the page follows; `GET .../submittals/map` has the
    result."""
    from app.routers.jobs import JobOut, RUN_INLINE, _out, _refuse_duplicate

    project = _get_project_or_404(db, project_id)
    if not project.source_folder_path:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="The project has no archive folder to check")
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="The project's archive folder is not reachable")
    reason = submittal_reader.available(project)
    if reason:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=reason)
    with _scan_lock:
        db.expire_all()
        _refuse_duplicate(db, project, submittal_reader.JOB_KIND)
        user_id = current_user.id

        def work(session: Session, ctx: jobs.JobContext) -> dict:
            target = session.get(Project, project_id)
            actor = session.get(User, user_id)
            result = submittal_reader.check(session, target, actor, ctx=ctx)
            counts = result.get("register_counts") or {}
            activity.record(session, actor, "submittal.ai_checked",
                            f"AI checked the material submittals: {result['submittals']} submittal{'s' if result['submittals'] != 1 else ''} "
                            f"mapped from {result['forms']} form{'s' if result['forms'] != 1 else ''}"
                            + (f"; {len(result['actions'])} action{'s' if len(result['actions']) != 1 else ''} required" if result["actions"] else ""),
                            project=target, entity_type="submittal",
                            detail={"files": result["files"], "forms": result["forms"], "submittals": result["submittals"],
                                    "calls": result["calls"], "reused": result["reused"], **counts})
            return {"files": result["files"], "forms": result["forms"], "submittals": result["submittals"],
                    "actions": result["actions"], "calls": result["calls"], "reused": result["reused"], **counts}

        job = jobs.start(db, kind=submittal_reader.JOB_KIND, project_id=project.id, user_id=current_user.id, work=work,
                         run_inline=RUN_INLINE)
    return JobOut.model_validate(_out(job).model_dump())


@router.get("/{project_id}/submittals/map", response_model=SubmittalMapOut)
def submittal_map(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmittalMapOut:
    """The latest map the AI drew of the project's material submittals, and
    whether a check can run now."""
    project = _get_project_or_404(db, project_id)
    reason = submittal_reader.available(project)
    latest = submittal_reader.latest_map(db, project) or {}
    return SubmittalMapOut(available=reason is None, reason=reason,
                           **{k: v for k, v in latest.items() if k in SubmittalMapOut.model_fields and k not in ("available", "reason")})


@router.get("/{project_id}/submittals/export.xlsx")
def export_submittals(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The register as a workbook: the submittals, then the materials of each
    system with the datasheet found for them."""
    project = _get_project_or_404(db, project_id)
    activity.record(db, current_user, "submittal.register_exported", "Exported the submittal register", project=project,
                    entity_type="project", entity_id=project.id, detail={"submittals": len(project.submittals)})
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


# --- building the package ---------------------------------------------------

# Assembling is slow (hundreds of pages of PDF) and reads the shared library,
# so one at a time per process, as the BOQ extraction does.
_package_lock = threading.Lock()


def _submittal_library() -> Path | None:
    """The submittal builder folder, or None if it is not reachable.

    The company library holds it (`library/submittal`); the archive copy is
    the fallback for a machine that has not synced it yet. See
    `app/services/company_library.py`.
    """
    return company_library.submittal_folder()


def _specs_for(project: Project, system_code: str | None) -> list[tuple[str, str]]:
    """The project's specification, as (label, path) for the package."""
    if not project.source_folder_path:
        return []
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        return []
    try:
        matches, _errors = find_specs(folder, {(system_code or "").upper()} if system_code else set())
    except Exception:  # noqa: BLE001
        return []
    # Only a specification served straight from a file can be merged; one
    # inside a zip is read for the compliance statement, not for the package.
    # The same spec filed loose and again inside its archive is one document.
    seen: set[str] = set()
    documents: list[tuple[str, str]] = []
    for match in sorted(matches, key=lambda m: m.path):
        if match.member or match.filename.lower() in seen:
            continue
        full = folder / match.path
        if full.suffix.lower() != ".pdf" or not full.is_file():
            continue
        seen.add(match.filename.lower())
        documents.append((full.name, str(full)))
    return documents


def _battery_panels(db: Session, project: Project, sections: set[int]):
    """The panels the battery section encloses, sized from the saved BOQ.

    Computed here because the calculation reads the part-current catalogue
    out of the database, which the package service has no handle on. Asked
    for only when the section is chosen -- it is not a cheap call.
    """
    if BATTERY_SECTION not in sections:
        return []
    from app.routers.design import _battery_calculation

    try:
        return list(_battery_calculation(db, project).panels)
    except Exception:  # noqa: BLE001
        # A calculation that cannot be produced leaves the section with its
        # divider and a note, rather than failing the whole package.
        return []


def _plan_for(project: Project, sections: set[int], system_code: str | None, db: Session | None = None) -> PackagePlan:
    library = _submittal_library()
    # The project's own folder, not the archive root: see _search_archive.
    folder = Path(project.source_folder_path) if project.source_folder_path else None
    # Both of these are expensive against a synced archive -- indexing the
    # datasheet library, and walking the project folder for a specification --
    # so neither is done unless a chosen section actually needs it.
    libraries = get_libraries() if DATASHEET_SECTION in sections else {}
    specs = _specs_for(project, system_code) if SPEC_SECTION in sections else []
    panels = _battery_panels(db, project, sections) if db is not None else []
    return plan_package(
        project, sections, library, folder, libraries,
        spec_documents=specs, system_code=system_code, battery_panels=panels,
    )


def _plan_out(project: Project, plan: PackagePlan, system_code: str | None) -> PackagePlanOut:
    return PackagePlanOut(
        sections=[
            PackageSectionOut(
                number=s.number, name=s.name, selected=s.selected,
                found=s.found, missing=s.missing, note=s.note,
                documents=[
                    PackageDocumentOut(
                        name=d.name, source=d.source, part_no=d.part_no, covers=d.covers,
                        included=bool(d.path) or d.source == "generated",
                        missing_reason=d.missing_reason,
                    )
                    for d in s.documents
                ],
            )
            for s in plan.sections
        ],
        library_found=plan.library_found,
        library_path=plan.library_path,
        system_code=system_code,
        warnings=plan.warnings,
    )


@router.get("/{project_id}/submittal/package/plan", response_model=PackagePlanOut)
def package_plan(
    project_id: int,
    sections: str | None = None,
    system_code: str | None = None,
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> PackagePlanOut:
    """What each chosen section would contribute, without assembling anything.

    This is what the checklist shows before the package is built: which
    documents the submittal builder holds, which came from the project, and
    which are missing -- so a gap is dealt with before the package goes out,
    not found in it afterwards.
    """
    project = _get_project_or_404(db, project_id)
    chosen = _parse_sections(sections)
    plan = _plan_for(project, chosen, system_code, db)
    return _plan_out(project, plan, system_code)


def _parse_sections(sections: str | None) -> set[int]:
    """"1,5,7" -> {1, 5, 7}. Everything, when nothing is named."""
    if sections is None or not sections.strip():
        return {number for number, _ in SECTIONS}
    chosen = set()
    for part in sections.split(","):
        part = part.strip()
        if part.isdigit() and int(part) in SECTION_NAMES:
            chosen.add(int(part))
    return chosen


@router.post("/{project_id}/submittal/package")
def build_submittal_package(
    project_id: int,
    payload: PackageBuildIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Assemble the package and return it as one PDF.

    Nothing is stored: the package is the documents it was built from, and
    keeping a copy would be a second version of them to go stale. Issue it by
    saving the download into the project folder.
    """
    project = _get_project_or_404(db, project_id)
    chosen = {n for n in payload.sections if n in SECTION_NAMES}
    if not chosen:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Choose at least one section")

    with _package_lock:
        plan = _plan_for(project, chosen, payload.system_code, db)
        panels = _battery_panels(db, project, chosen)
        try:
            built = build_package(
                project, plan, _submittal_library(),
                revision=payload.revision or "R0",
                systems=payload.title or _system_title(project, payload.system_code),
                system_code=payload.system_code,
                battery_panels=panels,
            )
        except PackageBuildError as exc:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    name = f"EP-{project.ep_number} - Material Submittal - {payload.revision or 'R0'}.pdf"
    activity.record(db, current_user, "submittal.package_built",
                    f"Built the material submittal package {payload.revision or 'R0'} ({built.pages} pages)",
                    project=project, entity_type="submittal",
                    detail={"system": payload.system_code, "sections": ", ".join(str(SECTION_NAMES[n]) for n in sorted(chosen)),
                            "pages": built.pages, "warnings": len(built.warnings)})
    return Response(
        built.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
            "X-Package-Pages": str(built.pages),
            "X-Package-Warnings": str(len(built.warnings)),
        },
    )


def _system_title(project: Project, system_code: str | None) -> str:
    """What the cover calls the systems, from the DRF's own wording -- and
    only the systems this package is for."""
    code = system_rules.effective_code(system_code, project) or ""
    if code in ("FAS", "ELS"):
        rows = system_rules.drf_rows(code, project)
        covered = [s.name for s in project.systems if s.name in rows]
        if covered:
            return _join_systems(covered)
    return SYSTEM_TITLES.get(code, "Material Submittal")


def _join_systems(names: list[str]) -> str:
    """"Fire Alarm, Voice Evacuation & Fire Telephone System". A name that
    already ends in "System" loses it first, so nothing reads "System
    System"."""
    bare = [re.sub(r"\s+system$", "", name.strip(), flags=re.IGNORECASE) for name in names]
    joined = ", ".join(bare[:-1]) + (" & " if len(bare) > 1 else "") + bare[-1]
    return joined + " System"


MAX_CHECKLIST_BYTES = 20 * 1024 * 1024


@router.post("/{project_id}/submittal/package/checklist", response_model=ChecklistReadOut)
async def read_submittal_checklist(
    project_id: int,
    file: UploadFile = File(...),
    _current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ChecklistReadOut:
    """Read a filled-in checklist and say which sections it ticks.

    The checklist is the form the team already fills in for every submittal,
    so attaching it is the same choice as ticking the custom index -- made
    once, on paper, by whoever signed it.
    """
    _get_project_or_404(db, project_id)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Attach the checklist as a PDF")
    data = await file.read()
    if len(data) > MAX_CHECKLIST_BYTES:
        raise HTTPException(status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The checklist is too large")

    answers, warnings = read_checklist(data)
    if not answers:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=warnings[0] if warnings else "No ticks were found on this checklist",
        )
    return ChecklistReadOut(
        answers=answers,
        sections=sorted(n for n, mark in answers.items() if mark == "yes"),
        warnings=warnings,
    )
