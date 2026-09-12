"""The VE amplifier calculation against the engineers' own workbooks.

The fixtures were generated from two real amplifier calculations (see each
file's `source`); the zone watts and, for EP-24601, each channel's required
watts and amplifier are the engineer's figures, so they are the oracle for
the arithmetic. Neither sheet checks loading at all, so the pass/fail
assertions pin the design limit instead (80% of the rating, from the
DesignRule), not anything in the sheets.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas_design import Channel, Rack, SpeakerType, VoiceEvacuationDesign, Zone
from app.services.ve_calculation import calculate

FIXTURES = Path(__file__).parent / "fixtures"


def _design_from_fixture(name: str, max_load_fraction: float = 0.8) -> tuple[VoiceEvacuationDesign, dict]:
    data = json.loads((FIXTURES / name).read_text())
    design = VoiceEvacuationDesign(
        speaker_types=[SpeakerType(**s) for s in data["speaker_types"]],
        zones=[Zone(name=z["name"], counts=z["counts"], sheet_watts=z["watts"]) for z in data["zones"]],
        channels=[
            Channel(
                first_zone=c["first_zone"],
                label=c.get("label"),
                amplifier_watts=c.get("amplifier_watts"),
                sheet_required_watts=c.get("required_watts"),
            )
            for c in data["engineer_channels"]
        ],
        racks=[
            Rack(name=r["name"], location=r.get("location"), first_channel=r["first_channel"])
            for r in data["engineer_aps"]
        ],
        max_load_fraction=max_load_fraction,
    )
    return design, data


@pytest.mark.parametrize("fixture", ["ve_ep24601_tower_a.json", "ve_ep29495.json"])
def test_zone_watts_match_the_engineers_sheet(fixture):
    design, data = _design_from_fixture(fixture)
    result = calculate(design)
    assert [z.watts for z in result.zones] == [z["watts"] for z in data["zones"]]
    assert not any(z.sheet_mismatch for z in result.zones)


def test_channel_required_watts_match_ep24601():
    design, data = _design_from_fixture("ve_ep24601_tower_a.json")
    result = calculate(design)
    assert [c.required_watts for c in result.channels] == [
        c["required_watts"] for c in data["engineer_channels"]
    ]
    assert result.sheet_mismatches == 0


def test_ep24601_loading_against_an_80_percent_limit():
    design, _ = _design_from_fixture("ve_ep24601_tower_a.json")
    result = calculate(design)
    # 50 W amplifiers: the limit is 40 W. Exactly 40 W is within it.
    failing = [(c.index, c.required_watts) for c in result.channels if c.status != "ok"]
    assert failing == [(5, 42), (6, 41)]
    assert all(c.status == "over_limit" for c in result.channels if c.index in (5, 6))
    assert result.channels_failing == 2
    assert {c.limit_watts for c in result.channels} == {40}
    at_limit = [c for c in result.channels if c.required_watts == 40]
    assert at_limit and all(c.status == "ok" for c in at_limit)


def test_the_limit_comes_from_the_design_not_the_module():
    design, _ = _design_from_fixture("ve_ep24601_tower_a.json", max_load_fraction=1.0)
    assert calculate(design).channels_failing == 0


def test_ep29495_has_no_ratings_so_nothing_passes_or_fails():
    design, _ = _design_from_fixture("ve_ep29495.json")
    result = calculate(design)
    assert {c.status for c in result.channels} == {"no_rating"}
    assert result.channels_failing == 0
    # Ranges end where the next begins: the first channel is UG, BF, GF.
    assert (result.channels[0].first_zone, result.channels[0].last_zone) == (0, 2)
    assert result.channels[0].required_watts == 0 + 18.25 + 13.75
    assert result.channels[-1].last_zone == len(design.zones) - 1


def test_racks_group_channels_and_sum_them():
    design, _ = _design_from_fixture("ve_ep24601_tower_a.json")
    result = calculate(design)
    first = result.racks[0]
    assert (first.first_channel, first.last_channel) == (0, 1)
    assert first.required_watts == 37.5 + 37.5
    assert first.amplifier_watts == 100
    assert sum(r.required_watts for r in result.racks) == result.total_required_watts


def test_over_rating_is_worse_than_over_limit():
    design = VoiceEvacuationDesign(
        speaker_types=[SpeakerType(key="c", name="Ceiling", tap_watts=1)],
        zones=[Zone(name="L1", counts={"c": 55}), Zone(name="L2", counts={"c": 45}), Zone(name="L3", counts={"c": 10})],
        channels=[
            Channel(first_zone=0, amplifier_watts=50),
            Channel(first_zone=1, amplifier_watts=50),
            Channel(first_zone=2),
        ],
        max_load_fraction=0.8,
    )
    assert [c.status for c in calculate(design).channels] == ["over_rating", "over_limit", "no_rating"]


def test_zones_before_the_first_channel_are_reported_unassigned():
    design = VoiceEvacuationDesign(
        speaker_types=[SpeakerType(key="c", name="Ceiling", tap_watts=0.75)],
        zones=[Zone(name="B1", counts={"c": 4}), Zone(name="GF", counts={"c": 4})],
        channels=[Channel(first_zone=1, amplifier_watts=50)],
        max_load_fraction=0.8,
    )
    result = calculate(design)
    assert result.unassigned_zones == [0]
    assert result.channels[0].required_watts == 3


def test_a_sheet_figure_that_disagrees_with_its_counts_is_flagged():
    design = VoiceEvacuationDesign(
        speaker_types=[SpeakerType(key="c", name="Ceiling", tap_watts=0.75)],
        zones=[Zone(name="GF", counts={"c": 10}, sheet_watts=7)],
        channels=[Channel(first_zone=0, amplifier_watts=50, sheet_required_watts=7.5)],
        max_load_fraction=0.8,
    )
    result = calculate(design)
    assert result.zones[0].sheet_mismatch
    assert not result.channels[0].sheet_mismatch
    assert result.sheet_mismatches == 1


@pytest.mark.parametrize(
    "change",
    [
        {"channels": [Channel(first_zone=0), Channel(first_zone=0)]},
        {"channels": [Channel(first_zone=1), Channel(first_zone=0)]},
        {"channels": [Channel(first_zone=5)]},
        {"racks": [Rack(name="APS-1", first_channel=3)]},
        {"zones": [Zone(name="GF", counts={"horn": 1})]},
    ],
)
def test_inconsistent_designs_are_rejected(change):
    fields = {
        "speaker_types": [SpeakerType(key="c", name="Ceiling", tap_watts=0.75)],
        "zones": [Zone(name="GF", counts={"c": 1}), Zone(name="L1", counts={"c": 1})],
        "channels": [Channel(first_zone=0)],
        "max_load_fraction": 0.8,
    } | change
    with pytest.raises(ValidationError):
        VoiceEvacuationDesign(**fields)
