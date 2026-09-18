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
import re

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


# The speakers in a staircase are on circuits of their own, never shared
# with a floor's: a staircase is evacuated as one, top to bottom, whatever
# the floors either side of it are doing. Which parts they are is a design
# rule (`ve.staircase/speakers`); a line whose own wording says "stair"
# is one as well, whatever it is ordered as.
_STAIR_RE = re.compile(r"\bSTAIR", re.IGNORECASE)


def _watts(value: float) -> float:
    return round(value + 0.0, 4)


def is_staircase(item: dict, parts: set[str]) -> bool:
    """Whether a speaker line is a staircase's rather than a floor's."""
    part = ((item.get("material") or {}).get("part_no") or item.get("catalog_no") or "").strip().upper()
    return bool((part and part in parts) or _STAIR_RE.search(item.get("description") or ""))


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
    # The staircase circuits it feeds, on the Staircase tab; a floor
    # amplifier feeds floors and has none.
    circuits: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "floors": list(self.floors), "watts": _watts(self.watts),
                "cabinet": self.cabinet, "over_limit": self.over_limit, "circuits": list(self.circuits)}


@dataclasses.dataclass
class Cabinet:
    name: str
    amplifiers: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "amplifiers": list(self.amplifiers)}


@dataclasses.dataclass
class StairCircuit:
    """One staircase circuit: a run of floors of one stair, on one module."""

    name: str                       # "ST1-1": stair 1, its first circuit
    stair: int
    floors: list[str] = dataclasses.field(default_factory=list)
    speakers: int = 0
    watts: float = 0.0
    amplifier: str | None = None
    # One speaker drawing more than a circuit may carry -- a tapping no
    # staircase would be set to, but reported rather than hidden.
    over_limit: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "stair": self.stair, "floors": list(self.floors),
                "speakers": self.speakers, "watts": _watts(self.watts),
                "amplifier": self.amplifier, "over_limit": self.over_limit}


