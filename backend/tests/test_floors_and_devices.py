"""What a floor is called, and what a piece of text names.

The two things the floor-wise schedule reads its sheet with. Both came out
of the drawings route when that was removed, and both are now only ever
fed the wording an engineer typed into a schedule -- no geometry, no
legend, no recognition.
"""

from app.services.floors import floor_of
from app.services.symbol_taxonomy import (
    DETECTORS,
    LIGHT,
    PULL_STATION,
    canonical,
    device_in,
    family_of,
    system_of,
)


# --- floors ---------------------------------------------------------------------------------


def test_a_column_for_a_range_of_floors_stands_for_each_of_them():
    assert floor_of("1 to 13").covers == tuple(f"Level {n}" for n in range(1, 14))
    assert floor_of("3rd to 16th floor").covers[0] == "Level 3"
    assert len(floor_of("3rd to 16th floor").covers) == 14
    assert floor_of("TYPICAL 2ND TO 14TH").covers[-1] == "Level 14"


def test_a_single_floor_covers_only_itself():
    for name in ("GF", "Ground floor", "3rd Basement", "Roof Floor", "Mech floor"):
        assert floor_of(name).covers == (), name
        assert floor_of(name).is_typical is False


def test_a_floor_is_placed_in_the_building():
    """The schedule keeps its own column order, but a floor still knows
    roughly where it sits -- a basement below the ground floor, the roof
    above everything."""
    assert floor_of("B2").order < floor_of("GF").order < floor_of("Level 3").order < floor_of("ROOF").order


# --- what a line names ------------------------------------------------------------------------


def test_the_words_a_schedule_names_its_devices_by():
    assert device_in("Photoelectric smoke detector") == "Smoke detector"
    assert device_in("Smoke with Sounder Base") == "Smoke detector with sounder base"
    assert device_in("Heat Detector") == "Heat detector"
    assert device_in("Multi Detector- Smoke and Heat") == "Multisensor detector"
    assert device_in("Intelligent Manual Call Point") == "Manual call point"
    assert device_in("Telephone Jack-Lift") == "Fire telephone"
    assert device_in("Exit Light - Above Door") == "Exit light"
    assert device_in("Self Contained EM Light") == "Emergency light"


def test_an_add_on_does_not_change_what_the_device_is():
    """A speaker with a flasher is a speaker, not a flasher."""
    assert device_in("Wall Speaker C/W Flasher") == "Speaker"
    assert device_in("Wall Sounder Flasher WP") == "Sounder"
    # ...but a flasher on its own is one.
    assert device_in("Xenon beacon") == "Strobe"


def test_a_wording_that_names_no_device_is_left_alone():
    assert device_in("Cable tray accessories") is None
    assert device_in("") is None and device_in(None) is None


def test_a_device_belongs_to_a_family_and_a_system():
    assert family_of("Smoke detector") is DETECTORS
    assert family_of("Manual call point") is PULL_STATION
    assert family_of("Exit light") is LIGHT

    # Emergency lighting is its own job, its own BOQ and its own submittal.
    assert system_of("Exit light") == "ELS"
    assert system_of("Emergency light") == "ELS"
    assert system_of("Smoke detector") == "FAS"
    assert system_of("Fire telephone") == "FAS"
    # A line the platform cannot name belongs to neither.
    assert system_of(None) is None and system_of("") is None


def test_a_variant_is_named_from_what_the_drawing_said_about_it():
    assert canonical("Smoke detector with sounder base") == "Smoke with Sounder Base"
    assert canonical("Multisensor detector", {"add_ons": ["sounder base"]}) == "Multisensor + Sounder Base"
    assert canonical("Speaker", {"mounting": "wall"}) == "Wall Speaker"
    assert canonical("Manual call point", {"environment": "outdoor"}) == "Manual Pull Station WP"
