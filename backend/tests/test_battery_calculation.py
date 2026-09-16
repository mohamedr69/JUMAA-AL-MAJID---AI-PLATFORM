"""Panel standby battery sizing from the BOQ.

EP-20779's FAS battery workbook is the oracle for the arithmetic and the
selection: its three EST3 panels, with the currents the engineer used,
must come out at the engineer's figures and batteries. The currents are
test data here -- the platform's catalogue starts empty and is filled from
datasheets.
"""

import json
from pathlib import Path

import pytest

from app.services.battery_calculation import (
    BatteryUnit,
    BoqLine,
    PartCurrent,
    Sizing,
    battery_from_text,
    calculate_boq,
    calculate_panel,
    classify_group,
    part_key,
    select_batteries,
)

FIXTURES = Path(__file__).parent / "fixtures"
SIZING = Sizing(standby_hours=24, alarm_minutes=30, spare_factor=1.2, panel_voltage=24)

# The ROCKET batteries EP-20779 selects from: 26, 42 and 65 Ah 12 V blocks.
BATTERIES = {
    part_key(p): BatteryUnit(part_no=p, capacity_ah=ah, voltage=12)
    for p, ah in [("12V26A", 26), ("12V42A", 42), ("12V65A", 65)]
}

# Per-part (standby mA, alarm mA) as in EP-20779's "6. FAS Battery Calculation.xlsx".
EP20779_CURRENTS = {
    "3-CPU3": (155, 165), "3-RS232": (58, 58), "3-RS485B": (98, 98), "3-SDDC1": (264, 336),
    "3-LCD": (40, 42), "3-ZA20B": (62, 1120), "3-PPS/M-230": (70, 70), "3-12/S1RY": (12, 15),
    "3-ASU/FT": (112, 112), "3-FTCU": (112, 112),
}
CURRENTS = {part_key(p): PartCurrent(standby_ma=s, alarm_ma=a) for p, (s, a) in EP20779_CURRENTS.items()}

EP20779_PANELS = {
    "MFACP-01": (
        [("3-CPU3", 1), ("3-RS232", 1), ("3-RS485B", 1), ("3-SDDC1", 5), ("3-LCD", 1), ("3-ZA20B", 2),
         ("3-PPS/M-230", 1), ("3-12/S1RY", 2), ("3-ASU/FT", 1)],
        2001, 4495, 60.3258, 65,
    ),
    "FACP-2": (
        [("3-CPU3", 1), ("3-RS232", 1), ("3-RS485B", 1), ("3-SDDC1", 2), ("3-LCD", 1), ("3-PPS/M-230", 1),
         ("3-12/S1RY", 3), ("3-FTCU", 1)],
        1097, 1262, 32.3508, 42,
    ),
    "FACP-02": (
        [("3-CPU3", 1), ("3-RS485B", 1), ("3-SDDC1", 2), ("3-LCD", 1), ("3-PPS/M-230", 1), ("3-12/S1RY", 1)],
        903, 1062, 26.6436, 42,
    ),
}


def _lines(parts, heading="EST3 Fire Alarm Control Panel", count=1, battery=None):
    lines = [BoqLine("FAS", heading, None, "EST3 fire alarm control panel. Includes:", str(count))]
    lines += [BoqLine("FAS", heading, part, part, str(qty)) for part, qty in parts]
    if battery:
        lines.append(BoqLine("FAS", heading, battery[0], battery[1], str(battery[2])))
    return lines


@pytest.mark.parametrize("name", EP20779_PANELS)
def test_ep20779_panels_come_out_at_the_engineers_figures(name):
    parts, standby, alarm, required, selected = EP20779_PANELS[name]
    panel = calculate_panel(name, "FAS", _lines(parts), SIZING, CURRENTS, BATTERIES)
    assert (panel.standby_ma, panel.alarm_ma) == (standby, alarm)
    assert panel.required_ah == pytest.approx(required)
    assert not panel.lower_bound
    assert [(p.capacity_ah, p.units, p.strings) for p in panel.selected] == [(selected, 2, 1)]
    assert panel.status == "ok" and panel.quoted_ah is None


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (26, [(26, 1)]),
        (26.01, [(42, 1)]),
        (80.2074, [(65, 1), (26, 1)]),  # EP-29076 / EP-30784 sheet: "1 NOS 65AH, 1 NOS 26AH"
        (130, [(65, 2)]),
        (150, [(65, 2), (26, 1)]),
        (170, [(65, 2), (42, 1)]),
        (180, [(65, 3)]),
    ],
)
def test_selection_uses_one_battery_or_the_largest_plus_a_top_up(required, expected):
    chosen = select_batteries(required, list(BATTERIES.values()), 24)
    assert [(c.capacity_ah, c.strings) for c in chosen] == expected
    assert sum(c.capacity_ah * c.strings for c in chosen) >= required


