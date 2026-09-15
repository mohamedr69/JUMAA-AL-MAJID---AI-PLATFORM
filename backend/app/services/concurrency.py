"""Optimistic concurrency: a save names the version it was edited from.

Two engineers with the same BOQ open each save the whole table. Without a
check the second save silently replaces the first. Every editable document
here -- the BOQ, the project information, the design inputs, a compliance
statement -- carries a version number that goes up on every write. The page
reads it with the document and sends it back as `If-Match` when saving; if
it is no longer current, the save is refused with 409 and a body the page
can show as "someone else saved this -- reload to see their changes".

A request without `If-Match` is accepted (scripts, older clients); the pages
always send it.
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Response, status
from sqlalchemy import event
from sqlalchemy.orm import object_session

from app.models import ComplianceStatement, ProjectDesign

VERSION_HEADER = "X-Resource-Version"
STALE_WRITE = "stale_write"


def parse_if_match(value: str | None) -> int | None:
    """`"3"`, `W/"3"` or `3` -> 3; absent or `*` -> None (no check)."""
    if value is None:
        return None
    value = value.strip()
    if not value or value == "*":
        return None
    match = re.fullmatch(r'(?:W/)?"?(\d+)"?', value)
    if not match:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="If-Match must be a version number")
    return int(match.group(1))


def require_current(if_match: str | None, current: int, what: str) -> None:
    expected = parse_if_match(if_match)
    if expected is not None and expected != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": STALE_WRITE,
                "message": f"{what} was changed by someone else after you opened it "
                           f"(you edited version {expected}; it is now version {current}). "
                           "Reload to see their changes, then make yours again.",
                "your_version": expected,
                "current_version": current,
            },
        )


def set_version_header(response: Response, version: int) -> None:
    response.headers[VERSION_HEADER] = str(version)
    response.headers["ETag"] = f'"{version}"'


@event.listens_for(ProjectDesign, "before_update")
@event.listens_for(ComplianceStatement, "before_update")
def _bump_version(_mapper, _connection, target) -> None:
    """Any real change to a design or a statement is a new version -- from a
    page, a background AI job or a recheck alike, so a save made against the
    state before any of them is caught."""
    session = object_session(target)
    if session is not None and session.is_modified(target, include_collections=False):
        target.version = (target.version or 0) + 1