@dataclasses.dataclass
class Staircase:
    """The staircase speakers, worked out apart from the floors'.

    How many staircases a building has is read off the schedule: every
    stair has one speaker on each floor it serves, so a floor with two
    staircase speakers is served by two stairs, and the building has as
    many stairs as its busiest floor. Each stair is walked floor by floor
    in the schedule's order and its speakers put on a circuit until the
    next would take it over the limit; then a new circuit starts, on a
    module of its own. The circuits are then fed from amplifiers, whole,
    as floors are on the Speakers tab.
    """

    columns: list[SpeakerColumn] = dataclasses.field(default_factory=list)
    stairs: int = 0
    floors: list[dict] = dataclasses.field(default_factory=list)
    circuits: list[StairCircuit] = dataclasses.field(default_factory=list)
    amplifiers: list["Amplifier"] = dataclasses.field(default_factory=list)
    circuit_limit_watts: float = 0.0
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def total_watts(self) -> float:
        return sum(circuit.watts for circuit in self.circuits)

    @property
    def total_speakers(self) -> int:
        return sum(circuit.speakers for circuit in self.circuits)

    def as_dict(self) -> dict:
        return {
            "columns": [column.as_dict() for column in self.columns],
            "stairs": self.stairs,
            "floors": self.floors,
            "circuits": [circuit.as_dict() for circuit in self.circuits],
            "amplifiers": [amplifier.as_dict() for amplifier in self.amplifiers],
            "circuit_limit_watts": _watts(self.circuit_limit_watts),
            "total_circuits": len(self.circuits),
            "total_speakers": self.total_speakers,
            "total_watts": _watts(self.total_watts),
            "total_watts_with_spare": _watts(self.total_watts * (1 + SPARE_FRACTION)),
            "totals_by_column": {
                column.key: sum(floor["counts"].get(column.key, 0) for floor in self.floors)
                for column in self.columns
            },
            "warnings": self.warnings,
        }


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
    # None where the project keeps no staircase apart (no rule given).
    staircase: Staircase | None = None

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
            "staircase": self.staircase.as_dict() if self.staircase is not None else None,
            # What the job orders, the floors' and the staircases' together:
            # the cabinets are shared, so an odd amplifier on each tab is
            # still one cabinet between them.
            "job": {
                "amplifiers": len(self.amplifiers) + (len(self.staircase.amplifiers) if self.staircase else 0),
                "modules": self.total_modules + (len(self.staircase.circuits) if self.staircase else 0),
                "cabinets": len(self.cabinets),
                "speakers": self.total_speakers + (self.staircase.total_speakers if self.staircase else 0),
                "watts": _watts(self.total_watts + (self.staircase.total_watts if self.staircase else 0)),
            },
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
              fraction: float = 0.8, module: dict | None = None, staircase: dict | None = None) -> Result:
    """The amplifier schedule a floor-wise BOQ makes, the staircases apart.

    `staircase` is the `ve.staircase/speakers` rule: which parts are the
    speakers in a staircase. Given, those lines are taken off the floors
    and worked out on circuits of their own (`Result.staircase`); not
    given, every speaker is a floor's, as it always was.
    """
    parts = None if staircase is None else {
        part.strip().upper() for part in (staircase.get("parts") or []) if part and part.strip()}
    floors, stairs = schedule, None
    if parts is not None:
        on_floors, on_stairs = [], []
        for item in schedule.get("items", []):
            device = item.get("device") or device_in(item.get("description"))
            (on_stairs if device in SPEAKER_DEVICES and is_staircase(item, parts) else on_floors).append(item)
        floors = {**schedule, "items": on_floors}
        stairs = {**schedule, "items": on_stairs}
    result = _floor_speakers(floors, taps=taps, chosen=chosen, fraction=fraction, module=module,
                             stair_speakers=bool(stairs and stairs["items"]))
    if stairs is not None:
        result.staircase = _staircase(stairs, taps=taps, chosen=chosen, result=result)
    _cabinets(result)
    return result


def _floor_speakers(schedule: dict, *, taps: dict[str, dict], chosen: dict[str, float] | None,
                    fraction: float, module: dict | None, stair_speakers: bool = False) -> Result:
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
            "Every speaker on the floor-wise BOQ is a staircase's; they are worked out on the Staircase tab."
            if stair_speakers else
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


def _cabinets(result: Result) -> None:
    """Two amplifiers to a cabinet: the floors' in the order they were
    filled, then the staircases'. The cabinets are one set for the job, so
    an odd amplifier on each tab shares a cabinet rather than taking two."""
    amplifiers = result.amplifiers + (result.staircase.amplifiers if result.staircase else [])
    for index in range(0, len(amplifiers), AMPLIFIERS_PER_CABINET):
        members = amplifiers[index: index + AMPLIFIERS_PER_CABINET]
        cabinet = Cabinet(name=f"{CABINET_PART}-{index // AMPLIFIERS_PER_CABINET + 1}",
                          amplifiers=[amplifier.name for amplifier in members])
        result.cabinets.append(cabinet)
        for amplifier in members:
            amplifier.cabinet = cabinet.name


