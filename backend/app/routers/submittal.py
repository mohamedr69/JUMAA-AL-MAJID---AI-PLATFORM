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

from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status as http_status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import (
    Project,
    ProjectDocument,
    ProjectSubmittal,
    ProjectSubmittalEvent,
    SubmittalReply,
    SubmittalStatus,
    User,
)
from app.routers.projects import CREATOR_ROLES, DELETER_ROLES, XLSX_MEDIA_TYPE, _get_project_or_404
from app.services import project_state
from app.services.submittal_identity import brand_key
from app.schemas_design import (
    SubmittalRevisionOut,
    ChecklistReadOut,
    MaterialItemOut,
    MaterialSubmittalOut,
    PackageBuildIn,
    PackageDocumentOut,
    PackagePlanOut,
    PackageSectionOut,
    SubmittalMapOut,
    StorageFolderOut,
    ReplyRow,
    SubmittalEventOut,
    SubmittalReplyIn,
    SubmittalReplyOut,
    SubmittalIn,
    SubmittalOut,
    SubmittalPatch,
    SubmittalRegisterOut,
    SubmittalSuggestionOut,
)
from app.services import equipment_currents
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
from app.services import brands, reply_sheet, submittal_replies
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

    from sqlalchemy.orm import Session as _Session

    _attach_datasheets(list(items.values()), libraries, _Session.object_session(project))
    return sorted(items.values(), key=lambda i: ((i.system_code or "~"), i.part_no))


def _attach_datasheets(items: list[MaterialItemOut], libraries: dict, db: Session | None = None) -> None:
    """The datasheet each material has in its manufacturer's library: the
    one linked to the part by hand first (app.services.datasheet_links),
    else the one the library finds by file name and text."""
    from app.services import datasheet_links

    for item in items:
        linked = datasheet_links.lookup(db, item.manufacturer, item.part_no) if db is not None else None
        if linked is not None:
            library = libraries.get(linked.library) or next((lib for name, lib in libraries.items() if name.upper() == linked.library.upper()), None)
            if library is not None:
                path = library.folder / linked.path
                item.datasheet_library, item.datasheet_path = linked.library, linked.path
                item.datasheet_filename, item.datasheet_linked = path.name, True
                item.datasheet_named_for_part = True
                continue
        for library in libraries_for(item.manufacturer, libraries):
            match = next(iter(library.find(item.part_no)), None)
            if match:
                item.datasheet_library = match.library
                item.datasheet_path = match.path
                item.datasheet_filename = match.filename
                item.document_no = match.document_no
                item.datasheet_named_for_part = match.matched_on in ("filename", "family")
                break


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


def _revisions_out(submittal: ProjectSubmittal) -> list[SubmittalRevisionOut]:
    """R0 first. A register row entered before revisions were kept has one:
    the revision it is at."""
    from app.services.submittal_filing import _revision_number

    rows = sorted(submittal.revisions, key=lambda r: _revision_number(r.revision))
    if not rows:
        return [SubmittalRevisionOut(revision=submittal.revision, status=submittal.status.value,
                                     reply_code=submittal.reply_code, reference=submittal.reference,
                                     manufacturer=submittal.manufacturer, document_path=submittal.document_path,
                                     note=submittal.note, updated_at=submittal.updated_at)]
    return [SubmittalRevisionOut(revision=r.revision, status=r.status.value, reply_code=r.reply_code,
                                 reference=r.reference, also_filed_as=list(r.also_filed_as or []),
                                 manufacturer=r.manufacturer, document_path=r.document_path, note=r.note,
                                 updated_at=r.updated_at)
            for r in rows]


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
        revisions=_revisions_out(submittal),
    )


def _one_per_brand(found: list[SubmittalOut], held: set[tuple[str, str]]) -> list[SubmittalOut]:
    """The forms on file that the register does not hold, as submittals: one
    per system and brand (per reference for a form that names no system),
    at its latest revision, with each revision it has been filed at. A
    system and brand the register already has is its register row's."""
    from app.services.submittal_filing import _revision_number
    from app.services.submittal_identity import brand_key, settle_unknown_brands

    answered = {"approved": 2, "rejected": 2, "under_review": 1, "not_submitted": 0}
    brand = {id(item): brand_key(item.manufacturer) if item.system_code else "" for item in found}
    settled = settle_unknown_brands([(item.system_code, brand[id(item)]) for item in found if item.system_code]
                                    + list(held))
    groups: dict[str, list[SubmittalOut]] = {}
    for item in found:
        if item.system_code:
            mine = settled.get((item.system_code, brand[id(item)]), brand[id(item)])
            if (item.system_code, mine) in held:
                continue
            groups.setdefault(f"{item.system_code}|{mine}", []).append(item)
        else:
            groups.setdefault(f"REF:{(item.reference or '').upper()}", []).append(item)
    out = []
    for items in groups.values():
        best: dict[int, SubmittalOut] = {}
        for item in items:
            n = _revision_number(item.revision)
            if n not in best or answered[item.status] > answered[best[n].status]:
                best[n] = item
        latest = best[max(best)]
        revisions = [SubmittalRevisionOut(revision=f"R{n:02d}", status=best[n].status, reply_code=best[n].reply_code,
                                          reference=best[n].reference, manufacturer=best[n].manufacturer,
                                          document_path=best[n].document_path, note=best[n].note,
                                          updated_at=best[n].updated_at,
                                          also_filed_as=sorted({i.reference for i in items
                                                                if _revision_number(i.revision) == n and i.reference
                                                                and i.reference != best[n].reference}))
                     for n in sorted(best)]
        out.append(latest.model_copy(update={"revisions": revisions}))
    return out


