r"""The 24 V power calculation, floor by floor.

The same shape as the amplifier calculation, for the other half of a fire
alarm system. Where the amplifier feeds speakers off a 70 V line, this
feeds everything that runs on 24 V direct current:

    a sounder                 an audible on its own
    a flasher                 a strobe on its own
    a speaker with a flasher  the speaker is on the amplifier; the
                              flasher is here
    a sounder base            an audible under a detector

Each draws a current its datasheet states, and a floor's load is the sum
of them. Floors are filled into booster power supplies in the schedule's
order, exactly as they are filled into amplifiers.

Two things differ from the amplifier, and both come from how the devices
are wired.

**The module is a SIGA-CC1, not a CC2A.** A notification circuit is
driven from a single-input control module, one to a floor.

**A sounder base needs no module at all.** It sits under a Signature
detector on the loop and is addressed there; it draws 24 V from the
supply, so it counts towards the power, but it is not on a notification
circuit and takes no CC1. A floor carrying nothing but sounder bases
therefore needs power and no module.
"""

from __future__ import annotations

import dataclasses
import math

from app.services.symbol_taxonomy import device_in

# The supply, the module, and how hard each may be worked. All three are
# overridden by a `DesignRule`; these are what the platform falls back on.
SUPPLY_PART = "BPS10A/230"
SUPPLY_AMPS = 10.0
SUPPLY_FRACTION = 0.8
MODULE_PART = "SIGA-CC1"
MODULE_PER_FLOOR = 1

# What runs on 24 V. A plain speaker does not: it is on the amplifier's
# 70 V line and draws nothing from the supply. A speaker with a flasher
# is here for the flasher alone, which is why it is counted.
POWERED_DEVICES = ("Sounder", "Strobe", "Horn/strobe", "Speaker/strobe")
# The audible under a detector: powered, but on the loop rather than on a
# notification circuit, so it needs no module.
BASE_DEVICES = ("Smoke detector with sounder base",)


def _round(value: float) -> float:
    return round(value + 0.0, 4)


@dataclasses.dataclass
class DeviceColumn:
    """One 24 V appliance the project proposes, as a column."""

    key: str                        # the part number it is ordered as
    description: str
    device: str = ""
    # What the datasheet says it draws, and which of those this project
    # uses. A device with one figure still offers it as a choice of one;
    # `current_ma` is None until somebody chooses, and nothing is assumed.
    currents: list[dict] = dataclasses.field(default_factory=list)
    current_ma: float | None = None
    # A sounder base is powered but needs no module.
    needs_module: bool = True
    lines: list[str] = dataclasses.field(default_factory=list)
    unsettled: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "description": self.description, "device": self.device,
                "currents": self.currents, "current_ma": self.current_ma,
                "needs_module": self.needs_module, "lines": self.lines, "unsettled": self.unsettled}


@dataclasses.dataclass
class FloorRow:
    floor: str
    counts: dict[str, int] = dataclasses.field(default_factory=dict)
    current_ma: float = 0.0
    supply: str | None = None
    modules: int = 0

    @property
    def devices(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict:
        return {"floor": self.floor, "counts": dict(self.counts), "current_ma": _round(self.current_ma),
                "devices": self.devices, "supply": self.supply, "modules": self.modules}


@dataclasses.dataclass
class Supply:
    name: str
    floors: list[str] = dataclasses.field(default_factory=list)
    current_ma: float = 0.0
    over_limit: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "floors": list(self.floors), "current_ma": _round(self.current_ma),
                "over_limit": self.over_limit}


@dataclasses.dataclass
class Result:
    columns: list[DeviceColumn] = dataclasses.field(default_factory=list)
    floors: list[FloorRow] = dataclasses.field(default_factory=list)
    supplies: list[Supply] = dataclasses.field(default_factory=list)
    supply_part: str = SUPPLY_PART
    supply_amps: float = SUPPLY_AMPS
    limit_ma: float = SUPPLY_AMPS * SUPPLY_FRACTION * 1000
    module_part: str = MODULE_PART
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def total_ma(self) -> float:
        return sum(floor.current_ma for floor in self.floors)

    @property
    def total_devices(self) -> int:
        return sum(floor.devices for floor in self.floors)

    @property
    def total_modules(self) -> int:
        return sum(floor.modules for floor in self.floors)

    def as_dict(self) -> dict:
        return {
            "columns": [column.as_dict() for column in self.columns],
            "floors": [floor.as_dict() for floor in self.floors],
            "supplies": [supply.as_dict() for supply in self.supplies],
            "supply_part": self.supply_part,
            "supply_amps": self.supply_amps,
            "limit_ma": _round(self.limit_ma),
            "module_part": self.module_part,
            "total_floors": len(self.floors),
            "total_devices": self.total_devices,
            "total_modules": self.total_modules,
            "total_ma": _round(self.total_ma),
            "total_amps": _round(self.total_ma / 1000),
            "totals_by_column": {
                column.key: sum(floor.counts.get(column.key, 0) for floor in self.floors)
                for column in self.columns
            },
            "warnings": self.warnings,
        }


