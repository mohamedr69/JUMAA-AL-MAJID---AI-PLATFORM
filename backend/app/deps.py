from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.database import get_db
from app.models import RoleEnum, User

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

    # These divisions currently have only their project area and self-service account.
    # Enforce this on the server too, including design routes that allow any user.
    path = request.url.path.rstrip("/")
    area = {RoleEnum.estimation_engineer: "estimation", RoleEnum.fire_fighting_engineer: "fire-fighting", RoleEnum.elv_engineer: "elv"}.get(user.role)
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