def _counts(items: list[SubmittalOut]) -> dict[str, int]:
    """Submittals by where their latest revision stands -- one each, never
    a revision, reply or file each."""
    counts = {status.value: 0 for status in SubmittalStatus}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    counts["total"] = len(items)
    return counts


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


# What the consultant's reply code means for a submittal's standing.
REPLY_STATUS = {"A": SubmittalStatus.approved, "B": SubmittalStatus.approved,
                "C": SubmittalStatus.rejected, "D": SubmittalStatus.rejected}


def _maker(name: str | None) -> str | None:
    """The manufacturer a submittal form names. The form writes it as the
    letter does -- "M/s. EDWARDS" -- and the brand is what follows."""
    if not name:
        return None
    return re.sub(r"^\s*m\s*/\s*s\.?\s*", "", name, flags=re.IGNORECASE).strip() or None


def submittal_supplier(name: str | None) -> str | None:
    """The supplier behind a brand, in one spelling.

    A submittal is filed under the brand on its cover, and the same
    supplier is written more than one way: Menvier is Eaton's name for
    monitored self-contained emergency lighting, so a form saying MENVIER
    and one saying EATON are the same supplier. A brand the knowledge
    base does not know keeps its own normalised spelling rather than
    folding into everything else unknown.
    """
    from app.knowledge.policy import canonical_manufacturer
    from app.services import brands

    return canonical_manufacturer(name) or brands.normalise(name) or None


def same_submittal(system_code: str | None, revision: str | None, manufacturer: str | None) -> tuple:
    """What makes two forms one submittal: the system, the revision and
    the supplier.

    A system does not have two submittals of one revision from one
    supplier -- what it has is the form we prepared and the form that came
    back, filed under our reference and the main contractor's. It does
    have two from *different* suppliers, which is how fire rated cables
    are quoted, so the supplier is part of the key and not left out of it.
    """
    from app.services.submittal_filing import _revision_number

    return (system_code or "", _revision_number(revision or ""), submittal_supplier(manufacturer))


def _filed_in_the_folder(db: Session, project: Project, held: set[str],
                        *, covered: set[tuple] | None = None) -> list[SubmittalOut]:
    """The submittals on file that the register does not hold.

    A submittal prepared outside the platform and filed in the project
    folder is a submittal: the register that leaves it out says "no
    material submittal is filed for this project" over one sitting in
    `02- Material Submittals`. Read from the document index rather than
    written into the register -- the form on the drive is the record, and
    copying it in would leave two that drift apart.

    `held` are the references the register already has, so a submittal
    the platform built and filed is not listed twice. `covered` is the
    same thing said by system, revision and supplier rather than by
    reference: our own copy of a form and the one the consultant answered
    carry different references -- ours and the main contractor's -- and
    are one submittal, not two.
    """
    from app.services import brands, document_sync

    rows = db.query(ProjectDocument).filter(
        ProjectDocument.project_id == project.id,
        ProjectDocument.state != document_sync.REMOVED,
    ).all()
    replies = submittal_replies.on_file(rows)

    found: dict[str, SubmittalOut] = {}
    for row in rows:
        if row.role != document_sync.ROLE_SUBMITTAL:
            continue
        reading = (row.extracted or {}).get("form") or {}
        if not reading.get("is_submittal"):
            continue
        records = (row.extracted or {}).get("records") or []
        reference = (records[0].get("reference") if records else None) or reading.get("reference") or ""
        if not reference or reference in held:
            continue
        relative = (row.relative_path or "").replace("\\", "/")
        # The same vetting the map does: a resubmission prints the comments
        # it answers, and the code read off an R1 form is usually the
        # consultant's word on R0. Without this the logs say RR over a
        # revision the map has already worked out is still under review.
        reading = submittal_replies.vetted({**reading, "relative": relative}, replies)
        system = submittal_reader._system_code(reading, relative)
        revision = f"R{reading['revision']}" if reading.get("revision") is not None else "R0"
        key = f"{reference}|{revision}"
        if key in found:
            continue
        maker = brands.normalise(_maker(reading.get("manufacturer")))
        # The same submittal as one the register already holds: our copy of
        # the form beside the one that came back answered. The register row
        # is the one to show -- it carries the consultant's reply.
        if covered and same_submittal(system_rules.effective_code(system, project), revision, maker) in covered:
            continue
        # The reply is filed in the revision's own folder, beside what it
        # answers: .../FA/R0/Submitted holds the form, .../FA/R0/Received
        # the consultant's comments on it.
        status, text = submittal_replies.for_revision(
            submittal_replies.revision_folder(relative) or "", replies)
        code = submittal_reader.CODES.get(status or "", "")
        code = submittal_reader.REGISTER.get(code, (None, None))[1] or (
            (reading.get("reply") or {}).get("code") or "").strip().upper() or None
        found[key] = SubmittalOut(
            # Negative, so it cannot be mistaken for a register row's id
            # by anything that would try to open or revise it.
            id=-len(found) - 1,
            title=reading.get("title") or Path(relative).stem,
            reference=reference,
            reply_code=code,
            system_code=system_rules.effective_code(system, project),
            manufacturer=maker,
            revision=f"R{int(reading.get('revision') or 0):02d}",
            status=REPLY_STATUS.get(code or "", SubmittalStatus.under_review).value,
            document_path=relative,
            note=text,
            created_by=None,
            created_at=row.first_seen_at or utc_now(),
            updated_at=row.last_seen_at or row.first_seen_at or utc_now(),
            from_folder=True,
        )
    return list(found.values())


