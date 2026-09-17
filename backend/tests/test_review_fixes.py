"""Regressions for the ten-project review of 13 September 2026.

Each test names the review finding it pins (FX-01 ... FX-10) and reproduces
the observation with a synthetic fixture built from what the real document
did. The live-archive checks in the other test files are what confirm the
same on the real DRFs and design sheets.
"""

import io
import time
from pathlib import Path

import pymupdf
import pytest

from app.core.config import get_settings
from app.services import drf_extractor
from app.services.ep_resolver import (
    DocumentMatch,
    canonical_system_code,
    declared_revision,
    find_drf_candidates,
    find_design_sheet_candidates,
    infer_single_system,
    mark_superseded,
    resolve_project,
)

from .conftest import login, make_user
from app.models import RoleEnum

settings = get_settings()


def _admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


# --- FX-01: the selected folder is the source folder ---------------------------


def test_fx01_resolution_names_the_folder_the_documents_came_from(tmp_path):
    for client_name in ("AG Engineering", "Al Ghurair MEP"):
        scan = tmp_path / client_name / "EP-31112 Substation" / "Scan Document"
        scan.mkdir(parents=True)
        (scan / "EP-31112 DRF.pdf").write_bytes(b"%PDF")

    chosen = tmp_path / "Al Ghurair MEP" / "EP-31112 Substation"
    resolution = resolve_project(tmp_path, "31112", selected_folder=chosen)

    assert resolution.source_folder == chosen
    assert all(str(chosen) in str(c.path) for c in resolution.drf_candidates)


