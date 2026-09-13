"""Standby battery sizing for fire alarm panels, from the project's BOQ.

The method is the one in the engineers' own battery workbooks (EP-20779,
EP-30784, EP-29076 all agree):

    Is = total standby mA x 24 h
    Ia = total alarm mA x 30 min
    required Ah = (Is + Ia) / 1000 x 1.2          (20% spare)

with the durations and the factor held in a DesignRule, not here.

Each BOQ group headed as a panel is one panel type; its heading line (the
line with no part number, "... Includes:") gives how many identical panels
the group quotes, and the lines under it are per panel -- a panel cannot
share a chassis or a backbox, and the sub-panel groups quote one of each.
So every figure here is per panel.

A part's current comes only from the catalogue (DesignRule "part.current",
entered from datasheets). Until every part in a panel has one, the load is a
partial sum -- a lower bound. That can prove a quoted battery too small
("already needs more than it has") but never big enough, so a panel with a
missing part is never reported as within capacity.

Unlike a VE design, nothing here is stored: it is a check of the BOQ as it
stands against the catalogue as it stands, recomputed on every read, and a
check that froze while its inputs moved on would be worse than none. Which
rule versions produced each figure is returned with it.
"""

import math
import re
from dataclasses import dataclass

from app.schemas_design import (
    BatteryLineOut,
    BatteryPanelOut,
    BatterySetOut,
    BoqGroupOut,
)

# A group is a panel when its heading says so ...
PANEL_HEADING_RE = re.compile(r"panel|facp|\bfacu\b|control\s*unit|\bcie\b", re.IGNORECASE)
# ... unless it is an amplifier or booster power supply cabinet, which the
# platform does not size (they carry their own batteries, sized separately).
APS_BPS_HEADING_RE = re.compile(r"amplifier|booster|\baps\b|\bbps\b|power\s*supply", re.IGNORECASE)

# A battery line, when its part is not in the catalogue: the BOQ's own words,
# "Battery, 12 V @ 65 AH", "10Ah Sealed Lead Acid Battery - 12 Vdc".
_BATTERY_WORD_RE = re.compile(r"\bbatter(y|ies)\b", re.IGNORECASE)
_NOT_A_BATTERY_RE = re.compile(r"cabinet|enclosure|box|bracket|charger", re.IGNORECASE)
_AH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*a\.?\s*h\b", re.IGNORECASE)
_VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*v(?:dc|de)?\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")

# A part that holds modules up rather than drawing current: when its
# datasheet gives no current, its BOQ description decides that it has none.
MECHANICAL_RE = re.compile(
    r"chassis|filler|blank\s+plate|\bplate\b|back\s*box|wall\s*box|cabinet|enclosure|\bdoor\b|bracket|trim\s+ring",
    re.IGNORECASE,
)

# Parts that draw no current of their own because they are built into another
# module, so whatever they draw is already inside that module's figure.
# Counting them again would double it; leaving them missing is worse still --
# their own datasheet will never give a current, so the panel stays a lower
# bound for ever and no battery is ever proposed for it.
#
# Keyed by part_key(). The value is the module the part belongs to, which is
# what the recorded source says.
INCLUDED_IN_MODULE: dict[str, str] = {
    # The common relay is on the EST4 CPU board and has no separate datasheet
    # entry (platform owner, 13 September 2026).
    "4-COMREL": "4-CPU",
}


def included_in_module(part_no: str | None) -> str | None:
    """The module a part is built into, if it is one of them."""
    return INCLUDED_IN_MODULE.get(part_key(part_no or ""))


_EPSILON = 1e-9


def part_key(part_no: str) -> str:
    """How a part number is matched to the catalogue: "4-LCDLE ", "BC-1 ,"
    and "bc-1" are the same part."""
    key = re.sub(r"[^A-Z0-9/+.\-]", "", part_no.upper())
    return key.strip("-./+")


def _quantity(text: str | None) -> float | None:
    match = _NUMBER_RE.match(text or "")
    return float(match.group(1)) if match else None


@dataclass
class Sizing:
    standby_hours: float
    alarm_minutes: float
    spare_factor: float
    panel_voltage: float


@dataclass
class PartCurrent:
    standby_ma: float
    alarm_ma: float
    rule_id: int | None = None
    rule_version: int | None = None
    source: str | None = None
    # {"library", "path", "pages"} when it was read off a datasheet.
    datasheet: dict | None = None


def _datasheet_fields(datasheet: dict | None) -> dict:
    if not datasheet:
        return {}
    pages = datasheet.get("pages") or []
    return {
        "datasheet_library": datasheet.get("library"),
        "datasheet_path": datasheet.get("path"),
        "datasheet_page": pages[0] if pages else None,
    }


@dataclass
class BatteryUnit:
    part_no: str
    capacity_ah: float
    voltage: float
    brand: str | None = None
    # {"library", "path"} when it comes from a datasheet in the library.
    datasheet: dict | None = None


