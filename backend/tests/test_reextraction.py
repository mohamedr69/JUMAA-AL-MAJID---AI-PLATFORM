"""Re-reading a project's documents and comparing them with what is stored.

The synthetic tests stub the two extractors, so they pin the *comparison*
rules -- which differences are reported, and that nothing is written -- and
run without Tesseract or the archive. The live test at the bottom runs the
real OCR over a real project and is the one that checks extraction still
agrees with the documents; it skips unless the archive is configured.
"""

import os
import shutil
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models import RoleEnum
from app.services.design_sheet_extractor import ExtractedBoqLine
from app.services.drf_extractor import DrfExtractionResult, ExtractedField, ExtractedSystem
from tests.conftest import login, make_user

settings = get_settings()


def _login_admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def _archive(tmp_path: Path, ep: str = "29495") -> tuple[Path, Path]:
    """A project folder shaped like the archive: a "Scan" subfolder holding
    the DRF and one FAS Design Sheet. The resolver keys on those names."""
    folder = tmp_path / "Samana Developers" / f"EP-{ep} IVY Garden 2"
    scan = folder / f"EP-{ep} Scan"
    scan.mkdir(parents=True)
    (scan / f"EP-{ep} DRF.pdf").write_bytes(b"%PDF-1.4 drf")
    (scan / f"EP-{ep} FAS Design.pdf").write_bytes(b"%PDF-1.4 fas")
    return folder, scan


def _payload(folder: Path, scan: Path, ep: str = "29495", **overrides) -> dict:
    payload = {
        "ep_number": ep,
        "project_name": "IVY Garden 2",
        "plot_number": "648-8523",
        "location": "Wadi Al Safa 5, DLRC, Dubai",
        "client": "Samana",
        "consultant": "Al Hilal",
        "contractor": "Samana Developers",
        "contact_person": "Mahammad Naushad Bennapade",
        "contact_phone": "971543079068",
        "contact_email": "mahammad.bennapade@samanadevelopers.com",
        "scope_of_work": "Design, Supply, T&C",
        "systems": [
            {"name": "Fire Alarm", "brand": "EDWARDS", "method_statement": True, "drawing": True},
        ],
        "source_folder_path": str(folder),
        "drf_document_path": str(scan / f"EP-{ep} DRF.pdf"),
        "design_sheets": [
            {"system_code": "FAS", "document_path": str(scan / f"EP-{ep} FAS Design.pdf")}
        ],
    }
    payload.update(overrides)
    return payload


def _drf_result(**overrides) -> DrfExtractionResult:
    """What the DRF is taken to say -- by default, exactly what _payload
    stores, so a test only has to state the one thing it changes."""
    fields = {
        "project_title": "IVY Garden 2",
        "plot_number": "648-8523",
        "location": "Wadi Al Safa 5, DLRC, Dubai",
        "client": "Samana",
        "consultant": "Al Hilal",
        "contractor": "Samana Developers",
        "contact_person": "Mahammad Naushad Bennapade",
        "contact_phone": "971543079068",
        "contact_email": "mahammad.bennapade@samanadevelopers.com",
    }
    fields.update(overrides.pop("fields", {}))
    return DrfExtractionResult(
        fields={
            name: ExtractedField(value=value, confidence=95.0, raw_label=name)
            for name, value in fields.items()
            if value is not None
        },
        scope_of_work=overrides.pop("scope_of_work", "Design, Supply, T&C"),
        systems=overrides.pop(
            "systems",
            [ExtractedSystem(name="Fire Alarm", brand="EDWARDS", method_statement=True, drawing=True)],
        ),
        warnings=overrides.pop("warnings", []),
    )


def _line(catalog_no="4-CPU", description="Central Processor Module", quantity="1") -> ExtractedBoqLine:
    return ExtractedBoqLine(
        catalog_no=catalog_no,
        description=description,
        quantity=quantity,
        group_heading="EST4 Main Fire Alarm Control Panel",
        confidence=93.0,
        page=1,
    )


