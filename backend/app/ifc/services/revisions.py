"""Which drawing a file is, at which revision -- and a revision chain that
stays one chain whatever happens at once.

A drawing is known by its reference ("FA-101"), not its file name:
"FA-101-R00.dwg" and "FA-101 R01.dwg" are two revisions of one drawing,
and the revision is kept on its own. Where the reference cannot say for
sure which drawing a file revises, the engineer is asked
(`IdentityError` "revision_confirmation_required") rather than history
being rewritten on a guess.

The chain R0 -> R1 -> R2 is protected by the database, not by a look
followed by an insert: a partial unique index lets a live drawing be
revised once (`supersedes_id`). Two workers that both revise R0 race to
the index; one wins, the other gets `RevisionConflict` naming the
revision that won.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProjectIfcDrawing as Drawing

_REVISION = re.compile(r"R(?:EV)?\.?\s*0*(\d{1,2})", re.I)
# A revision at the end of a file name: "FA-101-R01", "FA-101 Rev 2", "FA-101_R1", "FA-101 (R3)"
_REVISION_SUFFIX = re.compile(r"[\s_.-]*\(?\s*\bR(?:EV)?\.?\s*0*(\d{1,2})\s*\)?\s*$", re.I)
# What the platform adds to a file it filed twice: "FA-101 (uploaded 2026-09-19 1405)"
_UPLOADED = re.compile(r"\s*\(uploaded [^)]*\)\s*$", re.I)
# A drawing number: letters, a dash, digits ("FA-101", "E-FA-105A"). An EP
# number is the project's, not the drawing's.
_DRAWING_NUMBER = re.compile(r"(?<![A-Z0-9])((?:[A-Z]{1,5}-){1,2}\d{2,4}[A-Z]?)(?![A-Z0-9])")


def revision_number(revision: str | None) -> int:
    m = _REVISION.fullmatch((revision or "").strip())
    return int(m.group(1)) if m else -1


def _stem(filename: str) -> str:
    stem = PurePath(filename).stem if re.search(r"\.(dwg|dxf)$", filename, re.I) else filename
    return _UPLOADED.sub("", stem).strip()


def revision_from_name(filename: str) -> str | None:
    """The revision a file name states ("FA-101-R01.dwg" -> "R1"), or None."""
    m = _REVISION_SUFFIX.search(_stem(filename))
    return f"R{int(m.group(1))}" if m else None


def reference(filename: str) -> str:
    """The drawing a file is, whatever revision it is at: its one drawing
    number when it has exactly one, else its name without the revision."""
    stem = _REVISION_SUFFIX.sub("", _stem(filename)).strip(" -_.")
    upper = " ".join(stem.replace("_", " ").upper().split())
    numbers = {n for n in _DRAWING_NUMBER.findall(upper) if not n.startswith("EP-")}
    if len(numbers) == 1:
        return numbers.pop()[:120]
    return (upper or filename.upper())[:120]


# --- the live drawings -------------------------------------------------------------


def live(db: Session, project_id: int):
    """A project's drawings, archived ones left out."""
    return db.query(Drawing).filter(Drawing.project_id == project_id, Drawing.deleted_at.is_(None))


def superseded(db: Session, project_id: int) -> dict[int, Drawing]:
    """{drawing id: the live drawing that supersedes it}."""
    rows = live(db, project_id).filter(Drawing.supersedes_id.isnot(None)).all()
    return {d.supersedes_id: d for d in rows}


def in_force(db: Session, project_id: int) -> list[Drawing]:
    """The live drawings nothing supersedes, oldest first."""
    later = superseded(db, project_id)
    return [d for d in live(db, project_id).order_by(Drawing.uploaded_at, Drawing.id).all() if d.id not in later]


def latest_of(db: Session, drawing: Drawing) -> Drawing:
    """The revision in force of the chain `drawing` belongs to."""
    later = superseded(db, drawing.project_id)
    at, seen = drawing, set()
    while at.id in later and at.id not in seen:
        seen.add(at.id)
        at = later[at.id]
    return at


# --- planning an import ------------------------------------------------------------


class IdentityError(Exception):
    """Refused before anything is read. `detail` is what the page is told:
    a sentence, or {"code", "message", ...} when it has a decision to offer."""

    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.status = status
        self.detail = detail


class RevisionConflict(Exception):
    """Another process revised the drawing, or issued the same revision,
    first. The message is for the engineer."""


@dataclass
class Plan:
    revision: str
    supersedes: Drawing | None
    reference: str


def _summary(d: Drawing) -> dict:
    return {"id": d.id, "filename": d.filename, "revision": d.revision or "R0", "reference": d.drawing_reference}


def duplicate_of(db: Session, project_id: int, sha256: str | None) -> Drawing | None:
    """The live drawing already imported from exactly this file, if any."""
    if not sha256:
        return None
    return live(db, project_id).filter(Drawing.source_sha256 == sha256).order_by(Drawing.id).first()