@dataclass
class BoqLine:
    system_code: str | None
    group_heading: str | None
    catalog_no: str | None
    description: str
    quantity: str | None
    manufacturer: str | None = None


def classify_group(heading: str | None) -> str:
    if not heading:
        return "ungrouped"
    if APS_BPS_HEADING_RE.search(heading):
        return "skipped_aps_bps"
    if PANEL_HEADING_RE.search(heading):
        return "panel"
    return "not_a_panel"


def group_lines(lines: list[BoqLine]) -> list[tuple[str | None, str | None, list[BoqLine]]]:
    """(system, heading, lines) per BOQ group, in BOQ order."""
    groups: dict[tuple[str | None, str | None], list[BoqLine]] = {}
    for line in lines:
        groups.setdefault((line.system_code, line.group_heading), []).append(line)
    return [(system, heading, members) for (system, heading), members in groups.items()]


def battery_from_text(description: str) -> tuple[float, float] | None:
    """(capacity Ah, voltage) a battery line's description states."""
    if not _BATTERY_WORD_RE.search(description) or _NOT_A_BATTERY_RE.search(description):
        return None
    ah, volts = _AH_RE.search(description), _VOLT_RE.search(description)
    if not ah or not volts:
        return None
    return float(ah.group(1)), float(volts.group(1))


def _strings(units: float, voltage: float, panel_voltage: float) -> float:
    return units * voltage / panel_voltage


def select_batteries(required_ah: float, units: list[BatteryUnit], panel_voltage: float) -> list[BatterySetOut] | None:
    """The engineers' selection: the smallest battery that covers the
    requirement on its own; failing that, as many of the largest as fit
    under it plus the smallest that covers what remains (80.2 Ah from
    26 / 42 / 65 -> 65 + 26). Strings of panel voltage, in parallel.

    None when there is nothing to choose from."""
    # One entry per capacity, of batteries that make up the panel voltage.
    by_capacity: dict[float, BatteryUnit] = {}
    for unit in sorted(units, key=lambda u: u.part_no):
        per_string = panel_voltage / unit.voltage
        if abs(per_string - round(per_string)) > _EPSILON:
            continue
        by_capacity.setdefault(unit.capacity_ah, unit)
    if not by_capacity:
        return None
    sizes = sorted(by_capacity)

    def string(unit: BatteryUnit, count: int) -> BatterySetOut:
        per_string = round(panel_voltage / unit.voltage)
        return BatterySetOut(
            part_no=unit.part_no, capacity_ah=unit.capacity_ah, voltage=unit.voltage,
            units=count * per_string, strings=count, brand=unit.brand,
            datasheet_library=(unit.datasheet or {}).get("library"),
            datasheet_path=(unit.datasheet or {}).get("path"),
        )

    single = next((s for s in sizes if s >= required_ah - _EPSILON), None)
    if single is not None:
        return [string(by_capacity[single], 1)]

    largest = sizes[-1]
    count = math.floor((required_ah + _EPSILON) / largest)
    remainder = required_ah - count * largest
    chosen = [string(by_capacity[largest], count)]
    if remainder > _EPSILON:
        top_up = next(s for s in sizes if s >= remainder - _EPSILON)
        if top_up == largest:
            chosen[0] = string(by_capacity[largest], count + 1)
        else:
            chosen.append(string(by_capacity[top_up], 1))
    return chosen


