"""The archive directory: search it, see it, rebuild it.

The EP folders in the synced archive, indexed in the database
(app.services.ep_directory) so the search box can suggest a project while
someone types instead of walking a synced drive for every keystroke.

    GET  /archive/search?q=294   what to suggest for what has been typed
    GET  /archive/status         whether the index is searchable, and how fresh
    POST /archive/scan           rebuild it now, in the background
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import RoleEnum, User
from app.services import ep_directory

router = APIRouter(prefix="/archive", tags=["archive"])

# Anyone signed in may search -- it is how a project is found. Rebuilding
# the index reads the whole archive, so it sits with the roles that own a
# project's lifecycle.
SCAN_ROLES = (RoleEnum.admin, RoleEnum.design_manager, RoleEnum.design_engineer)


class ArchiveSuggestionOut(BaseModel):
    """One line of the search box's dropdown."""

    ep_number: str
    # From the platform's project once it has one (read off the DRF),
    # otherwise from the folder's name. Null when the folder is only a
    # number.
    project_name: str | None
    folder_name: str
    # Below the archive root, never the whole path: the index is shared
    # between machines and the archive sits under each user's own profile.
    relative_path: str
    locations: int
    project_id: int | None
    project_status: str | None


class ArchiveStatusOut(BaseModel):
    configured: bool
    reachable: bool
    archive_path: str | None
    # pending | scanning | ready | partial | failed
    scan_status: str
    scanning: bool
    # Distinct EP numbers, and the folders they are filed in.
    projects: int
    folders: int
    # Whether the search box can answer from the index yet.
    searchable: bool
    last_scan_at: str | None
    last_successful_scan_at: str | None
    last_error: str | None


class ArchiveScanStartedOut(BaseModel):
    started: bool
    message: str


@router.get("/search", response_model=list[ArchiveSuggestionOut])
def search_archive(
    q: str = Query("", max_length=120, description="What has been typed so far: an EP number or part of a name"),
    limit: int = Query(8, ge=1, le=25),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ArchiveSuggestionOut]:
    """Suggestions for a partly typed EP number or project name.

    Always a list, never an error: a search box that has nothing to suggest
    -- because the index is empty, or the archive is not configured on this
    machine -- suggests nothing and lets the number be typed out in full.
    /archive/status is where a page asks why.
    """
    if not get_settings().archive_index_enabled:
        return []
    return [
        ArchiveSuggestionOut(**vars(s))
        for s in ep_directory.search(db, q, limit=limit)
    ]


@router.get("/status", response_model=ArchiveStatusOut)
def archive_status(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArchiveStatusOut:
    return ArchiveStatusOut(**ep_directory.status(db))


@router.post("/scan", response_model=ArchiveScanStartedOut)
def start_scan(
    _current_user: User = Depends(require_role(*SCAN_ROLES)),
    db: Session = Depends(get_db),
) -> ArchiveScanStartedOut:
    """Walk the archive now. Returns as soon as the walk has started: it
    takes minutes over a synced drive, and /archive/status is how the page
    follows it."""
    try:
        ep_directory.archive_root()
    except ep_directory.ArchiveUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if not ep_directory.start_background_scan("requested"):
        return ArchiveScanStartedOut(started=False, message="A scan of the archive is already running")
    return ArchiveScanStartedOut(started=True, message="Scanning the archive; this page will show the result")