def device_columns(schedule: dict, currents: dict[str, dict]) -> list[DeviceColumn]:
    """The 24 V appliances a project proposes, one column each."""
    found: dict[str, DeviceColumn] = {}
    for item in schedule.get("items", []):
        device = item.get("device") or device_in(item.get("description"))
        if device not in POWERED_DEVICES and device not in BASE_DEVICES:
            continue
        material = item.get("material") or {}
        settled = (material.get("part_no") or "").strip()
        key = settled or (item.get("catalog_no") or "").strip() or item["description"].strip()
        column = found.get(key)
        if column is None:
            # A pair -- "SIGA-OSD-FCN + SIGA-LPS" -- is looked up by the
            # base, which is the half that draws the current.
            known = currents.get(key.upper(), {})
            if not known and "+" in key:
                for half in (part.strip().upper() for part in key.split("+")):
                    if half in currents:
                        known = currents[half]
                        break
            column = found[key] = DeviceColumn(
                key=key,
                description=(material.get("description") or known.get("description")
                             or item.get("description") or "").strip(),
                device=device or "",
                currents=[dict(entry) for entry in known.get("currents", [])],
                needs_module=device not in BASE_DEVICES,
            )
            if len(column.currents) == 1:
                # One figure on the datasheet is still the engineer's to
                # accept, but there is nothing to choose between.
                column.current_ma = float(column.currents[0]["ma"])
            column.unsettled = not settled
        if item["description"] not in column.lines:
            column.lines.append(item["description"])
    return sorted(found.values(), key=lambda column: column.key)


def calculate(schedule: dict, *, currents: dict[str, dict], chosen: dict[str, float] | None = None,
              supply: dict | None = None, module: dict | None = None) -> Result:
    """The 24 V power schedule a floor-wise BOQ makes."""
    supply = supply or {}
    module = module or {}
    amps = float(supply.get("amps") or SUPPLY_AMPS)
    fraction = float(supply.get("fraction") or SUPPLY_FRACTION)
    result = Result(
        supply_part=str(supply.get("part_no") or SUPPLY_PART),
        supply_amps=amps,
        limit_ma=_round(amps * fraction * 1000),
        module_part=str(module.get("part_no") or MODULE_PART),
    )
    result.columns = device_columns(schedule, currents)
    for column in result.columns:
        picked = (chosen or {}).get(column.key)
        if picked is not None:
            column.current_ma = float(picked)

    if not result.columns:
        result.warnings.append(
            "No device on the floor-wise BOQ runs on 24 V, so there is nothing to draw from a power supply."
        )
        return result

    unknown = [column for column in result.columns if column.current_ma is None]
    if unknown:
        result.warnings.append(
            f"{len(unknown)} device{'s have' if len(unknown) != 1 else ' has'} no current set "
            f"({', '.join(column.key for column in unknown[:4])}"
            f"{', ...' if len(unknown) > 4 else ''}). Choose one from the device's datasheet figures; "
            "until then those devices draw nothing."
        )

    by_line = {line: column.key for column in result.columns for line in column.lines}
    needs_module = {column.key: column.needs_module for column in result.columns}
    draw = {column.key: column.current_ma or 0.0 for column in result.columns}

    for floor in schedule.get("floors", []):
        row = FloorRow(floor=floor)
        for item in schedule.get("items", []):
            key = by_line.get(item.get("description", ""))
            if key is None:
                continue
            count = (item.get("per_floor") or {}).get(floor) or 0
            if count:
                row.counts[key] = row.counts.get(key, 0) + int(count)
        row.current_ma = _round(sum(count * draw[key] for key, count in row.counts.items()))
        # One module a floor, for the notification circuit. A floor whose
        # only 24 V load is sounder bases has no notification circuit and
        # takes none.
        row.modules = MODULE_PER_FLOOR if any(needs_module[key] and count
                                              for key, count in row.counts.items()) else 0
        result.floors.append(row)

    _assign(result)
    return result


def _assign(result: Result) -> None:
    """Fill supplies with whole floors, in the schedule's own order."""
    limit = result.limit_ma
    current: Supply | None = None
    for row in result.floors:
        if not row.current_ma:
            continue
        if row.current_ma > limit:
            supply = Supply(name=f"BPS-{len(result.supplies) + 1}", floors=[row.floor],
                            current_ma=row.current_ma, over_limit=True)
            result.supplies.append(supply)
            row.supply = supply.name
            current = None
            continue
        if current is None or current.current_ma + row.current_ma > limit:
            current = Supply(name=f"BPS-{len(result.supplies) + 1}")
            result.supplies.append(current)
        current.floors.append(row.floor)
        current.current_ma = _round(current.current_ma + row.current_ma)
        row.supply = current.name

    over = [supply for supply in result.supplies if supply.over_limit]
    if over:
        result.warnings.append(
            f"{len(over)} floor{'s draw' if len(over) != 1 else ' draws'} more than one "
            f"{result.supply_part} can give at {limit / 1000:g} A "
            f"({', '.join(supply.floors[0] for supply in over[:3])}"
            f"{', ...' if len(over) > 3 else ''}). Each is shown on a supply of its own, over its limit."
        )


def supplies_for(milliamps: float, limit_ma: float) -> int:
    """How many supplies a load needs, ignoring how it splits by floor."""
    return math.ceil(milliamps / limit_ma) if limit_ma > 0 else 0