def calculate_panel(
    heading: str,
    system_code: str | None,
    lines: list[BoqLine],
    sizing: Sizing,
    currents: dict[str, PartCurrent],
    batteries: dict[str, BatteryUnit],
    extras: list | None = None,
) -> BatteryPanelOut:
    """`extras`: loads the engineer added to this panel (ExtraComponent)."""
    notes: list[str] = []
    count = 1
    out_lines: list[BatteryLineOut] = []
    quoted: list[BatterySetOut] = []
    missing: list[str] = []
    standby = alarm = 0.0
    unreadable = False

    header_seen = False
    for line in lines:
        quantity = _quantity(line.quantity)
        if not line.catalog_no or not part_key(line.catalog_no):
            # The heading line: how many of this panel the group quotes.
            if not header_seen and quantity is not None:
                header_seen = True
                count = max(1, round(quantity))
            out_lines.append(BatteryLineOut(part_no=None, description=line.description, quantity=quantity, kind="no_part"))
            continue

        key = part_key(line.catalog_no)
        unit = batteries.get(key)
        stated = None if unit else battery_from_text(line.description)
        if unit or stated:
            capacity, voltage = (unit.capacity_ah, unit.voltage) if unit else stated  # type: ignore[misc]
            units = quantity or 0
            strings = _strings(units, voltage, sizing.panel_voltage)
            if abs(strings - round(strings)) > _EPSILON:
                notes.append(
                    f"{line.catalog_no}: {units:g} x {voltage:g} V does not make whole {sizing.panel_voltage:g} V strings."
                )
            if not unit:
                notes.append(
                    f"{line.catalog_no} is not in the battery catalogue; its {capacity:g} Ah is as the BOQ describes it."
                )
            quoted.append(
                BatterySetOut(part_no=line.catalog_no.strip(), capacity_ah=capacity, voltage=voltage, units=units, strings=strings)
            )
            out_lines.append(BatteryLineOut(part_no=line.catalog_no.strip(), description=line.description, quantity=quantity, kind="battery"))
            continue

        current = currents.get(key)
        if quantity is None:
            # Counting it as nothing would understate the load without a sign.
            unreadable = True
            notes.append(
                f"{line.catalog_no}: quantity '{line.quantity or ''}' is not a number, so its load is not counted "
                "and the total is only a lower bound. Correct it in the BOQ."
            )
        if current is None:
            if key not in missing:
                missing.append(key)
            out_lines.append(
                BatteryLineOut(
                    part_no=line.catalog_no.strip(), description=line.description, quantity=quantity,
                    manufacturer=line.manufacturer, kind="load", missing_current=True,
                )
            )
            continue
        total_standby = current.standby_ma * (quantity or 0)
        total_alarm = current.alarm_ma * (quantity or 0)
        standby += total_standby
        alarm += total_alarm
        out_lines.append(
            BatteryLineOut(
                part_no=line.catalog_no.strip(), description=line.description, quantity=quantity,
                manufacturer=line.manufacturer, kind="load", standby_ma=current.standby_ma, alarm_ma=current.alarm_ma,
                total_standby_ma=total_standby, total_alarm_ma=total_alarm,
                current_rule_id=current.rule_id, current_rule_version=current.rule_version,
                current_source=current.source,
                **_datasheet_fields(current.datasheet),
            )
        )

    for index, extra in enumerate(extras or []):
        total_standby = extra.standby_ma * extra.quantity
        total_alarm = extra.alarm_ma * extra.quantity
        standby += total_standby
        alarm += total_alarm
        out_lines.append(
            BatteryLineOut(
                part_no=extra.part_no, description=extra.description, quantity=extra.quantity, kind="load",
                standby_ma=extra.standby_ma, alarm_ma=extra.alarm_ma,
                total_standby_ma=total_standby, total_alarm_ma=total_alarm,
                current_source=extra.source, extra_index=index,
            )
        )

    standby_mah = standby * sizing.standby_hours
    alarm_mah = alarm * sizing.alarm_minutes / 60
    total_ah = (standby_mah + alarm_mah) / 1000
    required_ah = total_ah * sizing.spare_factor
    lower_bound = bool(missing) or unreadable
    # Every panel draws current. A group that adds up to nothing has not had
    # its modules itemized (a lump-sum line, rows lost to OCR) -- not a panel
    # with no load, and a quoted battery must not pass against it.
    no_load = standby <= _EPSILON and alarm <= _EPSILON
    if no_load and not lower_bound:
        notes.append(
            "Nothing in this group draws any current, so its modules are probably not itemized in the BOQ; "
            "the panel cannot be sized from it."
        )

    quoted_ah = sum(q.strings * q.capacity_ah for q in quoted) if quoted else None
    # The BOQ's battery is checked against the requirement; a partial load
    # already over it proves it short too.
    quoted_short = quoted_ah is not None and required_ah > quoted_ah + _EPSILON

    # The panel's battery is selected from the catalogue (the ROCKET range),
    # never on a load that is not fully known.
    selected = (
        None if lower_bound or no_load else select_batteries(required_ah, list(batteries.values()), sizing.panel_voltage)
    )
    if lower_bound or no_load:
        status = "incomplete"
    elif selected is None:
        status = "no_selection"
    else:
        status = "ok"
    return BatteryPanelOut(
        heading=heading,
        system_code=system_code,
        count=count,
        lines=out_lines,
        standby_ma=round(standby, 4),
        alarm_ma=round(alarm, 4),
        standby_mah=round(standby_mah, 4),
        alarm_mah=round(alarm_mah, 4),
        total_ah=round(total_ah, 4),
        required_ah=round(required_ah, 4),
        lower_bound=lower_bound,
        missing_parts=missing,
        quoted=quoted,
        quoted_ah=None if quoted_ah is None else round(quoted_ah, 4),
        quoted_short=quoted_short,
        status=status,
        selected=selected,
        selected_ah=None if selected is None else round(sum(p.strings * p.capacity_ah for p in selected), 4),
        notes=notes,
    )


def calculate_boq(
    lines: list[BoqLine],
    sizing: Sizing,
    currents: dict[str, PartCurrent],
    batteries: dict[str, BatteryUnit],
) -> tuple[list[BatteryPanelOut], list[BoqGroupOut]]:
    panels: list[BatteryPanelOut] = []
    groups: list[BoqGroupOut] = []
    for system, heading, members in group_lines(lines):
        treatment = classify_group(heading)
        groups.append(BoqGroupOut(heading=heading, system_code=system, lines=len(members), treatment=treatment))
        if treatment == "panel":
            panels.append(calculate_panel(heading, system, members, sizing, currents, batteries))  # type: ignore[arg-type]
    return panels, groups
