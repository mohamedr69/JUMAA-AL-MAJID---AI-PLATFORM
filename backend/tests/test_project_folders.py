"""The project's folder structure on OneDrive: made on open, never moving
what is there, and where a built submittal is filed."""

from pathlib import Path

from app.core.config import get_settings
from app.models import Project
from app.services import project_folders

from .conftest import login

settings = get_settings()


def test_the_structure_is_made_once_and_only_what_is_missing(tmp_path):
    root = tmp_path / "EP-30880"
    (root / "Commercial Document").mkdir(parents=True)          # the intake's folder already exists
    (root / "02- Material Submittals" / "FA" / "R0").mkdir(parents=True)
    (root / "02- Material Submittals" / "FA" / "R0" / "old.pdf").write_bytes(b"%PDF")
    project = Project(ep_number="30880", project_name="Titania", source_folder_path=str(root))

    created = project_folders.ensure(project)
    assert "01- Scan" not in created                              # a scan / commercial folder exists
    assert "02- Material Submittals/FA/R0" not in created           # was there
    assert "02- Material Submittals/ELS/R0" in created
    assert "03- Drawings/IFC/Electrical/Light" in created and "03- Drawings/IFC/Builder Work" in created
    assert "03- Drawings/SD/ELS" in created and "03- Drawings/SD/Approved" in created
    assert (root / "02- Material Submittals" / "FA" / "R0" / "old.pdf").is_file()   # nothing moved
    for relative in project_folders.STRUCTURE:
        assert (root / relative).is_dir(), relative

    assert project_folders.ensure(project) == []                  # nothing missing now

    # No scan folder at all: one is made.
    bare = tmp_path / "EP-30881"
    bare.mkdir()
    assert "01- Scan" in project_folders.ensure(Project(ep_number="30881", project_name="B", source_folder_path=str(bare)))
    # Not reachable: nothing happens.
    assert project_folders.ensure(Project(ep_number="30882", project_name="C", source_folder_path=str(tmp_path / "nope"))) == []
    assert project_folders.ensure(Project(ep_number="30883", project_name="D", source_folder_path=None)) == []


def test_where_a_submittal_is_filed():
    project = Project(ep_number="30880", project_name="Titania", source_folder_path=None)
    assert project_folders.system_folder("FAS") == "FA" and project_folders.system_folder("FA") == "FA"
    assert project_folders.system_folder("EML") == "ELS" and project_folders.system_folder("CBS") == "ELS"
    assert project_folders.system_folder("PAVA") is None
    assert project_folders.submittal_folder(project, "FAS", "R1") is None


def test_opening_a_project_makes_its_folders(client, tmp_path):
    root = tmp_path / "EP-30884"
    root.mkdir()
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={"ep_number": "30884", "project_name": "Skyblade",
                                               "source_folder_path": str(root), "design_sheets": []}).json()["id"]
    assert (root / "02- Material Submittals" / "FA" / "R0").is_dir()
    assert (root / "03- Drawings" / "IFC" / "Mechanical" / "SM").is_dir()
    # Opening again: the folders stand; nothing to make.
    assert client.get(f"/projects/{project_id}").status_code == 200
    assert (root / "03- Drawings" / "SD" / "FA").is_dir()