def register_items(db: Session, project: Project, materials: list[MaterialItemOut] | None = None) -> list[SubmittalOut]:
    """The project's material submittals: one per system and brand, each at
    its latest revision with every revision it has been filed at.

    The one read of them. The Material Submittals register, the Material
    Submittal Log, Project Home, the drawings' material-approval
    prerequisite and the project's actions all come from this, so no page
    works out a submittal's status for itself (app.services.project_state)."""
    by_system = _by_system(materials if materials is not None else _materials(project))
    items = [_out(s, by_system) for s in project.submittals]
    # And the ones on file that were never entered here.
    # A form on file that is the same submittal as a register row -- our
    # own copy beside the one the consultant answered -- is not a second
    # submittal, and listing it as one shows the system twice, once under
    # review and once answered.
    # One submittal per system and brand: a form of a submittal the
    # register holds is a revision of it, not another one.
    held = {s.reference for s in project.submittals if s.reference}
    held |= {r.reference for s in project.submittals for r in s.revisions if r.reference}
    held |= {ref for s in project.submittals for r in s.revisions for ref in (r.also_filed_as or [])}
    items += _one_per_brand(
        _filed_in_the_folder(db, project, held,
                             covered={same_submittal(s.system_code, s.revision, s.manufacturer) for s in project.submittals}),
        {(s.system_code, s.brand_key or "") for s in project.submittals if s.system_code})

    # A reply comes back as a scan of the consultant's comments with no
    # reference on it, so the form reader never sees it and the register
    # row stays "under review" over an answer sitting in the folder. What
    # ties them is the revision folder the reply was filed in.
    from app.services import document_sync as _sync

    replies = submittal_replies.on_file(db.query(ProjectDocument).filter(
        ProjectDocument.project_id == project.id,
        ProjectDocument.state != _sync.REMOVED).all())
    for index, item in enumerate(items):
        if item.reply_code or not item.document_path:
            continue
        status, text = submittal_replies.for_revision(
            submittal_replies.revision_folder(item.document_path) or "", replies)
        code = submittal_reader.REGISTER.get(submittal_reader.CODES.get(status or "", ""), (None, None))[1]
        if code:
            answered = REPLY_STATUS.get(code, SubmittalStatus.under_review).value
            revisions = [r.model_copy(update={"reply_code": code, "status": answered, "note": r.note or text})
                         if r.revision == item.revision and not r.reply_code else r for r in item.revisions]
            items[index] = item.model_copy(update={
                "reply_code": code,
                "status": answered,
                "note": item.note or text,
                "revisions": revisions,
            })
    return items


@router.get("/{project_id}/submittals", response_model=SubmittalRegisterOut)
def list_submittals(
    project_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmittalRegisterOut:
    project = _get_project_or_404(db, project_id)
    materials = _materials(project)
    by_system = _by_system(materials)
    items = register_items(db, project, materials)

    counts = _counts(items)
    counts_by_system = {system or "": _counts([i for i in items if (i.system_code or "") == (system or "")])
                        for system in {i.system_code for i in items}}

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
    covered = {item.system_code for item in items}
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
        counts_by_system=counts_by_system,
        systems=sorted({i.system_code or "" for i in materials} | {s.system_code or "" for s in project.submittals}
                       | set(system_rules.project_codes(project))),
        activity=activity,
        suggestions=suggestions,
        storage=_storage(project),
    )


