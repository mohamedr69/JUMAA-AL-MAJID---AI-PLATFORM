"""The company library: one local folder for everything that is not about a
particular project.

Two things are being pinned here. **Where a document is looked for** -- the
library first, the archive only as a fallback, so a half-migrated machine
works and a migrated one never touches the synced drive. And **that the index
survives a restart**, which is the whole reason the library is local: re-reading
86 datasheets took eleven seconds off OneDrive on every start.
"""

import time
from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings, get_settings
from app.services import company_library
from app.services.datasheet_library import DatasheetLibrary, get_libraries

from .conftest import login


@pytest.fixture()
def library_at(tmp_path, monkeypatch):
    """Point the company library and the archive at folders under tmp_path.

    `get_settings` is cached, so the settings object itself is replaced --
    reading the environment again would not reach the running app.
    """

    def configure(**overrides) -> Settings:
        settings = get_settings().model_copy(
            update={
                "library_root": str(tmp_path / "library"),
                "cache_root": str(tmp_path / "cache"),
                "projects_root": str(tmp_path / "archive"),
                "datasheet_libraries": {},
                "archive_datasheet_libraries": {},
                "archive_submittal_library": "",
                "submittal_library": "submittal",
                "library_rescan_seconds": 0.0,
                **overrides,
            }
        )
        monkeypatch.setattr(company_library, "get_settings", lambda: settings)
        return settings

    return configure