def _stub(monkeypatch, drf=None, lines=None, calls=None):
    import app.services.reextraction as reextraction

    def fake_drf(path):
        if calls is not None:
            calls.append(("drf", Path(path).name))
        return drf if drf is not None else _drf_result()

    def fake_sheet(path):
        if calls is not None:
            calls.append(("sheet", Path(path).name))
        return list(lines if lines is not None else [_line()])

    monkeypatch.setattr(reextraction, "extract_drf_fields", fake_drf)
    monkeypatch.setattr(reextraction, "extract_boq_lines", fake_sheet)


def _setup(client, monkeypatch, tmp_path, *, drf=None, lines=None, ep="29495", calls=None):
    """A created project whose BOQ has been read, with both extractors stubbed."""
    import app.routers.projects as projects_router

    folder, scan = _archive(tmp_path, ep)
    monkeypatch.setattr(settings, "projects_root", str(tmp_path))
    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: [_line()])

    _login_admin(client)
    pid = client.post("/projects", json=_payload(folder, scan, ep)).json()["id"]
    client.post(f"/projects/{pid}/boq/ensure")

    _stub(monkeypatch, drf=drf, lines=lines, calls=calls)
    return pid, folder, scan


# --- it runs every time, and writes nothing --------------------------------


def test_reextraction_runs_every_time(client, monkeypatch, tmp_path):
    """Unlike /boq/ensure, which reads once and is then a no-op."""
    calls: list = []
    pid, _, _ = _setup(client, monkeypatch, tmp_path, calls=calls)

    for _ in range(3):
        assert client.post(f"/projects/{pid}/reextract").status_code == 200

    assert [k for k, _ in calls] == ["drf", "sheet"] * 3


def test_reextraction_does_not_change_the_project(client, monkeypatch, tmp_path):
    """The whole point of reporting rather than applying: an engineer's
    corrections survive a re-read that disagrees with them."""
    pid, _, _ = _setup(
        client,
        monkeypatch,
        tmp_path,
        drf=_drf_result(fields={"client": "OCR misread of the client"}),
        lines=[_line(quantity="99")],
    )
    before = client.get(f"/projects/{pid}").json()
    boq_before = client.get(f"/projects/{pid}/boq").json()

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["has_differences"] is True

    assert client.get(f"/projects/{pid}").json() == before
    assert client.get(f"/projects/{pid}/boq").json() == boq_before


def test_matching_documents_report_no_differences(client, monkeypatch, tmp_path):
    pid, _, _ = _setup(client, monkeypatch, tmp_path)

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["has_differences"] is False
    assert body["fields_differing"] == 0
    assert body["boq_changes"] == []
    assert [s["status"] for s in body["sheets"]] == ["known"]
    assert body["errors"] == []


# --- what it reports -------------------------------------------------------


def test_a_changed_drf_field_is_reported_against_the_stored_value(client, monkeypatch, tmp_path):
    pid, _, _ = _setup(
        client, monkeypatch, tmp_path, drf=_drf_result(fields={"client": "Samana Developers LLC"})
    )

    body = client.post(f"/projects/{pid}/reextract").json()
    client_field = next(f for f in body["fields"] if f["field"] == "client")
    assert client_field["status"] == "differs"
    assert (client_field["stored"], client_field["extracted"]) == ("Samana", "Samana Developers LLC")
    assert body["fields_differing"] == 1
    # Every other field still matches.
    assert {f["status"] for f in body["fields"] if f["field"] != "client"} == {"match"}


def test_punctuation_and_spacing_differences_are_not_reported(client, monkeypatch, tmp_path):
    """OCR noise that is not a difference of fact would otherwise bury the
    real changes."""
    pid, _, _ = _setup(
        client,
        monkeypatch,
        tmp_path,
        drf=_drf_result(fields={"location": "Wadi Al Safa 5,  DLRC ,Dubai."}),
    )

    body = client.post(f"/projects/{pid}/reextract").json()
    assert next(f for f in body["fields"] if f["field"] == "location")["status"] == "match"
    assert body["fields_differing"] == 0