def _staircase(schedule: dict, *, taps: dict[str, dict], chosen: dict[str, float] | None,
               result: Result) -> Staircase:
    """The staircase speakers on circuits of their own (see `Staircase`).

    A circuit is loaded to what one amplifier may feed -- or, where the
    module is rated for less, to the module's rating: a SIGA-CC2A carries
    35 W on a 70 V line, below a SIGA-AA50's 40 W, and a circuit over its
    module's rating is one the module cannot switch.
    """
    stair = Staircase(circuit_limit_watts=_watts(min(result.limit_watts, result.module_max_watts)))
    stair.columns = speaker_columns(schedule, taps)
    for column in stair.columns:
        picked = (chosen or {}).get(column.key)
        if picked is not None:
            column.tap = float(picked)
            if column.taps and column.tap not in column.taps:
                column.taps = sorted({*column.taps, column.tap})
    if not stair.columns:
        return stair
    unknown = [column for column in stair.columns if column.tap is None]
    if unknown:
        stair.warnings.append(
            f"{len(unknown)} staircase speaker{'s have' if len(unknown) != 1 else ' has'} no tapping set "
            f"({', '.join(column.key for column in unknown[:4])}). Choose one; until then the staircase "
            "circuits carry no load and are fed from no amplifier."
        )

    # Each floor's staircase speakers, one to a stair, in a fixed order.
    limit = stair.circuit_limit_watts
    by_floor: list[tuple[str, list[float]]] = []
    for floor in schedule.get("floors", []):
        counts: dict[str, int] = {}
        speakers: list[float] = []
        for column in stair.columns:
            count = sum(int((item.get("per_floor") or {}).get(floor) or 0)
                        for item in schedule.get("items", []) if item.get("description") in column.lines)
            if count:
                counts[column.key] = count
                speakers += [column.tap or 0.0] * count
        by_floor.append((floor, speakers))
        stair.floors.append({"floor": floor, "counts": counts, "stairs": len(speakers),
                             "speakers": len(speakers), "watts": _watts(sum(speakers)), "circuits": []})
    stair.stairs = max((len(speakers) for _, speakers in by_floor), default=0)

    # Each stair, top of the schedule to the bottom, onto its circuits.
    rows = {row["floor"]: row for row in stair.floors}
    for number in range(1, stair.stairs + 1):
        circuit: StairCircuit | None = None
        made = 0
        for floor, speakers in by_floor:
            if len(speakers) < number:
                continue                    # this stair does not reach this floor
            watts = speakers[number - 1]
            alone = watts > limit
            if alone or circuit is None or circuit.watts + watts > limit:
                made += 1
                circuit = StairCircuit(name=f"ST{number}-{made}", stair=number, over_limit=alone)
                stair.circuits.append(circuit)
            circuit.floors.append(floor)
            circuit.speakers += 1
            circuit.watts = _watts(circuit.watts + watts)
            rows[floor]["circuits"].append(circuit.name)
            if alone:
                circuit = None              # nothing more goes on a circuit already over its limit

    # The circuits onto amplifiers, whole, in order.
    amplifier: Amplifier | None = None
    for circuit in stair.circuits:
        if not circuit.watts:
            continue                        # no tapping set: nothing to feed yet
        if circuit.watts > result.limit_watts:
            own = Amplifier(name=f"{AMPLIFIER_PART.replace('SIGA-', '')}-ST{len(stair.amplifiers) + 1}",
                            watts=circuit.watts, over_limit=True, circuits=[circuit.name])
            stair.amplifiers.append(own)
            circuit.amplifier = own.name
            amplifier = None
            continue
        if amplifier is None or amplifier.watts + circuit.watts > result.limit_watts:
            amplifier = Amplifier(name=f"{AMPLIFIER_PART.replace('SIGA-', '')}-ST{len(stair.amplifiers) + 1}")
            stair.amplifiers.append(amplifier)
        amplifier.circuits.append(circuit.name)
        amplifier.watts = _watts(amplifier.watts + circuit.watts)
        circuit.amplifier = amplifier.name

    over = [circuit for circuit in stair.circuits if circuit.over_limit]
    if over:
        stair.warnings.append(
            f"{len(over)} staircase speaker{'s draw' if len(over) != 1 else ' draws'} more than one circuit "
            f"may carry at {limit:g} W ({', '.join(circuit.floors[0] for circuit in over[:3])}). Each is on a "
            "circuit of its own, over its limit."
        )
    return stair


def cabinets_for(amplifiers: int) -> int:
    """How many cabinets a number of amplifiers needs. An odd amplifier
    still needs a cabinet of its own."""
    return math.ceil(amplifiers / AMPLIFIERS_PER_CABINET)
