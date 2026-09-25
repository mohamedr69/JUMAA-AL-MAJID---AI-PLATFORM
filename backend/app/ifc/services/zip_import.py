"""A zip of the building: every drawing in it, read a floor at a time.

The archive stays on disk. It is checked from its directory -- how many
files, what they are called, what they declare they unpack to -- before
anything is unpacked, and refused whole only for what makes the archive
itself unusable (not a zip, too many files, too big unpacked, two floors
under one name, no drawing at all). Then each drawing is unpacked on its
own to a temporary file, with its bytes counted as they come out (a
member that unpacks to more than it declared is stopped), read, and its
temporary file removed before the next. One broken floor is reported and
the others are read.

A file in the archive is matched to a drawing already imported by its
reference (FA-101), not its name, and never on a guess:

  * the same file (same SHA-256) as a drawing already imported -> unchanged
  * a reference in force, and a later revision stated in the file name
    ("FA-101-R01.dwg" after R0) -> that drawing's revision
  * a reference in force and no later revision in the name -> not read:
    "revision confirmation required", to be imported on its own where the
    engineer says what it is
  * a reference not seen before -> a new drawing, at the revision in its
    name or R0
"""
from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.ifc.services import revisions, upload

MIB = 1024 * 1024
MAX_MEMBERS = get_settings().ifc_max_zip_members
# Checked against the *uncompressed* sizes the archive declares, before
# anything is unpacked: a zip bomb is small until it is opened.
MAX_UNPACKED_BYTES = get_settings().ifc_max_zip_unpacked_mb * MIB


def _by_floor_then_name(pair: tuple[str, object]) -> list:
    """Level 2 before Level 10: the digits in a name count as a number."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", pair[0].lower())]


def members(source) -> tuple[list[tuple[str, zipfile.ZipInfo]], list[str]]:
    """The DWG and DXF files an archive holds, as (name, member) in floor
    order, and what was left out. `source`: a path, or bytes (tests).

    A member is refused rather than trusted -- an archive is a list of paths
    someone else wrote, and a path that climbs out of the folder it is
    unpacked into is the oldest trick there is."""
    import io

    skipped: list[str] = []
    found: list[tuple[str, zipfile.ZipInfo]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    except (zipfile.BadZipFile, OSError):
        raise HTTPException(422, "This file is not a zip archive")
    with archive:
        entries = [m for m in archive.infolist() if not m.is_dir()]
        if len(entries) > MAX_MEMBERS:
            raise HTTPException(422, f"The archive holds {len(entries)} files; {MAX_MEMBERS} is the most that can be "
                                     f"read at once")
        unpacked = 0
        for member in entries:
            # One normalised spelling, checked once: a path rooted with a
            # backslash is the same path as one rooted with a slash.
            path = member.filename.replace("\\", "/")
            name = PurePosixPath(path).name
            parts = PurePosixPath(path).parts
            ext = upload.extension(name)
            if (path.startswith("/") or ".." in parts or ":" in path
                    or any(part.startswith("__MACOSX") for part in parts) or name.startswith(".")):
                skipped.append(member.filename)
                continue
            if ext not in ("dwg", "dxf"):
                skipped.append(member.filename)
                continue
            if member.file_size > upload.MAX_BYTES:
                skipped.append(member.filename)
                continue
            unpacked += member.file_size
            if unpacked > MAX_UNPACKED_BYTES:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"The drawings in this archive come to more than "
                                           f"{MAX_UNPACKED_BYTES // MIB} MB unpacked")
            if ext == "dwg":
                # Named .dwg and is not one: the same check a single upload
                # gets, on the first bytes only.
                with archive.open(member) as handle:
                    if not handle.read(len(upload.DWG_HEADER)).startswith(upload.DWG_HEADER):
                        skipped.append(member.filename)
                        continue
            found.append((name, member))
    if not found:
        raise HTTPException(422, "This archive holds no DWG or DXF drawings")
    # Only the file name is kept, so two floors filed under different
    # folders but the same name would be one drawing. Say so instead.
    seen: set[str] = set()
    for name, _ in found:
        if name.lower() in seen:
            raise HTTPException(422, f"Two files in the archive are both called {name}. Give each floor a name of "
                                     f"its own -- the folders they sit in are not kept.")
        seen.add(name.lower())
    found.sort(key=_by_floor_then_name)
    return found, skipped


class MemberTooLarge(Exception):
    pass


@dataclass
class Unpacked:
    path: Path
    sha256: str
    size: int


def unpack(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> Unpacked:
    """One member to a temporary file in the staging folder, hashed as it
    comes out; stopped when it unpacks to more than it declared or than the
    upload limit. The caller removes the file."""
    folder = upload.staging_dir()
    folder.mkdir(parents=True, exist_ok=True)
    ext = upload.extension(member.filename)
    path = folder / f"{uuid.uuid4().hex}.{ext}"
    digest = hashlib.sha256()
    size = 0
    cap = min(member.file_size, upload.MAX_BYTES)
    try:
        with archive.open(member) as src, open(path, "wb") as out:
            while chunk := src.read(upload.CHUNK):
                size += len(chunk)
                if size > cap:
                    raise MemberTooLarge()
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        upload.discard(path)
        raise
    return Unpacked(path=path, sha256=digest.hexdigest(), size=size)


def plan_member(db, project_id: int, name: str, sha256: str) -> tuple[str, object]:
    """What to do with one file of the archive: ("read", Plan),
    ("unchanged", the drawing it is), or ("confirm", the drawing it may revise)."""
    dup = revisions.duplicate_of(db, project_id, sha256)
    if dup is not None:
        return "unchanged", dup
    ref = revisions.reference(name)
    named = revisions.revision_from_name(name)
    same = [d for d in revisions.in_force(db, project_id) if d.drawing_reference == ref]
    if same:
        previous = same[-1]
        if named is not None and revisions.revision_number(named) > revisions.revision_number(previous.revision):
            return "read", revisions.Plan(revision=named, supersedes=previous, reference=ref)
        return "confirm", previous
    return "read", revisions.Plan(revision=named or "R0", supersedes=None, reference=ref)
