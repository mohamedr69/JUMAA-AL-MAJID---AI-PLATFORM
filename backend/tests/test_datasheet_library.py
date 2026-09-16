"""Finding a part's datasheet in a manufacturer's library.

The synthetic PDFs lay their spec tables out the way the Edwards datasheets
do -- labels in one column, values in the next -- because that is what the
row rebuilding exists for. The live test reads the real Edwards library.
"""

import os
from pathlib import Path

import pymupdf
import pytest

from app.core.config import get_settings
from app.services.datasheet_library import DatasheetLibrary, get_libraries, libraries_for

from .conftest import login

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")
requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)
settings = get_settings()


def _pdf(path: Path, pages: list[list[tuple[float, float, str]]]) -> Path:
    """Each page is a list of (x, y, text) placements."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for placements in pages:
        page = doc.new_page()
        for x, y, text in placements:
            page.insert_text((x, y), text, fontsize=9)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def library(tmp_path) -> DatasheetLibrary:
    root = tmp_path / "01- EST4"
    _pdf(
        root / "01- PANEL" / "03- 4-LCDLE.pdf",
        [
            [(72, 60, "D A T A S H E E T E85014-0007"), (72, 90, "4-LCDLE Display")],
            [(72, 200, "Current"), (200, 200, "Standby"), (300, 200, "40 mA at 24 Vdc"),
             (200, 215, "Alarm/active"), (300, 215, "93 mA at 24 Vdc")],
        ],
    )
    _pdf(
        root / "01- PANEL" / "06- 4-NET.pdf",
        [[(72, 60, "D A T A S H E E T E85014-0008"), (72, 90, "Models 4-NET-TP and 4-NET-MM"),
          (72, 200, "Standby/Alarm"), (200, 200, "5 mA at 24 VDC")]],
    )
    _pdf(
        root / "01- PANEL" / "01- 4-CPU.pdf",
        [[(72, 60, "D A T A S H E E T E85014-0010"), (72, 90, "Accessories: 4-NET-TP, 4-LCDLE"),
          (72, 200, "Standby"), (200, 200, "211 mA at 24 Vdc")]],
    )
    (root / "broken.pdf").write_bytes(b"not a pdf")
    return DatasheetLibrary("EDWARDS", root)


def test_a_datasheet_named_for_the_part_comes_first(library):
    (match, *others) = library.find("4-lcdle ")
    assert (match.filename, match.matched_on, match.document_no) == ("03- 4-LCDLE.pdf", "filename", "E85014-0007")
    # The label stays on the row with its value.
    assert [(p, t) for p, t in match.current_rows] == [
        (2, "Current | Standby | 40 mA at 24 Vdc"),
        (2, "Alarm/active | 93 mA at 24 Vdc"),
    ]
    assert match.source == "Edwards datasheet E85014-0007 (03- 4-LCDLE.pdf), p.2"
    assert [m.filename for m in others] == ["01- 4-CPU.pdf"]


def test_the_part_family_datasheet_beats_a_passing_mention(library):
    matches = library.find("4-NET-TP")
    assert [(m.filename, m.matched_on) for m in matches] == [
        ("06- 4-NET.pdf", "family"),
        ("01- 4-CPU.pdf", "text"),
    ]


def test_a_suffixed_part_finds_its_base_datasheet(library):
    assert library.find("4-LCDLE/M")[0].filename == "03- 4-LCDLE.pdf"


def test_unknown_parts_and_unreadable_files(library):
    assert library.find("4-COMREL") == []
    assert library.find(" , ") == []
    assert library.unreadable() == ["broken.pdf"]


def test_files_are_reindexed_when_they_change(library):
    assert library.find("4-FT") == []
    _pdf(library.folder / "10- 4-AUDTEL.pdf", [[(72, 60, "4-FT Firefighter handset"), (72, 80, "Standby"), (200, 80, "9mA")]])
    assert library.find("4-FT")[0].filename == "10- 4-AUDTEL.pdf"


def test_only_library_files_can_be_opened(library, tmp_path):
    assert library.resolve("01- PANEL/03- 4-LCDLE.pdf") is not None
    outside = _pdf(tmp_path / "secret.pdf", [[(72, 60, "x")]])
    assert library.resolve("../secret.pdf") is None
    assert library.resolve(str(outside)) is None
    assert library.resolve("01- PANEL") is None


def test_libraries_resolve_against_the_projects_root(tmp_path):
    (tmp_path / "Systems" / "EST4").mkdir(parents=True)
    libraries = get_libraries({"Edwards": "Systems/EST4", "Notifier": "Systems/missing"}, str(tmp_path))
    assert list(libraries) == ["EDWARDS"]
    assert libraries_for("EDWARDS (EST4)", libraries) == [libraries["EDWARDS"]]
    # A manufacturer with no library of its own: search them all.
    assert libraries_for("HOCHIKI", libraries) == [libraries["EDWARDS"]]
    assert get_libraries({"Edwards": "Systems/EST4"}, None) == {}


def test_api_finds_and_serves_datasheets(client, library, monkeypatch):
    import app.routers.design_rules as design_rules_router

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": library})
    login(client, settings.default_admin_email, settings.default_admin_password)

    body = client.get("/design-rules/datasheets", params={"part_no": "4-LCDLE", "manufacturer": "EDWARDS"}).json()
    assert body[0]["filename"] == "03- 4-LCDLE.pdf"
    assert body[0]["current_rows"][1] == {"page": 2, "text": "Alarm/active | 93 mA at 24 Vdc"}

    resp = client.get("/design-rules/datasheets/file", params={"library": "edwards", "path": body[0]["path"]})
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    resp = client.get("/design-rules/datasheets/file", params={"library": "EDWARDS", "path": "../secret.pdf"})
    assert resp.status_code == 404


@pytest.fixture()
def mixed_library(tmp_path) -> DatasheetLibrary:
    """A library filed the way the synced Edwards folder really is: product
    datasheets, the submittal builder alongside them, section dividers, and
    another manufacturer's sheet that carries no Edwards document number."""
    root = tmp_path / "EDWARDS"
    _pdf(root / "01- PANEL" / "01- 4-CPU.pdf",
         [[(72, 60, "D A T A S H E E T E85014-0010"), (72, 90, "Central Processor")]])
    _pdf(root / "01- PANEL" / "00- coverpage.pdf", [[(72, 60, "Panel")]])
    _pdf(root / "12- Battery_Rocket" / "ES 18-12.pdf", [[(72, 60, "ES 18-12 12V 18Ah")]])
    _pdf(root / "submittal builder" / "Trade License" / "licence.pdf", [[(72, 60, "Trade Licence")]])
    _pdf(root / "00- submittal" / "userguide.pdf", [[(72, 60, "EST4 User Guide")]])
    return DatasheetLibrary("EDWARDS", root)


