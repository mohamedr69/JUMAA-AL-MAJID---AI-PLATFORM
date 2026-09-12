"""Finding a system's specification in a project folder.

The synthetic specifications copy the real ones (EP-30784's, which arrived
zipped from the estimation team): a CSI section number and title on the
first page, then the parts. The live test reads that project.
"""

import os
import zipfile
from pathlib import Path

import pymupdf
import pytest

from app.services.spec_finder import find_specs, looks_like_a_spec, open_spec, system_of

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")
requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)

SPEC_PAGE = """PROJECT ON PLOT 3466814, BUSINESS BAY, DUBAI
SECTION {number}
{title}
PART 1 - GENERAL
1.1 SUMMARY
This section covers the {title} system.
QUALITY ASSURANCE"""


def _pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((50, 60), text, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def _spec(path: Path, number: str, title: str, extra_pages: int = 1) -> Path:
    body = [SPEC_PAGE.format(number=number, title=title)]
    body += [f"SECTION {number}\n{title}\nclause {i}" for i in range(extra_pages)]
    return _pdf(path, body)


def test_a_section_is_recognised_by_its_number_and_title():
    assert [s.code for s in system_of("283111 - ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION")] == ["FAS", "VES"]
    assert [s.code for s in system_of("265200 - CENTRAL EMERGENCY LIGHTING")] == ["EML", "CBS"]
    assert system_of("211313 - SPRINKLER SYSTEMS") == []


def test_only_what_looks_like_a_specification_is_opened():
    assert looks_like_a_spec("02- inputs/Specification/283111 - FIRE DETECTION.pdf", "283111 - FIRE DETECTION.pdf")
    assert looks_like_a_spec("anywhere/265200 - CENTRAL EMERGENCY LIGHTING.pdf", "265200 - CENTRAL EMERGENCY LIGHTING.pdf")
    # A drawing, a submittal or the compliance statement itself is not one.
    assert not looks_like_a_spec("Specification/FIRE ALARM LAYOUT.pdf", "FIRE ALARM LAYOUT.pdf")
    assert not looks_like_a_spec("03- MS/02- Compliance Statement/Compliance Statement.pdf", "Compliance Statement.pdf")
    assert not looks_like_a_spec("04- Drawings/Shop Drawing for Basement.pdf", "Shop Drawing for Basement.pdf")


def test_specifications_of_their_own(tmp_path):
    _spec(tmp_path / "Specification" / "283111 - ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION.pdf", "283111", "ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION")
    _spec(tmp_path / "Specification" / "265200 - CENTRAL EMERGENCY LIGHTING.pdf", "265200", "CENTRAL EMERGENCY LIGHTING")
    _spec(tmp_path / "Specification" / "211313 - SPRINKLER SYSTEMS.pdf", "211313", "SPRINKLER SYSTEMS")
    matches, warnings = find_specs(tmp_path, {"FAS", "EML"})
    assert warnings == []
    assert sorted((m.system_code, m.section_no, m.kind) for m in matches) == [
        ("EML", "265200", "document"),
        ("FAS", "283111", "document"),
    ]
    assert matches[0].snippet.startswith("PROJECT ON PLOT")


def test_specifications_inside_the_archive_they_arrived_in(tmp_path):
    """EP-30784's specifications come as one Specification.zip."""
    inner = tmp_path / "build"
    _spec(inner / "283111 - FIRE DETECTION AND VOICE EVACUATION.pdf", "283111", "FIRE DETECTION AND VOICE EVACUATION")
    _spec(inner / "211313 - SPRINKLER SYSTEMS.pdf", "211313", "SPRINKLER SYSTEMS")
    archive = tmp_path / "02- inputs" / "Specification.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as z:
        for pdf in inner.glob("*.pdf"):
            z.write(pdf, f"Specification/{pdf.name}")
    for pdf in inner.glob("*.pdf"):
        pdf.unlink()

    (match,) = find_specs(tmp_path, {"FAS", "EML"})[0]
    assert (match.system_code, match.member) == ("FAS", "Specification/283111 - FIRE DETECTION AND VOICE EVACUATION.pdf")
    assert match.path.endswith("Specification.zip")
    # And it can be read back out of the archive.
    assert open_spec(tmp_path, match.path, match.member).startswith(b"%PDF")
    assert open_spec(tmp_path, match.path, "Specification/nope.pdf") is None
    assert open_spec(tmp_path, "../outside.pdf", None) is None


def test_a_section_inside_a_combined_electrical_specification(tmp_path):
    """One specification covering every division: the system's section is a
    run of pages inside it, and that run is what the engineer needs."""
    pages = [
        "ELECTRICAL SPECIFICATION\nDIVISION 26\nPART 1 - GENERAL",
        "SECTION 260519\nLOW VOLTAGE ELECTRICAL POWER CONDUCTORS\nPART 1 - GENERAL",
        "SECTION 265200\nCENTRAL EMERGENCY LIGHTING\nPART 1 - GENERAL",
        "SECTION 265200\nCENTRAL EMERGENCY LIGHTING\nclause 2",
        "SECTION 283111\nADDRESSABLE FIRE DETECTION\nPART 1 - GENERAL",
        "SECTION 283111\nADDRESSABLE FIRE DETECTION\nclause 2",
    ]
    _pdf(tmp_path / "Tender" / "Electrical Specification.pdf", pages)
    matches, _ = find_specs(tmp_path, {"FAS", "EML"})
    assert [(m.system_code, m.section_no, m.kind, m.first_page, m.last_page) for m in matches] == [
        ("EML", "265200", "section", 3, 4),
        ("FAS", "283111", "section", 5, 6),
    ]
    assert all(m.matched_on == "heading" for m in matches)


def test_a_project_with_no_specification(tmp_path):
    _pdf(tmp_path / "04- Drawings" / "FIRE ALARM LAYOUT.pdf", ["FIRE ALARM LAYOUT\nGROUND FLOOR"])
    _pdf(tmp_path / "03- MS" / "Compliance Statement.pdf", ["SECTION 283111\nADDRESSABLE FIRE DETECTION\ncompliance"])
    assert find_specs(tmp_path, {"FAS", "EML"})[0] == []


@requires_live_archive
def test_live_ep30784_specifications():
    folder = Path(LIVE_ROOT) / "Granada Europe/EP-30784 - Binghatti Skyblade"
    matches, warnings = find_specs(folder, {"FAS", "EML"})
    assert warnings == []
    found = {(m.system_code, m.section_no, Path(m.filename).stem[:6]) for m in matches}
    assert ("FAS", "283111", "283111") in found
    assert ("EML", "265200", "265200") in found
    # The fire-suppression sections beside them are not offered.
    assert not any("SPRINKLER" in m.filename.upper() for m in matches)
