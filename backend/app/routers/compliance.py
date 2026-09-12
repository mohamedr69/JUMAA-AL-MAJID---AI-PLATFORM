"""Compliance statements: the specification each of the project's systems
must be answered against.

A compliance statement is written clause by clause against the consultant's
specification, so the first thing the engineer needs is that specification
-- and the honest answer is often that the project was never given one. So
this finds what the project folder holds per system (see
app.services.spec_finder) and, where it holds nothing, offers the two ways
forward: ask the contractor for it, with a draft mail ready to send, or
upload the copy the engineer has.
"""

import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import Project, User
from app.routers.projects import CREATOR_ROLES, _get_project_or_404, _save_upload
from app.schemas_design import (
    ComplianceOut,
    ComplianceSystemOut,
    DraftMailOut,
    SpecMatchOut,
)
from app.services.spec_finder import SYSTEMS, find_specs, open_spec

router = APIRouter(prefix="/projects", tags=["compliance"])

# Reading the specifications means walking the project folder and opening
# what looks like one, which takes half a minute on a synced drive: the
# answer is held briefly so opening the page is not that wait every time.
CACHE_FOR = timedelta(minutes=15)
_cache: dict[int, tuple[datetime, list, list[str]]] = {}
_cache_lock = threading.Lock()

# The DRF rows that mean a system, so the page shows the systems the project
# actually has.
SYSTEM_DRF_ROWS = {
    "FAS": ("Fire Alarm",),
    "EML": ("Emergency Light Monitoring",),
    "VES": ("Voice Evacuation",),
    "CBS": ("Central Battery System",),
}


def _project_systems(project: Project) -> list[str]:
    """The systems this project's compliance statements cover: the ones it
    actually delivers, which is what its Design Sheets and BOQ are for. The
    DRF's marks are the fallback for a project with neither -- on their own
    they list more than the project builds (EP-30784's DRF marks Voice
    Evacuation, which its fire alarm specification covers)."""
    codes = {(sheet.system_code or "").upper() for sheet in project.design_sheets}
    codes |= {(item.system_code or "").upper() for item in project.boq_items}
    if not codes & set(SYSTEMS):
        marked = {s.name for s in project.systems}
        codes = {code for code, rows in SYSTEM_DRF_ROWS.items() if marked & set(rows)}
    return [code for code in SYSTEMS if code in codes]


def _specs(project: Project, refresh: bool = False) -> tuple[list, list[str]]:
    wanted = set(_project_systems(project))
    if not project.source_folder_path:
        return [], ["The project has no archive folder, so nothing can be searched."]
    folder = Path(project.source_folder_path)
    if not folder.is_dir():
        return [], ["The project's archive folder is not reachable."]

    with _cache_lock:
        cached = _cache.get(project.id)
        if cached and not refresh and utc_now() - cached[0] < CACHE_FOR:
            return cached[1], cached[2]

    matches, warnings = find_specs(folder, wanted)
    # A specification kept loose and again inside the archive it arrived in
    # is one specification.
    unique: dict[tuple[str, str, str | None], object] = {}
    for match in sorted(matches, key=lambda m: (m.member is not None, m.path)):
        unique.setdefault((match.system_code, match.filename.lower(), match.section_no), match)
    found = list(unique.values())

    # Whatever the engineer uploaded for this project counts too.
    uploads = Path(get_settings().uploads_root) / f"EP-{project.ep_number}"
    if uploads.is_dir():
        uploaded, _ = find_specs(uploads, wanted)
        for match in uploaded:
            match.path = str((uploads / match.path).resolve())
            match.uploaded = True
        found += uploaded

    with _cache_lock:
        _cache[project.id] = (utc_now(), found, warnings)
    return found, warnings


