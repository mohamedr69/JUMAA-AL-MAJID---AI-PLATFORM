from app.services.project_directory import find_drawings


def test_directory_categories_systems_and_refresh(tmp_path):
    for name in ["FAS/Material Submittals/panel.pdf", "CCTV/Drawings/layout.pdf", "VES/plan.dwg", "Submittals/unassigned.docx", "misc.pdf"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    drawings, warnings = find_drawings(tmp_path, {"CCTV"})
    assert not warnings
    assert {(item.name, item.system_code) for item in drawings} == {("layout.pdf", "CCTV"), ("plan.dwg", "VES")}
    materials, warnings = find_drawings(tmp_path, {"CCTV"}, material=True)
    assert not warnings
    assert {(item.name, item.system_code) for item in materials} == {("panel.pdf", "FAS"), ("unassigned.docx", None)}
    assert all(item.modified.tzinfo is not None for item in materials)
    (tmp_path / "FAS/Material Submittals/panel.pdf").unlink()
    materials, _ = find_drawings(tmp_path, set(), material=True)
    assert [item.name for item in materials] == ["unassigned.docx"]


def test_integrated_system_and_cable_directory_codes(tmp_path):
    for code in ["VE", "FT", "FRC", "EML"]:
        folder = tmp_path / code / "Material Submittals"
        folder.mkdir(parents=True)
        (folder / "submission.pdf").write_text("fixture")
    materials, warnings = find_drawings(tmp_path, set(), material=True)
    assert not warnings
    assert {item.system_code for item in materials} == {"VE", "FT", "FRC", "EML"}


def test_samples_are_separate_from_materials(tmp_path):
    folder = tmp_path / "FAS" / "Material Submittals" / "Samples"
    folder.mkdir(parents=True)
    (folder / "sample-R0.pdf").write_text("fixture")
    materials, _ = find_drawings(tmp_path, set(), material=True)
    samples, _ = find_drawings(tmp_path, set(), sample=True)
    assert not materials
    assert [item.name for item in samples] == ["sample-R0.pdf"]


def test_log_files_stay_in_project_directory(client, db_session, tmp_path):
    from app.models import Project, User
    from app.core.config import get_settings
    from .conftest import login
    root = tmp_path / "project"
    root.mkdir()
    (root / "drawing.pdf").write_bytes(b"%PDF-1.4")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private")
    project = Project(ep_number="99991", source_folder_path=str(root), created_by_id=db_session.query(User).first().id)
    db_session.add(project)
    db_session.commit()
    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)
    url = f"/projects/{project.id}/logs/file"
    assert client.get(url, params={"path": "drawing.pdf"}).status_code == 200
    assert client.get(url, params={"path": "../outside.pdf"}).status_code == 403
    assert client.get(url, params={"path": str(outside)}).status_code == 403
    assert client.get(url, params={"path": "missing.pdf"}).status_code == 404