def _refuse_a_second(project: Project, system: str | None, brand: str, *, but: ProjectSubmittal | None = None) -> None:
    """A system has one material submittal per brand: a new revision or a
    reply is a change to it, not another one. Where either brand is not
    known, which one it is cannot be told, and the second is refused too."""
    if not system:
        return
    existing = next((s for s in project.submittals if s is not but and s.system_code == system
                     and (s.brand_key == brand or not brand or not s.brand_key)), None)
    if existing is not None:
        named = f"{system} ({existing.brand_key})" if existing.brand_key else system
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail={
            "code": "submittal_exists", "submittal_id": existing.id,
            "message": f"{named} already has its material submittal ({existing.reference or existing.title}, "
                       f"{existing.revision}): change its revision or status, or give the other brand, "
                       f"instead of adding another."})


def _log(submittal: ProjectSubmittal, kind: str, detail: str, user: User) -> None:
    submittal.events.append(ProjectSubmittalEvent(kind=kind, detail=detail, by_id=user.id, at=utc_now()))


def _set_revision(submittal: ProjectSubmittal, revision: str, status: SubmittalStatus, user: User | None, *,
                  source: str) -> None:
    """The submittal's revision `revision` stands at `status`: one current
    status per revision, the previous one kept in its history."""
    from app.models import ProjectSubmittalRevision, ProjectSubmittalStatusChange
    from app.services.submittal_filing import _revision_number

    name = f"R{_revision_number(revision):02d}"
    row = next((r for r in submittal.revisions if _revision_number(r.revision) == _revision_number(revision)), None)
    if row is None:
        row = ProjectSubmittalRevision(revision=name, status=status, reply_code=submittal.reply_code,
                                       reference=submittal.reference, also_filed_as=[],
                                       manufacturer=submittal.manufacturer, document_path=submittal.document_path,
                                       note=submittal.note, updated_at=utc_now())
        row.history.append(ProjectSubmittalStatusChange(previous_status=None, new_status=status.value,
                                                        reply_code=submittal.reply_code, source=source,
                                                        by_id=user.id if user else None, changed_at=utc_now()))
        submittal.revisions.append(row)
        return
    if row.status != status:
        row.history.append(ProjectSubmittalStatusChange(previous_status=row.status.value, new_status=status.value,
                                                        reply_code=submittal.reply_code, source=source,
                                                        by_id=user.id if user else None, changed_at=utc_now()))
        row.status, row.updated_at = status, utc_now()


