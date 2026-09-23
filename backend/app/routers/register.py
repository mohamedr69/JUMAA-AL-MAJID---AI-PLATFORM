"""The project register: every job, and the engineer designing it.

The design manager's own record, kept as a workbook in the archive and
read here so the work can be reviewed in one place
(app.services.project_register).

    GET /register/projects   every job, newest action first
    GET /register/revision   what the workbook is now, for a page watching it

Read-only. The register is maintained outside the platform, and nothing
here writes to it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.deps import require_role
from app.models import RoleEnum, User
from app.services import project_register

router = APIRouter(prefix="/register", tags=["register"])

# The design manager's tool. An admin is included so the platform's owner
# can see what a manager sees -- the rail entry is the manager's alone.
REVIEW_ROLES = (RoleEnum.design_manager, RoleEnum.admin)


class RegisterProjectOut(BaseModel):
    ep_number: str
    project_name: str | None = None
    client: str | None = None
    consultant: str | None = None
    contractor: str | None = None
    scope: str | None = None
    # Lists, not single values: a job split between two engineers, or
    # carrying two statuses at once, is what a review is looking for.
    engineers: list[str] = []
    statuses: list[str] = []
    systems: list[str] = []
    products: list[str] = []
    started: datetime | None = None
    last_action: datetime | None = None
    rows: int = 0


class EngineerLoadOut(BaseModel):
    name: str
    # No longer with the company. Their jobs are still in the register and
    # still have to be accounted for.
    resigned: bool = False
    total: int = 0
    by_status: dict[str, int] = {}
    latest: RegisterProjectOut | None = None


class DivisionOut(BaseModel):
    key: str
    label: str
    total: int = 0
    # Every status the division's jobs carry. The page gives tiles to the
    # four largest; the rest are here so a total that does not add up can
    # be accounted for rather than guessed at.
    by_status: dict[str, int] = {}
    engineers: list[EngineerLoadOut] = []
    latest: RegisterProjectOut | None = None
    # Of `total`, how many are carried by someone who has left.
    resigned_total: int = 0


class RegisterOut(BaseModel):
    """What the register holds, and whether it could be read at all."""

    available: bool
    # Why not, when it could not be: a path that is not there reads as an
    # empty register otherwise, which is a different thing entirely.
    unavailable_reason: str | None = None
    updated_at: datetime | None = None
    total: int = 0
    engineers: list[str] = []
    statuses: list[str] = []
    projects: list[RegisterProjectOut] = []
    # The same jobs by division, and within it by engineer. A job in two
    # divisions is counted in both, so these do not sum to `total`.
    divisions: list[DivisionOut] = []
    # The statuses that get a tile, in the order the page shows them.
    tile_statuses: list[str] = []
    # What the workbook was when this was read. A page holding an older
    # one knows to read again.
    revision: str | None = None


class RegisterRevisionOut(BaseModel):
    """What the workbook is now. A `stat`, not a read."""

    available: bool
    revision: str | None = None
    updated_at: datetime | None = None


@router.get("/revision", response_model=RegisterRevisionOut)
def register_revision(
    _current_user: User = Depends(require_role(*REVIEW_ROLES)),
) -> RegisterRevisionOut:
    """Whether the register has changed, for a page watching it.

    Cheap on purpose: it looks at the file's timestamp and size and reads
    nothing. A page asks for this on a timer and fetches the register
    again only when the answer differs from the one it is holding.
    """
    current = project_register.revision()
    return RegisterRevisionOut(
        available=current is not None,
        revision=current,
        updated_at=project_register.updated_at(),
    )


@router.get("/projects", response_model=RegisterOut)
def list_register_projects(
    engineer: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    _current_user: User = Depends(require_role(*REVIEW_ROLES)),
) -> RegisterOut:
    """Every job the register holds, newest action first.

    `engineer` narrows to one engineer's jobs -- a job split between two
    is returned for either of them. `status` narrows to the jobs carrying
    it; a job whose rows disagree carries both, and is returned for
    either. `q` matches the EP number, the project name or the client.
    """
    try:
        found = project_register.projects()
        everyone = project_register.engineers()
    except project_register.RegisterUnavailable as exc:
        return RegisterOut(available=False, unavailable_reason=str(exc))

    if engineer:
        # Matched through the roster, so asking for "Wahab" also finds the
        # rows the register spells "wahab" -- one person, entered two ways.
        wanted = project_register.engineer_of(engineer)
        target = wanted.name.casefold() if wanted else engineer.strip().casefold()

        def carries(project) -> bool:
            for name in project.engineers:
                known = project_register.engineer_of(name)
                if (known.name.casefold() if known else name.strip().casefold()) == target:
                    return True
            return False

        found = [p for p in found if carries(p)]
    if status:
        wanted = status.strip().casefold()
        found = [p for p in found if any(s.casefold() == wanted for s in p.statuses)]
    if q:
        needle = q.strip().casefold()
        found = [
            p for p in found
            if needle in p.ep_number.casefold()
            or needle in (p.project_name or "").casefold()
            or needle in (p.client or "").casefold()
        ]

    def out(project) -> RegisterProjectOut:
        return RegisterProjectOut(**vars(project))

    # The divisions describe the whole register, not the filtered list: a
    # manager narrowing to one engineer still wants the shape of the work.
    divisions = [
        DivisionOut(
            key=d.key,
            label=d.label,
            total=d.total,
            by_status=d.by_status,
            resigned_total=d.resigned_total,
            latest=out(d.latest) if d.latest else None,
            engineers=[
                EngineerLoadOut(name=e.name, resigned=e.resigned, total=e.total,
                                by_status=e.by_status, latest=out(e.latest) if e.latest else None)
                for e in d.engineers
            ],
        )
        for d in project_register.divisions()
    ]

    return RegisterOut(
        available=True,
        updated_at=project_register.updated_at(),
        total=len(found),
        engineers=everyone,
        statuses=project_register.statuses(),
        projects=[out(p) for p in found[:limit]],
        divisions=divisions,
        tile_statuses=list(project_register.TILE_STATUSES),
        revision=project_register.revision(),
    )
