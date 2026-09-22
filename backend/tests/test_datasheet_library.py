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


def test_a_stored_reference_names_the_sheet(client, mixed_library, monkeypatch, db_session):
    """The listing shows the manufacturer's document number, and an
    engineer's entry beats what was read off the pages. A number is kept
    against the file, not the other way round: one Edwards document covers
    a product family, so two files can hold the same number."""
    import app.routers.design_rules as design_rules_router
    from app.services import datasheet_documents

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": mixed_library})
    login(client, settings.default_admin_email, settings.default_admin_password)

    rocket = "12- Battery_Rocket/ES 18-12.pdf"
    cpu = "01- PANEL/01- 4-CPU.pdf"

    # The Rocket sheet prints no number, so it has none until one is typed in.
    before = {r["path"]: r["reference_no"] for r in client.get("/design-rules/datasheets/all").json()}
    assert before[rocket] is None
    assert before[cpu] == "E85014-0010"  # read from the page-1 header by the index

    datasheet_documents.set_reference(db_session, library="EDWARDS", path=rocket, reference_no="E99999-0001")
    # An engineer's entry stands over the header the index read.
    datasheet_documents.set_reference(db_session, library="EDWARDS", path=cpu, reference_no="E85014-9999")

    after = {r["path"]: r["reference_no"] for r in client.get("/design-rules/datasheets/all").json()}
    assert after[rocket] == "E99999-0001"
    assert after[cpu] == "E85014-9999"

    # The same number may name more than one file, and neither loses it.
    datasheet_documents.set_reference(db_session, library="EDWARDS", path=rocket, reference_no="E85014-9999")
    both = {r["path"]: r["reference_no"] for r in client.get("/design-rules/datasheets/all").json()}
    assert both[rocket] == both[cpu] == "E85014-9999"


def test_the_library_is_grouped_by_system(client, mixed_library, monkeypatch):
    """Each system counts only its own brands' sheets, and a brand whose
    folder is nowhere is still listed -- a supplier the company uses whose
    sheets are not filed yet is not the same as one it does not use. The
    count is the sheets the listing would show, not every PDF in the
    folder, so the card and the table beneath it agree."""
    import app.routers.design_rules as design_rules_router

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": mixed_library})
    login(client, settings.default_admin_email, settings.default_admin_password)

    systems = {s["label"]: s for s in client.get("/design-rules/datasheet-systems").json()}
    assert set(systems) == {"Fire Alarm", "Monitored Self Contained", "Central Battery Systems", "Fire Rated Cables"}

    fire_alarm = systems["Fire Alarm"]
    assert fire_alarm["datasheets"] == len(mixed_library.listing())
    assert [m["name"] for m in fire_alarm["manufacturers"]] == ["EDWARDS"]
    assert fire_alarm["manufacturers"][0]["available"] is True

    # Nothing is filed for the central battery system yet, and Menvier's
    # sheets belong to the self-contained system rather than being counted
    # twice here.
    central = systems["Central Battery Systems"]
    assert central["datasheets"] == 0
    assert all(m["available"] is False for m in central["manufacturers"])
    assert "MENVIER" not in [m["name"] for m in central["manufacturers"]]


def test_the_listing_carries_a_timestamp_and_a_preview(client, mixed_library, monkeypatch):
    """What the card shows beside a sheet: when the file last changed, and
    its first page. Nothing records who added it -- the library is a synced
    folder -- so the page says "updated", not "uploaded by"."""
    import app.routers.design_rules as design_rules_router

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": mixed_library})
    login(client, settings.default_admin_email, settings.default_admin_password)

    rows = client.get("/design-rules/datasheets/all").json()
    assert all(r["modified"] > 0 for r in rows)

    png = client.get("/design-rules/datasheets/thumbnail",
                     params={"library": "EDWARDS", "path": rows[0]["path"]})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[1:4] == b"PNG"

    # The same guard as serving the file itself: nothing outside the library.
    escape = client.get("/design-rules/datasheets/thumbnail",
                        params={"library": "EDWARDS", "path": "../secret.pdf"})
    assert escape.status_code == 404


def test_the_search_index_offers_files_and_linked_parts(client, mixed_library, monkeypatch, db_session):
    """The dropdown is built from both halves of the library.

    A file is offered under its own name. A part number is offered only
    because a link records it -- its sheet is named for something else --
    and without that half the search would under-report what is there.
    """
    import app.routers.design_rules as design_rules_router
    from app.models import PartDatasheetLink

    monkeypatch.setattr(design_rules_router, "_libraries", lambda: {"EDWARDS": mixed_library})
    db_session.add(PartDatasheetLink(manufacturer="EDWARDS", key="4LCDLE", part_no="4-LCDLE",
                                     library="EDWARDS", path="01- PANEL/01- 4-CPU.pdf",
                                     note="Local rail display", source="ordering_table"))
    db_session.commit()
    login(client, settings.default_admin_email, settings.default_admin_password)

    rows = client.get("/design-rules/datasheets/index", params={"manufacturer": "EDWARDS"}).json()
    documents = {r["label"] for r in rows if r["kind"] == "document"}
    parts = {r["label"]: r for r in rows if r["kind"] == "part"}

    # A sheet is offered under its document number, and only falls back to
    # its file name when it prints none -- the Rocket battery sheet here.
    assert documents == {"E85014-0010", "ES 18-12"}
    assert parts["4-LCDLE"]["path"] == "01- PANEL/01- 4-CPU.pdf"
    assert parts["4-LCDLE"]["description"] == "Local rail display"

    # A brand none of the libraries answer to falls back to all of them --
    # `libraries_for`, so an unrecognised BOQ brand searches everything
    # rather than nothing. The page only ever sends a name it was given.
    everything = client.get("/design-rules/datasheets/index", params={"manufacturer": "NOSUCHBRAND"}).json()
    assert {r["label"] for r in everything if r["kind"] == "document"} == documents


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
