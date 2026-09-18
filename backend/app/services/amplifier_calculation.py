r"""Voice evacuation amplifier loading, floor by floor.

What a job needs is worked out from what it installs. The floor-wise BOQ
already says how many speakers sit on each floor
(`app.services.floor_schedule`) and which part each line is ordered as, so
the amplifier schedule is arithmetic over that rather than a second thing
to keep up to date:

    a speaker's load   = the tapping it is set to, off its datasheet
    a floor's load     = sum over speaker types of count x tapping
    an amplifier       = as many whole floors as fit under its limit
    a cabinet          = two amplifiers

Three things are worth stating plainly, because each is a decision rather
than arithmetic.

**A floor is never split between amplifiers.** An amplifier feeds whole
floors: the floors are taken in the order the schedule sets them out and
added to the current amplifier until the next one would take it over its
limit, at which point a new amplifier starts. That is what the engineer
does by hand, and it is why the answer depends on the order.

**The limit is not the rating.** A 50 W amplifier is loaded to a fraction
of its rating -- the platform's own design decision, kept as a
`DesignRule` (`ve.limit/amplifier_max_load`, 0.8) rather than written
here, so it can be corrected without a release. At 0.8 a SIGA-AA50 takes
40 W.

**Only speakers count.** A sounder, a horn or a flasher is on a
notification circuit, not on the amplifier, so it is left out however
much the schedule counts of it.
"""

from __future__ import annotations

import dataclasses
import math

from app.services.symbol_taxonomy import SPEAKER, device_in, family_of

# The amplifier the company builds with, and what a cabinet holds.
AMPLIFIER_PART = "SIGA-AA50"
AMPLIFIER_WATTS = 50.0
AMPLIFIERS_PER_CABINET = 2
CABINET_PART = "APS"

# What the platform shows as the job's load beside the plain sum: the
# spare capacity a voice evacuation design is sized with.
SPARE_FRACTION = 0.25

# The devices that load an amplifier. A sounder and a flasher are on a
# notification circuit and are not among them, however the schedule counts
# them.
SPEAKER_DEVICES = ("Speaker", "Speaker/strobe")

# The audio riser module a floor is fed through, where the design rule
# says nothing. One module a floor, carrying at most this much speaker
# load -- a floor with more than that needs a second, which is worth
# knowing before the riser is drawn rather than after.
MODULE_PART = "SIGA-CC2A"
MODULE_MAX_WATTS = 35.0


def _watts(value: float) -> float:
    return round(value + 0.0, 4)


@dataclasses.dataclass
class SpeakerColumn:
    """One speaker the project proposes, as a column of the schedule."""

    key: str                       # the part number it is ordered as
    description: str
    # The tappings the part can be set to, off its datasheet, and the one
    # this project uses. `tap` is None where the platform has no datasheet
    # for the part -- the engineer picks one and nothing is assumed.
    taps: list[float] = dataclasses.field(default_factory=list)
    tap: float | None = None
    # The schedule lines this column came from, for a reader following a
    # number back.
    lines: list[str] = dataclasses.field(default_factory=list)
    # True where the line has not been settled as a part on the Proposed
    # Materials tab, so the column is named after the schedule's wording.
    unsettled: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "description": self.description, "taps": self.taps,
                "tap": self.tap, "lines": self.lines, "unsettled": self.unsettled}


@dataclasses.dataclass
class FloorRow:
    floor: str
    counts: dict[str, int] = dataclasses.field(default_factory=dict)
    watts: float = 0.0
    # The amplifier this floor's speakers are fed from.
    amplifier: str | None = None
    # How many audio riser modules the floor needs: one, unless its load
    # is more than a single module may carry. Nought where the floor has
    # no speakers -- there is nothing to feed.
    modules: int = 0

    @property
    def speakers(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict:
        return {"floor": self.floor, "counts": dict(self.counts), "watts": _watts(self.watts),
                "speakers": self.speakers, "amplifier": self.amplifier, "modules": self.modules}


@dataclasses.dataclass
class Amplifier:
    name: str
    floors: list[str] = dataclasses.field(default_factory=list)
    watts: float = 0.0
    cabinet: str | None = None
    # A floor whose own load is over the limit cannot be fed by one
    # amplifier at all; it is reported rather than quietly split.
    over_limit: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "floors": list(self.floors), "watts": _watts(self.watts),
                "cabinet": self.cabinet, "over_limit": self.over_limit}


@dataclasses.dataclass
class Cabinet:
    name: str
    amplifiers: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "amplifiers": list(self.amplifiers)}