def plan(db: Session, project_id: int, *, filename: str, sha256: str | None, revision: str | None,
         supersedes_id: int | None, confirm_new: bool = False, refuse_duplicate: bool = True) -> Plan:
    """The revision a single upload is issued as, and the drawing it
    replaces. Refuses (IdentityError) a file already imported (unless
    `refuse_duplicate` is off: the synchronous endpoint's old contract), a
    revision not later than the one it replaces, a drawing already revised,
    and -- unless the engineer said it is a new drawing -- a file whose
    reference matches a drawing in force that it was not said to revise."""
    stated = (revision or "").strip()
    if stated and revision_number(stated) < 0:
        raise IdentityError(422, f"'{revision}' is not a revision: choose R0, R1, R2 ...")
    dup = duplicate_of(db, project_id, sha256) if refuse_duplicate else None
    if dup is not None:
        raise IdentityError(409, {
            "code": "duplicate_drawing",
            "message": f"This file was already imported as {dup.filename} {dup.revision}. Nothing was read again.",
            "drawing": _summary(dup)})

    ref = reference(filename)
    previous = None
    if supersedes_id is not None:
        previous = live(db, project_id).filter(Drawing.id == supersedes_id).first()
        if previous is None:
            raise IdentityError(404, "The drawing to revise was not found. Refresh the IFC drawings list.")
        later = superseded(db, project_id).get(previous.id)
        if later is not None:
            raise IdentityError(409, f"{previous.filename} {previous.revision} is already revised by "
                                     f"{later.filename} {later.revision}: import the revision of that one.")
        ref = previous.drawing_reference or ref
    elif not confirm_new:
        same = [d for d in in_force(db, project_id) if d.drawing_reference and d.drawing_reference == ref]
        if same:
            d = same[-1]
            raise IdentityError(409, {
                "code": "revision_confirmation_required",
                "message": (f"Possible existing drawing found: {d.filename} {d.revision} ({ref}). "
                            f"Is {filename} a revision of {ref}?"),
                "uploaded": filename,
                "candidates": [_summary(x) for x in same],
                "suggested_revision": _next(d, filename)})

    if not stated:
        stated = _next(previous, filename) if previous is not None else (revision_from_name(filename) or "R0")
    n = revision_number(stated)
    if previous is not None and n <= revision_number(previous.revision):
        raise IdentityError(422, f"{previous.filename} is at {previous.revision}: its revised drawing must be a "
                                 f"later revision than that.")
    return Plan(revision=f"R{n}", supersedes=previous, reference=ref)


def _next(previous: Drawing | None, filename: str) -> str:
    """The revision a file revising `previous` is most likely at: the one
    its name states when that is later, else the next one."""
    after = revision_number(previous.revision) + 1 if previous is not None else 0
    named = revision_number(revision_from_name(filename))
    return f"R{max(after, named)}"


# --- saving, atomically ------------------------------------------------------------


def insert(db: Session, drawing: Drawing) -> None:
    """Add the drawing and flush, so the database's indexes decide now --
    before anything is filed -- whether this revision may exist. Raises
    RevisionConflict, with the transaction rolled back, when another
    process got there first."""
    db.add(drawing)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise RevisionConflict(conflict_message(db, drawing)) from exc


def conflict_message(db: Session, drawing: Drawing) -> str:
    ref = drawing.drawing_reference or drawing.filename
    if drawing.supersedes_id is not None:
        previous = db.get(Drawing, drawing.supersedes_id)
        if previous is not None:
            now = latest_of(db, previous)
            return (f"{previous.drawing_reference or previous.filename} {previous.revision} was revised by another "
                    f"process while this was being read. Current latest revision: {now.revision}. Refresh the IFC "
                    f"drawings list and import this file as a revision of {now.revision}.")
    return (f"{ref} {drawing.revision} could not be saved: the IFC drawings changed while it was being read. "
            f"Refresh the IFC drawings list and import it again.")


# --- deleting ----------------------------------------------------------------------


def delete_rule(db: Session, drawing: Drawing) -> str:
    """How a drawing may go: "hard" (a standalone import made by mistake:
    nothing revises it and it revises nothing), "archive" (the revision in
    force of a chain: kept for the history, and the one before it is in
    force again), or refused with the reason (a revision a later one
    depends on)."""
    later = superseded(db, drawing.project_id).get(drawing.id)
    if later is not None:
        raise IdentityError(409, f"{drawing.filename} {drawing.revision} is the history of {later.filename} "
                                 f"{later.revision}, which revises it. A revision with a later one is kept; "
                                 f"delete {later.revision} first if this one was imported by mistake.")
    return "archive" if drawing.supersedes_id is not None else "hard"


def reference_like(db: Session, project_id: int, ref: str) -> list[Drawing]:
    return live(db, project_id).filter(func.upper(Drawing.drawing_reference) == ref.upper()).all()