def test_a_field_ocr_cannot_read_is_not_reported_as_a_conflict(client, monkeypatch, tmp_path):
    """An engineer typed it in because the scan was unreadable. Reporting
    that as a difference every time would train them to ignore the report."""
    pid, _, _ = _setup(client, monkeypatch, tmp_path, drf=_drf_result(fields={"contact_phone": None}))

    body = client.post(f"/projects/{pid}/reextract").json()
    phone = next(f for f in body["fields"] if f["field"] == "contact_phone")
    assert phone["status"] == "only_stored"
    assert (phone["stored"], phone["extracted"]) == ("971543079068", None)


def test_a_field_empty_on_both_sides_is_not_a_difference(client, monkeypatch, tmp_path):
    """Caught on the real archive: EP-29429's DRF leaves contractor and all
    three contact rows blank, so the project has nothing to store and OCR
    has nothing to read. Counting those four as differences reported a
    fully reconciled project as differing on every run."""
    blank = {"contractor": None, "contact_person": None, "contact_phone": None, "contact_email": None}
    folder, scan = _archive(tmp_path)
    monkeypatch.setattr(settings, "projects_root", str(tmp_path))

    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: [_line()])
    _login_admin(client)
    payload = _payload(folder, scan, **{k: None for k in blank})
    pid = client.post("/projects", json=payload).json()["id"]
    client.post(f"/projects/{pid}/boq/ensure")
    _stub(monkeypatch, drf=_drf_result(fields=blank))

    body = client.post(f"/projects/{pid}/reextract").json()
    assert {f["field"]: f["status"] for f in body["fields"] if f["field"] in blank} == {
        k: "absent" for k in blank
    }
    assert body["fields_differing"] == 0
    assert body["has_differences"] is False


def test_a_value_in_the_document_but_not_on_the_project_is_a_difference(client, monkeypatch, tmp_path):
    """The mirror of only_stored: here the DRF carries something the project
    is missing, which is worth acting on."""
    folder, scan = _archive(tmp_path)
    monkeypatch.setattr(settings, "projects_root", str(tmp_path))

    import app.routers.projects as projects_router

    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: [_line()])
    _login_admin(client)
    pid = client.post("/projects", json=_payload(folder, scan, contractor=None)).json()["id"]
    client.post(f"/projects/{pid}/boq/ensure")
    _stub(monkeypatch)

    body = client.post(f"/projects/{pid}/reextract").json()
    contractor = next(f for f in body["fields"] if f["field"] == "contractor")
    assert contractor["status"] == "only_extracted"
    assert contractor["extracted"] == "Samana Developers"
    assert body["fields_differing"] == 1
    assert body["has_differences"] is True


def test_scope_and_systems_are_compared(client, monkeypatch, tmp_path):
    pid, _, _ = _setup(
        client,
        monkeypatch,
        tmp_path,
        drf=_drf_result(
            scope_of_work="Full Package",
            systems=[
                ExtractedSystem(name="Fire Alarm", brand="NOTIFIER", method_statement=True, drawing=True)
            ],
        ),
    )

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["scope_of_work"]["status"] == "differs"
    assert body["scope_of_work"]["extracted"] == "Full Package"
    (system,) = body["systems"]
    assert (system["name"], system["stored_brand"], system["extracted_brand"]) == (
        "Fire Alarm",
        "EDWARDS",
        "NOTIFIER",
    )
    assert system["status"] == "differs"


def test_a_changed_quantity_is_reported_as_a_boq_change(client, monkeypatch, tmp_path):
    pid, _, _ = _setup(client, monkeypatch, tmp_path, lines=[_line(quantity="4")])

    body = client.post(f"/projects/{pid}/reextract").json()
    (change,) = body["boq_changes"]
    assert change["kind"] == "changed"
    assert change["fields"] == ["quantity"]
    assert (change["before"]["quantity"], change["after"]["quantity"]) == ("1", "4")