def test_selection_needs_a_catalogue():
    assert select_batteries(10, [], 24) is None
    # A 6 V block does make 24 V strings; a 10 V one does not.
    odd = [BatteryUnit("B10V", 40, 10), BatteryUnit("B6V", 30, 6)]
    assert [(c.part_no, c.units) for c in select_batteries(10, odd, 24)] == [("B6V", 4)]


def test_a_quoted_battery_is_checked():
    parts = EP20779_PANELS["MFACP-01"][0]
    ok = calculate_panel("P", "FAS", _lines(parts, battery=("12V65A", "Battery, 12 V @ 65 AH", 2)), SIZING, CURRENTS, BATTERIES)
    assert (ok.quoted_ah, ok.quoted_short, ok.status) == (65, False, "ok")
    small = calculate_panel("P", "FAS", _lines(parts, battery=("12V42A", "Battery 12V 42Ah", 2)), SIZING, CURRENTS, BATTERIES)
    # The BOQ's 42 Ah is short; the selection (65 Ah) is the panel's battery.
    assert (small.quoted_ah, small.quoted_short, small.status) == (42, True, "ok")
    assert small.selected_ah == 65


def test_a_missing_current_makes_the_load_a_lower_bound():
    parts = EP20779_PANELS["MFACP-01"][0] + [("3-NEWCARD", 1)]
    quoted = ("12V65A", "Battery, 12 V @ 65 AH", 2)
    panel = calculate_panel("P", "FAS", _lines(parts, battery=quoted), SIZING, CURRENTS, BATTERIES)
    # 60.3 Ah known, 65 Ah quoted: could still be enough or not -- never "ok".
    assert panel.lower_bound and panel.missing_parts == ["3-NEWCARD"]
    assert panel.status == "incomplete"
    assert panel.selected is None

    # Already over the quoted battery with a part still missing: the BOQ is short.
    panel = calculate_panel(
        "P", "FAS", _lines(parts, battery=("12V42A", "Battery 12V 42Ah", 2)), SIZING, CURRENTS, BATTERIES
    )
    assert panel.lower_bound and panel.quoted_short and panel.status == "incomplete"


def test_a_panel_that_adds_up_to_nothing_is_never_sufficient():
    """A lump-sum panel line and a battery: no module is itemized, the load
    sums to 0 mA, and the quoted battery must not pass against that."""
    lines = [
        BoqLine("FAS", "FACP", None, "Fire alarm panel complete with all modules. Includes:", "1"),
        BoqLine("FAS", "FACP", "12V65A", "Battery, 12 V @ 65 AH", "2"),
    ]
    panel = calculate_panel("FACP", "FAS", lines, SIZING, CURRENTS, BATTERIES)
    assert panel.required_ah == 0 and not panel.missing_parts
    assert panel.status == "incomplete" and panel.selected is None
    assert any("not itemized" in note for note in panel.notes)


def test_an_unreadable_module_quantity_makes_the_load_a_lower_bound():
    parts = EP20779_PANELS["MFACP-01"][0]
    lines = _lines(parts, battery=("12V65A", "Battery, 12 V @ 65 AH", 2))
    lines.append(BoqLine("FAS", "EST3 Fire Alarm Control Panel", "3-SDDC1", "Loop card", "Lot"))
    panel = calculate_panel("P", "FAS", lines, SIZING, CURRENTS, BATTERIES)
    assert panel.lower_bound and not panel.missing_parts
    assert panel.status == "incomplete" and panel.selected is None


def test_battery_lines_are_read_from_the_boq_text_when_not_catalogued():
    assert battery_from_text("Battery, 12 V @ 65 AH") == (65, 12)
    assert battery_from_text("10Ah Sealed Lead Acid Battery - 12 Vdc") == (10, 12)
    assert battery_from_text("10Ah Sealed Lead Acid Battery - 12 Vde") == (10, 12)  # OCR of "Vdc"
    assert battery_from_text("Battery cabinet") is None
    assert battery_from_text("Battery backed clock") is None