def test_the_listing_leaves_out_what_is_not_a_datasheet(mixed_library):
    paths = [f.path for f in mixed_library.listing()]
    assert paths == ["01- PANEL/01- 4-CPU.pdf", "12- Battery_Rocket/ES 18-12.pdf"]
    # All five PDFs are still indexed and findable; only the listing narrows.
    assert mixed_library.count() == 5


def test_the_listing_reports_a_sheet_without_a_document_number(mixed_library):
    rocket = next(f for f in mixed_library.listing() if f.folder == "12- Battery_Rocket")
    # Another manufacturer's sheet: no Edwards document number, and still the
    # datasheet for those parts, so it is listed and flagged rather than hidden.
    assert rocket.reads_as_datasheet is False
    assert rocket.document_no is None
    assert rocket.filename == "ES 18-12.pdf" and rocket.pages == 1

    cpu = next(f for f in mixed_library.listing() if f.folder == "01- PANEL")
    assert cpu.reads_as_datasheet is True and cpu.document_no == "E85014-0010"


def test_api_lists_the_whole_library(client, mixed_library, monkeypatch):
    import app.routers.design_rules as design_rules_router

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": mixed_library})
    login(client, settings.default_admin_email, settings.default_admin_password)

    rows = client.get("/design-rules/datasheets/all").json()
    assert [r["path"] for r in rows] == ["01- PANEL/01- 4-CPU.pdf", "12- Battery_Rocket/ES 18-12.pdf"]
    assert rows[0]["library"] == "EDWARDS" and rows[0]["folder"] == "01- PANEL"

    # The listed path is what /datasheets/file takes, forward slashes and all.
    served = client.get("/design-rules/datasheets/file",
                        params={"library": "EDWARDS", "path": rows[0]["path"]})
    assert served.status_code == 200 and served.content.startswith(b"%PDF")


@requires_live_archive
def test_live_edwards_library():
    libraries = get_libraries(settings.model_fields["archive_datasheet_libraries"].default, LIVE_ROOT)
    edwards = libraries["EDWARDS"]
    match = edwards.find("4-LCDLE")[0]
    assert match.document_no == "E85014-0007"
    assert any("Standby" in row and "40 mA" in row for _, row in match.current_rows)
    assert any("Alarm/active" in row and "93 mA" in row for _, row in match.current_rows)
    assert edwards.find("4-NET-TP")[0].filename == "06- 4-NET.pdf"
    assert edwards.find("4-AUDTELS")[0].filename == "10- 4-AUDTEL.pdf"