@router.post("/{project_id}/submittals", response_model=SubmittalOut, status_code=http_status.HTTP_201_CREATED)
def create_submittal(
    project_id: int,
    payload: SubmittalIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalOut:
    project = _get_project_or_404(db, project_id)
    system = system_rules.effective_code(payload.system_code, project)
    brand = brand_key(payload.manufacturer) if system else ""
    _refuse_a_second(project, system, brand)
    submittal = ProjectSubmittal(
        project_id=project.id,
        title=payload.title.strip(),
        system_code=system,
        brand_key=brand,
        manufacturer=payload.manufacturer,
        revision=payload.revision.strip(),
        status=SubmittalStatus(payload.status),
        document_path=payload.document_path,
        note=payload.note,
        created_by_id=current_user.id,
    )
    _log(submittal, "created", f"Created {submittal.revision}", current_user)
    _set_revision(submittal, submittal.revision, submittal.status, current_user, source="manual")
    db.add(submittal)
    db.flush()
    project_state.submittals_changed(db, project, [("created", submittal.id, submittal.system_code)])
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
    if "system_code" in changes or "manufacturer" in changes:
        system = changes.get("system_code", submittal.system_code)
        brand = brand_key(changes.get("manufacturer", submittal.manufacturer)) if system else ""
        if (system, brand) != (submittal.system_code, submittal.brand_key):
            _refuse_a_second(project, system, brand, but=submittal)
        submittal.brand_key = brand
    for field, value in changes.items():
        setattr(submittal, field, value.strip() if isinstance(value, str) and field in ("title", "revision") else value)
    # The revision it is at now, with the status it stands at: updated in
    # place, its old status kept in its history.
    _set_revision(submittal, submittal.revision, submittal.status, current_user, source="manual")
    submittal.updated_at = utc_now()
    # The revision, its history, the submittal, the actions and the change
    # row in one transaction: every page reads the same record after it.
    project_state.submittals_changed(db, project, [("updated", submittal.id, submittal.system_code)])
    db.commit()
    if changed:
        activity.record(db, current_user, "submittal.updated",
                        f"Changed submittal \"{submittal.title}\": {', '.join(changed)}",
                        project=project, entity_type="submittal", entity_id=submittal.id, detail=changed)
    db.refresh(project)
    return _out(submittal, _by_system(_materials(project)))


class SubmittalDeletedOut(BaseModel):
    reference: str | None
    files: list[str]
    missing: list[str]
    register_rows: int
    map_rebuilt: bool


class SubmittalDeleteIn(BaseModel):
    reference: str


def _delete_for_good(db: Session, project: Project, user: User, *, reference: str | None, submittal: ProjectSubmittal | None) -> SubmittalDeletedOut:
    """Delete a material submittal permanently -- its files in the project
    folder (OneDrive), its index rows, its register row -- after the page
    has had the engineer confirm it (app.services.submittal_filing)."""
    from app.services import submittal_filing

    title = submittal.title if submittal else reference
    revision = submittal.revision if submittal else ""
    if reference:
        result = submittal_filing.delete_submittal(db, project, user, reference=reference)
    else:
        gone = [("deleted", submittal.id, submittal.system_code)]
        db.delete(submittal)
        project_state.submittals_changed(db, project, gone)
        db.commit()
        result = {"reference": None, "files": [], "missing": [], "register_rows": 1, "map_rebuilt": False}
    activity.record(db, user, "submittal.deleted",
                    f"Deleted submittal \"{title}\" {revision}".rstrip()
                    + (f" and {len(result['files'])} file{'s' if len(result['files']) != 1 else ''} from the project folder" if result["files"] else ""),
                    project=project, entity_type="submittal", detail=result)
    return SubmittalDeletedOut(**result)


@router.delete("/{project_id}/submittals/{submittal_id}", response_model=SubmittalDeletedOut)
def delete_submittal(
    project_id: int,
    submittal_id: int,
    current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalDeletedOut:
    """Delete a register row for good. A row with a reference takes its
    filed forms with it: the files in the project folder, their index
    rows, and the map's row -- the page warns and asks first."""
    project = _get_project_or_404(db, project_id)
    submittal = _get_submittal(project, submittal_id)
    return _delete_for_good(db, project, current_user, reference=submittal.reference, submittal=submittal)


@router.post("/{project_id}/submittals/delete", response_model=SubmittalDeletedOut)
def delete_submittal_by_reference(
    project_id: int,
    payload: SubmittalDeleteIn,
    current_user: User = Depends(require_role(*DELETER_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalDeletedOut:
    """The same deletion, by reference (from the Logs tab, which lists the
    forms by their reference)."""
    project = _get_project_or_404(db, project_id)
    reference = payload.reference.strip().upper()
    submittal = next((s for s in project.submittals if (s.reference or "").upper() == reference), None)
    from app.models import ProjectDocument

    known = db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id, ProjectDocument.reference == reference,
                                            ProjectDocument.state != "removed").count()
    if submittal is None and not known:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="No material submittal with this reference")
    return _delete_for_good(db, project, current_user, reference=reference, submittal=submittal)


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


def _as_the_register_stands(db: Session, project: Project, drawn: dict) -> dict:
    """The map as drawn, each revision at the status the register holds for
    it now and the actions the project holds now.

    The map is what the AI read off the forms when it was drawn; the
    register is the record. A status changed on the page since, or a reply
    filed since, is the register's, and the map shown beside the register
    must not say otherwise -- nor list an action that has been resolved."""
    import copy

    if not drawn:
        return drawn
    out = copy.deepcopy(drawn)
    submittals = project_state.items(db, project)
    by_reference: dict[str, SubmittalOut] = {}
    for item in submittals:
        names = {item.reference} | {r.reference for r in item.revisions} | {
            ref for r in item.revisions for ref in r.also_filed_as}
        for name in names:
            if name:
                by_reference.setdefault(name.upper(), item)
    open_by_submittal: dict[int, str] = {}
    actions = project_state.open_actions(db, project.id)
    for action in actions:
        if action.entity_type == "submittal" and action.entity_id:
            open_by_submittal[action.entity_id] = action.text
    from app.services.submittal_filing import _revision_number

    for system in out.get("systems") or []:
        for row in system.get("rows") or []:
            item = next((by_reference[r.upper()] for r in [row.get("reference"), *(row.get("references") or [])]
                         if r and r.upper() in by_reference), None)
            if item is None:
                continue
            revisions = {_revision_number(r.revision): r for r in item.revisions}
            for label, cell in (row.get("cells") or {}).items():
                rev = revisions.get(_revision_number(label))
                code = project_state.cell_code(rev.status, rev.reply_code) if rev else "NS"
                if code != "NS":
                    cell["status"] = code
            latest = (row.get("cells") or {}).get(row.get("latest") or "")
            if latest:
                row["latest_status"] = latest["status"]
            row["action"] = open_by_submittal.get(item.id)
    out["actions"] = [a.text for a in actions]
    return out


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
    latest = _as_the_register_stands(db, project, submittal_reader.latest_map(db, project) or {})
    # From the database only: whether the folder changed is the shared
    # document sync's business (app.services.document_sync), never an open's.
    delta = {"changed": False, "listing_files": int(latest.get("listing_files") or 0), "reason": ""}
    return SubmittalMapOut(available=reason is None, reason=reason, changed=delta["changed"],
                           listing_files=delta["listing_files"], change_reason=delta["reason"],
                           **{k: v for k, v in latest.items()
                              if k in SubmittalMapOut.model_fields and k not in ("available", "reason", "changed", "listing_files", "change_reason")})


# --- the reply to the consultant's comments -------------------------------------


def _reply_context(db: Session, project: Project, reference: str) -> dict:
    """What the sheet's heading says, from the submittal it answers."""
    wanted = reference.strip().upper()
    found = next((s for s in project.submittals if (s.reference or "").upper() == wanted), None)
    supplier = None
    manufacturer = found.manufacturer if found is not None else None
    for row in db.query(ProjectDocument).filter(
            ProjectDocument.project_id == project.id,
            ProjectDocument.role == "submittal_form",
            ProjectDocument.state != "removed").all():
        reading = (row.extracted or {}).get("form") or {}
        records = (row.extracted or {}).get("records") or []
        seen = (records[0].get("reference") if records else None) or reading.get("reference") or ""
        if seen.upper() != wanted:
            continue
        supplier = supplier or reading.get("supplier")
        manufacturer = manufacturer or brands.normalise(_maker(reading.get("manufacturer")))
    system = found.system_code if found is not None else None
    if not system:
        # The platform names its own packages for the system they cover
        # ("EP-29387-MAS-FAS"), so the reference says which when there is
        # no register row to ask.
        tail = system_rules.canonical(reference.strip().upper().rsplit("-", 1)[-1])
        system = tail if tail in system_rules.CODE_NAMES else None
    title = system_rules.system_display_name(project, system) if system else (found.title if found else reference)
    return {"system_code": system, "system_title": title, "manufacturer": manufacturer,
            "consultant": project.consultant, "supplier": supplier}


def _consultant_words(db: Session, project: Project, reference: str, revision: str) -> str | None:
    """What the consultant said on **this revision** of this submittal.

    The revision matters: a resubmission is filed with the comments it
    answers, so seeding an R1 sheet from whatever comments the project
    holds would hand the engineer R0's remarks to answer twice.
    """
    from app.services.submittal_filing import _revision_number

    wanted = _revision_number(revision)
    rows = db.query(ProjectDocument).filter(
        ProjectDocument.project_id == project.id, ProjectDocument.state != "removed").all()
    replies = submittal_replies.on_file(rows)
    for row in rows:
        if row.role != "submittal_form":
            continue
        reading = (row.extracted or {}).get("form") or {}
        records = (row.extracted or {}).get("records") or []
        seen = (records[0].get("reference") if records else None) or reading.get("reference") or ""
        if seen.upper() != reference.strip().upper():
            continue
        folder = submittal_replies.revision_folder(row.relative_path)
        if _revision_number(Path(folder).name if folder else "") != wanted:
            continue
        said = submittal_replies.for_revision(folder or "", replies)[1]
        # A reply filed as a scan has no reading, so its own page is read.
        return said or submittal_replies.words_of(rows, folder or "")
    return None


@router.get("/{project_id}/submittals/{reference}/{revision}/reply", response_model=SubmittalReplyOut)
def get_submittal_reply(
    project_id: int,
    reference: str,
    revision: str,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmittalReplyOut:
    """The reply sheet for one submittal revision: the saved one, or a new
    one opened from the consultant's own words."""
    project = _get_project_or_404(db, project_id)
    held = (db.query(SubmittalReply)
            .filter(SubmittalReply.project_id == project.id,
                    SubmittalReply.reference == reference,
                    SubmittalReply.revision == revision).one_or_none())
    rows = list(held.rows or []) if held is not None else reply_sheet.seed_rows(
        _consultant_words(db, project, reference, revision))
    context = _reply_context(db, project, reference)
    return SubmittalReplyOut(
        reference=reference,
        revision=revision,
        project_name=project.project_name,
        system_code=context["system_code"],
        system_title=context["system_title"],
        manufacturer=(held.manufacturer if held else None) or context["manufacturer"],
        consultant=(held.consultant if held else None) or context["consultant"],
        supplier=reply_sheet.supplier_name(context["supplier"]),
        rows=[ReplyRow(**row) for row in rows],
        saved=held is not None,
        updated_at=held.updated_at if held else None,
    )


@router.put("/{project_id}/submittals/{reference}/{revision}/reply", response_model=SubmittalReplyOut)
def save_submittal_reply(
    project_id: int,
    reference: str,
    revision: str,
    payload: SubmittalReplyIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> SubmittalReplyOut:
    """Keep the sheet as it stands, to come back to.

    Saving a reply is not submitting the next revision: the submittal's
    status, and the action asking for the next revision, are left alone
    until that revision is actually filed.
    """
    project = _get_project_or_404(db, project_id)
    held = (db.query(SubmittalReply)
            .filter(SubmittalReply.project_id == project.id,
                    SubmittalReply.reference == reference,
                    SubmittalReply.revision == revision).one_or_none())
    if held is None:
        held = SubmittalReply(project_id=project.id, reference=reference, revision=revision,
                              created_by_id=current_user.id)
        db.add(held)
    context = _reply_context(db, project, reference)
    held.system_code = context["system_code"]
    held.consultant = payload.consultant or context["consultant"]
    held.manufacturer = payload.manufacturer or context["manufacturer"]
    held.rows = [row.model_dump() for row in payload.rows]
    db.commit()
    db.refresh(held)
    activity.record(db, current_user, "submittal.reply_saved",
                    f"Saved the reply to {reference} {revision} ({len(held.rows)} comments)",
                    project=project, entity_type="submittal_reply", entity_id=held.id)
    return get_submittal_reply(project_id, reference, revision, _current_user=current_user, db=db)


@router.get("/{project_id}/submittals/{reference}/{revision}/reply.xlsx")
def export_submittal_reply(
    project_id: int,
    reference: str,
    revision: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The reply sheet as the workbook the company sends."""
    project = _get_project_or_404(db, project_id)
    sheet = get_submittal_reply(project_id, reference, revision, _current_user=current_user, db=db)
    content = reply_sheet.workbook(
        project_name=sheet.project_name or "",
        reference=sheet.reference,
        revision=sheet.revision,
        system_title=sheet.system_title,
        manufacturer=sheet.manufacturer,
        consultant=sheet.consultant,
        supplier=sheet.supplier,
        rows=[row.model_dump() for row in sheet.rows],
    )
    activity.record(db, current_user, "submittal.reply_exported",
                    f"Exported the reply to {reference} {revision}", project=project,
                    entity_type="submittal_reply")
    name = f"EP-{project.ep_number} Reply - {reference} {revision}.xlsx"
    disposition = 'attachment; filename="reply.xlsx"; ' + f"filename*=UTF-8''{quote(name)}"
    return Response(content, media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": disposition})


@router.get("/{project_id}/submittals/{reference}/{revision}/reply.pdf")
def export_submittal_reply_pdf(
    project_id: int,
    reference: str,
    revision: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The reply sheet as the PDF sent back with the next revision.

    The same sheet as the workbook beside it: that one is for an engineer
    who wants to work on it in Excel, this is the one the consultant is
    given.
    """
    project = _get_project_or_404(db, project_id)
    sheet = get_submittal_reply(project_id, reference, revision, _current_user=current_user, db=db)
    content = reply_sheet.pdf(
        project_name=sheet.project_name or "",
        reference=sheet.reference,
        revision=sheet.revision,
        system_title=sheet.system_title,
        manufacturer=sheet.manufacturer,
        consultant=sheet.consultant,
        supplier=sheet.supplier,
        rows=[row.model_dump() for row in sheet.rows],
    )
    activity.record(db, current_user, "submittal.reply_exported",
                    f"Exported the reply to {reference} {revision}", project=project,
                    entity_type="submittal_reply")
    name = f"EP-{project.ep_number} Reply - {reference} {revision}.pdf"
    disposition = 'attachment; filename="reply.pdf"; ' + f"filename*=UTF-8''{quote(name)}"
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": disposition})


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
    # What the compliance page's search found, kept on the project, before
    # the folder is searched again (app.routers.compliance).
    from app.routers.compliance import _stored_specs

    stored = _stored_specs(project)
    if stored is not None:
        wanted = (system_code or "").upper()
        matches = [m for m in stored if not wanted or m.system_code == wanted]
    else:
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
        from app.services import document_control

        # Past 260 characters Windows needs the extended-length form, and a
        # datasheet deep in a synced project folder is easily past it. Asked
        # the ordinary way it is not there, and it would drop out of the
        # package without a word.
        if full.suffix.lower() != ".pdf" or not os.path.isfile(document_control._os_path(full)):
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
    from app.services.submittal_package import index_for

    index = index_for(system_code)
    library = _submittal_library()
    # The project's own folder, not the archive root: see _search_archive.
    folder = Path(project.source_folder_path) if project.source_folder_path else None
    # Both of these are expensive against a synced archive -- indexing the
    # datasheet library, and walking the project folder for a specification --
    # so neither is done unless a chosen section actually needs it.
    libraries = get_libraries() if index.datasheet in sections else {}
    specs = _specs_for(project, system_code) if index.spec is not None and index.spec in sections else []
    # The battery calculation is the fire alarm's: a system without one (a
    # monitored self-contained emergency light system; a central battery
    # system until its own is built) has no such section to tick.
    applies, _why = system_rules.battery_calculation_applies(project, system_code)
    if index.battery is not None and not applies:
        sections = set(sections) - {index.battery}
    panels = _battery_panels(db, project, sections) if db is not None and index.battery is not None else []
    links = equipment_currents.index(db) if db is not None and index.datasheet in sections and not index.library_datasheets else {}
    plan = plan_package(
        project, sections, library, folder, libraries,
        spec_documents=specs, system_code=system_code, battery_panels=panels,
        brand=_brand_of(project, system_code), datasheet_links=links,
    )
    if index.battery is not None and not applies:
        plan.sections = [s for s in plan.sections if s.number != index.battery]
    return plan


def _brand_of(project: Project, system_code: str | None) -> str | None:
    """The manufacturer a system's submittal is for: the brand the DRF gives
    the system, else the one its BOQ lines carry -- and for the fire-rated
    cables, the cable brand chosen on the Proposed Materials tab."""
    from app.routers.projects import _brand_for

    if system_rules.canonical(system_code) == "FRC":
        from sqlalchemy.orm import Session as _Session

        from app.services import frc_cables

        session = _Session.object_session(project)
        row = frc_cables.get(session, project) if session is not None else None
        return row.brand if row and row.brand else None
    brand = _brand_for(system_code, project.systems, project.separate_ve_panel) if system_code else None
    if brand:
        return brand.strip().upper()
    wanted = (system_code or "").strip().upper()
    carried = {(i.manufacturer or "").strip().upper() for i in project.boq_items
               if i.manufacturer and (not wanted or (i.system_code or "").upper() == wanted)}
    return carried.pop() if len(carried) == 1 else None


def _battery_calculation_out(project: Project, system_code: str | None) -> dict:
    """Whether this system's submittal encloses a battery calculation, why
    not, and -- only when one is coming -- the note the page shows. A system
    that has none by nature says nothing: its section is not in the index,
    so there is nothing for the engineer to do about it."""
    applies, reason = system_rules.battery_calculation_applies(project, system_code)
    return {"applies": applies, "reason": reason,
            "note": reason if reason == system_rules.CENTRAL_BATTERY_PENDING else None}


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
        battery_calculation=_battery_calculation_out(project, system_code),
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
    chosen = _parse_sections(sections, system_code)
    plan = _plan_for(project, chosen, system_code, db)
    return _plan_out(project, plan, system_code)


def _parse_sections(sections: str | None, system_code: str | None = None) -> set[int]:
    """"1,5,7" -> {1, 5, 7}. Everything in the system's index, when nothing is named."""
    from app.services.submittal_package import index_for

    names = index_for(system_code).names
    if sections is None or not sections.strip():
        return set(names)
    chosen = set()
    for part in sections.split(","):
        part = part.strip()
        if part.isdigit() and int(part) in names:
            chosen.add(int(part))
    return chosen


@router.post("/{project_id}/submittal/package")
def build_submittal_package(
    project_id: int,
    payload: PackageBuildIn,
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Assemble the package and return it as one PDF -- and, unless
    `file` is off, file it: written to 02- Material Submittals/<system>/
    <revision>/ in the project folder and entered in the register, the
    document index and the log in the same step (app.services.submittal_filing),
    so the tab shows it at once with nothing scanned and no model asked.
    """
    project = _get_project_or_404(db, project_id)
    from app.services.submittal_package import index_for

    index = index_for(payload.system_code)
    chosen = {n for n in payload.sections if n in index.names}
    if index.battery is not None and not system_rules.battery_calculation_applies(project, payload.system_code)[0]:
        chosen.discard(index.battery)
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
    filed = None
    if payload.file:
        from app.services import submittal_filing

        try:
            filed = submittal_filing.file_package(
                db, project, current_user, pdf=built.pdf, system_code=payload.system_code,
                revision=payload.revision or "R0", title=payload.title or _system_title(project, payload.system_code),
                pages=built.pages, manufacturer=_brand_of(project, payload.system_code), replace=payload.replace)
        except submittal_filing.AlreadyPrepared as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.detail)
        except OSError as exc:
            db.rollback()
            filed = None
            built.warnings.append(f"The package could not be filed in the project folder ({exc}); save the download there.")
    activity.record(db, current_user, "submittal.package_built",
                    f"Built the material submittal package {payload.revision or 'R0'} ({built.pages} pages)"
                    + (f" and filed it as {filed.relative}" if filed else ""),
                    project=project, entity_type="submittal",
                    detail={"system": payload.system_code, "sections": ", ".join(str(index.names[n]) for n in sorted(chosen)),
                            "pages": built.pages, "warnings": len(built.warnings),
                            "filed": filed.relative if filed else None, "reference": filed.reference if filed else None})
    headers = {
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
        "X-Package-Pages": str(built.pages),
        "X-Package-Warnings": str(len(built.warnings)),
    }
    if filed:
        headers["X-Package-Filed"] = quote(filed.relative)
        headers["X-Package-Reference"] = filed.reference
    return Response(built.pdf, media_type="application/pdf", headers=headers)


def _system_title(project: Project, system_code: str | None) -> str:
    """What the cover calls the systems, from the DRF's own wording -- and
    only the systems this package is for. The fire-rated cables name their
    brands: "Fire Rated Cable (M/s. Fireguard & M/s. Ramcro)"."""
    code = system_rules.effective_code(system_code, project) or ""
    if code == "FRC":
        from sqlalchemy.orm import Session as _Session

        from app.services import frc_cables

        session = _Session.object_session(project)
        row = frc_cables.get(session, project) if session is not None else None
        brands = [b for b in [row.brand if row else None, (frc_cables.monitoring_for(row, project) or (None,))[0]] if b]
        named = " & ".join(f"M/s. {b.title()}" for b in dict.fromkeys(brands))
        return f"Fire Rated Cable ({named})" if named else "Fire Rated Cable"
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
