from pathlib import Path

from app.services.ep_resolver import (
    find_design_sheet_candidates,
    find_drf_candidates,
    find_ep_folders,
    resolve_project,
)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake pdf bytes")


# --- find_ep_folders: mirrors the real nesting shapes found in the archive ---


def test_finds_ep_folder_directly_under_root(tmp_path):
    folder = tmp_path / "EP-21152 - Court & Prosecution @ Al Madam, Sharjah"
    folder.mkdir()

    matches = find_ep_folders(tmp_path, "21152")
    assert matches == [folder]


def test_finds_ep_folder_nested_under_client(tmp_path):
    folder = tmp_path / "Samana Developers" / "EP-29495 IVY Garden 2"
    folder.mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "29495")
    assert matches == [folder]


def test_finds_ep_folder_nested_two_levels(tmp_path):
    folder = tmp_path / "Abdul-ELV" / "Al Arabia" / "EP-13480 Some Project"
    folder.mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "13480")
    assert matches == [folder]


def test_prefix_match_does_not_collide_with_longer_number(tmp_path):
    (tmp_path / "EP-294950 - Different Project").mkdir()

    matches = find_ep_folders(tmp_path, "29495")
    assert matches == []


def test_non_numeric_ep_folder_does_not_match_numeric_search(tmp_path):
    (tmp_path / "AG Engineering" / "EP-Meisem 1 Tower").mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "29495")
    assert matches == []


def test_ep_number_not_found_returns_empty(tmp_path):
    (tmp_path / "Some Client" / "EP-11111 Other Project").mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "99999")
    assert matches == []


def test_duplicate_ep_number_across_clients_returns_all(tmp_path):
    a = tmp_path / "Client A" / "EP-30000 Tower A"
    b = tmp_path / "Client B" / "EP-30000 Tower A Copy"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "30000")
    assert sorted(matches) == sorted([a, b])


def test_does_not_descend_into_matched_project_folder(tmp_path):
    project = tmp_path / "Client" / "EP-40000 Project"
    project.mkdir(parents=True)
    # A red herring nested folder that happens to look like another EP -- the
    # resolver should not need to find it, and shouldn't error trying.
    (project / "Drawings" / "EP-40000 Revision 2").mkdir(parents=True)

    matches = find_ep_folders(tmp_path, "40000")
    assert matches == [project]


# --- find_drf_candidates ---


