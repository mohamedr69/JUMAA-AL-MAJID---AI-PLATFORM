"""Where this PC keeps the platform's data.

A project lives in the database, and by default that database is a file
under `backend/`. Two PCs therefore hold two different lists of projects,
which is what an engineer notices when the office PC shows projects the
laptop does not.

The cure is one shared data folder -- `DATA_ROOT` in `backend/.env`,
pointing at a folder OneDrive syncs -- and this endpoint is how a user
sees, on each PC, which database that PC is actually using and how many
projects are in it. `backend/scripts/use_shared_data.py` performs the
switch; `backend/scripts/compare_databases.py` says what one database
holds that the other does not.
"""

from __future__ import annotations

import socket
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Project, User

router = APIRouter(tags=["data"])


class DataLocationOut(BaseModel):
    """What this PC is working from."""

    machine: str
    database: str
    # A folder shared between PCs (DATA_ROOT), rather than this PC's own copy.
    shared: bool
    data_root: str | None
    uploads: str
    backups: str | None
    projects: int
    users: int
    # What to do to see the same projects on every PC.
    advice: str


@router.get("/data-location", response_model=DataLocationOut)
def data_location(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DataLocationOut:
    settings = get_settings()
    database = settings.database_url
    if database.startswith("sqlite:///"):
        database = str(Path(database.removeprefix("sqlite:///")).resolve())
    shared = bool(settings.data_root)
    projects = db.query(func.count(Project.id)).scalar() or 0
    advice = (
        "This PC shares its data folder, so every PC pointed at the same folder shows the same projects. "
        "Open the platform on one PC at a time so OneDrive can finish syncing between them."
        if shared
        else "This PC keeps its own database, so its projects are its own. To see the same projects everywhere, "
             "set DATA_ROOT in backend/.env to a folder OneDrive syncs (the same folder on every PC) and restart -- "
             "backend/scripts/use_shared_data.py does it, keeping what is already here."
    )
    return DataLocationOut(
        machine=socket.gethostname(),
        database=database,
        shared=shared,
        data_root=settings.data_root,
        uploads=str(Path(settings.uploads_root).resolve()),
        backups=settings.backups_root,
        projects=projects,
        users=db.query(func.count(User.id)).scalar() or 0,
        advice=advice,
    )
