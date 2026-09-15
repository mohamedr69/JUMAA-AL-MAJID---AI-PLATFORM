"""Database backups for administrators: take one, list them, prove one restores.

  GET  /admin/backups                 the backups kept, newest first
  POST /admin/backups                 take a verified backup now
  POST /admin/backups/{name}/verify   restore it into a scratch database and read it

A backup is only worth something if it restores. Verifying copies the backup
to a temporary file, opens it as a database, runs SQLite's integrity check and
reads the projects, BOQ lines and issued revisions out of it -- the same
reads the platform makes -- without touching the live database.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.deps import require_role
from app.migrations import BACKUPS_ROOT, BackupFailed, backup_sqlite
from app.models import RoleEnum, User
from app.services import activity

router = APIRouter(prefix="/admin/backups", tags=["backups"])
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.db$")


def _backup_path(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Not a backup name")
    path = (BACKUPS_ROOT / name).resolve()
    if not path.is_relative_to(BACKUPS_ROOT.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such backup")
    return path


@router.get("")
def list_backups(_current_user: User = Depends(require_role(RoleEnum.admin))) -> list[dict]:
    if not BACKUPS_ROOT.is_dir():
        return []
    rows = []
    for path in BACKUPS_ROOT.glob("*.db"):
        stat = path.stat()
        rows.append({"name": path.name, "size": stat.st_size,
                     "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()})
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


@router.post("", status_code=status.HTTP_201_CREATED)
def take_backup(current_user: User = Depends(require_role(RoleEnum.admin)), db: Session = Depends(get_db)) -> dict:
    try:
        path = backup_sqlite(engine, "manual")
    except BackupFailed as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This database is not a file the platform can back up itself")
    activity.record(db, current_user, "admin.backup_taken", f"Took a database backup ({path.name})", entity_type="backup")
    return {"name": path.name, "size": path.stat().st_size}


@router.post("/{name}/verify")
def verify_backup(name: str, current_user: User = Depends(require_role(RoleEnum.admin)), db: Session = Depends(get_db)) -> dict:
    source = _backup_path(name)
    with tempfile.TemporaryDirectory() as scratch:
        restored = Path(scratch) / "restored.db"
        shutil.copy2(source, restored)
        connection = sqlite3.connect(str(restored))
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ("projects", "project_boq_items", "project_boq_revisions", "users")}
            revision = connection.execute(
                "SELECT p.ep_number, r.number, length(r.items) FROM project_boq_revisions r "
                "JOIN projects p ON p.id = r.project_id ORDER BY r.id DESC LIMIT 1").fetchone()
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"The backup does not open as a database: {exc}") from exc
        finally:
            connection.close()
    ok = integrity == "ok" and counts["users"] > 0
    activity.record(db, current_user, "admin.backup_verified", f"Verified backup {name}: {'restores' if ok else 'FAILED'}",
                    entity_type="backup", detail={"integrity": integrity, **counts})
    return {"name": name, "restores": ok, "integrity": integrity, "counts": counts,
            "schema_version": version[0] if version else None,
            "latest_revision": {"ep_number": revision[0], "number": revision[1]} if revision else None}