@pytest.mark.parametrize(
    ("heading", "treatment"),
    [
        ("EST4 Main Fire Alarm Control Panel", "panel"),
        ("EST4 Fire Alarm Panel", "panel"),
        ("Sub FACP", "panel"),
        ("Amplifier (50 Watt)", "aps"),
        ("Booster Power Supply", "bps"),
        ("Booster Power Supply Panel", "bps"),
        ("EST4 Repeater Panel", "repeater"),
        ("Detectors", "not_a_panel"),
        (None, "ungrouped"),
    ],
)
def test_group_classification(heading, treatment):
    assert classify_group(heading) == treatment


def test_part_keys_ignore_spacing_and_stray_punctuation():
    assert part_key("4-LCDLE ") == part_key("4-lcdle") == "4-LCDLE"
    assert part_key("BC-1 ,") == "BC-1"
    assert part_key("3-PPS/M-230") == "3-PPS/M-230"


def _ep30784_lines() -> list[BoqLine]:
    data = json.loads((FIXTURES / "boq_ep30784.json").read_text(encoding="utf-8"))
    return [BoqLine(**line) for line in data["lines"]]


def test_ep30784_boq_groups():
    panels, groups = calculate_boq(_ep30784_lines(), SIZING, {}, {})
    assert [(g.heading, g.treatment) for g in groups] == [
        (None, "ungrouped"),  # EML
        ("EST4 Main Fire Alarm Control Panel", "panel"),
        ("EST4 Fire Alarm Panel", "panel"),
        ("Amplifier (50 Watt)", "aps"),
        ("Booster Power Supply", "bps"),
        (None, "ungrouped"),  # FAS field devices
    ]
    main, sub, aps, bps = panels
    # The amplifier and booster cabinets are sized too -- once each, though
    # the BOQ quotes 18 closets and 8 booster cabinets.
    assert (aps.kind, aps.count, bps.kind, bps.count) == ("aps", 18, "bps", 8)
    assert aps.quoted_ah == bps.quoted_ah == 10 and any("calculated here once" in n for n in aps.notes)
    # The sub-panel group quotes two panels; its lines are per panel.
    assert (main.count, sub.count) == (1, 2)
    # 2 x 12 V 65 Ah per panel: one 24 V string, 65 Ah.
    assert [(q.capacity_ah, q.units, q.strings) for q in main.quoted] == [(65, 2, 1)]
    assert main.quoted_ah == sub.quoted_ah == 65
    # Nothing catalogued yet: every load part is missing and nothing is claimed.
    assert main.status == sub.status == "incomplete"
    assert "3-SDDC2" in main.missing_parts and "12V65A" not in main.missing_parts
    assert "BC-1" in main.missing_parts  # a cabinet: the engineer records it as no load


def test_ep30784_main_panel_with_partial_currents_already_exceeds_its_battery():
    """With the currents from the engineers' EST4 sheet for the parts it lists
    (and no load for the mechanical parts), the main panel's known load alone
    needs more than the 65 Ah quoted -- 4-USBHUB and 4-COMREL still missing."""
    known = {
        "4-CPU": (211, 211), "4-PPS/M": (45, 45), "3-SDDC2": (264, 336), "4-LCDLE": (40, 93),
        "4-NET-TP": (5, 5), "4-FWAL4": (130, 130), "3-ZA20A": (62, 1120), "4-AUDTELS": (101, 85),
        "4-MIC": (8, 38), "4-FT": (9, 168),
    }
    passive = ["4-BRKT-CB", "3-CHAS7", "4-FIL", "3-CAB21B", "4-CAB24DL", "BC-1"]
    currents = {part_key(p): PartCurrent(s, a) for p, (s, a) in known.items()}
    currents |= {part_key(p): PartCurrent(0, 0) for p in passive}

    main = calculate_boq(_ep30784_lines(), SIZING, currents, {})[0][0]
    assert main.missing_parts == ["4-USBHUB", "4-COMREL"]
    assert (main.standby_ma, main.alarm_ma) == (3086, 6057)
    assert main.required_ah == pytest.approx((3086 * 24 + 6057 * 0.5) / 1000 * 1.2)
    assert main.lower_bound and main.quoted_short and main.status == "incomplete"