@dataclasses.dataclass
class Result:
    columns: list[SpeakerColumn] = dataclasses.field(default_factory=list)
    floors: list[FloorRow] = dataclasses.field(default_factory=list)
    amplifiers: list[Amplifier] = dataclasses.field(default_factory=list)
    cabinets: list[Cabinet] = dataclasses.field(default_factory=list)
    limit_watts: float = 0.0
    module_part: str = MODULE_PART
    module_max_watts: float = MODULE_MAX_WATTS
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def total_watts(self) -> float:
        return sum(floor.watts for floor in self.floors)

    @property
    def total_speakers(self) -> int:
        return sum(floor.speakers for floor in self.floors)

    @property
    def total_modules(self) -> int:
        return sum(floor.modules for floor in self.floors)

    def as_dict(self) -> dict:
        return {
            "columns": [column.as_dict() for column in self.columns],
            "floors": [floor.as_dict() for floor in self.floors],
            "amplifiers": [amplifier.as_dict() for amplifier in self.amplifiers],
            "cabinets": [cabinet.as_dict() for cabinet in self.cabinets],
            "amplifier_part": AMPLIFIER_PART,
            "amplifier_watts": AMPLIFIER_WATTS,
            "limit_watts": _watts(self.limit_watts),
            "amplifiers_per_cabinet": AMPLIFIERS_PER_CABINET,
            "module_part": self.module_part,
            "module_max_watts": _watts(self.module_max_watts),
            "total_modules": self.total_modules,
            "total_floors": len(self.floors),
            "total_speakers": self.total_speakers,
            "total_watts": _watts(self.total_watts),
            # The plain sum with the design's spare capacity on top, which
            # is what a voice evacuation schedule is sized against. The
            # amplifiers are assigned on the plain sum against a limit that
            # already carries its own headroom, so the two are shown apart
            # rather than multiplied together.
            "spare_fraction": SPARE_FRACTION,
            "total_watts_with_spare": _watts(self.total_watts * (1 + SPARE_FRACTION)),
            "totals_by_column": {
                column.key: sum(floor.counts.get(column.key, 0) for floor in self.floors)
                for column in self.columns
            },
            "warnings": self.warnings,
        }


def speaker_columns(schedule: dict, taps: dict[str, dict]) -> list[SpeakerColumn]:
    """The speakers a project proposes, one column each.

    A column is named by the part the line is ordered as. Where the
    engineer has not settled that on the Proposed Materials tab the
    schedule's own wording stands in, and the column says so: an unsettled
    line is a question, not an unknown speaker.
    """
    found: dict[str, SpeakerColumn] = {}
    for item in schedule.get("items", []):
        device = item.get("device") or device_in(item.get("description"))
        if device not in SPEAKER_DEVICES or family_of(device) is not SPEAKER:
            continue
        material = item.get("material") or {}
        settled = (material.get("part_no") or "").strip()
        key = settled or (item.get("catalog_no") or "").strip() or item["description"].strip()
        column = found.get(key)
        if column is None:
            known = taps.get(key.upper(), {})
            column = found[key] = SpeakerColumn(
                key=key,
                description=(material.get("description") or known.get("description")
                             or item.get("description") or "").strip(),
                taps=[float(tap) for tap in known.get("taps", [])],
                tap=float(known["default_tap"]) if known.get("default_tap") is not None else None,
                unsettled=not settled,
            )
        if item["description"] not in column.lines:
            column.lines.append(item["description"])
    return sorted(found.values(), key=lambda column: column.key)


def calculate(schedule: dict, *, taps: dict[str, dict], chosen: dict[str, float] | None = None,
              fraction: float = 0.8, module: dict | None = None) -> Result:
    """The amplifier schedule a floor-wise BOQ makes.

    `taps` is the speaker database (`DesignRule` "ve.speaker") and
    `chosen` the tapping this project sets each speaker to.

    There is no quantity of its own here. How many speakers a floor has is
    the floor-wise BOQ's answer and nowhere else's, so changing a count on
    this page changes the BOQ -- the two tabs are two views of one number,
    and a second copy kept here would be a way for them to disagree.
    """
    module = module or {}
    result = Result(
        limit_watts=_watts(AMPLIFIER_WATTS * fraction),
        module_part=str(module.get("part_no") or MODULE_PART),
        module_max_watts=float(module.get("max_watts") or MODULE_MAX_WATTS),
    )
    result.columns = speaker_columns(schedule, taps)
    for column in result.columns:
        picked = (chosen or {}).get(column.key)
        if picked is not None:
            column.tap = float(picked)
            if column.taps and column.tap not in column.taps:
                column.taps = sorted({*column.taps, column.tap})

    if not result.columns:
        result.warnings.append(
            "No speaker is proposed on the floor-wise BOQ, so there is nothing to load an amplifier with."
        )
        return result

    unknown = [column for column in result.columns if column.tap is None]
    if unknown:
        result.warnings.append(
            f"{len(unknown)} speaker{'s have' if len(unknown) != 1 else ' has'} no tapping set "
            f"({', '.join(column.key for column in unknown[:4])}"
            f"{', ...' if len(unknown) > 4 else ''}). Choose one from the speaker database; until then "
            "those speakers add nothing to the load."
        )
    unsettled = [column for column in result.columns if column.unsettled]
    if unsettled:
        result.warnings.append(
            f"{len(unsettled)} speaker line{'s are' if len(unsettled) != 1 else ' is'} not settled as a "
            f"part on the Proposed Materials tab ({', '.join(column.key for column in unsettled[:3])}"
            f"{', ...' if len(unsettled) > 3 else ''}), so the column is named after the schedule's own "
            "wording."
        )

    # A floor's speakers, from the schedule and from any hand adjustment.
    by_line: dict[str, str] = {}
    for column in result.columns:
        for line in column.lines:
            by_line[line] = column.key
    for floor in schedule.get("floors", []):
        row = FloorRow(floor=floor)
        for item in schedule.get("items", []):
            key = by_line.get(item.get("description", ""))
            if key is None:
                continue
            count = (item.get("per_floor") or {}).get(floor) or 0
            if count:
                row.counts[key] = row.counts.get(key, 0) + int(count)
        taps_by_key = {column.key: column.tap for column in result.columns}
        row.watts = _watts(sum(count * (taps_by_key.get(key) or 0.0) for key, count in row.counts.items()))
        result.floors.append(row)

    _assign(result)
    _modules(result)
    return result


