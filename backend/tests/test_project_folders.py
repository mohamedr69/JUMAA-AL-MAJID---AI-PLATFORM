"""The project's folder structure on OneDrive: made on open, never moving
what is there, and where a built submittal is filed."""

from pathlib import Path

from app.core.config import get_settings
from app.models import Project, ProjectSystem
from app.services import project_folders

from .conftest import login

settings = get_settings()


def _project(ep, root, *, systems=("Fire Alarm",), drawing=True, **kw):
    """A project as the DRF leaves it: its systems, and whether a drawing
    is marked against them."""
    return Project(
        ep_number=ep,
        project_name=kw.pop("name", "Titania"),
        source_folder_path=str(root),
        systems=[ProjectSystem(name=name, drawing=drawing) for name in systems],
        **kw,
    )


def test_the_folders_follow_the_project_systems(tmp_path):
    """A fire-alarm-only job grows no ELS folder. An empty one reads as a
    submittal we owe and have not sent."""
    root = tmp_path / "EP-29387"
    (root / "Commercial Document").mkdir(parents=True)          # the intake's folder already exists
    created = project_folders.ensure(_project("29387", root))

    assert "01- Scan" not in created                            # a scan / commercial folder exists
    assert "02- Material Submittals/FA/R0/Submitted" in created
    assert "02- Material Submittals/FA/R0/Received" in created
    assert not any(c.startswith("02- Material Submittals") and "ELS" in c for c in created), created
    assert "03- Drawings/SD/ELS" not in created   # nor a shop drawing folder for it

    # Both systems, and both get their pair.
    two = tmp_path / "EP-30880"
    (two / "Commercial Document").mkdir(parents=True)
    created = project_folders.ensure(_project("30880", two, systems=("Fire Alarm", "Emergency Light Monitoring")))
    assert "02- Material Submittals/ELS/R0/Submitted" in created
    assert "02- Material Submittals/ELS/R0/Received" in created


def test_drawings_folders_are_made_only_when_the_drawings_are_ours(tmp_path):
    """The DRF marks a drawing per system; a project whose rows all say no
    is one we supply and commission but do not draw."""
    ours = tmp_path / "EP-30880"
    (ours / "Commercial Document").mkdir(parents=True)
    created = project_folders.ensure(_project("30880", ours, drawing=True))
    assert "03- Drawings/IFC/Electrical/Light" in created and "03- Drawings/SD/FA" in created

    theirs = tmp_path / "EP-29387"
    (theirs / "Commercial Document").mkdir(parents=True)
    created = project_folders.ensure(_project("29387", theirs, drawing=False))
    assert not any(c.startswith("03- Drawings") for c in created), created
    assert not (theirs / "03- Drawings").exists()


def test_nothing_that_is_there_is_moved_or_made_twice(tmp_path):
    root = tmp_path / "EP-30880"
    (root / "Commercial Document").mkdir(parents=True)
    # Filed the old way, before Submitted/Received: it stays exactly so.
    (root / "02- Material Submittals" / "FA" / "R0").mkdir(parents=True)
    (root / "02- Material Submittals" / "FA" / "R0" / "old.pdf").write_bytes(b"%PDF")

    project = _project("30880", root)
    project_folders.ensure(project)
    assert (root / "02- Material Submittals" / "FA" / "R0" / "old.pdf").is_file()
    assert project_folders.ensure(project) == []                # nothing missing now


def test_a_project_folder_that_is_not_there(tmp_path):
    # No scan folder at all: one is made.
    bare = tmp_path / "EP-30881"
    bare.mkdir()
    assert "01- Scan" in project_folders.ensure(_project("30881", bare, name="B"))
    # Not reachable: nothing happens.
    assert project_folders.ensure(_project("30882", tmp_path / "nope", name="C")) == []
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
    project_id = client.post("/projects", json={
        "ep_number": "30884", "project_name": "Skyblade", "source_folder_path": str(root),
        "design_sheets": [],
        # The DRF's Systems table: what the folders are made from.
        "systems": [{"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True}],
    }).json()["id"]
    assert (root / "02- Material Submittals" / "FA" / "R0" / "Submitted").is_dir()
    assert (root / "02- Material Submittals" / "FA" / "R0" / "Received").is_dir()
    assert (root / "03- Drawings" / "IFC" / "Mechanical" / "SM").is_dir()
    # Only the system it has: no ELS folder on a fire-alarm-only job.
    assert not (root / "02- Material Submittals" / "ELS").exists()
    # Opening again: the folders stand; nothing to make.
    assert client.get(f"/projects/{project_id}").status_code == 200
    assert (root / "03- Drawings" / "SD" / "FA").is_dir()
