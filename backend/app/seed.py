from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import DesignRule, RoleEnum, User

settings = get_settings()

VE_LOAD_LIMIT_CATEGORY = "ve.limit"
VE_LOAD_LIMIT_KEY = "amplifier_max_load"
BATTERY_SIZING_CATEGORY = "battery.sizing"
BATTERY_SIZING_KEY = "fas_panel"
BATTERY_SELECTION_CATEGORY = "battery.selection"
BATTERY_SELECTION_KEY = "fas_panel"
# Catalogue categories, keyed by part number and entered from datasheets --
# never seeded.
PART_CURRENT_CATEGORY = "part.current"
BATTERY_UNIT_CATEGORY = "battery.unit"

# Rules the platform starts with. Each is a decision someone made, not a
# default picked in code, and `source` says whose -- a rule without one has
# no business deciding pass or fail. Seeded only when the rule has no row at
# all, so a correction made in the database (a new version) is never undone
# by a restart.
INITIAL_DESIGN_RULES = [
    {
        "category": VE_LOAD_LIMIT_CATEGORY,
        "key": VE_LOAD_LIMIT_KEY,
        "data": {"fraction": 0.8},
        "source": (
            "Decided by the platform owner on 2026-09-11: a channel fails when its required "
            "watts exceed 80% of its amplifier's rating (20% spare), e.g. 40 W on a 50 W "
            "amplifier. The engineers' amplifier workbooks apply no limit of their own."
        ),
    },
    {
        "category": BATTERY_SIZING_CATEGORY,
        "key": BATTERY_SIZING_KEY,
        "data": {"standby_hours": 24, "alarm_minutes": 30, "spare_factor": 1.2, "panel_voltage": 24},
        "source": (
            "The engineers' FAS battery workbooks (EP-20779 '6. FAS Battery Calculation.xlsx', "
            "EP-30784 'BC.xlsx'): required Ah = (standby mA x 24 h + alarm mA x 30 min) / 1000 x 1.2 "
            "(20% spare); confirmed by the platform owner on 2026-09-11. Panels run on 24 V DC: the "
            "BOQs quote their batteries as pairs of 12 V blocks (e.g. EP-30784's 2 x 12V65A)."
        ),
    },
    {
        "category": BATTERY_SELECTION_CATEGORY,
        "key": BATTERY_SELECTION_KEY,
        "data": {"brand": "ROCKET"},
        "source": (
            "Decided by the platform owner on 2026-09-11: panel batteries are selected from the ROCKET "
            "range -- the nearest size that covers the requirement -- using the ROCKET datasheets in "
            "the datasheet library."
        ),
    },
]


def seed_design_rules(db: Session) -> None:
    for rule in INITIAL_DESIGN_RULES:
        exists = (
            db.query(DesignRule)
            .filter(DesignRule.category == rule["category"], DesignRule.key == rule["key"])
            .first()
        )
        if not exists:
            db.add(DesignRule(version=1, **rule))
    db.commit()


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