def _modules(result: Result) -> None:
    """One audio riser module a floor, and a second where one will not do.

    Every floor that carries a speaker is fed through a module of its own,
    so the count is the number of floors with speakers rather than
    anything divided. A floor whose load is more than a single module may
    carry needs another, which is reported: the riser is drawn off this,
    and finding out afterwards is expensive.
    """
    limit = result.module_max_watts
    over: list[FloorRow] = []
    for row in result.floors:
        if not row.watts:
            row.modules = 0
            continue
        row.modules = max(1, math.ceil(row.watts / limit - 1e-9)) if limit > 0 else 1
        if row.modules > 1:
            over.append(row)
    if over:
        result.warnings.append(
            f"{len(over)} floor{'s carry' if len(over) != 1 else ' carries'} more than one "
            f"{result.module_part} can take at {limit:g} W "
            f"({', '.join(f'{row.floor} {row.watts:g} W' for row in over[:3])}"
            f"{', ...' if len(over) > 3 else ''}). Each is shown with the number of modules it needs."
        )


def _assign(result: Result) -> None:
    """Fill amplifiers with whole floors, in the schedule's own order.

    Floors are added to the current amplifier until the next one would
    take it over the limit; then a new amplifier starts. A floor carrying
    more than one amplifier can feed gets one of its own and is reported,
    rather than being split across two, which is not something an engineer
    would do without deciding to.
    """
    limit = result.limit_watts
    current: Amplifier | None = None
    for row in result.floors:
        if not row.watts:
            continue                      # no speakers on this floor
        if row.watts > limit:
            current = Amplifier(name=f"{AMPLIFIER_PART.replace('SIGA-', '')}-{len(result.amplifiers) + 1}",
                                floors=[row.floor], watts=row.watts, over_limit=True)
            result.amplifiers.append(current)
            row.amplifier = current.name
            current = None                # nothing more may join it
            continue
        if current is None or current.watts + row.watts > limit:
            current = Amplifier(name=f"{AMPLIFIER_PART.replace('SIGA-', '')}-{len(result.amplifiers) + 1}")
            result.amplifiers.append(current)
        current.floors.append(row.floor)
        current.watts = _watts(current.watts + row.watts)
        row.amplifier = current.name

    over = [amplifier for amplifier in result.amplifiers if amplifier.over_limit]
    if over:
        result.warnings.append(
            f"{len(over)} floor{'s carry' if len(over) != 1 else ' carries'} more speakers than one "
            f"{AMPLIFIER_PART} can feed at {limit:g} W "
            f"({', '.join(amplifier.floors[0] for amplifier in over[:3])}"
            f"{', ...' if len(over) > 3 else ''}). Each is shown on an amplifier of its own, over its "
            "limit, for the engineer to split or to feed from a larger amplifier."
        )

    # Two amplifiers to a cabinet, in the order the amplifiers were filled.
    for index in range(0, len(result.amplifiers), AMPLIFIERS_PER_CABINET):
        members = result.amplifiers[index: index + AMPLIFIERS_PER_CABINET]
        cabinet = Cabinet(name=f"{CABINET_PART}-{index // AMPLIFIERS_PER_CABINET + 1}",
                          amplifiers=[amplifier.name for amplifier in members])
        result.cabinets.append(cabinet)
        for amplifier in members:
            amplifier.cabinet = cabinet.name


def cabinets_for(amplifiers: int) -> int:
    """How many cabinets a number of amplifiers needs. An odd amplifier
    still needs a cabinet of its own."""
    return math.ceil(amplifiers / AMPLIFIERS_PER_CABINET)
