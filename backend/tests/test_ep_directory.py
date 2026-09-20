"""Stage 2-5: scanning the archive, searching the index, and staying fresh."""

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models import EpArchiveFolder, EpArchiveRoot, Project, ProjectStatus, RoleEnum
from app.services import ep_directory
from tests.conftest import login, make_user


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    """A stand-in archive: two clients, one duplicated EP number, and an
    internal folder named after another EP number that must not be indexed
    as a project of its own."""
    root = tmp_path / "SSD FIRE ALARM PROJECTS - Fire Alarm 2021 Projects"
    (root / "Client A" / "EP-29495 IVY Garden 2" / "Scan Document").mkdir(parents=True)
    (root / "Client B" / "EP-29495 IVY Garden 2 (Variation)").mkdir(parents=True)
    (root / "Client A" / "EP-30851 Al Wasl Tower").mkdir(parents=True)
    (root / "EP-13705").mkdir(parents=True)
    # Inside a project folder, not a project of its own.
    (root / "Client A" / "EP-30851 Al Wasl Tower" / "EP-99999 Cabinet Sample").mkdir(parents=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "projects_root", str(root))
    monkeypatch.setattr(settings, "archive_index_enabled", True)
    return root


def scan(db, archive):
    return ep_directory.scan(db, root=archive)


# --- the scan ---------------------------------------------------------


def test_scan_indexes_every_ep_folder_and_never_descends_into_one(db_session, archive):
    result = scan(db_session, archive)
    assert result.complete
    numbers = sorted({row.ep_number for row in db_session.query(EpArchiveFolder)})
    assert numbers == ["13705", "29495", "30851"], "EP-99999 sits inside a project folder"
    assert sorted(result.added) == ["13705", "29495", "29495", "30851"]
    assert db_session.query(EpArchiveFolder).filter_by(ep_number="29495").count() == 2


def test_scan_stores_paths_below_the_root_so_the_index_is_not_one_users(db_session, archive):
    scan(db_session, archive)
    stored = {row.relative_path for row in db_session.query(EpArchiveFolder)}
    assert stored == {
        "Client A/EP-29495 IVY Garden 2",
        "Client B/EP-29495 IVY Garden 2 (Variation)",
        "Client A/EP-30851 Al Wasl Tower",
        "EP-13705",
    }
    assert not any(str(archive) in path for path in stored)


def test_the_same_archive_under_another_profile_reuses_the_index(db_session, archive, tmp_path):
    """The heart of it: a second engineer's OneDrive puts the same library
    at a different absolute path, and must not re-index the whole archive."""
    scan(db_session, archive)
    folders_before = db_session.query(EpArchiveFolder).count()

    other_profile = tmp_path / "another-user" / archive.name
    other_profile.mkdir(parents=True)
    record = ep_directory.get_root(db_session, other_profile)

    assert db_session.query(EpArchiveRoot).count() == 1
    assert record.root_path == str(other_profile), "the path follows the machine"
    assert db_session.query(EpArchiveFolder).count() == folders_before
    found = ep_directory.folders_for(db_session, "30851", other_profile)
    assert found == [other_profile / "Client A" / "EP-30851 Al Wasl Tower"]


def test_rescanning_adds_the_new_folder_and_leaves_the_rest_alone(db_session, archive):
    scan(db_session, archive)
    first_seen = {r.relative_path: r.first_seen_at for r in db_session.query(EpArchiveFolder)}

    (archive / "Client C" / "EP-31725 New Site").mkdir(parents=True)
    result = scan(db_session, archive)

    assert result.added == ["31725"]
    assert result.missing == []
    unchanged = {r.relative_path: r.first_seen_at for r in db_session.query(EpArchiveFolder)}
    assert all(unchanged[path] == when for path, when in first_seen.items())


def test_a_removed_folder_is_kept_as_history_and_stops_being_suggested(db_session, archive):
    scan(db_session, archive)
    for child in (archive / "Client B" / "EP-29495 IVY Garden 2 (Variation)").iterdir():
        child.rmdir()
    (archive / "Client B" / "EP-29495 IVY Garden 2 (Variation)").rmdir()

    result = scan(db_session, archive)

    assert result.missing == ["29495"]
    assert db_session.query(EpArchiveFolder).count() == 4, "the row is kept, not deleted"
    assert ep_directory.folders_for(db_session, "29495", archive) == [
        archive / "Client A" / "EP-29495 IVY Garden 2"
    ]


def test_a_scan_that_read_nothing_concludes_nothing_is_gone(db_session, archive, monkeypatch):
    """A folder is only judged by the directory that holds it. A walk that
    read no directory at all has judged nothing."""
    scan(db_session, archive)

    def half_a_walk(root, progress=None):
        return {}, ["Z:/Client A: The device is not ready"], set()

    monkeypatch.setattr(ep_directory, "_walk_archive", half_a_walk)
    result = scan(db_session, archive)

    assert not result.complete and result.status == "partial"
    assert result.missing == []
    assert db_session.query(EpArchiveFolder).filter_by(is_available=True).count() == 4
    record = ep_directory.get_root(db_session, archive)
    assert record.scan_status == "partial" and "could not be read" in record.last_error


