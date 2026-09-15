from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, verify_password
from app.core.timeutils import utc_now
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.routers.users import account_export, account_out, activity_page
from app.schemas import AccountOut, ActivityPageOut, LoginRequest, UserOut
from app.services import activity

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.email == payload.email.lower()).first()

    generic_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
    )

    if user is None:
        raise generic_error

    now = utc_now()
    if user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked due to repeated failed logins. Try again in {remaining} minute(s).",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    if not verify_password(payload.password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.max_failed_login_attempts:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0
        db.commit()
        raise generic_error

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    db.refresh(user)
    activity.record(db, user, "auth.login", "Signed in")
    db.refresh(user)

    token = create_access_token(subject=str(user.id), role=user.role.value)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )
    return user


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    # Logging out works without a valid session; it is only recorded with one.
    token = request.cookies.get(settings.cookie_name)
    try:
        user = db.get(User, int(decode_access_token(token)["sub"])) if token else None
    except Exception:  # noqa: BLE001 -- an expired or bad token still logs out
        user = None
    if user is not None:
        activity.record(db, user, "auth.logout", "Signed out")
    response.delete_cookie(settings.cookie_name, path="/")
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


# A user's own record: the same as an admin sees under /users/{id}/account.


@router.get("/me/account", response_model=AccountOut)
def my_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccountOut:
    return account_out(db, current_user)


@router.get("/me/activity", response_model=ActivityPageOut)
def my_activity(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project_id: int | None = None,
    action: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivityPageOut:
    return activity_page(db, current_user, limit, offset, project_id, action)


@router.get("/me/account/export.xlsx")
def export_my_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    return account_export(db, current_user)
