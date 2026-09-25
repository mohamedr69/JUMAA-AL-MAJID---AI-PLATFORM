"""A zip of the building: every drawing in it, read a floor at a time."""

import io
import zipfile

import pytest
from fastapi import HTTPException

from app.routers.ifc_boq import MAX_MEMBERS, MAX_UNPACKED_BYTES, _drawings_in_a_zip
from tests.test_ifc_platform import _admin, _dxf, _project

DXF = '\n'.join(["0", "SECTION", "2", "ENTITIES", "0", "ENDSEC", "0", "EOF", ""]).encode()   # the least a DXF can be
DWG = b"AC1032" + bytes(64)          # the header a real DWG starts with


def zipped(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_the_drawings_are_taken_and_the_rest_left():
    """A project's archive holds more than drawings, and what a Mac adds
    to a zip is not a floor."""
    found, skipped = _drawings_in_a_zip(zipped([
        ("Level 03.dxf", DXF),
        ("Level 01.dxf", DXF),
        ("Level 02.dwg", DWG),
        ("sub/Level 04.dxf", DXF),
        ("notes.txt", b"not a drawing"),
        ("__MACOSX/._Level 01.dxf", b"resource fork"),
        (".DS_Store", b"junk"),
    ]))
    # Read in the order the floors are named, not the archive's own.
    assert [name for name, _ in found] == ["Level 01.dxf", "Level 02.dwg", "Level 03.dxf", "Level 04.dxf"]
    assert skipped == ["notes.txt", "__MACOSX/._Level 01.dxf", ".DS_Store"]


@pytest.mark.parametrize("path", ["../../evil.dxf", "/etc/evil.dxf", "C:/evil.dxf", "sub/../../evil.dxf",
                                  "\\etc\\evil.dxf", "..\\..\\evil.dxf"])
def test_a_member_that_climbs_out_of_the_folder_is_left_behind(path):
    """An archive is a list of paths someone else wrote. The bad member is
    left out while the real floor beside it is still read -- proving it is
    the path that was refused and not the archive as a whole."""
    found, skipped = _drawings_in_a_zip(zipped([(path, DXF), ("Level 01.dxf", DXF)]))
    assert [name for name, _ in found] == ["Level 01.dxf"]
    # zipfile stores names with forward slashes whatever was handed to it,
    # so the member is compared by that spelling rather than the one written.
    assert skipped == [path.replace("\\", "/")]


def test_an_archive_of_nothing_but_bad_paths_is_refused():
    with pytest.raises(HTTPException) as refused:
        _drawings_in_a_zip(zipped([("../../evil.dxf", DXF)]))
    assert refused.value.status_code == 422


def test_a_renamed_file_is_not_a_dwg():
    """Named .dwg and is not one: the same header check a single upload
    gets, so the converter is not handed something it cannot read. The
    real floor beside it is still read."""
    found, skipped = _drawings_in_a_zip(zipped([("Level 09.dwg", DXF), ("Level 01.dxf", DXF)]))
    assert [name for name, _ in found] == ["Level 01.dxf"]
    assert skipped == ["Level 09.dwg"]


def test_an_archive_that_unpacks_to_more_than_the_limit_is_refused():
    """A zip bomb is small until it is opened, so the declared sizes are
    added up before anything is read -- against the configured limit, the
    same number the message gives (it was once 250 MB in the message and
    500 MB in the check)."""
    big = DXF + b"0" * (60 * 1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for n in range(MAX_UNPACKED_BYTES // len(big) + 1):
            archive.writestr(f"Level {n:02d}.dxf", big)
    payload = buffer.getvalue()
    assert len(payload) < MAX_UNPACKED_BYTES      # small on the wire
    with pytest.raises(HTTPException) as refused:
        _drawings_in_a_zip(payload)
    assert refused.value.status_code == 413
    assert f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB" in refused.value.detail


def test_too_many_files_and_no_files_at_all():
    with pytest.raises(HTTPException) as many:
        _drawings_in_a_zip(zipped([(f"L{n:03d}.dxf", DXF) for n in range(MAX_MEMBERS + 1)]))
    assert str(MAX_MEMBERS) in str(many.value.detail)

    with pytest.raises(HTTPException) as none:
        _drawings_in_a_zip(zipped([("readme.md", b"x")]))
    assert "no DWG or DXF" in str(none.value.detail)

    with pytest.raises(HTTPException) as broken:
        _drawings_in_a_zip(b"this is not a zip")
    assert "not a zip" in str(broken.value.detail)


def test_two_floors_of_the_same_name_are_refused():
    """Only the file name is kept, so a name used twice would quietly
    supersede one floor with another inside a single import."""
    with pytest.raises(HTTPException) as refused:
        _drawings_in_a_zip(zipped([("Tower A/Level 01.dxf", DXF), ("Tower B/Level 01.dxf", DXF)]))
    assert refused.value.status_code == 422
    assert "Level 01.dxf" in str(refused.value.detail)


def test_the_floors_are_counted_not_spelled():
    """Level 2 comes before Level 10, which sorting by name does not do."""
    found, _ = _drawings_in_a_zip(zipped([
        (f"Level {n}.dxf", DXF) for n in (10, 2, 1, 21, 3)
    ]))
    assert [name for name, _ in found] == ["Level 1.dxf", "Level 2.dxf", "Level 3.dxf",
                                          "Level 10.dxf", "Level 21.dxf"]


# --- the whole way through: an archive posted, a drawing per floor ---------------


def test_a_zip_of_floors_becomes_a_drawing_each_and_revises_only_on_evidence(client, monkeypatch, tmp_path):
    """The building arrives as one archive: every floor is a drawing of its
    own at R0, read in counted order. The same archive again changes
    nothing -- the same files are the same drawings. A floor whose name
    states a later revision revises its drawing; one that only has the same
    name is not taken as a revision on a guess: the engineer confirms it."""
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    _admin(client)
    project_id = _project(client, "90210")
    floors = [f'Level {n}.dxf' for n in (10, 2, 1)]
    archive = zipped([(name, _dxf(tmp_path / name).read_bytes()) for name in floors]
                     + [("notes.txt", b"not a drawing")])

    started = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                          files={"file": ("floors.zip", archive, "application/zip")})
    assert started.status_code == 202, started.text
    job = client.get(f"/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded" and job["kind"] == "ifc_read_zip"
    result = job["result"]
    # Counted, not spelled: Level 2 before Level 10.
    assert [r["filename"] for r in result["read"]] == ["Level 1.dxf", "Level 2.dxf", "Level 10.dxf"]
    assert {r["revision"] for r in result["read"]} == {"R0"}
    assert result["failed"] == [] and result["skipped"] == ["notes.txt"]
    assert result["drawings"] == 3 and result["archive"] == "floors.zip"

    # The same archive again: nothing is read twice, nothing is revised.
    again = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                        files={"file": ("floors.zip", archive, "application/zip")})
    same = client.get(f"/jobs/{again.json()['id']}").json()["result"]
    assert same["read"] == [] and sorted(r["filename"] for r in same["unchanged"]) == sorted(floors)
    assert len(client.get(f"/projects/{project_id}/ifc-drawings").json()) == 3

    # New content: "Level 1-R1" states its revision and revises Level 1;
    # "Level 2" under its old name is not guessed to be a revision.
    changed = zipped([("Level 1-R1.dxf", _dxf(tmp_path / "L1R1.dxf", count=5).read_bytes()),
                      ("Level 2.dxf", _dxf(tmp_path / "L2b.dxf", count=6).read_bytes())])
    third = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                        files={"file": ("changed.zip", changed, "application/zip")})
    result = client.get(f"/jobs/{third.json()['id']}").json()["result"]
    assert [(r["filename"], r["revision"]) for r in result["read"]] == [("Level 1-R1.dxf", "R1")]
    assert [r["filename"] for r in result["needs_confirmation"]] == ["Level 2.dxf"]
    listed = {d["filename"]: d for d in client.get(f"/projects/{project_id}/ifc-drawings").json()}
    assert listed["Level 1-R1.dxf"]["current"] and not listed["Level 1.dxf"]["current"]
    assert listed["Level 2.dxf"]["current"] and listed["Level 2.dxf"]["revision"] == "R0"


def test_an_archive_naming_one_floor_twice_is_refused_at_the_door(client, tmp_path):
    """Refused before a job starts, so the user is told rather than left
    with one floor quietly superseded by another."""
    _admin(client)
    project_id = _project(client, "90211")
    drawing = _dxf(tmp_path / "Level 01.dxf").read_bytes()
    archive = zipped([("Tower A/Level 01.dxf", drawing), ("Tower B/Level 01.dxf", drawing)])
    refused = client.post(f"/projects/{project_id}/ifc-drawings/zip/jobs",
                          files={"file": ("towers.zip", archive, "application/zip")})
    assert refused.status_code == 422
    assert "Level 01.dxf" in refused.json()["detail"]
