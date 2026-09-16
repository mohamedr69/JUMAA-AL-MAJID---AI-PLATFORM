"""The equipment current table: what each part of a fire alarm system draws.

One row per part, shared by every project: the mechanical parts that draw
nothing (backboxes, chassis, doors, brackets, filler plates, battery
cabinets), the parts built into another module whose draw is already in
that module's figure, and the devices and modules that draw current, with
their standby and alarm figures and where each came from.

It exists so the question is settled once. Before it, a mechanical part
was set to draw no current *automatically* on every project that quoted
it, and every one of those settings waited for an engineer to confirm --
the same "Confirm: no current" for 3-CAB14B on project after project. A
part in this table is known: the battery calculation takes its figure from
here, records it in the catalogue as confirmed, and asks nobody.

The table is filled three ways: seeded with the Edwards parts the platform
owner settled on 2026-09-16; from the catalogue as datasheets are read and
figures are typed in; and from an engineer's confirmation or rejection on
the battery page, which is written here as well so it holds everywhere.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import DesignRule, EquipmentCurrent, User
from app.seed import PART_CURRENT_CATEGORY
from app.services.battery_calculation import part_key

MANUFACTURER = "EDWARDS"
TABLE_SOURCE = "Equipment current table"
SEED_SOURCE = "Edwards EST3 / EST4 parts that draw no current; settled by the platform owner on 2026-09-16"

# kind: "mechanical" | "built_in" | "device" | "unknown"
MECHANICAL = "mechanical"
BUILT_IN = "built_in"
DEVICE = "device"
UNKNOWN = "unknown"

# (part number, description) -- Edwards parts that are metalwork: no load.
NO_LOAD_PARTS: list[tuple[str, str]] = [
    ("3-CAB5B", "Backbox, black. Supports five Local Rail Modules."),
    ("3-CAB7B", "Wallbox with 1 chassis space"),
    ("3-CAB14B", "White Backbox with 14 x LRM spaces, no door"),
    ("3-CAB21B", "White Backbox with 21 x LRM spaces, no door"),
    ("3-CAB28B", "Backbox with 28 x LRM spaces"),
    ("3-CHAS4", "Four Space Chassis with Rails"),
    ("3-CHAS7", "Seven Space Chassis with Rails"),
    ("3-LRMF", "Blank LRM Filler"),
    ("4-BRKT-CB", "Mounting Bracket (chassis)"),
    ("4-BRKT-CS", "Mounting Bracket (chassis)"),
    ("4-CAB8D", "Door Assembly - Bronze outer door and black inner door"),
    ("4-CAB16D", "Door Assembly - Bronze, with 16 user interface spaces"),
    ("4-CAB24D", "Door Assembly - Bronze outer door and black inner door"),
    ("4-CAB24DL", "Door Assembly - Bronze outer door and black inner door (left hand)"),
    ("4-FIL", "Blank Filler Plate"),
    ("BC-1", "Battery cabinet"),
    # An APS cabinet's battery calculation is its amplifiers (SIGA-AA50) and
    # modules (SIGA-CT2), each a line of the BOQ with its datasheet figure;
    # the power supply unit itself is not a line of it (platform owner,
    # 16 September 2026).
    ("APS6A", "6.5 A Auxiliary Power Supply: its load is its amplifiers and modules"),
    ("APS6A/230", "6.5 A Auxiliary Power Supply (220 V): its load is its amplifiers and modules"),
    ("APS10A", "10 A Auxiliary Power Supply: its load is its amplifiers and modules"),
    ("APS10A/230", "10 A Auxiliary Power Supply (220 V): its load is its amplifiers and modules"),
]
# Devices whose figures are read off their datasheets in the library when
# the table is seeded, so a project's first battery page has them at once.
DATASHEET_SEEDS: list[tuple[str, str]] = [
    ("BPS10A", "10 A Booster Power Supply"),
    ("BPS6A", "6.5 A Booster Power Supply"),
    ("SIGA-AA50", "Intelligent Audio Amplifier - 50 W"),
    ("SIGA-AA30", "Intelligent Audio Amplifier - 30 W"),
    ("SIGA-CT2", "Dual Input Module"),
    ("SIGA-CT1", "Single Input Module"),
]
# (part number, description, the module it is built into).
BUILT_IN_PARTS: list[tuple[str, str, str]] = [
    ("4-COMREL", "Common Relay Module", "4-CPU"),
]
# A figure is never typed in from a template: a device's current comes off
# its datasheet in the library (app.services.datasheet_currents), read the
# first time a project quotes it, and joins the table with the datasheet as
# its source. Rows an earlier build seeded from the company's BC template
# are taken out so the datasheet read replaces them.
BC_TEMPLATE = "Company EST4 battery calculation template (EST4/Templates/BC.xlsx)"
RETIRED_SOURCES = (BC_TEMPLATE, "Edwards SIGA-CT2 datasheet: 396 uA standby")

# Spellings the scanned sheets have produced for these parts, and the part
# numbers a sheet uses for what the datasheets call something else. Applied
# before a part is looked up here or in the datasheet library.
ALIASES: dict[str, str] = {
    "3-CABSB": "3-CAB5B",
    "4-CABI6D": "4-CAB16D",
    "4-PPS": "4-PPS/M",
    "SIGA-AAS0": "SIGA-AA50",
    "3-AA50": "SIGA-AA50",
    "3-AA30": "SIGA-AA30",
}


def canonical(part_no: str | None) -> str:
    """The spelling the datasheets use for a BOQ part number."""
    key = _key(part_no)
    return ALIASES.get(key, part_no.strip() if part_no else "")


def _key(part_no: str | None) -> str:
    return part_key(part_no or "")


# --- reading the table ----------------------------------------------------------------


def all_rows(db: Session) -> list[EquipmentCurrent]:
    return db.query(EquipmentCurrent).order_by(EquipmentCurrent.key).all()


def index(db: Session) -> dict[str, EquipmentCurrent]:
    """Every row by its key and by each of its aliases."""
    rows: dict[str, EquipmentCurrent] = {}
    for row in all_rows(db):
        rows[row.key] = row
        for alias in row.aliases or []:
            rows.setdefault(_key(alias), row)
    return rows


def lookup(db: Session, part_no: str | None) -> EquipmentCurrent | None:
    key = _key(canonical(part_no))
    if not key:
        return None
    row = db.query(EquipmentCurrent).filter(EquipmentCurrent.key == key).one_or_none()
    if row is not None:
        return row
    for candidate in all_rows(db):
        if key in {_key(a) for a in candidate.aliases or []}:
            return candidate
    return None


def settled(row: EquipmentCurrent) -> bool:
    """Whether the row answers the question: no load, or a figure. A row
    that says only "draws current, figure unknown" does not."""
    return bool(row.no_load) or (row.standby_ma is not None and row.alarm_ma is not None)


def rule_data(row: EquipmentCurrent, part_no: str, description: str | None) -> tuple[dict, str]:
    """The catalogue entry a settled row becomes for a project's part:
    confirmed already, so the battery page asks no one."""
    data = {
        "part_no": part_no, "description": description or row.description, "auto": True,
        "standby_ma": 0 if row.no_load else float(row.standby_ma),
        "alarm_ma": 0 if row.no_load else float(row.alarm_ma),
        "confirmed_by": TABLE_SOURCE, "equipment_current_id": row.id,
    }
    if row.no_load:
        data["no_load"] = True
        if row.included_in:
            data["included_in"] = row.included_in
    what = ("no current of its own: built into " + row.included_in) if row.included_in else \
        ("no electrical load" if row.no_load else f"{data['standby_ma']:g} mA standby, {data['alarm_ma']:g} mA alarm")
    return data, f"{TABLE_SOURCE}: {what} ({row.source})"[:1000]


# --- writing it ------------------------------------------------------------------------


def upsert(db: Session, *, part_no: str, description: str | None = None, no_load: bool = False,
           standby_ma: float | None = None, alarm_ma: float | None = None, included_in: str | None = None,
           kind: str | None = None, source: str, confirmed_by: str | None = None, user: User | None = None,
           aliases: list[str] | None = None, manufacturer: str = MANUFACTURER) -> EquipmentCurrent:
    key = _key(part_no)
    if not key:
        raise ValueError("The part number has no letters or digits")
    row = db.query(EquipmentCurrent).filter(EquipmentCurrent.key == key).one_or_none()
    if row is None:
        row = EquipmentCurrent(manufacturer=manufacturer, key=key, part_no=part_no.strip(), created_by_id=user.id if user else None)
        db.add(row)
    row.part_no = part_no.strip() or row.part_no
    if description:
        row.description = description[:300]
    row.no_load = bool(no_load)
    row.included_in = included_in
    row.standby_ma = None if no_load else standby_ma
    row.alarm_ma = None if no_load else alarm_ma
    row.kind = kind or (BUILT_IN if included_in else MECHANICAL if no_load else DEVICE if standby_ma is not None else UNKNOWN)
    row.source = source[:1000]
    row.confirmed_by = confirmed_by
    if aliases is not None:
        row.aliases = [a.strip() for a in aliases if a and a.strip()]
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def seed(db: Session) -> int:
    """The Edwards parts the platform starts with, added only where the part
    has no row -- a correction made in the table is never undone by a
    restart -- and the catalogue's datasheet-read figures, so the table
    holds every part that draws current with where its figure came from.
    Returns how many rows were added."""
    existing = {row.key for row in all_rows(db)}
    added = 0
    for part_no, description in NO_LOAD_PARTS:
        if _key(part_no) not in existing:
            db.add(EquipmentCurrent(manufacturer=MANUFACTURER, key=_key(part_no), part_no=part_no, description=description,
                                    no_load=True, kind=MECHANICAL, source=SEED_SOURCE, confirmed_by="platform owner",
                                    aliases=[a for a, target in ALIASES.items() if target == part_no]))
            existing.add(_key(part_no))
            added += 1
    for part_no, description, host in BUILT_IN_PARTS:
        if _key(part_no) not in existing:
            db.add(EquipmentCurrent(manufacturer=MANUFACTURER, key=_key(part_no), part_no=part_no, description=description,
                                    no_load=True, included_in=host, kind=BUILT_IN, source=SEED_SOURCE,
                                    confirmed_by="platform owner"))
            existing.add(_key(part_no))
            added += 1
    for row in all_rows(db):
        if row.source and row.source.startswith(RETIRED_SOURCES):
            db.delete(row)
            existing.discard(row.key)
    db.flush()
    # A spelling learned later for a part already in the table.
    for alias, target in ALIASES.items():
        row = db.query(EquipmentCurrent).filter(EquipmentCurrent.key == _key(target)).one_or_none()
        if row is not None and _key(alias) not in {_key(a) for a in row.aliases or []}:
            row.aliases = [*(row.aliases or []), alias]
    added += seed_from_datasheets(db, existing)
    # What the catalogue already learned from datasheets and engineers.
    rules = (db.query(DesignRule)
             .filter(DesignRule.category == PART_CURRENT_CATEGORY, DesignRule.superseded_at.is_(None)).all())
    for rule in rules:
        data = rule.data or {}
        if rule.source and any(marker in rule.source for marker in RETIRED_SOURCES):
            # A catalogue version the table wrote from the template: retired,
            # so the part is missing a current again and the next fill reads
            # its datasheet.
            rule.superseded_at = utc_now()
            continue
        if rule.key in existing or data.get("rejected_no_load") or data.get("no_load"):
            continue
        if data.get("standby_ma") is None or data.get("alarm_ma") is None:
            continue
        db.add(EquipmentCurrent(manufacturer=MANUFACTURER, key=rule.key, part_no=data.get("part_no") or rule.key,
                                description=(data.get("description") or None), no_load=False, kind=DEVICE,
                                standby_ma=float(data["standby_ma"]), alarm_ma=float(data["alarm_ma"]),
                                source=(rule.source or "catalogue")[:1000],
                                confirmed_by=data.get("confirmed_by") if not data.get("auto") else None))
        existing.add(rule.key)
        added += 1
    db.commit()
    return added


def seed_from_datasheets(db: Session, existing: set[str]) -> int:
    """DATASHEET_SEEDS read off their datasheets in the Edwards library, for
    the parts not in the table yet. Nothing is typed in: a part whose
    datasheet is not in the library, or gives no current, is left out, to
    be read when a project quotes it. Returns how many rows were added."""
    from app.services.datasheet_currents import read_part_current
    from app.services.datasheet_library import get_libraries

    try:
        library = get_libraries().get(MANUFACTURER)
    except Exception:  # noqa: BLE001 -- no library on this machine: nothing to read
        return 0
    if library is None:
        return 0
    added = 0
    for part_no, description in DATASHEET_SEEDS:
        if _key(part_no) in existing:
            continue
        for match in library.find(part_no):
            reading = read_part_current(library.folder / match.path, part_no,
                                        doc_named_for_part=match.matched_on in ("filename", "family"))
            if reading is None:
                continue
            pages = ", ".join(str(p) for p in reading.pages)
            source = f"{match.source.rsplit(', p.', 1)[0]}, p.{pages}: read automatically"
            if reading.notes:
                source += " (" + "; ".join(reading.notes) + ")"
            db.add(EquipmentCurrent(manufacturer=MANUFACTURER, key=_key(part_no), part_no=part_no, description=description,
                                    no_load=False, kind=DEVICE, standby_ma=reading.standby_ma, alarm_ma=reading.alarm_ma,
                                    source=source[:1000], confirmed_by=None))
            existing.add(_key(part_no))
            added += 1
            break
    return added


def as_dict(row: EquipmentCurrent) -> dict:
    return {
        "id": row.id, "manufacturer": row.manufacturer, "part_no": row.part_no, "key": row.key,
        "description": row.description, "kind": row.kind, "no_load": bool(row.no_load),
        "standby_ma": None if row.standby_ma is None else float(row.standby_ma),
        "alarm_ma": None if row.alarm_ma is None else float(row.alarm_ma),
        "included_in": row.included_in, "source": row.source, "confirmed_by": row.confirmed_by,
        "aliases": list(row.aliases or []), "settled": settled(row),
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def touched_at(row: EquipmentCurrent) -> datetime:
    return row.updated_at or row.created_at