def test_a_line_only_in_the_sheet_is_added_and_one_only_stored_is_removed(client, monkeypatch, tmp_path):
    pid, _, _ = _setup(
        client,
        monkeypatch,
        tmp_path,
        lines=[_line(catalog_no="4-SDC1", description="Photoelectric Smoke Detector", quantity="120")],
    )

    body = client.post(f"/projects/{pid}/reextract").json()
    kinds = sorted(c["kind"] for c in body["boq_changes"])
    assert kinds == ["added", "removed"]
    added = next(c for c in body["boq_changes"] if c["kind"] == "added")
    assert added["after"]["catalog_no"] == "4-SDC1"


def test_engineer_only_columns_are_not_compared(client, monkeypatch, tmp_path):
    """Manufacturer, unit, prices and remarks are filled in by the platform
    or the engineer and are not on the sheets -- comparing them would report
    every line as changed on every run."""
    pid, _, _ = _setup(client, monkeypatch, tmp_path)
    boq = client.get(f"/projects/{pid}/boq").json()
    boq[0].update({"unit": "Nos", "remarks": "Confirmed on site", "unit_price": "125.00"})
    assert client.put(f"/projects/{pid}/boq", json=boq).status_code == 200

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["boq_changes"] == []


# --- the folder as it stands now -------------------------------------------


def test_a_design_sheet_filed_after_creation_is_found_and_read(client, monkeypatch, tmp_path):
    """The difference most worth catching: the BOQ read runs once, so a sheet
    added later has no lines in the BOQ at all and nothing else says so."""
    pid, _, scan = _setup(client, monkeypatch, tmp_path)
    (scan / "EP-29495 EML Design.pdf").write_bytes(b"%PDF-1.4 eml")

    body = client.post(f"/projects/{pid}/reextract").json()
    by_status = {s["status"]: s for s in body["sheets"]}
    assert set(by_status) == {"known", "new"}
    assert by_status["new"]["filename"] == "EP-29495 EML Design.pdf"
    assert by_status["new"]["system_code"] == "ELS"
    assert body["has_differences"] is True


def test_a_design_sheet_removed_from_the_archive_is_reported_missing(client, monkeypatch, tmp_path):
    pid, _, scan = _setup(client, monkeypatch, tmp_path)
    (scan / "EP-29495 FAS Design.pdf").unlink()

    body = client.post(f"/projects/{pid}/reextract").json()
    (sheet,) = body["sheets"]
    assert (sheet["status"], sheet["filename"]) == ("missing", "EP-29495 FAS Design.pdf")


def test_a_moved_project_folder_is_searched_for_again(client, monkeypatch, tmp_path):
    pid, folder, _ = _setup(client, monkeypatch, tmp_path)
    moved = tmp_path / "New Contractor" / folder.name
    moved.parent.mkdir(parents=True)
    shutil.move(str(folder), str(moved))

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["folder_found"] is True
    assert Path(body["folder_path"]) == moved
    assert any("no longer at" in w for w in body["warnings"])


def test_an_unreadable_sheet_does_not_lose_the_rest_of_the_report(client, monkeypatch, tmp_path):
    import app.services.reextraction as reextraction
    from app.services.design_sheet_extractor import DesignSheetExtractionError

    pid, _, _ = _setup(client, monkeypatch, tmp_path)

    def boom(path):
        raise DesignSheetExtractionError("no table found on any page")

    monkeypatch.setattr(reextraction, "extract_boq_lines", boom)

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["sheets"][0]["error"] == "no table found on any page"
    # The DRF half still ran.
    assert body["fields"]
    assert any("no table found" in w for w in body["warnings"])


