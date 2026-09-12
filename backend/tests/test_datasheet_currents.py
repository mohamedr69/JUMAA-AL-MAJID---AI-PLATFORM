"""Reading standby / alarm currents off datasheets.

Each synthetic datasheet copies one layout found in the Edwards EST4
library (named per test), including which text is bold -- headings are told
from row labels by their type. The live test pins the values read from the
real library for EP-30784's panel modules.
"""

import os
from pathlib import Path

import pymupdf
import pytest

from app.core.config import get_settings
from app.services.datasheet_currents import read_part_current
from app.services.datasheet_library import get_libraries

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")
requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)

BOLD = "hebo"
REGULAR = "helv"


def _pdf(tmp_path: Path, placements: list[tuple], name: str = "sheet.pdf") -> Path:
    """(x, y, text) or (x, y, text, font) on one Letter page."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *font in placements:
        page.insert_text((x, y), text, fontsize=8, fontname=font[0] if font else REGULAR)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _read(path, part, named=True):
    reading = read_part_current(path, part, doc_named_for_part=named)
    return None if reading is None else (reading.standby_ma, reading.alarm_ma)


def test_label_column_beside_a_value_column(tmp_path):
    """4-LCDLE: a two-model header, labels in their own column."""
    path = _pdf(tmp_path, [
        (350, 108, "4-LCD", BOLD), (443, 108, "4-3 LCD", BOLD),
        (42, 154, "Contact us"), (214, 140, "Current"),
        (234, 154, "Standby"), (350, 154, "40 mA at 24 Vdc"),
        (234, 164, "Alarm/active"), (350, 164, "93 mA at 24 Vdc"),
    ])
    assert _read(path, "4-LCDLE") == (40, 93)


def test_side_by_side_models_are_told_apart_by_column(tmp_path):
    """3-SDDC2 & 3-SSDC2: one row, a figure pair under each model."""
    path = _pdf(tmp_path, [
        (53, 56, "Catalog Number", BOLD), (298, 56, "3-SSDC2", BOLD), (473, 56, "3-SDDC2", BOLD),
        (53, 101, "Operating Current [Note 2]"),
        (260, 101, "Standby 144 mA Alarm 204 mA"), (436, 101, "Standby 264 mA Alarm 336 mA"),
        (53, 163, "2-Wire Smoke Power Output"), (289, 163, "100 mA per 3-SDC1 Card"),
    ])
    assert _read(path, "3-SDDC2") == (264, 336)
    assert _read(path, "3-SSDC2") == (144, 204)


def test_sections_on_one_half_of_the_page(tmp_path):
    """4-AUDTEL: several modules, each under its own bold title."""
    path = _pdf(tmp_path, [
        (50, 37, "Wiring, 4-AUDTELS", BOLD), (322, 37, "4-MIC Paging Microphone", BOLD),
        (109, 65, "4-CPU"), (326, 54, "Current"),
        (336, 65, "Active"), (414, 65, "38mA"), (336, 74, "Standby"), (414, 74, "8mA"),
        (322, 201, "4-FT Firefighter Telephone Master Handset", BOLD),
        (346, 227, "Active"), (414, 227, "168mA"), (346, 237, "Standby"), (414, 237, "9mA"),
        (322, 305, "4-AUDTELS Audio and Telephone Interface/Riser Module", BOLD),
        (336, 331, "Standby"), (417, 331, "85 mA at 24 VDC"),
        (336, 342, "Alarm/active"), (417, 342, "101 mA at 24 VDC"),
    ], name="10- 4-AUDTEL.pdf")
    assert _read(path, "4-MIC", named=False) == (8, 38)
    assert _read(path, "4-FT", named=False) == (9, 168)
    assert _read(path, "4-AUDTELS", named=False) == (85, 101)
    assert _read(path, "4-CPU", named=False) is None


def test_an_alarm_pointer_takes_the_standby_figure(tmp_path):
    """4-CPU: "Alarm: See the 4-COMREL Common Relay Module"."""
    path = _pdf(tmp_path, [
        (322, 53, "Current"), (322, 64, "Standby"), (412, 64, "211 mA at 24 Vdc"),
        (322, 74, "Alarm"), (412, 84, "See the 4-COMREL Common Relay Module"),
    ])
    reading = read_part_current(path, "4-CPU", doc_named_for_part=True)
    assert (reading.standby_ma, reading.alarm_ma) == (211, 211)
    assert "4-COMREL" in reading.notes[0]
    # Without the pointer, a missing alarm figure is not made up.
    bare = _pdf(tmp_path, [(322, 64, "Standby"), (412, 64, "211 mA at 24 Vdc")], name="bare.pdf")
    assert _read(bare, "4-CPU") is None


def test_figures_per_supply_voltage_are_taken_at_the_panel_voltage(tmp_path):
    """4-FWAL4: three voltages, under a larger "4-FWAL Specifications" title."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 473), "4-FWAL Specifications", fontsize=10, fontname=REGULAR)
    for x, y, text in [
        (55, 500, "Current: Standby"), (144, 500, "175 mA at 16 VDC"), (144, 510, "130 mA at 24 VDC"),
        (144, 520, "105 mA at 32 VDC"), (88, 530, "Alarm/Active"), (144, 530, "175 mA at 16 VDC"),
        (144, 540, "130 mA at 24 VDC"), (144, 550, "105 mA at 32 VDC"),
    ]:
        page.insert_text((x, y), text, fontsize=8, fontname=REGULAR)
    page.insert_text((50, 648), "4-FWAL-CAT Specifications", fontsize=10, fontname=REGULAR)
    page.insert_text((54, 664), "Current", fontsize=8)
    page.insert_text((149, 664), "27 mA", fontsize=8)
    path = tmp_path / "05- 4-FAWL4.pdf"
    doc.save(path)
    assert _read(path, "4-FWAL4", named=False) == (130, 130)
    assert _read(path, "4-FWAL-CAT", named=False) == (27, 27)


