"""File a material submittal package the platform built into the project folder.

The package is written to 02- Material Submittals/<system>/<revision>/ and,
because the platform made it and knows exactly what it is, it is entered
into the database in the same step -- the document index row, a stored form
reading, the register row -- so the Material Submittal tab, the map and the
log show it at once, with nothing scanned and no model asked. A rebuild of
the same revision replaces the file.
"""

from __future__ import annotations

import dataclasses
import os
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import DocumentReading, Project, ProjectDocument, ProjectSubmittal, SubmittalStatus, User
from app.services import document_control, document_sync, project_folders, system_rules


@dataclasses.dataclass
class FiledPackage:
    path: Path
    relative: str
    reference: str
    revision: str
    map_rebuilt: bool
    replaced: bool


def reference_for(project: Project, system_code: str | None) -> str:
    """The reference the platform's own package goes by, one per system, so
    its revisions line up in the map: EP-30880-MAS-FA."""
    return f"EP-{project.ep_number}-MAS-{project_folders.system_folder(system_code) or 'XX'}"


def _revision_number(revision: str) -> int:
    digits = re.sub(r"\D", "", revision or "")
    return int(digits) if digits else 0


def file_package(db: Session, project: Project, user: User | None, *, pdf: bytes, system_code: str | None,
                 revision: str, title: str, pages: int, manufacturer: str | None = None) -> FiledPackage | None:
    """Write the package into the project folder and enter it in the
    database. None when the project has no reachable folder or the system
    has no folder of its own (the download is then all there is)."""
    from app.ai import submittal_reader

    folder = project_folders.submittal_folder(project, system_code, revision)
    if folder is None:
        return None
    revision = revision.strip().upper()
    code = system_rules.canonical(system_code)
    os.makedirs(document_control._os_path(folder), exist_ok=True)
    name = f"EP-{project.ep_number} - Material Submittal - {project_folders.system_folder(system_code)} - {revision}.pdf"
    path = folder / name
    replaced = os.path.isfile(document_control._os_path(path))
    with open(document_control._os_path(path), "wb") as handle:
        handle.write(pdf)
    stat = os.stat(document_control._os_path(path))
    root = Path(project.source_folder_path)
    relative = path.relative_to(root).as_posix()
    sha = document_sync.sha256_of(path) or ""
    reference = reference_for(project, system_code)
    number = _revision_number(revision)
    now = utc_now()

    # What a model would have read off the form, known here without one.
    reading = submittal_reader._normalise({
        "is_submittal": True, "reference": reference, "revision": number, "title": title, "system": title,
        "system_code": code or "", "supplier": "", "manufacturer": manufacturer or "",
        "submitted": now.strftime("%d %B %Y"),
        "reply": {"present": False, "from_consultant": False, "status": "none", "code": "", "consultant": "", "date": "",
                  "evidence": ""},
    })
    stored = submittal_reader.stored(db, sha)
    if stored is None:
        stored = DocumentReading(project_id=project.id, kind=submittal_reader.KIND, document_path=str(path),
                                 document_sha256=sha, model="platform", prompt_version=submittal_reader.PROMPT_VERSION,
                                 pages=min(2, max(pages, 1)), reading=reading, status="completed", calls=0,
                                 created_by_id=user.id if user else None)
        db.add(stored)
        db.flush()

    # The document index row, as a sync would have made it.
    record = document_control.ControlledDocument(
        system_code=code, name=path.name, path=relative, modified=datetime.fromtimestamp(stat.st_mtime),
        reference=reference, revision=f"R{number}", status="UR", category="submittals", source="platform")
    row = db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id, ProjectDocument.path == str(path)).first()
    if row is None:
        row = ProjectDocument(project_id=project.id, role=document_sync.ROLE_SUBMITTAL, path=str(path), relative_path=relative,
                              filename=path.name, first_seen_at=now, acknowledged=[], findings=[])
        db.add(row)
    row.role = document_sync.ROLE_SUBMITTAL
    row.relative_path, row.filename = relative, path.name
    row.sha256, row.size, row.mtime, row.last_seen_at = sha, stat.st_size, stat.st_mtime, now
    row.state, row.error = document_sync.FRESH, None
    row.reference, row.revision, row.status, row.system_code = reference, f"R{number}", "UR", code
    row.extracted = {"records": [document_sync._record_dict(record, root)], "notes": [], "form": reading}
    row.reading_id = stored.id
    row.last_processed_at, row.index_version = now, document_sync.INDEX_VERSION
    db.flush()
    document_sync.depend(db, row, "submittal", reference, f"filed by the platform as {relative}")
    document_sync.depend(db, row, "log", reference, f"register row from {relative}")

    # The register row: one per reference, at its latest revision.
    submittal = (db.query(ProjectSubmittal)
                 .filter(ProjectSubmittal.project_id == project.id, ProjectSubmittal.reference == reference).first())
    if submittal is None:
        submittal = ProjectSubmittal(project_id=project.id, title=title, reference=reference, system_code=code,
                                     manufacturer=manufacturer, revision=f"R{number}", status=SubmittalStatus.under_review,
                                     document_path=str(path), created_by_id=user.id if user else None)
        db.add(submittal)
    elif _revision_number(submittal.revision) <= number:
        submittal.revision, submittal.document_path = f"R{number}", str(path)
        submittal.status, submittal.reply_code = SubmittalStatus.under_review, None
        submittal.manufacturer = submittal.manufacturer or manufacturer
    db.commit()

    # The map, drawn again from every form the index holds -- readings all
    # stored, so no model is asked -- when the model is available to draw it.
    map_rebuilt = False
    if submittal_reader.available(project) is None:
        forms = [Path(r.path) for r in db.query(ProjectDocument)
                 .filter(ProjectDocument.project_id == project.id, ProjectDocument.role == document_sync.ROLE_SUBMITTAL,
                         ProjectDocument.state != document_sync.REMOVED)]
        try:
            submittal_reader.check(db, project, user, files=forms)
            map_rebuilt = True
        except Exception:  # noqa: BLE001 -- the filing stands; the next sync draws the map
            db.rollback()
    return FiledPackage(path=path, relative=relative, reference=reference, revision=f"R{number}",
                        map_rebuilt=map_rebuilt, replaced=replaced)