def _pdf(path: Path, lines: list[tuple[float, float, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def _datasheet(folder: Path, part: str) -> Path:
    return _pdf(
        folder / f"{part}.pdf",
        [(72, 60, f"D A T A S H E E T E85014-0007"), (72, 90, f"{part} module"),
         (72, 200, "Standby"), (200, 200, "40 mA at 24 Vdc")],
    )


# --- where a document is looked for ---------------------------------------


def test_a_brand_is_a_folder_in_the_library(tmp_path, library_at):
    library_at()
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")
    _datasheet(tmp_path / "library" / "datasheets" / "MENVIER", "ML-100")

    found = company_library.datasheet_folders()

    # Nothing was configured: dropping the folder in is what adds the brand.
    assert sorted(found) == ["EDWARDS", "MENVIER"]
    assert found["MENVIER"] == tmp_path / "library" / "datasheets" / "MENVIER"


def test_the_library_is_preferred_over_the_archive(tmp_path, library_at):
    library_at(archive_datasheet_libraries={"EDWARDS": "Systems/EST4"})
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")
    _datasheet(tmp_path / "archive" / "Systems" / "EST4", "4-CPU")

    found = company_library.datasheet_folders()

    assert found["EDWARDS"] == tmp_path / "library" / "datasheets" / "EDWARDS"
    assert company_library.source_of(found["EDWARDS"]) == "library"


def test_the_archive_still_answers_for_what_has_not_been_copied_across(tmp_path, library_at):
    """A machine part-way through the move works: the brand it has is read
    locally, the brand it has not is still read off the archive."""
    library_at(archive_datasheet_libraries={"EDWARDS": "Systems/EST4", "ROCKET": "Systems/Rocket"})
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")
    _datasheet(tmp_path / "archive" / "Systems" / "Rocket", "ES65-12")

    found = company_library.datasheet_folders()

    assert company_library.source_of(found["EDWARDS"]) == "library"
    assert company_library.source_of(found["ROCKET"]) == "archive"


def test_a_configured_library_overrides_a_discovered_one(tmp_path, library_at):
    elsewhere = tmp_path / "elsewhere" / "menvier"
    _datasheet(elsewhere, "ML-200")
    library_at(datasheet_libraries={"MENVIER": str(elsewhere)})
    _datasheet(tmp_path / "library" / "datasheets" / "MENVIER", "ML-100")

    assert company_library.datasheet_folders()["MENVIER"] == elsewhere


def test_a_configured_library_that_is_nowhere_is_not_invented(tmp_path, library_at):
    library_at(datasheet_libraries={"MENVIER": "no/such/folder"})
    assert company_library.datasheet_folders() == {}


def test_the_submittal_builder_comes_from_the_library(tmp_path, library_at):
    library_at(archive_submittal_library="Systems/submittal builder")
    _pdf(tmp_path / "library" / "submittal" / "Company Profile" / "profile.pdf", [(72, 60, "Profile")])
    _pdf(tmp_path / "archive" / "Systems" / "submittal builder" / "profile.pdf", [(72, 60, "Profile")])

    assert company_library.submittal_folder() == tmp_path / "library" / "submittal"


def test_the_submittal_builder_falls_back_to_the_archive(tmp_path, library_at):
    library_at(archive_submittal_library="Systems/submittal builder")
    _pdf(tmp_path / "archive" / "Systems" / "submittal builder" / "profile.pdf", [(72, 60, "Profile")])

    folder = company_library.submittal_folder()
    assert folder == tmp_path / "archive" / "Systems" / "submittal builder"
    assert company_library.source_of(folder) == "archive"


def test_nothing_anywhere_is_not_an_error(tmp_path, library_at):
    library_at()
    assert company_library.submittal_folder() is None
    assert company_library.datasheet_folders() == {}


def test_the_scaffold_is_the_documented_structure(tmp_path):
    root = company_library.scaffold(tmp_path / "fresh")

    assert (root / "datasheets").is_dir()
    for folder in company_library.SUBMITTAL_FOLDERS:
        assert (root / "submittal" / folder).is_dir(), folder
    # Running it twice is not an error: it is what a first start does.
    assert company_library.scaffold(root) == root


def test_status_reports_what_is_held_and_where(tmp_path, library_at):
    library_at(archive_datasheet_libraries={"ROCKET": "Systems/Rocket"})
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")
    _datasheet(tmp_path / "archive" / "Systems" / "Rocket", "ES65-12")

    rows = {row.name: row for row in company_library.status()}

    assert rows["Datasheets: EDWARDS"].source == "library"
    assert rows["Datasheets: EDWARDS"].detail == "1 PDF"
    assert rows["Datasheets: ROCKET"].source == "archive"
    assert rows["Submittal builder"].available is False


# --- the index that survives a restart ------------------------------------


def test_the_index_is_reused_after_a_restart(tmp_path):
    """The point of the local library: a second process reads the index off
    disk instead of opening every PDF again."""
    folder = tmp_path / "EDWARDS"
    _datasheet(folder, "4-CPU")
    cache = tmp_path / "cache" / "edwards.json.gz"

    first = DatasheetLibrary("EDWARDS", folder, cache_file=cache)
    assert first.find("4-CPU")[0].filename == "4-CPU.pdf"
    assert cache.is_file()

    # A second library object is a second run of the app over the same files.
    second = DatasheetLibrary("EDWARDS", folder, cache_file=cache)
    opened: list[Path] = []
    original = pymupdf.open

    def watched(*args, **kwargs):
        opened.append(args[0] if args else None)
        return original(*args, **kwargs)

    pymupdf.open = watched
    try:
        assert second.find("4-CPU")[0].filename == "4-CPU.pdf"
    finally:
        pymupdf.open = original
    assert opened == []


def test_a_changed_file_is_read_again_even_though_it_is_cached(tmp_path):
    folder = tmp_path / "EDWARDS"
    _datasheet(folder, "4-CPU")
    cache = tmp_path / "cache" / "edwards.json.gz"
    DatasheetLibrary("EDWARDS", folder, cache_file=cache).find("4-CPU")

    # Same name, different contents: the size changes, so the index does.
    time.sleep(0.01)
    _pdf(folder / "4-CPU.pdf", [(72, 60, "D A T A S H E E T E85014-0007"), (72, 90, "4-CPU and 4-COMREL"),
                                (72, 200, "Standby"), (200, 200, "211 mA at 24 Vdc")])

    assert DatasheetLibrary("EDWARDS", folder, cache_file=cache).find("4-COMREL")


def test_a_corrupt_index_costs_a_re_read_and_nothing_else(tmp_path):
    folder = tmp_path / "EDWARDS"
    _datasheet(folder, "4-CPU")
    cache = tmp_path / "cache" / "edwards.json.gz"
    DatasheetLibrary("EDWARDS", folder, cache_file=cache).find("4-CPU")

    cache.write_bytes(b"not gzip")

    assert DatasheetLibrary("EDWARDS", folder, cache_file=cache).find("4-CPU")[0].filename == "4-CPU.pdf"


def test_an_index_written_for_another_folder_is_ignored(tmp_path):
    """The cache names the folder it describes, so a library moved or renamed
    is re-read rather than answered with another library's files."""
    first, second = tmp_path / "EDWARDS", tmp_path / "MENVIER"
    _datasheet(first, "4-CPU")
    _datasheet(second, "ML-100")
    cache = tmp_path / "cache" / "shared.json.gz"

    DatasheetLibrary("EDWARDS", first, cache_file=cache).find("4-CPU")
    moved = DatasheetLibrary("MENVIER", second, cache_file=cache)

    assert moved.find("4-CPU") == []
    assert moved.find("ML-100")[0].filename == "ML-100.pdf"


def test_the_folder_is_not_walked_again_within_the_rescan_interval(tmp_path):
    """Looking a BOQ's parts up is one `find` per part. Re-walking a synced
    folder every time was the whole cost of the page."""
    folder = tmp_path / "EDWARDS"
    _datasheet(folder, "4-CPU")
    library = DatasheetLibrary("EDWARDS", folder, rescan_seconds=60.0)
    assert library.find("4-CPU")

    _datasheet(folder, "4-NET")
    assert library.find("4-NET") == []          # not yet: the listing is held
    assert library.reindex() == 2               # Reindex is the way to see it
    assert library.find("4-NET")[0].filename == "4-NET.pdf"


def test_an_unreachable_folder_keeps_the_index_it_had(tmp_path):
    """One failed listing -- a disconnected drive, a sync in progress --
    must not empty a library that was working a second ago."""
    folder = tmp_path / "EDWARDS"
    _datasheet(folder, "4-CPU")
    library = DatasheetLibrary("EDWARDS", folder)
    assert library.find("4-CPU")

    def explode(_self, _pattern):
        raise OSError("the drive is not there")

    original = Path.rglob
    Path.rglob = explode
    try:
        assert library.find("4-CPU")[0].filename == "4-CPU.pdf"
    finally:
        Path.rglob = original


# --- what the app sees -----------------------------------------------------


def test_get_libraries_reads_the_company_library(tmp_path, library_at, monkeypatch):
    import app.services.datasheet_library as datasheet_library

    settings = library_at()
    monkeypatch.setattr(datasheet_library, "get_settings", lambda: settings)
    datasheet_library._libraries.clear()
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")

    libraries = get_libraries()

    assert sorted(libraries) == ["EDWARDS"]
    assert libraries["EDWARDS"].find("4-CPU")[0].filename == "4-CPU.pdf"
    # The same object each time: the index it holds is the point of it.
    assert get_libraries()["EDWARDS"] is libraries["EDWARDS"]


def test_the_libraries_page_says_which_are_local_and_which_are_not(client, tmp_path, library_at, monkeypatch):
    import app.routers.design_rules as design_rules_router
    import app.services.datasheet_library as datasheet_library

    settings = library_at(archive_datasheet_libraries={"ROCKET": "Systems/Rocket"})
    monkeypatch.setattr(datasheet_library, "get_settings", lambda: settings)
    monkeypatch.setattr(design_rules_router, "get_settings", lambda: settings)
    datasheet_library._libraries.clear()
    _datasheet(tmp_path / "library" / "datasheets" / "EDWARDS", "4-CPU")
    _datasheet(tmp_path / "archive" / "Systems" / "Rocket", "ES65-12")

    app_settings = get_settings()
    login(client, app_settings.default_admin_email, app_settings.default_admin_password)
    rows = {r["name"]: r for r in client.get("/design-rules/datasheet-libraries").json()}

    assert rows["EDWARDS"]["source"] == "library" and rows["EDWARDS"]["datasheets"] == 1
    assert rows["ROCKET"]["source"] == "archive"
    assert client.post("/design-rules/datasheet-libraries/reindex").status_code == 200


def test_a_scaffolded_but_empty_library_does_not_shadow_the_archive(tmp_path, library_at):
    """`library/submittal` ships scaffolded and empty. Taking it on
    existence alone meant every company document came out of a package
    missing on a machine that had not run scripts/sync_library.py yet."""
    library_at(archive_submittal_library="Systems/submittal builder")
    scaffold = tmp_path / "library" / "submittal"
    (scaffold / "Company Profile").mkdir(parents=True)
    (scaffold / ".gitkeep").write_text("")
    archive = tmp_path / "archive" / "Systems" / "submittal builder"
    (archive / "Company Profile").mkdir(parents=True)
    (archive / "Company Profile" / "Company Profile.pdf").write_bytes(b"%PDF-1.4")

    assert company_library.submittal_folder() == archive


def test_the_library_wins_as_soon_as_it_holds_a_document(tmp_path, library_at):
    library_at(archive_submittal_library="Systems/submittal builder")
    scaffold = tmp_path / "library" / "submittal" / "Company Profile"
    scaffold.mkdir(parents=True)
    (scaffold / "Company Profile.pdf").write_bytes(b"%PDF-1.4")
    archive = tmp_path / "archive" / "Systems" / "submittal builder" / "Company Profile"
    archive.mkdir(parents=True)
    (archive / "Company Profile.pdf").write_bytes(b"%PDF-1.4")

    assert company_library.submittal_folder() == tmp_path / "library" / "submittal"


def test_an_empty_library_with_no_archive_is_still_named(tmp_path, library_at):
    """So the libraries page can say "here, and empty" rather than "nowhere"."""
    library_at()
    (tmp_path / "library" / "submittal").mkdir(parents=True)

    assert company_library.submittal_folder() == tmp_path / "library" / "submittal"


def test_an_empty_shelf_does_not_shadow_the_archive(tmp_path, library_at):
    """The scaffold creates every folder of the structure empty. Taking one
    on existence alone hid the archive copy, and a package came out with no
    company documents and nothing saying why."""
    library_at(archive_submittal_library="Systems/submittal builder")
    company_library.scaffold(tmp_path / "library")
    (tmp_path / "library" / "submittal" / "Company Profile" / ".gitkeep").write_text("")
    _pdf(tmp_path / "archive" / "Systems" / "submittal builder" / "profile.pdf", [(72, 60, "Profile")])

    folder = company_library.submittal_folder()

    assert folder == tmp_path / "archive" / "Systems" / "submittal builder"
    assert company_library.holds_documents(tmp_path / "library" / "submittal") is False


def test_an_empty_brand_folder_is_not_a_library(tmp_path, library_at):
    library_at(archive_datasheet_libraries={"EDWARDS": "Systems/EST4"})
    (tmp_path / "library" / "datasheets" / "EDWARDS").mkdir(parents=True)
    _datasheet(tmp_path / "archive" / "Systems" / "EST4", "4-CPU")

    found = company_library.datasheet_folders()

    assert company_library.source_of(found["EDWARDS"]) == "archive"


def test_the_submittal_builder_is_filed_by_brand(tmp_path):
    """COMMON holds what every package carries; a brand's folder what is the
    manufacturer's; the root itself is the layout before brands. Section
    names are matched without regard to case ("Previous approval")."""
    root = tmp_path / "submittal"
    _pdf(root / "COMMON" / "Company Profile" / "profile.pdf", [(72, 60, "Profile")])
    _pdf(root / "COMMON" / "templates" / "stamp.pdf", [(72, 60, "Stamp")])
    _pdf(root / "EDWARDS" / "Test certificates" / "ul.pdf", [(72, 60, "UL")])
    _pdf(root / "MENVIER" / "test certificate" / "ce.pdf", [(72, 60, "CE")])
    _pdf(root / "MENVIER" / "Previous approval" / "dcd.pdf", [(72, 60, "DCD")])
    _pdf(root / "Trade License" / "licence.pdf", [(72, 60, "Licence")])

    assert company_library.submittal_path(root, "MENVIER", "Test certificates") == root / "MENVIER" / "test certificate"
    assert company_library.submittal_path(root, "EDWARDS", "Test certificates") == root / "EDWARDS" / "Test certificates"
    assert company_library.submittal_path(root, "MENVIER", "Previous Approvals") == root / "MENVIER" / "Previous approval"
    # What the brand does not hold comes from COMMON, then from the root.
    assert company_library.submittal_path(root, "MENVIER", "Company Profile") == root / "COMMON" / "Company Profile"
    assert company_library.submittal_path(root, "MENVIER", "templates/stamp.pdf") == root / "COMMON" / "templates" / "stamp.pdf"
    assert company_library.submittal_path(root, "MENVIER", "Trade License") == root / "Trade License"
    assert company_library.submittal_path(root, None, "Test certificates") is None
    assert company_library.submittal_path(root, "MENVIER", "Country Of Origin") is None
    assert company_library.submittal_brands(root) == ["EDWARDS", "MENVIER"]
