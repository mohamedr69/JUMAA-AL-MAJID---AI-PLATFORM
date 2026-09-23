from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.database import get_db
from app.models import DISCIPLINE_AREAS, ESTIMATION_ROLES, RoleEnum, User, discipline_of

settings = get_settings()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # An estimation engineer has their project area and their own account,
    # and nothing else yet; enforced here rather than route by route, so a
    # design route that allows any user does not let them in by default.
    #
    # Design engineers are **not** confined: the design workflow -- the
    # BOQ, the calculations, the compliance statements, the submittals --
    # is the platform, and it is what they are here to do.
    path = request.url.path.rstrip("/")
    area = DISCIPLINE_AREAS.get(discipline_of(user.role)) if user.role in ESTIMATION_ROLES else None
    if area and not (
        path == "/auth/me" or path.startswith("/auth/me/")
        or path == f"/{area}/projects" or path.startswith(f"/{area}/projects/")
    ):
        raise HTTPException(status_code=403, detail="This module is not available to your division")
    return user


def require_role(*allowed_roles: RoleEnum):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this module",
            )
        return current_user

    return dependency
