from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import RoleEnum, User

settings = get_settings()


def seed_default_admin(db: Session) -> None:
    existing = db.query(User).filter(User.email == settings.default_admin_email).first()
    if existing:
        return

    admin = User(
        email=settings.default_admin_email,
        full_name="Platform Administrator",
        hashed_password=hash_password(settings.default_admin_password),
        role=RoleEnum.admin,
    )
    db.add(admin)
    db.commit()
