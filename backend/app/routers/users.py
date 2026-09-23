from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database import get_db
from app.deps import require_role
from app.models import RoleEnum, User
from app.schemas import AccountOut, ActivityEventOut, ActivityPageOut, UserCreate, UserOut, UserUpdate
from app.services import activity

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role(RoleEnum.admin))])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)) -> list[User]:
    return db.query(User).order_by(User.created_at.desc()).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> User:
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    activity.record(db, current_user, "user.created", f"Created the account of {user.full_name} ({user.email})",
                    entity_type="user", entity_id=user.id, detail={"role": user.role.value})
    return user


def _is_last_active_admin(db: Session, user: User) -> bool:
    if user.role != RoleEnum.admin or not user.is_active:
        return False
    other_active_admins = (
        db.query(User)
        .filter(User.role == RoleEnum.admin, User.is_active == True, User.id != user.id)  # noqa: E712
        .count()
    )
    return other_active_admins == 0


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    demoting = payload.role is not None and payload.role != RoleEnum.admin
    deactivating = payload.is_active is False
    if (demoting or deactivating) and _is_last_active_admin(db, user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote or deactivate the last active admin",
        )

    changes = {}
    if payload.full_name is not None and payload.full_name != user.full_name:
        changes["name"] = f"{user.full_name} -> {payload.full_name}"
        user.full_name = payload.full_name
    if payload.role is not None and payload.role != user.role:
        changes["role"] = f"{user.role.value} -> {payload.role.value}"
        user.role = payload.role
    if payload.is_active is not None and payload.is_active != user.is_active:
        changes["active"] = f"{user.is_active} -> {payload.is_active}"
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    if changes:
        activity.record(db, current_user, "user.updated", f"Changed the account of {user.full_name}",
                        entity_type="user", entity_id=user.id, detail=changes)
    return user


# What a user cannot be deleted away from: the tables that name them and
# will not take a null. The record of who did what outlives the account --
# that is what `activity_events` is for -- so an account with any of these
# is deactivated, never removed.
HISTORY: tuple[tuple[str, str, str], ...] = (
    ("activity_events", "user_id", "recorded action"),
    ("projects", "created_by_id", "project created"),
    ("project_boq_revisions", "issued_by_id", "BOQ revision issued"),
    ("estimation_projects", "created_by", "estimation project"),
    ("division_projects", "created_by", "division project"),
)


def _history_of(db: Session, user_id: int) -> dict[str, int]:
    """What this account has left behind, by kind and count."""
    found: dict[str, int] = {}
    for table, column, label in HISTORY:
        count = db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :id"), {"id": user_id}
        ).scalar_one()
        if count:
            found[label] = int(count)
    return found


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_user(
    user_id: int,
    current_user: User = Depends(require_role(RoleEnum.admin)),
    db: Session = Depends(get_db),
) -> Response:
    """Remove an account that has never been used.

    An account that has done anything is not removed: the platform keeps
    who did what after the thing itself is gone (`ActivityEvent`), and
    deleting the account would either erase that record or leave rows
    pointing at nobody. Such an account is deactivated instead -- it can
    no longer sign in, and its history still reads correctly. The refusal
    says what is in the way so the choice is an informed one.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "self_delete", "message": "You cannot delete your own account"},
        )
    # No check for the last admin here, unlike a role change: only an
    # admin may delete, and deleting yourself is already refused, so the
    # caller is always a second active admin and the target never the last.

    history = _history_of(db, user.id)
    if history:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "user_has_history",
                "message": (
                    f"{user.full_name} has "
                    + ", ".join(f"{count} {label}{'s' if count != 1 else ''}" for label, count in history.items())
                    + " on the platform. Deactivate the account instead, so that record still reads correctly."
                ),
                "history": history,
            },
        )

    name, email = user.full_name, user.email
    db.delete(user)
    db.commit()
    activity.record(db, current_user, "user.deleted", f"Deleted the unused account of {name} ({email})",
                    entity_type="user", entity_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- a user's record ------------------------------------------------------------
# Shared with /auth/me/..., where each user reads their own.


def account_out(db: Session, user: User) -> AccountOut:
    return AccountOut.model_validate(activity.account(db, user), from_attributes=True)


def activity_page(
    db: Session, user: User, limit: int, offset: int, project_id: int | None, action: str | None
) -> ActivityPageOut:
    rows, total = activity.events(db, user.id, limit=limit, offset=offset, project_id=project_id, action=action)
    return ActivityPageOut(
        events=[ActivityEventOut.model_validate(row) for row in rows], total=total, offset=offset, limit=limit
    )


def account_export(db: Session, user: User) -> Response:
    name = f"User record - {user.full_name}.xlsx"
    return Response(
        activity.account_workbook(db, user),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"user-record.xlsx\"; filename*=UTF-8''{quote(name)}"},
    )


def _user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/{user_id}/account", response_model=AccountOut)
def user_account(user_id: int, db: Session = Depends(get_db)) -> AccountOut:
    """Everything the platform holds about one user: the account, their
    projects, submittals, BOQ revisions, compliance statements and activity."""
    return account_out(db, _user_or_404(db, user_id))


@router.get("/{user_id}/activity", response_model=ActivityPageOut)
def user_activity(
    user_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project_id: int | None = None,
    action: str | None = None,
    db: Session = Depends(get_db),
) -> ActivityPageOut:
    return activity_page(db, _user_or_404(db, user_id), limit, offset, project_id, action)


@router.get("/{user_id}/account/export.xlsx")
def export_user_account(user_id: int, db: Session = Depends(get_db)) -> Response:
    return account_export(db, _user_or_404(db, user_id))
