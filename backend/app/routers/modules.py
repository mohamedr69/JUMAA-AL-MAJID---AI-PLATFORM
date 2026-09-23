from fastapi import APIRouter, Depends

from app.deps import require_role
from app.models import DESIGN_ROLES, RoleEnum, User

router = APIRouter(prefix="/modules", tags=["modules"])

# The design side of the platform: its manager, its engineers of any
# discipline, and the draftsman.
MODULE_ROLES = (RoleEnum.admin, RoleEnum.design_manager, *DESIGN_ROLES, RoleEnum.draftsman)
# Everyone the design side holds, and the viewer who only reads.
ALL_ROLES = (*MODULE_ROLES, RoleEnum.viewer)


@router.get("/admin")
def admin_module(current_user: User = Depends(require_role(RoleEnum.admin))) -> dict:
    return {"module": "admin", "user": current_user.email}


@router.get("/design")
def design_module(current_user: User = Depends(require_role(*MODULE_ROLES))) -> dict:
    return {"module": "design", "user": current_user.email}


@router.get("/viewer")
def viewer_module(current_user: User = Depends(require_role(*ALL_ROLES))) -> dict:
    return {"module": "viewer", "user": current_user.email}
