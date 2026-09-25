"""An uploaded drawing or zip, written to the staging folder as it arrives.

The upload is never held in memory: each chunk is written to a file under
<uploads>/_staging/ifc and added to the file's SHA-256 as it goes past, so
when the last chunk lands the file's path, size and hash are all known
without reading it again. A file over the limit is refused the moment it
passes it, and what was written of it is removed.

The limit is one number, IFC_MAX_UPLOAD_MB: every check and every message
is worked out from `MAX_BYTES` at the time, so the two can never disagree.

A staged file belongs to the job that reads it. The job removes it when it
ends, whichever way; `sweep` removes any a job no longer refers to (a job
cancelled while it waited, a worker that was killed), after a grace period.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.ifc import storage

log = logging.getLogger(__name__)

MIB = 1024 * 1024
MAX_MB = get_settings().ifc_max_upload_mb
MAX_BYTES = MAX_MB * MIB
CHUNK = 1 << 20
DWG_HEADER = b"AC10"
ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06")   # a zip, or an empty one


def limit_mb() -> int:
    """The limit as the engineer is told it: always worked out from MAX_BYTES."""
    return max(1, MAX_BYTES // MIB)


def too_large(name: str) -> HTTPException:
    return HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{name} is larger than {limit_mb()} MB")


def staging_dir() -> Path:
    return storage.uploads_root() / "_staging" / "ifc"


@dataclass
class Staged:
    path: Path
    name: str       # the file name as uploaded, without any folder
    ext: str        # "dwg" | "dxf" | "zip"
    size: int
    sha256: str


def extension(name: str) -> str:
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


async def stream_to_staging(file: UploadFile, *, allowed: tuple[str, ...], default_name: str) -> Staged:
    """Write the upload to the staging folder, chunk by chunk. Refuses (and
    removes) a file of another kind (415), an empty one (422), one past the
    limit (413), and a .dwg or .zip whose first bytes say it is not one (422)."""
    name = Path(file.filename or default_name).name
    ext = extension(name)
    if ext not in allowed:
        kinds = " or ".join(e.upper() for e in allowed)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"Upload a {kinds} file")
    folder = staging_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.{ext}"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(path, "wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise too_large(name)
                if len(head) < 8:
                    head += chunk[: 8 - len(head)]
                digest.update(chunk)
                await run_in_threadpool(out.write, chunk)
        if size == 0:
            raise HTTPException(422, f"{name} is empty")
        if ext == "dwg" and not head.startswith(DWG_HEADER):
            raise HTTPException(422, "This file is not a DWG drawing (it does not start with an AutoCAD DWG header)")
        if ext == "zip" and not head.startswith(ZIP_HEADERS):
            raise HTTPException(422, "This file is not a zip archive")
    except BaseException:
        discard(path)
        raise
    return Staged(path=path, name=name, ext=ext, size=size, sha256=digest.hexdigest())


def discard(path: Path | str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        log.warning("Could not remove the staged file %s", Path(path).name)


def staged_paths_in_use(db) -> set[str]:
    """The staged files a queued or running job will still read."""
    from app.models import BackgroundJob
    from app.services.jobs import ACTIVE

    rows = db.query(BackgroundJob.params).filter(BackgroundJob.status.in_(ACTIVE), BackgroundJob.params.isnot(None)).all()
    return {Path(p["staged_path"]).name for (p,) in rows if isinstance(p, dict) and p.get("staged_path")}


def sweep(db, *, max_age_hours: float | None = None) -> int:
    """Remove staged files no active job refers to, older than the grace
    period (an upload being written right now has no job yet). Returns how
    many were removed."""
    folder = staging_dir()
    if not folder.is_dir():
        return 0
    hours = get_settings().ifc_staging_max_age_hours if max_age_hours is None else max_age_hours
    cutoff = time.time() - hours * 3600
    in_use = staged_paths_in_use(db)
    removed = 0
    for path in folder.iterdir():
        try:
            if path.name in in_use or not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            os.remove(path)
            removed += 1
        except OSError:
            continue
    if removed:
        log.info("Removed %d abandoned staged IFC upload(s)", removed)
    return removed
