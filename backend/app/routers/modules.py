from fastapi import APIRouter, Depends

from app.deps import require_role
from app.models import RoleEnum, User

router = APIRouter(prefix="/modules", tags=["modules"])

DESIGN_ROLES = (RoleEnum.admin, RoleEnum.design_manager, RoleEnum.design_engineer, RoleEnum.draftsman)
ALL_ROLES = (*DESIGN_ROLES, RoleEnum.viewer)


@router.get("/admin")
def admin_module(current_user: User = Depends(require_role(RoleEnum.admin))) -> dict:
    return {"module": "admin", "user": current_user.email}


@router.get("/design")
def design_module(current_user: User = Depends(require_role(*DESIGN_ROLES))) -> dict:
    return {"module": "design", "user": current_user.email}


@router.get("/viewer")
def viewer_module(current_user: User = Depends(require_role(*ALL_ROLES))) -> dict:
    return {"module": "viewer", "user": current_user.email}