def test_one_unreadable_folder_does_not_hold_the_rest_of_the_index_back(db_session, archive, monkeypatch):
    """The real archive has three folders whose paths are past Windows'
    260-character limit, so every scan trips on them. That must not stop
    the rest of the archive being kept up to date -- nor must it ever be
    read as "these projects are gone"."""
    scan(db_session, archive)
    real_walk = ep_directory._walk_archive

    def walk_with_one_bad_folder(root, progress=None):
        found, errors, walked = real_walk(root, progress)
        # Client B could not be listed this time, so nothing below it was
        # seen -- including EP-29495's second location.
        found = {k: v for k, v in found.items() if not v[2].startswith("Client B/")}
        walked.discard("client b")
        return found, errors + [r"Z:\Client B\...: The system cannot find the path specified"], walked

    monkeypatch.setattr(ep_directory, "_walk_archive", walk_with_one_bad_folder)
    (archive / "Client A" / "EP-30851 Al Wasl Tower").rename(archive / "Client A" / "EP-30851 Al Wasl Tower Renamed")
    result = scan(db_session, archive)

    assert not result.complete
    assert "29495" not in result.missing, "unread is not gone"
    assert "30851" in result.missing, "a folder whose parent WAS read and no longer holds it"
    assert len(ep_directory.folders_for(db_session, "29495", archive)) == 2
    record = ep_directory.get_root(db_session, archive)
    assert record.last_successful_scan_at is not None, "the scan still happened"


def test_a_folder_that_comes_back_is_the_same_row_again(db_session, archive):
    scan(db_session, archive)
    row = db_session.query(EpArchiveFolder).filter_by(ep_number="13705").one()
    row.is_available = False
    db_session.commit()
    row_id = row.id

    result = scan(db_session, archive)

    assert result.returned == ["13705"]
    db_session.expire_all()
    assert db_session.get(EpArchiveFolder, row_id).is_available is True


# --- the search box ---------------------------------------------------


def test_search_suggests_while_the_number_is_still_being_typed(db_session, archive):
    scan(db_session, archive)
    hits = ep_directory.search(db_session, "294", root=archive)
    assert [h.ep_number for h in hits] == ["29495"]
    assert hits[0].project_name == "IVY Garden 2"
    assert hits[0].locations == 2, "one line per number, whatever it is filed under"
    assert hits[0].project_id is None


@pytest.mark.parametrize("typed", ["EP-294", "ep 294", "EP294", " 294 "])
def test_search_reads_the_ep_prefix_the_way_it_is_typed(db_session, archive, typed):
    scan(db_session, archive)
    assert [h.ep_number for h in ep_directory.search(db_session, typed, root=archive)] == ["29495"]


def test_search_finds_a_project_by_its_name(db_session, archive):
    scan(db_session, archive)
    assert [h.ep_number for h in ep_directory.search(db_session, "wasl", root=archive)] == ["30851"]


def test_search_says_when_the_platform_already_has_the_project(db_session, archive):
    scan(db_session, archive)
    user = make_user(db_session, "dm@example.com", RoleEnum.design_manager)
    project = Project(ep_number="30851", project_name="Al Wasl Tower Phase 2",
                      status=ProjectStatus.active, created_by_id=user.id)
    db_session.add(project)
    db_session.commit()

    hit = ep_directory.search(db_session, "30851", root=archive)[0]
    assert hit.project_id == project.id and hit.project_status == "active"
    assert hit.project_name == "Al Wasl Tower Phase 2", "the DRF's name beats the folder's"


def test_search_ignores_a_query_too_short_to_narrow_anything(db_session, archive):
    scan(db_session, archive)
    assert ep_directory.search(db_session, "2", root=archive) == []
    assert ep_directory.search(db_session, "%", root=archive) == []


def test_search_puts_the_exact_number_first(db_session, archive):
    (archive / "Client D" / "EP-2949 Older").mkdir(parents=True)
    scan(db_session, archive)
    assert [h.ep_number for h in ep_directory.search(db_session, "2949", root=archive)] == ["2949", "29495"]


# --- the API ----------------------------------------------------------


def test_the_search_endpoint_serves_suggestions_to_anyone_signed_in(client, db_session, archive):
    scan(db_session, archive)
    make_user(db_session, "viewer@example.com", RoleEnum.viewer)
    login(client, "viewer@example.com")

    body = client.get("/archive/search", params={"q": "294"}).json()
    assert [row["ep_number"] for row in body] == ["29495"]
    assert body[0]["project_name"] == "IVY Garden 2"
    assert body[0]["relative_path"] == "Client A/EP-29495 IVY Garden 2"
    assert str(archive) not in body[0]["relative_path"]