def _out(match) -> SpecMatchOut:
    return SpecMatchOut(
        system_code=match.system_code,
        path=match.path,
        filename=match.filename,
        member=match.member,
        kind=match.kind,
        section_no=match.section_no,
        heading=match.heading,
        first_page=match.first_page,
        last_page=match.last_page,
        pages=match.pages,
        snippet=match.snippet,
        matched_on=match.matched_on,
        uploaded=getattr(match, "uploaded", False),
    )


@router.get("/{project_id}/compliance", response_model=ComplianceOut)
def get_compliance(
    project_id: int,
    refresh: bool = False,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComplianceOut:
    """The specification found for each of the project's systems."""
    project = _get_project_or_404(db, project_id)
    matches, warnings = _specs(project, refresh=refresh)
    return ComplianceOut(
        systems=[
            ComplianceSystemOut(
                code=code,
                name=SYSTEMS[code].name,
                specs=[_out(m) for m in matches if m.system_code == code],
            )
            for code in _project_systems(project)
        ],
        warnings=warnings,
        searched=project.source_folder_path,
    )


@router.get("/{project_id}/compliance/file")
def open_specification(
    project_id: int,
    path: str,
    member: str | None = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The specification itself, out of the archive it arrived in if that is
    where it lives."""
    project = _get_project_or_404(db, project_id)
    roots = [Path(project.source_folder_path)] if project.source_folder_path else []
    roots.append(Path(get_settings().uploads_root) / f"EP-{project.ep_number}")
    for root in roots:
        if not root.is_dir():
            continue
        content = open_spec(root, path, member)
        if content is not None:
            name = Path(member or path).name
            return Response(
                content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"},
            )
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such specification in this project")


@router.post("/{project_id}/compliance/specs", response_model=ComplianceOut)
def upload_specification(
    project_id: int,
    file: UploadFile = File(...),
    system_code: str = Form(...),
    current_user: User = Depends(require_role(*CREATOR_ROLES)),
    db: Session = Depends(get_db),
) -> ComplianceOut:
    """Attach the specification the project folder does not hold."""
    project = _get_project_or_404(db, project_id)
    code = system_code.strip().upper()
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{system_code}'")
    _save_upload(project, file, f"Specification {code}")
    with _cache_lock:
        _cache.pop(project.id, None)
    return get_compliance(project_id, refresh=True, _current_user=current_user, db=db)


@router.get("/{project_id}/compliance/draft-mail", response_model=DraftMailOut)
def draft_mail(
    project_id: int,
    system_code: str,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftMailOut:
    """A mail to the contractor asking for the specification, ready to send.
    Only what the project records is used -- nothing about the specification
    is invented, because there is none to describe."""
    project = _get_project_or_404(db, project_id)
    code = system_code.strip().upper()
    if code not in SYSTEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown system '{system_code}'")
    system = SYSTEMS[code]

    project_name = project.project_name or f"EP-{project.ep_number}"
    greeting = f"Dear {project.contact_person}," if project.contact_person else "Dear Sir,"
    sections = ", ".join(f"{s[:2]} {s[2:4]} {s[4:]}" for s in system.sections[:2])
    body = "\n".join(
        [
            greeting,
            "",
            f"Subject: EP-{project.ep_number} — {project_name} — {system.name} specification",
            "",
            f"We are preparing the compliance statement for the {system.name} system on the above project"
            + (f" at {project.location}" if project.location else "")
            + ".",
            "",
            f"The project documents issued to us do not include the {system.name} specification"
            f" (typically section {sections}). Kindly forward the specification so that the compliance"
            " statement can be prepared and submitted against it.",
            "",
            "Should the project have no specification for this system, kindly confirm that in writing so"
            " that we may prepare the compliance statement against the applicable UAE Fire and Life Safety"
            " Code of Practice and the approved design.",
            "",
            "Thank you,",
        ]
    )
    return DraftMailOut(
        to=project.contact_email,
        to_name=project.contact_person or project.contractor,
        subject=f"EP-{project.ep_number} — {project_name} — request for {system.name} specification",
        body=body.strip(),
    )