def test_fx01_resolve_endpoint_returns_the_selected_folder(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    for client_name in ("AG Engineering", "Al Ghurair MEP"):
        scan = tmp_path / client_name / "EP-31112 Substation" / "Scan Document"
        scan.mkdir(parents=True)
        (scan / "EP-31112 DRF.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))
    _admin(client)

    first = client.post("/projects/resolve", json={"ep_number": "31112"}).json()
    assert first["is_ambiguous"] and first["source_folder"] is None

    chosen = str(tmp_path / "Al Ghurair MEP" / "EP-31112 Substation")
    second = client.post("/projects/resolve", json={"ep_number": "31112", "selected_folder": chosen}).json()
    assert second["source_folder"] == chosen
    assert second["drf_candidates"][0]["path"].startswith(chosen)


def test_fx01_a_document_outside_the_source_folder_is_refused(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    archive = tmp_path / "archive"
    right = archive / "Al Ghurair" / "EP-31112"
    wrong = archive / "AG Engineering" / "EP-31112"
    for folder in (right, wrong):
        (folder / "Scan Document").mkdir(parents=True)
        (folder / "Scan Document" / "EP-31112 DRF.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(archive))
    _admin(client)

    mismatched = client.post(
        "/projects",
        json={
            "ep_number": "31112",
            "source_folder_path": str(wrong),
            "drf_document_path": str(right / "Scan Document" / "EP-31112 DRF.pdf"),
            "design_sheets": [],
        },
    )
    assert mismatched.status_code == 422
    assert "outside the project folder" in mismatched.json()["detail"]

    outside_archive = client.post(
        "/projects",
        json={"ep_number": "31112", "source_folder_path": str(tmp_path / "elsewhere"), "design_sheets": []},
    )
    assert outside_archive.status_code == 422

    consistent = client.post(
        "/projects",
        json={
            "ep_number": "31112",
            "source_folder_path": str(right),
            "drf_document_path": str(right / "Scan Document" / "EP-31112 DRF.pdf"),
            "design_sheets": [],
        },
    )
    assert consistent.status_code == 201


def test_fx01_the_full_drf_pdf_outranks_its_half_page_jpg_copy(tmp_path):
    scan = tmp_path / "EP-30208 Kalba" / "Scan Document"
    scan.mkdir(parents=True)
    for name in ("EP-30208 DRF - Copy.jpg", "EP-30208 DRF - Copy.pdf", "EP-30208 DRF.pdf"):
        (scan / name).write_bytes(b"x")

    names = [c.path.name for c in find_drf_candidates(tmp_path / "EP-30208 Kalba")]

    assert names == ["EP-30208 DRF.pdf", "EP-30208 DRF - Copy.pdf", "EP-30208 DRF - Copy.jpg"]


# --- FX-02: nothing invented, nothing dropped ----------------------------------


@pytest.mark.parametrize(
    ("words", "confidence", "noise"),
    [
        (["ee", "ee", "ee"], 24.0, True),        # EP-31112's blank plot number
        (["ee"], 30.0, True),
        ([], None, True),
        (["2320316"], 54.0, False),              # EP-31725's plot number, sparse read
        (["AL", "GHURAIR"], 88.0, False),        # short words at high confidence
        (["M/s.", "Arenco"], 86.0, False),
    ],
)
def test_fx02_low_confidence_letter_fragments_are_an_empty_cell(words, confidence, noise):
    assert drf_extractor._is_ocr_noise(words, confidence) is noise


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("1. Quoted as per BOQ and Evac Only Considered.", True),
        ("2. Our scope is device fixing only, Cables are excluded.", True),
        ("ee Se", False),                # the signature scrawled across the box
        ("\\ \\ P", False),
        ("_", False),
        ("", False),
    ],
)
def test_fx02_notes_keep_writing_and_drop_scribble(line, kept):
    assert drf_extractor._looks_like_prose(line) is kept


def test_fx02_note_lines_lose_the_border_fragments():
    assert drf_extractor._clean_note_line("3. Testing and Commissioning Support Only _ \\ \\ P") == (
        "3. Testing and Commissioning Support Only"
    )
    assert drf_extractor._clean_note_line("{1. Kalba school's Building-2 having PA System already.") == (
        "1. Kalba school's Building-2 having PA System already."
    )


# --- FX-04: one system identity ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "code"),
    [("PA", "PAVA"), ("va", "PAVA"), ("VAS", "PAVA"), ("PAVA", "PAVA"), ("VE", "VES"), ("VES", "VES"),
     ("fas", "FAS"), ("EML", "ELS"), (None, None), ("", None)],
)
def test_fx04_filename_codes_are_normalised(raw, code):
    assert canonical_system_code(raw) == code


def test_fx04_design_sheet_filenames_carry_the_normalised_code(tmp_path):
    folder = tmp_path / "EP-31055 Royal Breeze" / "Commercial"
    folder.mkdir(parents=True)
    for name in ("EP-31055 FAS Design.pdf", "EP-31055 VAS Design Sheet.pdf", "EP-30208 PA Design Sheet.pdf"):
        (folder / name).write_bytes(b"%PDF")

    codes = {c.path.name: c.system_guess for c in find_design_sheet_candidates(tmp_path / "EP-31055 Royal Breeze")}

    assert codes == {
        "EP-31055 FAS Design.pdf": "FAS",
        "EP-31055 VAS Design Sheet.pdf": "PAVA",
        "EP-30208 PA Design Sheet.pdf": "PAVA",
    }


def test_fx04_a_generic_sheet_takes_the_drf_s_one_system():
    assert infer_single_system(["PA/VA & BGM"]) == "PAVA"
    assert infer_single_system(["Fire Alarm", "Fire Telephone"]) == "FAS"   # FT rides on the FAS sheet
    assert infer_single_system(["Fire Alarm", "Voice Evacuation"]) is None  # could be either sheet
    assert infer_single_system([]) is None


def test_fx04_resolve_labels_a_generic_sheet_from_a_single_system_drf(client, monkeypatch, tmp_path):
    import app.routers.projects as projects_router

    scan = tmp_path / "EP-30387 School" / "Scan"
    scan.mkdir(parents=True)
    (scan / "EP-30387 - Design.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(projects_router.settings, "projects_root", str(tmp_path))
    # A DRF whose reading marks the public-address row only.
    from app.ai import verification as ai_verification

    monkeypatch.setattr(ai_verification, "read_drf", lambda db, drf, **kwargs: _fake_reading([("PA/VA & BGM", "TOA")]))
    (scan / "EP-30387 - DRF.pdf").write_bytes(b"%PDF")
    _admin(client)

    body = client.post("/projects/resolve", json={"ep_number": "30387"}).json()

    sheet = body["design_sheet_candidates"][0]
    assert sheet["system_guess"] == "PAVA"
    assert "inferred" in sheet["matched_via"]
    assert body["extracted_other_information"] == "1. Testing and Commissioning Support Only"


def _fake_reading(systems):
    """What app.ai.verification.read_drf returns: fields, systems, the run."""
    import types

    fields = {"other_information": "1. Testing and Commissioning Support Only"}
    marked = {name: {"brand": brand, "method_statement": True, "drawing": True} for name, brand in systems}
    return fields, marked, types.SimpleNamespace(notes=[], calls=1, reused=0)


@pytest.mark.parametrize(
    ("filename", "revision"),
    [
        ("EP-30175 FAS Design sheet-R1.pdf", 1),
        ("EP-30175 VE Design sheet-R2.pdf", 2),
        ("EP-30175 VES Design.pdf", None),
        ("EP-29495 FAS Design Rev 3.pdf", 3),
        ("EP-24601 Design Sheet.pdf", None),
        ("R2D2 Design.pdf", None),
    ],
)
def test_fx04_the_declared_revision_is_read_off_the_filename(filename, revision):
    assert declared_revision(filename) == revision


def test_fx04_only_the_governing_revision_of_a_system_is_selected(tmp_path):
    """EP-30175 filed three voice-evacuation sheets and two fire-alarm ones;
    attaching all five read one system's BOQ three times over."""
    candidates = [
        DocumentMatch(Path("EP-30175 FAS Design.pdf"), "FAS", "m", revision=None),
        DocumentMatch(Path("EP-30175 FAS Design sheet-R1.pdf"), "FAS", "m", revision=1),
        DocumentMatch(Path("EP-30175 VE Design sheet-R1.pdf"), "VES", "m", revision=1),
        DocumentMatch(Path("EP-30175 VE Design sheet-R2.pdf"), "VES", "m", revision=2),
        DocumentMatch(Path("EP-30175 VES Design.pdf"), "VES", "m", revision=None),
        DocumentMatch(Path("EP-30175 Design.pdf"), None, "m", revision=None),
    ]

    selected = {c.path.name: c.selected for c in mark_superseded(candidates)}

    assert selected == {
        "EP-30175 FAS Design.pdf": False,
        "EP-30175 FAS Design sheet-R1.pdf": True,
        "EP-30175 VE Design sheet-R1.pdf": False,
        "EP-30175 VE Design sheet-R2.pdf": True,
        "EP-30175 VES Design.pdf": False,
        "EP-30175 Design.pdf": True,   # no code: nothing is assumed to supersede it
    }
    assert "superseded by EP-30175 VE Design sheet-R2.pdf" in next(
        c.matched_via for c in candidates if c.path.name == "EP-30175 VES Design.pdf"
    )


def test_fx04_a_sheet_saved_as_pa_is_stored_as_pava(client, tmp_path):
    _admin(client)
    body = client.post(
        "/projects",
        json={
            "ep_number": "30353",
            "design_sheets": [{"system_code": "pa", "document_path": str(tmp_path / "EP-30353 Design.pdf")}],
        },
    ).json()
    assert [s["system_code"] for s in body["design_sheets"]] == ["PAVA"]


def test_fx04_the_cover_names_only_the_fire_alarm_family(db_session, client):
    from app.models import Project, ProjectSystem
    from app.routers.submittal import _join_systems, _system_title

    project = Project(ep_number="29495", created_by_id=make_user(db_session, "t@x.com", RoleEnum.admin).id)
    project.systems = [
        ProjectSystem(name="Fire Alarm", brand="EDWARDS"),
        ProjectSystem(name="Voice Evacuation", brand="EDWARDS"),
        ProjectSystem(name="Fire Telephone", brand="EDWARDS"),
        ProjectSystem(name="Central Battery System", brand=None),
    ]

    assert _system_title(project, "FAS") == "Fire Alarm, Voice Evacuation & Fire Telephone System"
    assert _system_title(project, "CBS") == "Central Battery System"
    assert _system_title(project, "PAVA") == "Public Address & Voice Alarm System"
    # Nothing ever reads "System System".
    assert _join_systems(["Central Battery System"]) == "Central Battery System"


# --- FX-03: every ruled box on a multi-building sheet is read -------------------


def _ruled_sheet(path: Path, buildings: list[tuple[str, list[tuple[str, str, str]]]]) -> Path:
    """A design sheet in the six-rule layout, one ruled box per building
    under a capitalised banner and a totals row, the way EP-30208 is laid out."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    columns = [40, 100, 220, 430, 500, 570]
    y = 60
    page.insert_text((60, y), "Qty.", fontsize=8)
    page.insert_text((110, y), "Catalog No.", fontsize=8)
    page.insert_text((230, y), "Description", fontsize=8)
    y += 14
    for banner, items in buildings:
        page.insert_text((250, y), banner, fontsize=9)
        y += 10
        top = y
        for qty, catalog, description in items:
            y += 16
            page.insert_text((60, y), qty, fontsize=9)
            page.insert_text((110, y), catalog, fontsize=9)
            page.insert_text((230, y), description, fontsize=9)
        bottom = y + 8
        for x in columns:
            page.draw_line((x, top), (x, bottom), width=1)
        page.draw_line((columns[0], top), (columns[-1], top), width=1)
        page.draw_line((columns[0], bottom), (columns[-1], bottom), width=1)
        y = bottom + 14
        page.insert_text((150, y), "Total Price For Supply, Testing & Commissioning in AED", fontsize=8)
        y += 22
    doc.save(path)
    doc.close()
    return path


@pytest.mark.skipif(
    not __import__("tests.test_design_sheet_extractor", fromlist=["_tesseract_available"])._tesseract_available(),
    reason="tesseract is not installed/configured in this environment",
)
def test_fx03_every_building_box_is_read_and_kept_apart(tmp_path):
    from app.services.design_sheet_extractor import extract_boq_lines

    sheet = _ruled_sheet(
        tmp_path / "PA Design Sheet.pdf",
        [
            ("DHAID - B1 BUILDING", [("1", "VX-3004F", "Voice Evacuation Frame"), ("20", "PC-1860BS-C", "Ceiling Speaker")]),
            ("DHAID - B2 BUILDING", [("2", "VX-3004F", "Voice Evacuation Frame"), ("74", "PC-1860BS-C", "Ceiling Speaker")]),
            ("KALBA - B5 BUILDING", [("214", "PC-1860BS-C", "Ceiling Speaker"), ("3", "RM-300X", "Remote Microphone")]),
        ],
    )

    lines = extract_boq_lines(sheet)

    speakers = sorted((line.group_heading, line.quantity) for line in lines if line.catalog_no == "PC-1860BS-C")
    assert speakers == [("DHAID - B1 BUILDING", "20"), ("DHAID - B2 BUILDING", "74"), ("KALBA - B5 BUILDING", "214")]
    assert len(lines) == 6
    assert sum(int(line.quantity) for line in lines) == 314


# --- FX-05 / FX-06: the battery sheet ----------------------------------------------


def _panel(key: str, parts: int, **overrides):
    from app.schemas_design import BatteryLineOut, BatteryPanelOut, BatterySetOut

    # A selected battery, unless the fixture says the panel has none.
    selected = [] if overrides.get("lower") else [
        BatterySetOut(part_no="ES65-12", capacity_ah=65, voltage=12, units=2, strings=1, brand="ROCKET")
    ]

    lines = [
        BatteryLineOut(
            part_no=f"PART-{i:02d}", description=f"Module {i}", quantity=1, manufacturer=overrides.get("brand"),
            kind="load", standby_ma=10, alarm_ma=20, total_standby_ma=10, total_alarm_ma=20,
        )
        for i in range(parts)
    ]
    return BatteryPanelOut(
        heading="Fire Alarm Control Panel", system_code=overrides.get("system_code"), count=1, key=key,
        name="FACP-01", lines=lines, standby_ma=10.0 * parts, alarm_ma=20.0 * parts, standby_mah=240.0 * parts,
        alarm_mah=10.0 * parts, total_ah=0.25 * parts, required_ah=0.3 * parts, lower_bound=overrides.get("lower", False),
        missing_parts=overrides.get("missing", []), quoted=[], quoted_ah=None, quoted_short=False,
        status=overrides.get("status", "ok"), selected=selected, selected_ah=65 if selected else None,
    )


def _project(db_session, systems):
    from app.models import Project, ProjectSystem

    project = Project(ep_number="30175", project_name="Sky Hills", created_by_id=make_user(db_session, "b@x.com", RoleEnum.admin).id)
    project.systems = [ProjectSystem(name=n, brand=b) for n, b in systems]
    return project


def test_fx06_a_long_equipment_table_continues_on_further_sheets(db_session, client):
    from app.services.battery_pdf import battery_calculation_pdf

    project = _project(db_session, [("Fire Alarm", "EDWARDS")])
    panel = _panel("k", 60, brand="EDWARDS")

    doc = pymupdf.open(stream=battery_calculation_pdf(project, [panel], manufacturers={"k": "EDWARDS"}), filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)

    assert doc.page_count == 3
    assert all(f"PART-{i:02d}" in text for i in range(60)), "every part is on a page"
    assert "Calculation notes" in text
    # Neither the charger remark nor "below the requirement" goes out on the sheet.
    assert "Charger compatibility" not in text and "below the requirement" not in text
    assert "(continued, sheet 2 of 3)" in text
    # Nothing is drawn past the page: the last row of each page sits above its footer.
    for page in doc:
        rows = [b for b in page.get_text("blocks") if b[4].strip().startswith("PART-")]
        assert all(b[3] < page.rect.height - 30 for b in rows)


def test_fx05_the_sheet_names_the_panel_s_own_brand_or_none(db_session, client):
    from app.services.battery_pdf import battery_calculation_pdf, panel_manufacturer

    split = _project(db_session, [("Fire Alarm", "COOPER"), ("Voice Evacuation", "EDWARDS")])
    # BOQ lines that name their brand settle it.
    assert panel_manufacturer(split, _panel("a", 2, brand="EDWARDS")) == "EDWARDS"
    # The DRF's brand for the panel's system, when the lines name none.
    assert panel_manufacturer(split, _panel("b", 2, system_code="FAS")) == "COOPER"
    # Two brands on the DRF and none on the lines: nothing is guessed.
    assert panel_manufacturer(split, _panel("c", 2)) == ""

    pdf = battery_calculation_pdf(split, [_panel("c", 2)], manufacturers={"c": ""})
    text = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "MANUFACTURER NOT ESTABLISHED" in text
    assert "EST4" not in text, "EST4 is Edwards' platform, not every brand's"


def test_fx06_the_sheet_carries_the_screen_s_warnings(db_session, client):
    from app.services.battery_pdf import battery_calculation_pdf

    project = _project(db_session, [("Fire Alarm", "EDWARDS")])
    panel = _panel("k", 3, brand="EDWARDS", lower=True, missing=["4-COMREL", "4-ANN"], status="incomplete")

    text = pymupdf.open(stream=battery_calculation_pdf(project, [panel]), filetype="pdf")[0].get_text()

    assert "Load is a lower bound: 2 parts have no current on file" in text
    assert "Pending: a part has no current yet" in text


# --- FX-07: the page can read what the builder reports ----------------------------


def test_fx07_package_headers_are_exposed_to_the_browser():
    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    exposed = cors.kwargs["expose_headers"]
    assert {"Content-Disposition", "X-Package-Pages", "X-Package-Warnings"} <= set(exposed)


def test_fx07_a_failed_stage_is_named(db_session, client, tmp_path, monkeypatch):
    from app.services import submittal_package
    from app.services.submittal_package import PackageBuildError, PackagePlan, PackageSection, build_package

    project = _project(db_session, [("Fire Alarm", "EDWARDS")])
    plan = PackagePlan(sections=[PackageSection(number=5, name="Schedule of Material", selected=True,
                                                documents=[submittal_package.PackageDocument(name="s", source="generated")])])

    def broken(*_args, **_kwargs):
        raise RuntimeError("no such font")

    monkeypatch.setattr(submittal_package, "build_schedule", broken)
    with pytest.raises(PackageBuildError) as failure:
        build_package(project, plan, None)
    assert "05 Schedule of Material (generated) could not be produced (no such font)" in str(failure.value)


# --- FX-10: a session in use does not expire ---------------------------------------


def test_fx10_the_session_cookie_is_renewed_while_in_use(client, monkeypatch):
    import app.main as main_module
    from app.core import security

    _admin(client)
    issued = client.cookies.get(settings.cookie_name)

    # Pretend the cookie is past half its life: decode it with the clock moved on.
    real_decode = security.decode_access_token
    payload = real_decode(issued)
    monkeypatch.setattr(main_module, "decode_access_token", lambda token: {**payload, "exp": time.time() + 60})

    response = client.get("/auth/me")   # under /auth/: never renewed
    assert "set-cookie" not in {k.lower() for k in response.headers}

    response = client.get("/projects")
    assert response.status_code == 200
    assert "set-cookie" in {k.lower() for k in response.headers}, "the cookie is re-issued"
    renewed = response.cookies.get(settings.cookie_name)
    fresh = real_decode(renewed)
    assert fresh["sub"] == payload["sub"]
    # A full term again, not the minute the patched decode claimed was left.
    assert fresh["exp"] > time.time() + settings.access_token_expire_minutes * 60 - 120
