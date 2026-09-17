"""The fire-rated cables of a full-package project.

The engineer names the brand and the size of each system's cable; the
company's standards say what each should be, and a choice against them
is not refused but warned about, in the words the engineers use:

    fire alarm loop      standard 2C x 1.5 mm2; 2.5 chosen -> "1.5 mm2 shall be used"
    voice evacuation     1.5 chosen -> subject to the voltage drop calculation
    24 VDC power         1.5 chosen -> subject to the voltage drop calculation
    fire telephone       standard 2C x 1.5 mm2; 2.5 chosen -> "1.5 mm2 shall be used"

The cables are the FRC system's proposed materials: one line per cable
on the tab and on the Schedule of Material.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy.orm import Session

from app.models import Project, ProjectFrcCables

BRANDS = ("FIREGUARD", "SOLARTI", "FRONTIER", "TIANJIE")
SIZES = ("2Cx1.5mm", "2Cx2.5mm")
# The emergency light monitoring cable of a monitored self-contained
# system: one brand for now, one size -- both taken as given, nothing to choose.
MONITORING_BRANDS = ("RAMCRO",)
MONITORING_SIZE = "2Cx1.5mm"
MONITORING_NAME = "Emergency light monitoring cable"
SIZE_LABELS = {"2Cx1.5mm": "2C x 1.5 mm2", "2Cx2.5mm": "2C x 2.5 mm2"}

# (field, the cable, the standard size, the size that raises the warning, the warning)
CABLES: tuple[tuple[str, str, str, str, str], ...] = (
    ("fire_alarm_loop", "Fire alarm loop cable", "2Cx1.5mm", "2Cx2.5mm",
     "The standard fire alarm loop cable is 2C x 1.5 mm2: 1.5 mm2 shall be used."),
    ("voice_evacuation", "Voice evacuation cable", "2Cx2.5mm", "2Cx1.5mm",
     "Voice evacuation on 2C x 1.5 mm2 is subject to the voltage drop calculation."),
    ("power_24vdc", "24 VDC power cable", "2Cx2.5mm", "2Cx1.5mm",
     "A 24 VDC power cable of 2C x 1.5 mm2 is subject to the voltage drop calculation."),
    ("fire_telephone", "Fire telephone power cable", "2Cx1.5mm", "2Cx2.5mm",
     "The standard fire telephone cable is 2C x 1.5 mm2: 1.5 mm2 shall be used."),
)


@dataclasses.dataclass
class CableLine:
    field: str
    name: str
    size: str | None
    standard: str
    warning: str | None
    catalog_no: str
    description: str
    manufacturer: str | None
    # The systems the line covers (the fire alarm's cables of one size are one line).
    systems: list[str] = dataclasses.field(default_factory=list)


def get(db: Session, project: Project) -> ProjectFrcCables | None:
    return db.query(ProjectFrcCables).filter(ProjectFrcCables.project_id == project.id).first()


def monitoring_applies(project: Project) -> bool:
    """Whether the project has a monitored self-contained emergency light
    system and the fire-rated cables to run it on (a full-package project)."""
    from app.services import system_rules

    return (system_rules.is_full_package(project)
            and system_rules.SELF_CONTAINED in system_rules.emergency_lighting_kinds(getattr(project, "systems", None) or ()))


def monitoring_for(row: ProjectFrcCables | None, project: Project) -> tuple[str, str] | None:
    """(brand, size) of the monitoring cable in force: what was stored, else
    the only brand and the only size; None when the project has no
    monitored self-contained system."""
    if not monitoring_applies(project):
        return None
    brand = (getattr(row, "monitoring_brand", None) if row is not None else None) or MONITORING_BRANDS[0]
    size = (getattr(row, "monitoring_size", None) if row is not None else None) or MONITORING_SIZE
    return brand, size


def warnings_for(row: ProjectFrcCables | None) -> dict[str, str | None]:
    """The warning each cable's chosen size raises, by field; None when
    the choice is the standard one or none was made."""
    out: dict[str, str | None] = {}
    for field, _name, _standard, warned, warning in CABLES:
        chosen = getattr(row, field, None) if row is not None else None
        out[field] = warning if chosen == warned else None
    return out


# What each cable is called on the schedule, when lines of one size are joined.
_SYSTEM_WORDS = {
    "fire_alarm_loop": "fire alarm",
    "voice_evacuation": "voice evacuation",
    "power_24vdc": "24 VDC power",
    "fire_telephone": "fire telephone",
}


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " & " + words[-1]


def lines(db: Session, project: Project) -> list[CableLine]:
    """The cables as materials, as the schedule lists them: the fire alarm
    system's cables of one size are one line ("2Cx1.5mm -- fire alarm,
    voice evacuation & fire telephone"), two sizes two lines, each naming
    the systems it serves; the monitoring cable its own line. The part
    number is the size itself, never prefixed."""
    row = get(db, project)
    warnings = warnings_for(row)
    found: list[CableLine] = []
    if row is not None and row.brand:
        by_size: dict[str, list[str]] = {}
        warned: dict[str, list[str]] = {}
        for field, _name, _standard, _w, _t in CABLES:
            size = getattr(row, field, None)
            if not size:
                continue
            by_size.setdefault(size, []).append(field)
            if warnings[field]:
                warned.setdefault(size, []).append(warnings[field])
        for size in SIZES:
            fields = by_size.get(size)
            if not fields:
                continue
            label = SIZE_LABELS.get(size, size)
            systems = [_SYSTEM_WORDS[f] for f in fields]
            found.append(CableLine(
                field="+".join(fields), name=f"Fire alarm system cable ({_join(systems)})", size=size, standard=size,
                warning=" ".join(dict.fromkeys(warned.get(size, []))) or None,
                catalog_no=size, description=f"Fire rated cable, {label}, for the {_join(systems)} system{'s' if len(systems) > 1 else ''}",
                manufacturer=row.brand, systems=systems))
    monitoring = monitoring_for(row, project)
    if monitoring is not None:
        brand, size = monitoring
        label = SIZE_LABELS.get(size, size)
        found.append(CableLine(field="monitoring", name=MONITORING_NAME, size=size, standard=MONITORING_SIZE, warning=None,
                               catalog_no=size, description=f"Emergency light monitoring cable, fire-rated, {label}",
                               manufacturer=brand, systems=["emergency light monitoring"]))
    return found


def save(db: Session, project: Project, user_id: int | None, *, brand: str | None, sizes: dict[str, str | None],
         monitoring_brand: str | None = None, monitoring_size: str | None = None) -> ProjectFrcCables:
    if brand and brand.upper() not in BRANDS:
        raise ValueError(f"The brand must be one of {', '.join(BRANDS)}")
    if monitoring_brand and monitoring_brand.upper() not in MONITORING_BRANDS:
        raise ValueError(f"The monitoring cable brand must be {', '.join(MONITORING_BRANDS)}")
    if monitoring_size and monitoring_size != MONITORING_SIZE:
        raise ValueError(f"The monitoring cable comes in one size, {MONITORING_SIZE}")
    for field, size in sizes.items():
        if field not in {c[0] for c in CABLES}:
            raise ValueError(f"Unknown cable {field}")
        if size and size not in SIZES:
            raise ValueError(f"The size must be one of {', '.join(SIZES)}")
    row = get(db, project)
    if row is None:
        row = ProjectFrcCables(project_id=project.id)
        db.add(row)
    row.brand = brand.upper() if brand else None
    for field, size in sizes.items():
        setattr(row, field, size or None)
    row.monitoring_brand = monitoring_brand.upper() if monitoring_brand else row.monitoring_brand
    row.monitoring_size = monitoring_size or row.monitoring_size
    row.updated_by_id = user_id
    db.commit()
    db.refresh(row)
    return row
