"""Currents read off the datasheets of a booster, an amplifier and a module --
the three the company's BC template used to carry as typed-in figures."""

from pathlib import Path

import pymupdf

from app.services.datasheet_currents import read_part_current, read_power_supply_current

LIBRARY = Path(__file__).resolve().parents[1] / "library" / "datasheets" / "EDWARDS"


def _pdf(path: Path, lines: list[tuple[float, float, str, float]]) -> Path:
    """A page of (x, y, text, size) spans, laid out like a datasheet table."""
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text, size in lines:
        page.insert_text((x, y), text, fontsize=size, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def test_a_booster_is_its_internal_currents_plus_its_full_rated_output(tmp_path):
    pdf = _pdf(tmp_path / "bps.pdf", [
        (40, 60, "Specifications", 10),
        (40, 90, "Notification Appliance Circuit Ratings", 8), (250, 90, "3.0A max. per circuit @ 24Vdc", 8),
        (250, 100, "nominal  6.5A max total all NACs", 8), (400, 100, "3.0A max. per circuit @ 24Vdc", 8),
        (400, 110, "nominal  10A max total all NACs", 8),
        (40, 130, "Auxiliary Outputs", 8), (250, 130, "outputs and 200 mA dedicated auxiliary.", 8),
        (40, 150, "Booster Internal Supervisory Current", 8), (250, 150, "70mA + 35 mA for each circuit set to AUX", 8),
        (40, 170, "Booster Internal Alarm Current", 8), (250, 170, "270mA", 8),
    ])
    ten = read_power_supply_current(pdf, "BPS10A/230")
    assert (ten.standby_ma, ten.alarm_ma) == (70, 10270)
    assert any("full rated output 10 A" in n for n in ten.notes)
    six = read_part_current(pdf, "BPS6A", doc_named_for_part=True)
    assert (six.standby_ma, six.alarm_ma) == (70, 6770)
    # An APS has no figure of its own: its battery calculation is its
    # amplifiers and modules, each a line of the BOQ.
    assert read_part_current(pdf, "APS6A/230", doc_named_for_part=False) is None


def test_amperes_and_microamperes_are_read_in_milliamperes(tmp_path):
    pdf = _pdf(tmp_path / "amp.pdf", [
        (40, 60, "Specifications", 10),
        (250, 100, "SIGA-AA30", 10), (400, 100, "SIGA-AA50", 10),
        (40, 120, "Current:   Standby", 8), (250, 120, "2 mA at 24 V", 8), (400, 120, "2 mA at 24 V", 8),
        (110, 132, "Active", 8), (250, 132, "1.55 A at 24 V load", 8), (400, 132, "2.8 A at 24 V full load", 8),
    ])
    fifty = read_part_current(pdf, "SIGA-AA50", doc_named_for_part=True)
    assert (fifty.standby_ma, fifty.alarm_ma) == (2, 2800)
    thirty = read_part_current(pdf, "SIGA-AA30", doc_named_for_part=True)
    assert (thirty.standby_ma, thirty.alarm_ma) == (2, 1550)

    pdf = _pdf(tmp_path / "module.pdf", [
        (40, 60, "Specifications", 10),
        (200, 100, "SIGA-CT1HT", 10), (270, 100, "SIGA-CT1", 10), (340, 100, "SIGA-CT2", 10), (410, 100, "SIGA-MCT2", 10),
        (40, 140, "Operating Current", 8), (220, 140, "Standby = 250µA;", 8), (370, 140, "Standby = 396µA;", 8),
        (220, 150, "Activated = 400µA", 8), (370, 150, "Activated = 680µA", 8),
    ])
    ct2 = read_part_current(pdf, "SIGA-CT2", doc_named_for_part=True)
    assert (ct2.standby_ma, ct2.alarm_ma) == (0.396, 0.68), "a figure spanning two model columns belongs to both"
    ct1 = read_part_current(pdf, "SIGA-CT1", doc_named_for_part=True)
    assert (ct1.standby_ma, ct1.alarm_ma) == (0.25, 0.4)


def test_the_library_datasheets_give_the_template_figures():
    """The company's BC template typed these in; the datasheets in the
    library say the same, so nothing is typed in any more."""
    if not LIBRARY.is_dir():
        return
    cases = {
        "BPS10A/230": ("10- BPS/01- BPS10A.pdf", 70, 10270),
        "SIGA-AA50": ("09- Amplifier/02- SIGA-AA50.pdf", 2, 2800),
        "SIGA-CT2": ("04- Modules/01- Input devices/02- Conventional Contacts/02- Dual Input Module/01- SIGA-CT2, SIGA-MCT2.pdf",
                     0.396, 0.68),
    }
    for part, (relative, standby, alarm) in cases.items():
        pdf = LIBRARY / relative
        if not pdf.is_file():
            continue
        reading = read_part_current(pdf, part, doc_named_for_part=True)
        assert reading is not None, part
        assert (reading.standby_ma, reading.alarm_ma) == (standby, alarm), part