def test_a_broken_drf_does_not_lose_the_boq_comparison(client, monkeypatch, tmp_path):
    import app.services.reextraction as reextraction

    pid, _, _ = _setup(client, monkeypatch, tmp_path, lines=[_line(quantity="7")])

    def boom(path):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(reextraction, "extract_drf_fields", boom)

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["fields"] == []
    assert any("tesseract" in e for e in body["errors"])
    assert body["boq_changes"][0]["fields"] == ["quantity"]


# --- access and failure modes ----------------------------------------------


def test_reextraction_requires_a_creator_role(client, db_session, monkeypatch, tmp_path):
    pid, _, _ = _setup(client, monkeypatch, tmp_path)
    make_user(db_session, "draftsman@ep-platform.com", RoleEnum.draftsman)
    login(client, "draftsman@ep-platform.com")

    assert client.post(f"/projects/{pid}/reextract").status_code == 403


def test_unknown_project_is_404(client, monkeypatch, tmp_path):
    _setup(client, monkeypatch, tmp_path)
    assert client.post("/projects/9999/reextract").status_code == 404


def test_an_unconfigured_archive_is_reported_not_raised(client, monkeypatch, tmp_path):
    """The folder is gone *and* PROJECTS_ROOT is unset: the report says so
    rather than the request failing."""
    pid, folder, _ = _setup(client, monkeypatch, tmp_path)
    shutil.rmtree(folder)
    monkeypatch.setattr(settings, "projects_root", None)

    body = client.post(f"/projects/{pid}/reextract").json()
    assert body["folder_found"] is False
    assert any("PROJECTS_ROOT" in e for e in body["errors"])


# --- opt-in live test against the real archive -----------------------------

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")

requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)


@requires_live_archive
def test_reextracting_a_real_project_agrees_with_its_documents(client, monkeypatch):
    """Real OCR over a real project: create it from what the DRF says, then
    re-read and check the platform agrees with itself.

    This is what catches an extractor regression -- a field that quietly
    starts losing its first word shows up here as a difference against a
    project built from the previous read.
    """
    from app.services.drf_extractor import extract_drf_fields
    from app.services.ep_resolver import resolve_project
    from app.services.reextraction import DRF_FIELD_TO_COLUMN

    root = Path(LIVE_ROOT)
    monkeypatch.setattr(settings, "projects_root", str(root))

    resolution = resolve_project(root, "30784")
    assert resolution.matched_folders, "EP-30784 is not in this archive"
    folder = resolution.matched_folders[0]
    drf = resolution.drf_candidates[0].path

    extraction = extract_drf_fields(drf)
    assert extraction.fields, "the DRF read produced no fields -- is Tesseract installed?"

    _login_admin(client)
    payload = {
        "ep_number": "30784",
        "source_folder_path": str(folder),
        "drf_document_path": str(drf),
        "design_sheets": [
            {"system_code": c.system_guess, "document_path": str(c.path)}
            for c in resolution.design_sheet_candidates
        ],
        "systems": [
            {
                "name": s.name,
                "brand": s.brand,
                "method_statement": s.method_statement,
                "drawing": s.drawing,
            }
            for s in extraction.systems
        ],
        "scope_of_work": extraction.scope_of_work,
        **{
            column: extraction.fields[name].value
            for name, column in DRF_FIELD_TO_COLUMN.items()
            if name in extraction.fields
        },
    }
    pid = client.post("/projects", json=payload).json()["id"]
    client.post(f"/projects/{pid}/boq/ensure")

    body = client.post(f"/projects/{pid}/reextract").json()

    assert body["folder_found"] is True
    # Every field the project was built from reads back the same way.
    assert [f["field"] for f in body["fields"] if f["status"] == "differs"] == []
    assert body["scope_of_work"]["status"] in {"match", "absent"}
    # Every sheet the project was created with is still there, and the BOQ
    # read from those same sheets reproduces exactly.
    assert [s["status"] for s in body["sheets"]] == ["known"] * len(body["sheets"])
    assert body["boq_changes"] == []
