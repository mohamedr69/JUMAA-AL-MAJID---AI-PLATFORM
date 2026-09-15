"""Voice Evacuation amplifier loading, from the zone schedule.

The arithmetic is the engineer's own, read off their workbooks:

- a zone's watts = sum over speaker types of count x tap watts;
- a channel's required watts = the plain sum of its zones' watts;
- a rack's totals = the sums over its channels.

The one thing the workbooks never did is check the result against the
amplifier: the rating is typed in by hand and nothing compares the two. Here
a channel fails when its required watts exceed the design limit -- the
rating times `max_load_fraction`, which comes from a DesignRule rather than
from this module, since it is a design decision and not arithmetic.
"""

from app.services import calc_integrity
from app.schemas_design import (
    ChannelResult,
    ChannelStatus,
    RackResult,
    VoiceEvacuationDesign,
    VoiceEvacuationResult,
    ZoneResult,
)

# Taps are quarter-watts and counts are integers, so sums are exact in
# binary floating point in practice; this only absorbs a stray last bit.
_EPSILON = 1e-9

# A workbook figure within this of the calculated one agrees with it -- the
# sheets show watts to two decimals.
SHEET_TOLERANCE_WATTS = 0.005


def _watts(value: float) -> float:
    return round(value, 4)


def _runs(starts: list[int], total: int) -> list[tuple[int, int]]:
    """[(first, last)] for each start, each run ending where the next begins."""
    return [
        (start, (starts[i + 1] if i + 1 < len(starts) else total) - 1)
        for i, start in enumerate(starts)
    ]


def _status(required: float, rating: float | None, fraction: float) -> ChannelStatus:
    if rating is None:
        return "no_rating"
    if required > rating + _EPSILON:
        return "over_rating"
    if required > rating * fraction + _EPSILON:
        return "over_limit"
    return "ok"


def _disagrees(sheet: float | None, calculated: float) -> bool:
    return sheet is not None and abs(sheet - calculated) > SHEET_TOLERANCE_WATTS


def calculate(design: VoiceEvacuationDesign) -> VoiceEvacuationResult:
    taps = {s.key: s.tap_watts for s in design.speaker_types}
    zone_watts = [
        sum(count * taps[key] for key, count in zone.counts.items()) for zone in design.zones
    ]

    channel_runs = _runs([c.first_zone for c in design.channels], len(design.zones))
    rack_runs = _runs([r.first_channel for r in design.racks], len(design.channels))

    zone_channel: list[int | None] = [None] * len(design.zones)
    for index, (first, last) in enumerate(channel_runs):
        for z in range(first, last + 1):
            zone_channel[z] = index
    channel_rack: list[int | None] = [None] * len(design.channels)
    for index, (first, last) in enumerate(rack_runs):
        for c in range(first, last + 1):
            channel_rack[c] = index

    channels: list[ChannelResult] = []
    for index, (channel, (first, last)) in enumerate(zip(design.channels, channel_runs)):
        required = _watts(sum(zone_watts[first : last + 1]))
        rating = channel.amplifier_watts
        channels.append(
            ChannelResult(
                index=index,
                label=channel.label,
                first_zone=first,
                last_zone=last,
                required_watts=required,
                amplifier_watts=rating,
                load_fraction=None if not rating else round(required / rating, 4),
                limit_watts=None if rating is None else _watts(rating * design.max_load_fraction),
                status=_status(required, rating, design.max_load_fraction),
                sheet_required_watts=channel.sheet_required_watts,
                sheet_mismatch=_disagrees(channel.sheet_required_watts, required),
                rack=channel_rack[index],
            )
        )

    racks: list[RackResult] = []
    for index, (rack, (first, last)) in enumerate(zip(design.racks, rack_runs)):
        members = channels[first : last + 1]
        ratings = [c.amplifier_watts for c in members]
        racks.append(
            RackResult(
                index=index,
                name=rack.name,
                location=rack.location,
                first_channel=first,
                last_channel=last,
                required_watts=_watts(sum(c.required_watts for c in members)),
                amplifier_watts=None if None in ratings else _watts(sum(ratings)),
            )
        )

    zones = [
        ZoneResult(
            index=index,
            name=zone.name,
            watts=_watts(watts),
            sheet_watts=zone.sheet_watts,
            sheet_mismatch=_disagrees(zone.sheet_watts, watts),
            channel=zone_channel[index],
        )
        for index, (zone, watts) in enumerate(zip(design.zones, zone_watts))
    ]

    unassigned = [z.index for z in zones if z.channel is None]
    unrated = [c for c in channels if c.amplifier_watts is None]
    reasons: list[str] = []
    if not design.channels:
        reasons.append("No zone is assigned to an amplifier channel.")
    elif unassigned:
        reasons.append(f"{len(unassigned)} zone{'s are' if len(unassigned) != 1 else ' is'} on no channel: "
                       + ", ".join(zones[i].name for i in unassigned[:6]) + ("..." if len(unassigned) > 6 else ""))
    if unrated:
        reasons.append(f"{len(unrated)} channel{'s have' if len(unrated) != 1 else ' has'} no amplifier rating: "
                       + ", ".join(c.label or f"channel {c.index + 1}" for c in unrated[:6]))
    rackless = [c.index for c in channels if c.rack is None]
    if design.racks and rackless:
        reasons.append(f"{len(rackless)} channel{'s are' if len(rackless) != 1 else ' is'} in no rack.")

    result = VoiceEvacuationResult(
        zones=zones,
        channels=channels,
        racks=racks,
        total_required_watts=_watts(sum(zone_watts)),
        max_load_fraction=design.max_load_fraction,
        unassigned_zones=unassigned,
        channels_failing=sum(1 for c in channels if c.status in ("over_limit", "over_rating")),
        sheet_mismatches=sum(1 for z in zones if z.sheet_mismatch)
        + sum(1 for c in channels if c.sheet_mismatch),
        complete=not reasons,
        incomplete_reasons=reasons,
    )
    result.input_hash = calc_integrity.stable_hash(calc_integrity.ve_inputs(design))
    result.result_hash = calc_integrity.stable_hash(
        result.model_dump(mode="json", exclude={"input_hash", "result_hash"}))
    return result