def test_the_status_endpoint_reports_what_is_indexed(client, db_session, archive):
    scan(db_session, archive)
    make_user(db_session, "eng@example.com", RoleEnum.design_engineer)
    login(client, "eng@example.com")

    body = client.get("/archive/status").json()
    assert body["searchable"] is True
    assert body["projects"] == 3 and body["folders"] == 4
    assert body["scan_status"] == "ready" and body["last_successful_scan_at"]


def test_only_the_project_roles_may_rebuild_the_index(client, db_session, archive):
    make_user(db_session, "viewer@example.com", RoleEnum.viewer)
    login(client, "viewer@example.com")
    assert client.post("/archive/scan").status_code == 403


def test_a_search_with_nothing_indexed_suggests_nothing_rather_than_failing(client, db_session, archive):
    make_user(db_session, "eng@example.com", RoleEnum.design_engineer)
    login(client, "eng@example.com")
    assert client.get("/archive/search", params={"q": "294"}).json() == []
    assert client.get("/archive/status").json()["searchable"] is False


# --- finding a project without walking the archive --------------------


def test_find_project_answers_from_the_index_without_walking(client, db_session, archive, monkeypatch):
    scan(db_session, archive)
    make_user(db_session, "eng@example.com", RoleEnum.design_engineer)
    login(client, "eng@example.com")

    def refuse(*args, **kwargs):
        raise AssertionError("the archive was walked for a number the index holds")

    monkeypatch.setattr("app.services.ep_resolver.find_ep_folders", refuse)
    body = client.post("/projects/resolve", json={"ep_number": "30851"}).json()

    assert body["folder_found"] is True
    assert body["source_folder"] == str(archive / "Client A" / "EP-30851 Al Wasl Tower")


def test_a_number_added_since_the_last_scan_is_still_found_and_then_indexed(client, db_session, archive):
    scan(db_session, archive)
    make_user(db_session, "eng@example.com", RoleEnum.design_engineer)
    login(client, "eng@example.com")
    (archive / "Client C" / "EP-31725 New Site").mkdir(parents=True)

    body = client.post("/projects/resolve", json={"ep_number": "31725"}).json()

    assert body["folder_found"] is True
    db_session.expire_all()
    assert ep_directory.folders_for(db_session, "31725", archive) == [
        archive / "Client C" / "EP-31725 New Site"
    ], "the walk it cost is the last one anyone pays for"


def test_duplicate_ep_numbers_still_reach_the_folder_picker(client, db_session, archive):
    scan(db_session, archive)
    make_user(db_session, "eng@example.com", RoleEnum.design_engineer)
    login(client, "eng@example.com")

    body = client.post("/projects/resolve", json={"ep_number": "29495"}).json()
    assert body["is_ambiguous"] is True
    assert len(body["matched_folders"]) == 2
    assert body["source_folder"] is None


# --- staying fresh ----------------------------------------------------


def test_the_refresh_is_due_only_once_the_last_scan_is_old_enough(db_session, archive):
    assert ep_directory.due_for_refresh(db_session, 30, archive) is True
    scan(db_session, archive)
    assert ep_directory.due_for_refresh(db_session, 30, archive) is False
    record = ep_directory.get_root(db_session, archive)
    record.scan_finished_at = record.scan_finished_at.replace(year=2020)
    db_session.commit()
    assert ep_directory.due_for_refresh(db_session, 30, archive) is True


def test_a_scan_that_trips_on_a_folder_still_paces_the_next_one(db_session, archive, monkeypatch):
    """Otherwise the archive with the three unopenable folders is walked
    again every minute, for ever."""
    def always_trips(root, progress=None):
        return {}, ["Z:/somewhere: The system cannot find the path specified"], set()

    monkeypatch.setattr(ep_directory, "_walk_archive", always_trips)
    scan(db_session, archive)
    assert ep_directory.due_for_refresh(db_session, 30, archive) is False


def test_a_dry_run_names_the_new_numbers_and_writes_nothing(db_session, archive):
    scan(db_session, archive)
    (archive / "Client C" / "EP-31725 New Site").mkdir(parents=True)

    found, errors, new_numbers = ep_directory.preview_scan(db_session, archive)

    assert new_numbers == {"31725"} and not errors and len(found) == 5
    assert ep_directory.folders_for(db_session, "31725", archive) == []


def test_an_unconfigured_archive_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "projects_root", None)
    with pytest.raises(ep_directory.ArchiveUnavailable, match="not configured"):
        ep_directory.archive_root()
    monkeypatch.setattr(get_settings(), "projects_root", str(Path("Z:/no/such/archive")))
    with pytest.raises(ep_directory.ArchiveUnavailable, match="not reachable"):
        ep_directory.archive_root()