def test_a_per_unit_figure_is_counted_at_its_worst(tmp_path):
    """24L24S: 3.0 mA base + 0.23 mA per indicator, 24 indicators."""
    path = _pdf(tmp_path, [
        (117, 54, "4-24L", BOLD), (162, 54, "4-24L12S", BOLD), (213, 54, "4-24L18S", BOLD), (264, 54, "4-24L24S", BOLD),
        (55, 68, "Indicators"), (123, 68, "24"), (175, 68, "24"), (226, 68, "24"), (277, 68, "24"),
        (55, 95, "Current"),
        (69, 105, "Standby 3.0 mA (base) + 0.23 mA/Indicator ON @ 24 VDC"),
        (78, 115, "Alarm 3.0 mA (base) + 0.23 mA/Indicator ON @ 24 VDC"),
    ])
    reading = read_part_current(path, "4-24L24S", doc_named_for_part=False)
    assert (reading.standby_ma, reading.alarm_ma) == (8.52, 8.52)
    assert any("x 24" in note for note in reading.notes)


def test_an_explicit_maximum_beats_a_per_unit_figure(tmp_path):
    """4-USBHUB: 44 mA idle, 129 mA per port, 560 mA fully loaded."""
    path = _pdf(tmp_path, [
        (326, 56, "Current"), (338, 66, "Standby/Active"), (414, 66, "44 mA (zero nonisolated ports used)"),
        (414, 76, "129 mA per nonisolated ports used."), (414, 85, "560 mA for a fully loaded 4-USBHUB."),
    ])
    assert _read(path, "4-USBHUB") == (560, 560)


def test_a_spanning_figure_a_named_model_and_a_row_label_that_is_not_a_heading(tmp_path):
    """ZA: one standby figure for most models, one naming 3-ZA95, an alarm
    figure per column -- and "SIGA-CC1/2 Support", a row label, not a title."""
    path = _pdf(tmp_path, [
        (167, 262, "3-ZA20A", BOLD), (343, 262, "3-ZA40A", BOLD), (522, 262, "3-ZA95", BOLD),
        (55, 369, "SIGA-CC1/2 Support"), (326, 369, "10 Units, Maximum"),
        (305, 379, "62mA for 20 and 40 watt amps;"), (55, 384, "Standby Current"),
        (306, 389, "85mA for the 3-ZA95 watt amp"),
        (55, 400, "Alarm Current"), (168, 400, "1120mA"), (345, 400, "2480mA"), (521, 400, "5540mA"),
    ], name="ZA.pdf")
    assert _read(path, "3-ZA20A", named=False) == (62, 1120)
    assert _read(path, "3-ZA40A", named=False) == (62, 2480)
    assert _read(path, "3-ZA95", named=False) == (85, 5540)
    assert _read(path, "SIGA-CC1/2", named=False) is None


def test_figures_owned_by_another_model_are_not_borrowed(tmp_path):
    """4-2ANN's datasheet gives the 4-ANNCPU's current, not the assembly's."""
    path = _pdf(tmp_path, [
        (167, 448, "4-2ANN"), (236, 448, "4-4ANN"),
        (33, 568, "4-ANNCPU Central Processor", BOLD), (33, 604, "Current draw"),
        (57, 614, "Standby"), (150, 614, "183 mA at 16 VDC; 125 mA at 24 VDC; 119 mA at 32 VDC"),
        (57, 624, "Alarm/Active"), (150, 624, "188 mA at 16 VDC; 125 mA at 24 VDC; 124 mA at 32 VDC"),
    ], name="01- 4-2ANN.pdf")
    assert _read(path, "4-2ANN") is None
    assert _read(path, "4-ANNCPU", named=False) == (125, 125)


def test_a_datasheet_without_currents_gives_nothing(tmp_path):
    path = _pdf(tmp_path, [(50, 60, "3-CHAS7 Seven Space Chassis", BOLD), (50, 80, "Mounts modules")])
    assert _read(path, "3-CHAS7") is None
    (tmp_path / "broken.pdf").write_bytes(b"x")
    assert _read(tmp_path / "broken.pdf", "4-CPU") is None


EP30784_EXPECTED = {
    "4-CPU": (211, 211),
    "4-PPS/M": (45, 45),
    "4-LCDLE": (40, 93),
    "4-FWAL4": (130, 130),
    "4-NET-TP": (45, 45),
    "4-USBHUB": (560, 560),
    "3-SDDC2": (264, 336),
    "3-ZA20A": (62, 1120),
    "4-AUDTELS": (85, 101),
    "4-MIC": (8, 38),
    "4-FT": (9, 168),
    "4-24L24S": (8.52, 8.52),
    "4-COMREL": None,
    "4-2ANN": None,
}


@requires_live_archive
@pytest.mark.parametrize("part", EP30784_EXPECTED)
def test_live_edwards_currents(part):
    settings = get_settings()
    library = get_libraries(settings.model_fields["datasheet_libraries"].default, LIVE_ROOT)["EDWARDS"]
    got = None
    for match in library.find(part):
        reading = read_part_current(
            library.folder / match.path, part, doc_named_for_part=match.matched_on in ("filename", "family")
        )
        if reading:
            got = (reading.standby_ma, reading.alarm_ma)
            break
    assert got == EP30784_EXPECTED[part]