def test_finds_drf_in_scan_document_singular(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    drf = project / "Scan Document" / "EP-29495 DRF.pdf"
    touch(drf)

    matches = find_drf_candidates(project)
    assert [m.path for m in matches] == [drf]


def test_finds_drf_in_scan_documents_plural(tmp_path):
    project = tmp_path / "EP-13705 - Meydan One"
    drf = project / "Scan Documents" / "DRF-2606 EP-13705.pdf"
    touch(drf)

    matches = find_drf_candidates(project)
    assert [m.path for m in matches] == [drf]


def test_ignores_non_drf_files_in_scan_folder(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    touch(project / "Scan Document" / "Image (1).jpg")
    touch(project / "Scan Document" / "EP-29495 DRF.pdf")

    matches = find_drf_candidates(project)
    assert len(matches) == 1
    assert matches[0].path.name == "EP-29495 DRF.pdf"


def test_missing_drf_folder_returns_empty(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    touch(project / "Drawings" / "some.dwg")

    matches = find_drf_candidates(project)
    assert matches == []


def test_finds_drf_in_numbered_scan_folder_variant(tmp_path):
    # EP-30784's real shape: "01- EP-30784 Scan", not "Scan Document(s)".
    project = tmp_path / "EP-30784 - Binghatti Skyblade"
    drf = project / "01- EP-30784 Scan" / "EP-30784 DRF.pdf"
    touch(drf)

    matches = find_drf_candidates(project)
    assert [m.path for m in matches] == [drf]


# --- find_design_sheet_candidates ---


def test_finds_design_sheet_in_commercial_document_folder(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    ds = project / "Commercial Document" / "EP-29495 FAS Design.pdf"
    touch(ds)

    matches = find_design_sheet_candidates(project)
    assert len(matches) == 1
    assert matches[0].path == ds
    assert matches[0].system_guess == "FAS"


def test_finds_design_sheet_in_ep_number_commercial_folder(tmp_path):
    project = tmp_path / "EP-30851 - Rove Home Marasi Drive"
    ds = project / "EP-30851 Commercial" / "EP-30851 FAS Design.pdf"
    touch(ds)

    matches = find_design_sheet_candidates(project)
    assert [m.path for m in matches] == [ds]


def test_finds_multiple_design_sheets_one_per_system(tmp_path):
    project = tmp_path / "EP-28929 - Madar Mall"
    fas = project / "EP-28929 Commercial" / "EP-28929 FAS Design.pdf"
    pava = project / "EP-28929 Commercial" / "Edwards PAVA Design Sheet.pdf"
    touch(fas)
    touch(pava)

    matches = find_design_sheet_candidates(project)
    systems = {m.system_guess for m in matches}
    assert systems == {"FAS", "PAVA"}


def test_missing_design_sheet_folder_returns_empty(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    touch(project / "Drawings" / "some.dwg")

    matches = find_design_sheet_candidates(project)
    assert matches == []


def test_finds_design_sheets_alongside_drf_in_numbered_scan_folder(tmp_path):
    # EP-30784's real shape: DRF and multiple Design Sheets share one
    # "01- EP-30784 Scan" folder, no separate Commercial folder.
    project = tmp_path / "EP-30784 - Binghatti Skyblade"
    scan_folder = project / "01- EP-30784 Scan"
    touch(scan_folder / "EP-30784 DRF.pdf")
    touch(scan_folder / "EP-30784 FAS Design.pdf")
    touch(scan_folder / "EP-30784 EML Design.pdf")

    ds_matches = find_design_sheet_candidates(project)
    systems = {m.system_guess for m in ds_matches}
    assert systems == {"FAS", "EML"}

    drf_matches = find_drf_candidates(project)
    assert len(drf_matches) == 1


# --- resolve_project: end-to-end orchestration ---


def test_resolve_project_happy_path(tmp_path):
    project = tmp_path / "Samana Developers" / "EP-29495 IVY Garden 2"
    touch(project / "Scan Document" / "EP-29495 DRF.pdf")
    touch(project / "Commercial Document" / "EP-29495 FAS Design.pdf")

    result = resolve_project(tmp_path, "29495")

    assert not result.folder_not_found
    assert not result.is_ambiguous
    assert len(result.drf_candidates) == 1
    assert len(result.design_sheet_candidates) == 1
    assert result.warnings == []


def test_resolve_project_folder_not_found(tmp_path):
    result = resolve_project(tmp_path, "99999")
    assert result.folder_not_found
    assert any("No folder found" in w for w in result.warnings)


def test_resolve_project_ambiguous_duplicate(tmp_path):
    touch(tmp_path / "Client A" / "EP-30000 Tower" / "placeholder.txt")
    touch(tmp_path / "Client B" / "EP-30000 Tower" / "placeholder.txt")

    result = resolve_project(tmp_path, "30000")
    assert result.is_ambiguous
    assert any("Multiple folders found" in w for w in result.warnings)


def test_resolve_project_missing_drf_warns_but_still_resolves(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    touch(project / "Commercial Document" / "EP-29495 FAS Design.pdf")

    result = resolve_project(tmp_path, "29495")
    assert not result.folder_not_found
    assert result.drf_candidates == []
    assert any("No DRF found" in w for w in result.warnings)


def test_resolve_project_missing_design_sheet_warns_but_still_resolves(tmp_path):
    project = tmp_path / "EP-29495 IVY Garden 2"
    touch(project / "Scan Document" / "EP-29495 DRF.pdf")

    result = resolve_project(tmp_path, "29495")
    assert not result.folder_not_found
    assert result.design_sheet_candidates == []
    assert any("No Design Sheet found" in w for w in result.warnings)


def test_walk_permission_error_is_collected_not_raised(tmp_path, monkeypatch):
    import os

    from app.services import ep_resolver

    project = tmp_path / "EP-29495 IVY Garden 2"
    project.mkdir()

    real_walk = os.walk

    def flaky_walk(top, onerror=None, **kwargs):
        if onerror:
            onerror(PermissionError(13, "Permission denied", str(top / "Locked")))
        yield from real_walk(top, onerror=onerror, **kwargs)

    monkeypatch.setattr(ep_resolver.os, "walk", flaky_walk)

    errors: list[str] = []
    matches = ep_resolver.find_drf_candidates(project, errors)

    assert matches == []
    assert len(errors) == 1
    assert "Permission denied" in errors[0]
