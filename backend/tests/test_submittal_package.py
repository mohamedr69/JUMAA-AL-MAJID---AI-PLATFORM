"""Assembling a material submittal package.

The rules pinned here are the ones that are permanent in Python rather than
decided per package: the company's section list, one datasheet per part, the
cover and dividers coming from the company's templates, and a section the
platform cannot fill being carried as a divider rather than dropped.
"""

import pymupdf
import pytest

from app.core.config import get_settings
from app.models import RoleEnum
from app.services.submittal_package import (
    SECTIONS,
    build_package,
    datasheet_documents,
    plan_package,
)

from .conftest import login, make_user

settings = get_settings()

PANEL = "EST4 Main Fire Alarm Control Panel"


def _login_admin(client):
    return login(client, settings.default_admin_email, settings.default_admin_password)


def _pdf(path, lines, landscape=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=842 if landscape else 595, height=595 if landscape else 842)
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=11)
        y += 18
    doc.save(path)
    doc.close()


def _library(tmp_path, navy_block=False):
    """A submittal builder shaped like the real one."""
    root = tmp_path / "submittal builder"
    _pdf(root / "Company Profile" / "Company Profile.pdf", ["Company Profile"])
    _pdf(root / "Trade License" / "Trade Licence.pdf", ["Trade Licence"])
    _pdf(root / "Test certificates" / "Test Certificates.pdf", ["Test Certificates"])
    # A spare copy of the same document, as the real library holds.
    _pdf(root / "Test certificates" / "Test Certificates_copy.pdf", ["Test Certificates"])
    (root / "Country Of Origin").mkdir(parents=True, exist_ok=True)
    (root / "Country Of Origin" / "COO.xlsx").write_bytes(b"not a pdf")

    if navy_block:
        # The real cover: a navy block with the project name in white over it,
        # which is what the replacement has to preserve.
        (root / "templates").mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        page = doc.new_page(width=595.32, height=841.92)
        page.insert_text((44, 200), "MATERIAL SUBMITTAL", fontsize=20)
        page.insert_text((44, 225), "Fire Alarm, Voice Evacuation & Fire Telephone System", fontsize=13)
        page.draw_rect(pymupdf.Rect(44, 256, 551.3, 362), color=None, fill=(0.04, 0.2, 0.36))
        page.insert_text((66, 283), "PROJECT", fontsize=7.8, color=(0.55, 0.68, 0.82))
        page.insert_text((66, 309), "SAMANA PARK MEADOWS (DLRC 4)", fontsize=19, color=(1, 1, 1))
        page.insert_text((66, 331), "PROPOSED 2B + G + 16 + R RESIDENTIAL BUILDING", fontsize=11, color=(0.8, 0.86, 0.92))
        page.insert_text((66, 348), "PLOT NO. 648-8670, WADI AL SAFA 5, DLRC, DUBAI, U.A.E.", fontsize=10, color=(0.66, 0.74, 0.83))
        page.insert_text((44, 444), "M/s. Samana IFS Holding Limited", fontsize=11, color=(0.05, 0.31, 0.54))
        page.insert_text((297.7, 444), "M/s. Al Hilal Engineering Consultant", fontsize=11, color=(0.05, 0.31, 0.54))
        page.insert_text((44, 492), "M/s. Italtech Contracting L.L.C", fontsize=11, color=(0.05, 0.31, 0.54))
        page.insert_text((58, 714), "EP-30058", fontsize=9.5)
        page.insert_text((227.1, 714), "R0", fontsize=9.5)
        page.insert_text((396.2, 714), "06-09-2026", fontsize=9.5)
        doc.save(root / "templates" / "Cover Page - Material Submittal - R0.pdf")
        doc.close()
    else:
        _pdf(root / "templates" / "Cover Page - Material Submittal - R0.pdf", [
            "MATERIAL SUBMITTAL",
            "Fire Alarm, Voice Evacuation & Fire Telephone System",
            "SAMANA PARK MEADOWS (DLRC 4)",
            "EP-30058",
            "R0",
            "06-09-2026",
        ])
    # The index & divider template: index page first, then one page a section.
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), "SUBMITTAL INDEX", fontsize=14)
    for number, name in SECTIONS:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), "PROJECT:", fontsize=9)
        page.insert_text((50, 80), "IVY GARDEN 2 - 1B+G+5P+34+R RESIDENTIAL BUILDING", fontsize=11)
        page.insert_text((50, 300), f"{number:02d}", fontsize=13)
        page.insert_text((50, 330), name, fontsize=20)
        page.insert_text((50, 360), "SUBMITTAL SECTION", fontsize=10)
    (root / "templates").mkdir(parents=True, exist_ok=True)
    doc.save(root / "templates" / "Index & divider.pdf")
    doc.close()
    return root


def _datasheets(tmp_path):
    from app.services.datasheet_library import DatasheetLibrary

    root = tmp_path / "EST4"
    _pdf(root / "01- 4-CPU.pdf", ["4-CPU Central Processor Module", "Standby 211 mA"])
    _pdf(root / "10- 4-AUDTEL.pdf", ["4-AUDTELS Audio Telephone", "4-MIC Microphone", "4-FT Fire Telephone"])
    return DatasheetLibrary("EDWARDS", root)


def _project(client, lines=None):
    project_id = client.post("/projects", json={
        "ep_number": "30784", "project_name": "Binghatti Skyblade",
        "client": "Binghatti", "consultant": "Silver Stone", "contractor": "Granada Europe",
        "plot_number": "3450398", "location": "Burj Khalifa District", "design_sheets": [],
    }).json()["id"]
    line = lambda part, desc: {  # noqa: E731
        "system_code": "FAS", "group_heading": PANEL, "catalog_no": part,
        "description": desc, "quantity": "1", "manufacturer": "EDWARDS",
    }
    client.put(f"/projects/{project_id}/boq", json=lines if lines is not None else [
        line("4-CPU", "Central Processor Module"),
        line("4-AUDTELS", "Audio Telephone"),
        line("4-MIC", "Microphone"),
        line("4-FT", "Fire Telephone"),
    ])
    return project_id


def _db_project(db_session, project_id):
    from app.models import Project

    return db_session.get(Project, project_id)


# --- the section list is the company's ------------------------------------


def test_the_section_list_is_the_company_index():
    """Eighteen sections: the company's printed index of seventeen, plus
    Battery Calculation after the Schedule of Material."""
    assert len(SECTIONS) == 18
    assert SECTIONS[0] == (1, "Company Profile")
    # Battery Calculation sits after the schedule, so everything after it
    # moved down one from the printed template.
    assert dict(SECTIONS)[5] == "Schedule of Material"
    assert dict(SECTIONS)[6] == "Battery Calculation"
    assert dict(SECTIONS)[8] == "Technical Data Sheet"
    assert SECTIONS[-1][1].startswith("Others Documents")


# --- one datasheet per part -----------------------------------------------


def test_a_part_quoted_twice_contributes_one_datasheet(client, db_session, tmp_path):
    _login_admin(client)
    line = lambda part, group: {  # noqa: E731
        "system_code": "FAS", "group_heading": group, "catalog_no": part,
        "description": part, "quantity": "1", "manufacturer": "EDWARDS",
    }
    project_id = _project(client, [
        line("4-CPU", "Main Panel"),
        line("4-CPU", "Sub Panel"),      # the same part under another heading
        line("4-CPU", "Repeater Panel"),
    ])
    project = _db_project(db_session, project_id)

    documents = datasheet_documents(project, {"EDWARDS": _datasheets(tmp_path)}, "FAS")

    assert [d.name for d in documents] == ["01- 4-CPU.pdf"]


def test_one_sheet_covering_several_parts_is_merged_once(client, db_session, tmp_path):
    """Edwards documents a family on one sheet. It goes in once and names
    every part it serves, rather than once per part."""
    _login_admin(client)
    project_id = _project(client)
    project = _db_project(db_session, project_id)

    documents = datasheet_documents(project, {"EDWARDS": _datasheets(tmp_path)}, "FAS")

    assert sorted(d.name for d in documents) == ["01- 4-CPU.pdf", "10- 4-AUDTEL.pdf"]
    shared = next(d for d in documents if d.name == "10- 4-AUDTEL.pdf")
    assert sorted(shared.covers) == ["4-AUDTELS", "4-FT", "4-MIC"]


def test_only_the_submittal_s_own_system_is_read(client, db_session, tmp_path):
    """A fire alarm package that walked the whole BOQ would carry the
    emergency lighting parts too, and call every one a missing datasheet."""
    _login_admin(client)
    project_id = _project(client, [
        {"system_code": "FAS", "catalog_no": "4-CPU", "description": "CPU", "quantity": "1", "manufacturer": "EDWARDS"},
        {"system_code": "EML", "catalog_no": "SL2-42D3D", "description": "Emergency luminaire", "quantity": "9", "manufacturer": "MENVIER"},
    ])
    project = _db_project(db_session, project_id)

    documents = datasheet_documents(project, {"EDWARDS": _datasheets(tmp_path)}, "FAS")

    assert [d.part_no for d in documents] == ["4-CPU"]


# --- planning ---------------------------------------------------------------


def test_the_plan_reports_what_each_section_holds(client, db_session, tmp_path):
    _login_admin(client)
    project_id = _project(client)
    project = _db_project(db_session, project_id)
    library = _library(tmp_path)

    plan = plan_package(project, {n for n, _ in SECTIONS}, library, tmp_path,
                        {"EDWARDS": _datasheets(tmp_path)}, system_code="FAS")

    by_number = {s.number: s for s in plan.sections}
    assert plan.library_found
    assert by_number[1].found == 1                      # Company Profile
    assert by_number[8].found == 2                      # two datasheets
    # A spare copy of one document is the same document twice.
    assert by_number[12].found == 1
    # Country of Origin and the warranty are drawn from their templates
    # rather than merged, so neither is ever a missing document.
    assert (by_number[15].found, by_number[15].missing) == (1, 0)
    assert (by_number[16].found, by_number[16].missing) == (1, 0)
    # A library folder the builder does not hold is still reported.
    assert by_number[7].missing == 1
    assert "Not in the submittal builder" in by_number[7].documents[0].missing_reason


def test_a_section_that_is_not_built_says_so_rather_than_looking_empty(client, db_session, tmp_path):
    _login_admin(client)
    project = _db_project(db_session, _project(client))

    plan = plan_package(project, {4}, _library(tmp_path), tmp_path, {}, system_code="FAS")

    compliance = next(s for s in plan.sections if s.number == 4)
    assert compliance.note and "not generated yet" in compliance.note


# --- building ---------------------------------------------------------------


def test_the_package_carries_cover_index_and_a_divider_per_section(client, db_session, tmp_path):
    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    plan = plan_package(project, {1, 8, 10}, library, tmp_path,
                        {"EDWARDS": _datasheets(tmp_path)}, system_code="FAS")
    built = build_package(project, plan, library, revision="R0", systems="Fire Alarm System")

    labels = [label for label, _name, _a, _b in built.manifest]
    assert labels[0] == "Cover"
    assert labels[1] == "Index"
    assert "01 Company Profile -- divider" in labels
    assert "08 Technical Data Sheet -- divider" in labels
    assert "10 Trade License -- divider" in labels
    # Sections that were not ticked are not in the package at all.
    assert not any(label.startswith("02 ") for label in labels)


def test_the_cover_and_dividers_carry_this_project(client, db_session, tmp_path):
    """Both come from the company's template, with its project swapped for
    this one -- the artwork is approved and is never redrawn."""
    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    plan = plan_package(project, {1}, library, tmp_path, {}, system_code="FAS")
    built = build_package(project, plan, library, revision="R2", systems="Fire Alarm System")

    doc = pymupdf.open(stream=built.pdf, filetype="pdf")
    cover = doc[0].get_text()
    assert "BINGHATTI SKYBLADE" in cover
    assert "SAMANA PARK MEADOWS" not in cover        # the template's project is gone
    assert "EP-30784" in cover and "R2" in cover
    divider = doc[2].get_text()
    assert "Binghatti Skyblade".upper() in divider.upper()
    assert "IVY GARDEN" not in divider
    doc.close()


def test_pages_are_numbered_continuously_after_the_cover(client, db_session, tmp_path):
    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    plan = plan_package(project, {1}, library, tmp_path, {}, system_code="FAS")
    built = build_package(project, plan, library)

    doc = pymupdf.open(stream=built.pdf, filetype="pdf")
    assert "Page 1 of" not in doc[0].get_text()          # the cover is not numbered
    assert f"Page 2 of {built.pages}" in doc[1].get_text()
    doc.close()


def test_a_missing_document_is_reported_not_silently_skipped(client, db_session, tmp_path):
    """Section 07's folder is not in this builder: the package still carries
    its divider, and the gap is named rather than passed over."""
    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    plan = plan_package(project, {7}, library, tmp_path, {}, system_code="FAS")
    built = build_package(project, plan, library)

    assert any("Product Catalogue" in w for w in built.warnings)
    assert any("07 Product Catalogue or Brochure -- divider" == label for label, _n, _a, _b in built.manifest)


# --- the API ----------------------------------------------------------------


def test_the_plan_endpoint_answers_for_every_section(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login_admin(client)
    project_id = _project(client)

    body = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=FAS").json()

    assert body["library_found"] is True
    assert len(body["sections"]) == 18
    assert all(s["selected"] for s in body["sections"])


def test_building_returns_a_pdf(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login_admin(client)
    project_id = _project(client)

    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [1, 8], "system_code": "FAS", "revision": "R0"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert int(resp.headers["X-Package-Pages"]) > 0
    assert "EP-30784" in resp.headers["Content-Disposition"]


def test_choosing_no_section_is_refused(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    _login_admin(client)
    project_id = _project(client)

    resp = client.post(f"/projects/{project_id}/submittal/package", json={"sections": []})
    assert resp.status_code == 400


def test_building_is_for_editors(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    _login_admin(client)
    project_id = _project(client)
    make_user(db_session, "viewer@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer@ep-platform.com")

    assert client.post(f"/projects/{project_id}/submittal/package", json={"sections": [1]}).status_code == 403
    assert client.get(f"/projects/{project_id}/submittal/package/plan").status_code == 403


@pytest.mark.parametrize("sections,expected", [("1,8", {1, 8}), ("", set(range(1, 19))), ("99", set())])
def test_section_numbers_are_parsed_from_the_query(sections, expected):
    from app.routers.submittal import _parse_sections

    assert _parse_sections(sections) == expected


# --- reading a filled-in checklist ------------------------------------------


def _checklist(tmp_path, marks: dict[int, str], name="checklist.pdf"):
    """The company's checklist: numbered rows, a tick in one of three columns.

    Built at the real form's coordinates so the reader is exercised the way
    it is used -- the tick is a character with a position, not a field.
    Marked with "X" rather than a check glyph: the base PDF fonts have no
    U+2713, and pymupdf substitutes a middle dot for it when writing. The
    real form carries a true check character and is read the same way, by
    which column the mark falls in.
    """
    columns = {"yes": 331.4, "no": 371.6, "na": 411.8}
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((329.4, 227), "Yes", fontsize=9)
    page.insert_text((370.6, 227), "No", fontsize=9)
    page.insert_text((408.4, 227), "N/A", fontsize=9)
    y = 241.1
    for number, _name in SECTIONS:
        page.insert_text((79.9, y), str(number), fontsize=9)
        mark = marks.get(number)
        if mark:
            page.insert_text((columns[mark], y - 3), "X", fontsize=9)
        y += 23.4
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def test_a_checklist_is_read_by_tick_position(tmp_path):
    """Every row's text is the same tick, so which column it sits in is the
    only thing that distinguishes Yes from No from N/A."""
    from app.services.submittal_package import read_checklist

    marks = {n: "yes" for n, _ in SECTIONS}
    marks[9] = "no"          # Copy of Related Drawings
    marks[17] = "no"         # Material Sample Photos
    marks[18] = "na"         # Others Documents

    answers, warnings = read_checklist(_checklist(tmp_path, marks).read_bytes())

    assert answers[1] == "yes"
    assert answers[9] == "no"
    assert answers[17] == "no"
    assert answers[18] == "na"
    assert warnings == []


def test_an_unticked_row_is_left_out_rather_than_assumed(tmp_path):
    """Not ticked means the checklist was not filled in for that row, which
    is not the same as "not included"."""
    from app.services.submittal_package import read_checklist

    answers, warnings = read_checklist(_checklist(tmp_path, {1: "yes", 2: "no"}).read_bytes())

    assert set(answers) == {1, 2}
    assert any("03" in w for w in warnings)


def test_something_that_is_not_a_checklist_is_refused(tmp_path):
    from app.services.submittal_package import read_checklist

    _pdf(tmp_path / "other.pdf", ["Just a document", "with no columns"])
    answers, warnings = read_checklist((tmp_path / "other.pdf").read_bytes())

    assert answers == {}
    assert "Yes/No/N/A" in warnings[0]


def test_the_checklist_endpoint_returns_the_sections_to_build(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    _login_admin(client)
    project_id = _project(client)
    marks = {n: "yes" for n, _ in SECTIONS}
    marks[9] = "no"
    path = _checklist(tmp_path, marks)

    with path.open("rb") as handle:
        resp = client.post(
            f"/projects/{project_id}/submittal/package/checklist",
            files={"file": ("checklist.pdf", handle, "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert 9 not in body["sections"]
    assert body["answers"]["9"] == "no"
    assert len(body["sections"]) == 17


def test_a_checklist_that_is_not_one_is_a_422(client, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    _login_admin(client)
    project_id = _project(client)
    _pdf(tmp_path / "other.pdf", ["Not a checklist"])

    with (tmp_path / "other.pdf").open("rb") as handle:
        resp = client.post(
            f"/projects/{project_id}/submittal/package/checklist",
            files={"file": ("other.pdf", handle, "application/pdf")},
        )

    assert resp.status_code == 422


def test_a_package_is_built_for_a_system_that_already_has_a_submittal(client, tmp_path, monkeypatch):
    """A package is rebuilt often -- for a revision, or to see what it would
    contain -- so an existing submittal never blocks building another."""
    import app.routers.submittal as submittal_router

    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: _library(tmp_path))
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login_admin(client)
    project_id = _project(client)
    created = client.post(f"/projects/{project_id}/submittals",
                          json={"title": "Fire Alarm System", "system_code": "FAS"})
    assert created.status_code == 201

    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [1, 8], "system_code": "FAS", "revision": "R1"})

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


# --- the cover keeps the template's artwork --------------------------------


def test_the_project_name_stays_white_inside_the_navy_block(client, db_session, tmp_path):
    """The project block is a navy box with white text drawn over it. The
    replacement is redacted with no fill and rewritten in the template's own
    colour, so the box survives and the name stays white on it -- blanking
    the old text with white punched a hole through the block."""
    from app.services.submittal_package import build_cover

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path, navy_block=True)

    cover = build_cover(library, project, "R0", "Fire Alarm System")
    page = cover[0]

    box = next(
        d for d in page.get_drawings()
        if d.get("fill") and d["rect"].y0 <= 256 and d["rect"].y1 >= 360 and d["rect"].width > 400
    )
    assert [round(c, 2) for c in box["fill"]] == [0.04, 0.20, 0.36]

    title = next(
        span
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if "BINGHATTI" in span["text"]
    )
    assert title["color"] == 0xFFFFFF
    cover.close()


def test_a_long_project_name_is_shrunk_rather_than_run_over_the_box(client, db_session, tmp_path):
    from app.services.submittal_package import build_cover

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    project.project_name = "A Very Long Project Name That Would Never Fit Across The Cover Block At Full Size"
    db_session.commit()
    library = _library(tmp_path, navy_block=True)

    cover = build_cover(library, project, "R0", "Fire Alarm System")
    span = next(
        s for b in cover[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
        if "VERY LONG" in s["text"]
    )
    assert span["size"] < 19
    assert span["bbox"][2] < 560          # inside the page, not off the edge
    cover.close()


# --- the schedule is laid out like the design sheet ------------------------


def test_the_schedule_is_one_block_per_assembly(client, db_session, tmp_path):
    """Not a flat run of every line in sequence: the design sheet gives each
    assembly its own lettered block and lists that assembly's parts under it."""
    from app.services.submittal_package import schedule_blocks

    _login_admin(client)
    line = lambda part, desc, group: {  # noqa: E731
        "system_code": "FAS", "group_heading": group, "catalog_no": part,
        "description": desc, "quantity": "2", "manufacturer": "EDWARDS",
    }
    project_id = _project(client, [
        {"system_code": "FAS", "group_heading": "EST4 Main Fire Alarm Control Panel",
         "description": "EST4 panel complete with power supply. Includes:", "quantity": "1"},
        line("4-CPU", "Central Processor Module", "EST4 Main Fire Alarm Control Panel"),
        line("4-PPS/M", "Primary Power Supply", "EST4 Main Fire Alarm Control Panel"),
        line("APS6A/230", "Amplifier power supply", "Amplifier (50 Watt)"),
        line("BPS10A/230", "Booster power supply", "Booster Power Supply"),
        # No heading: the detectors and sounders that hang off the panels.
        line("SIGA-OSD", "Photoelectric Smoke Detector", ""),
        line("SIGA-278", "Manual Call Point", ""),
    ])
    project = _db_project(db_session, project_id)

    blocks = schedule_blocks(project, "FAS")

    assert [(letter, title, len(items)) for letter, title, items in blocks] == [
        ("A", "EST4 Main Fire Alarm Control Panel", 2),
        ("B", "Amplifier (50 Watt)", 1),
        ("C", "Booster Power Supply", 1),
        ("D", "Field Devices", 2),
    ]


def test_the_block_is_named_by_its_heading_not_its_description(client, db_session, tmp_path):
    """The "... Includes:" line describes the assembly; it is not its name,
    and using it made the main panel and the second panel read alike."""
    from app.services.submittal_package import schedule_blocks

    _login_admin(client)
    spelled = "EST4 fire alarm control panel complete with power supply/charger, sealed lead acid batteries"
    project_id = _project(client, [
        {"system_code": "FAS", "group_heading": "EST4 Main Fire Alarm Control Panel",
         "description": spelled, "quantity": "1"},
        {"system_code": "FAS", "group_heading": "EST4 Main Fire Alarm Control Panel",
         "catalog_no": "4-CPU", "description": "CPU", "quantity": "1"},
        {"system_code": "FAS", "group_heading": "EST4 Fire Alarm Panel",
         "description": spelled, "quantity": "1"},
        {"system_code": "FAS", "group_heading": "EST4 Fire Alarm Panel",
         "catalog_no": "4-CPU", "description": "CPU", "quantity": "1"},
    ])
    project = _db_project(db_session, project_id)

    titles = [title for _letter, title, _items in schedule_blocks(project, "FAS")]

    assert titles == ["EST4 Main Fire Alarm Control Panel", "EST4 Fire Alarm Panel"]


def test_only_the_submittal_s_system_is_scheduled(client, db_session, tmp_path):
    from app.services.submittal_package import schedule_blocks

    _login_admin(client)
    project_id = _project(client, [
        {"system_code": "FAS", "group_heading": "Panel", "catalog_no": "4-CPU", "description": "CPU", "quantity": "1"},
        {"system_code": "EML", "group_heading": "Luminaires", "catalog_no": "SL2", "description": "Luminaire", "quantity": "9"},
    ])
    project = _db_project(db_session, project_id)

    assert [t for _l, t, _i in schedule_blocks(project, "FAS")] == ["Panel"]
    assert [t for _l, t, _i in schedule_blocks(project, "EML")] == ["Luminaires"]


def test_the_schedule_page_carries_the_blocks_and_part_numbers(client, db_session, tmp_path):
    from app.services.submittal_package import build_schedule

    _login_admin(client)
    project = _db_project(db_session, _project(client))

    doc = build_schedule(project, "FAS")
    text = "\n".join(page.get_text() for page in doc)

    assert "SCHEDULE OF MATERIAL" in text
    assert "A1" in text                    # numbered within its block
    assert "4-CPU" in text
    doc.close()


def test_the_schedule_carries_no_quantity_or_unit(client, db_session, tmp_path):
    """On every project. The schedule says what material is proposed for
    approval; how much of it is bought is the BOQ's business, and it changes
    after the submittal is approved."""
    from app.services.submittal_package import build_schedule

    _login_admin(client)
    project_id = _project(client, [
        {"system_code": "FAS", "group_heading": "Panel", "catalog_no": "4-CPU",
         "description": "Central Processor Module", "quantity": "17", "unit": "Nos",
         "manufacturer": "EDWARDS"},
    ])
    project = _db_project(db_session, project_id)

    doc = build_schedule(project, "FAS")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "QTY" not in text and "UNIT" not in text
    assert "Nos" not in text
    # The quantity itself is nowhere on the page.
    assert "17" not in text
    # What it does carry is unchanged.
    assert "4-CPU" in text and "Central Processor Module" in text


# --- Country of Origin and the warranty certificate ------------------------


def _coo_sheet(tmp_path, rows):
    """The company's Country of Origin sheet: model, description, made in,
    shipped from -- with a header row, as the real one has."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SL.", "MODEL", "DESCRIPTION", "", "MADE IN", "SHIPPED FROM"])
    for row in rows:
        ws.append(row)
    path = tmp_path / "submittal builder" / "Country Of Origin" / "COO.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _warranty_docx(tmp_path, paragraphs):
    """A .docx is a zip of XML; the reader only needs word/document.xml."""
    import zipfile

    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    xml = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    path = tmp_path / "submittal builder" / "templates" / "Draft Warranty.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


DRAFT = [
    "DRAFT WARRANTY FOR FIRE ALARM &amp; VOICE EVACUATION SYSTEM",
    "DATE: 16/06/2025",
    "REF NO.: LTR/2028/MR/25",
    "PROJECT: Samana IVY Garden-2",
    "CLIENT: M/s. Samana IFM Holding Ltd",
    "CONSULTANT: M/s. Al Hilal Engineering Consultant",
    "CONTRACTOR: M/s. ITALTECH CONTRACTING L.L.C",
    "MEP CONTRACTOR : M/s. ITALTECH CONTRACTING L.L.C",
    "This is to certify that all fire alarm system materials manufactured &amp; supplied by "
    "M/s. EDWARDS are warranted in the above project for a period of TWO YEARS from TOC "
    "subject to the following terms and conditions.",
    "This warranty is interpreted according to the laws of United Arab Emirates.",
]


# The warranty is issued as the company's letter: the .docx is filled in and
# converted by Word. These tests exercise the filling, which is the part that
# is ours -- driving Word from a test suite is slow and crashes the runner,
# and the conversion is Word's behaviour, not this code's.


def _filled(tmp_path, project, years=None):
    """The draft's text after filling, read back out of the .docx."""
    from app.services.submittal_package import (
        WARRANTY_YEARS, _docx_paragraphs, _fill_docx, warranty_replacements,
    )

    template = _warranty_docx(tmp_path, DRAFT)
    data = _fill_docx(template, warranty_replacements(project, years or WARRANTY_YEARS))
    assert data is not None
    out = tmp_path / "filled.docx"
    out.write_bytes(data)
    return "\n".join(_docx_paragraphs(out))


def test_the_warranty_period_is_one_year_whatever_the_draft_says(client, db_session, tmp_path):
    """The draft in the builder still reads TWO YEARS. The standard is one
    year, and it is stated in Python so every project gets the same one."""
    from app.services.submittal_package import WARRANTY_YEARS

    _login_admin(client)
    project = _db_project(db_session, _project(client))

    text = _filled(tmp_path, project)

    assert WARRANTY_YEARS == 1
    assert "ONE YEAR" in text
    assert "TWO YEARS" not in text


def test_the_warranty_keeps_the_company_wording_and_takes_the_project_parties(client, db_session, tmp_path):
    _login_admin(client)
    project = _db_project(db_session, _project(client))

    text = _filled(tmp_path, project)

    # The company's own clause, untouched.
    assert "interpreted according to the laws of United Arab Emirates" in text
    # This project's parties, not the draft's.
    assert "Binghatti Skyblade".upper() in text.upper()
    assert "Silver Stone".upper() in text.upper()
    assert "Samana IVY Garden" not in text
    assert "Al Hilal" not in text
    assert f"REF NO.: EP-{project.ep_number}" in text


def test_filling_does_not_double_escape_an_ampersand(client, db_session, tmp_path):
    """The XML holds "&" as "&amp;". Escaping a rewritten line without
    unescaping it first put "&amp;" on the issued letter."""
    _login_admin(client)
    project = _db_project(db_session, _project(client))

    text = _filled(tmp_path, project)

    assert "&amp;" not in text
    assert "manufactured & supplied by" in text


def test_the_letter_is_used_when_word_can_convert_it(client, db_session, tmp_path, monkeypatch):
    """What goes into the package is the converted letterhead, not a page
    drawn from its words."""
    import app.services.submittal_package as package
    from app.services.submittal_package import build_warranty

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)
    _warranty_docx(tmp_path, DRAFT)

    converted = pymupdf.open()
    converted.new_page(width=595, height=842).insert_text((50, 60), "THE COMPANY LETTERHEAD")
    monkeypatch.setattr(package, "docx_to_pdf", lambda data: converted.tobytes())

    doc = build_warranty(project, library)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    converted.close()

    assert "THE COMPANY LETTERHEAD" in text
    assert "Word was not available" not in text


def test_without_word_the_fallback_says_it_is_not_the_letterhead(client, db_session, tmp_path, monkeypatch):
    """A warranty that quietly did not look like the company's letter would
    be issued by someone who never noticed."""
    import app.services.submittal_package as package
    from app.services.submittal_package import build_warranty

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)
    _warranty_docx(tmp_path, DRAFT)
    monkeypatch.setattr(package, "docx_to_pdf", lambda data: None)

    doc = build_warranty(project, library)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "Word was not available" in text
    # It still carries the right period and this project's parties.
    assert "ONE YEAR" in text
    assert "Binghatti Skyblade".upper() in text.upper()


def test_no_warranty_draft_says_so_rather_than_inventing_one(client, db_session, tmp_path, monkeypatch):
    import app.services.submittal_package as package
    from app.services.submittal_package import build_warranty

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)          # no Draft Warranty.docx in it
    monkeypatch.setattr(package, "docx_to_pdf", lambda data: None)

    doc = build_warranty(project, library)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "was not found in the submittal builder" in text
    assert "ONE YEAR" in text


def test_country_of_origin_comes_from_the_company_reference_sheet(client, db_session, tmp_path):
    from app.services.submittal_package import read_origins

    library = _library(tmp_path)
    _coo_sheet(tmp_path, [
        ["A", "EST4", "Intelligent Fire Alarm Control Panel", "", "USA/MEXICO/CANADA", "NETHERLAND"],
        ["", "SIGA-AA50", "Intelligent Audio Amplifiers", "", "MEXICO", "NETHERLAND"],
    ])

    origins = read_origins(library)

    assert origins["EST4"] == ("USA/MEXICO/CANADA", "NETHERLAND")
    assert origins["SIGAAA50"] == ("MEXICO", "NETHERLAND")
    # The header row is not an entry.
    assert "MODEL" not in origins


def test_a_row_covering_a_set_declares_every_part_it_names(client, db_session, tmp_path):
    """"PANEL ACCESSORIES" carries one origin for the parts listed in its
    description -- and those are the numbers a BOQ actually quotes, so
    without reading them every panel module comes back blank."""
    from app.services.submittal_package import read_origins

    library = _library(tmp_path)
    _coo_sheet(tmp_path, [
        ["", "PANEL ACCESSORIES", "4-CPU, 4-PPS/M, 4- FWAL4, 4-NET- TP", "", "USA/MEXICO/CANADA", "NETHERLAND"],
    ])

    origins = read_origins(library)

    assert origins["4CPU"] == ("USA/MEXICO/CANADA", "NETHERLAND")
    assert origins["4PPSM"] == ("USA/MEXICO/CANADA", "NETHERLAND")
    # Wrapped in the sheet as "4- FWAL4" and "4-NET- TP"; the key strips that.
    assert origins["4FWAL4"] == ("USA/MEXICO/CANADA", "NETHERLAND")
    assert origins["4NETTP"] == ("USA/MEXICO/CANADA", "NETHERLAND")


def test_an_unlisted_part_is_left_blank_not_guessed(client, db_session, tmp_path):
    """Country of origin is a customs declaration. A model the reference
    sheet does not list is for an engineer to complete."""
    from app.services.submittal_package import build_country_of_origin

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)
    _coo_sheet(tmp_path, [["", "4-CPU", "Central Processor Module", "", "USA", "NETHERLAND"]])

    doc = build_country_of_origin(project, library, "FAS")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "USA" in text                                  # the one it knows
    assert "to be completed before issue" in text         # and it says so
    # 4-MIC is in the BOQ and not on the sheet: no country invented for it.
    assert "4-MIC" in text


def test_country_of_origin_uses_the_same_blocks_as_the_schedule(client, db_session, tmp_path):
    from app.services.submittal_package import build_country_of_origin, schedule_blocks

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)
    _coo_sheet(tmp_path, [["", "4-CPU", "CPU", "", "USA", "NETHERLAND"]])

    doc = build_country_of_origin(project, library, "FAS")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "COUNTRY OF ORIGIN" in text
    assert "MADE IN" in text and "SHIPPED FROM" in text
    for _letter, title, _items in schedule_blocks(project, "FAS"):
        assert title.upper()[:40] in text.upper()


def test_both_sections_are_built_into_the_package(client, tmp_path, monkeypatch):
    """14 and 15 are drawn, not merged: their templates are .xlsx and .docx."""
    import app.routers.submittal as submittal_router

    import app.services.submittal_package as package

    library = _library(tmp_path)
    _coo_sheet(tmp_path, [["", "4-CPU", "CPU", "", "USA", "NETHERLAND"]])
    _warranty_docx(tmp_path, DRAFT)
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {})
    # The drawn fallback, so the suite never drives Word.
    monkeypatch.setattr(package, "docx_to_pdf", lambda data: None)
    _login_admin(client)
    project_id = _project(client)

    plan = client.get(f"/projects/{project_id}/submittal/package/plan?system_code=FAS").json()
    by_number = {s["number"]: s for s in plan["sections"]}
    assert by_number[15]["found"] == 1 and by_number[15]["missing"] == 0
    assert by_number[16]["found"] == 1 and by_number[16]["missing"] == 0

    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [15, 16], "system_code": "FAS", "revision": "R0"})
    assert resp.status_code == 200
    doc = pymupdf.open(stream=resp.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    assert "COUNTRY OF ORIGIN" in text
    assert "ONE YEAR" in text


# --- battery calculation, section 06 ---------------------------------------


def test_battery_calculation_sits_after_the_schedule_of_material():
    """Permanently: it is section 06 on every project, and the sections the
    printed template numbered 06 onwards each move down one."""
    numbers = dict(SECTIONS)
    assert numbers[5] == "Schedule of Material"
    assert numbers[6] == "Battery Calculation"
    assert numbers[7] == "Product Catalogue or Brochure"
    assert numbers[18] == "Others Documents (Certificates, Approvals Etc)"


def test_a_borrowed_divider_carries_the_new_section_name_and_number(client, db_session, tmp_path):
    """The printed template has no Battery Calculation divider, so one of
    the others is borrowed and its title and number rewritten -- the page
    has to look like the rest of the set."""
    from app.services.submittal_package import build_divider

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    divider = build_divider(library, 6, project)
    text = divider[0].get_text()
    divider.close()

    assert "Battery Calculation" in text
    assert "06" in text
    assert "Schedule of Material" not in text


def test_a_renumbered_section_keeps_the_template_artwork(client, db_session, tmp_path):
    """Technical Data Sheet was 07 in the printed template and is 08 now.
    The divider is found by name, and its number rewritten."""
    from app.services.submittal_package import build_divider

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    divider = build_divider(library, 8, project)
    text = divider[0].get_text()
    divider.close()

    assert "Technical Data Sheet" in text
    assert "08" in text
    assert "07" not in text


def test_the_battery_section_encloses_the_calculation(client, db_session, tmp_path):
    from app.services.submittal_package import build_package, plan_package

    _login_admin(client)
    project = _db_project(db_session, _project(client))
    library = _library(tmp_path)

    class _Panel:
        key, name, heading = "FAS|Panel|1", "FACP-01", "Panel"
        lines, settings, notes = [], {"standby_hours": 24, "alarm_minutes": 30, "spare_factor": 1.2}, []
        standby_ma = alarm_ma = standby_mah = alarm_mah = 0.0
        total_ah = required_ah = 0.0
        lower_bound = quoted_short = False
        quoted_ah = None
        selected = None

    plan = plan_package(project, {6}, library, tmp_path, {}, system_code="FAS", battery_panels=[_Panel()])
    built = build_package(project, plan, library, system_code="FAS", battery_panels=[_Panel()])

    labels = [label for label, _n, _a, _b in built.manifest]
    assert "06 Battery Calculation -- divider" in labels
    assert "06 Battery Calculation" in labels


def test_without_a_calculation_the_section_says_so(client, db_session, tmp_path):
    from app.services.submittal_package import plan_package

    _login_admin(client)
    project = _db_project(db_session, _project(client))

    plan = plan_package(project, {6}, _library(tmp_path), tmp_path, {}, system_code="FAS", battery_panels=[])

    battery = next(s for s in plan.sections if s.number == 6)
    assert battery.found == 0
    assert battery.note and "no calculation to enclose" in battery.note


def _brand_library(tmp_path):
    root = tmp_path / "submittal builder"
    _pdf(root / "COMMON" / "Company Profile" / "Company Profile.pdf", ["Company Profile"])
    _pdf(root / "EDWARDS" / "Test certificates" / "UL listing.pdf", ["UL"])
    _pdf(root / "MENVIER" / "test certificate" / "CE certificate.pdf", ["CE"])
    return root


def test_company_documents_come_from_the_submittal_s_own_brand(client, db_session, tmp_path):
    """One submittal per system, and the manufacturer's documents are the
    manufacturer's: the Menvier package carries Menvier's test certificate,
    the Edwards package Edwards', and both the company profile from COMMON."""
    from app.models import Project

    _login_admin(client)
    pid = client.post("/projects", json={"ep_number": "40410", "project_name": "P", "design_sheets": []}).json()["id"]
    project = db_session.get(Project, pid)
    root = _brand_library(tmp_path)

    menvier = plan_package(project, {1, 12}, root, None, brand="MENVIER")
    by_number = {s.number: s for s in menvier.sections}
    assert [d.name for d in by_number[12].documents] == ["CE certificate.pdf"]
    assert [d.name for d in by_number[1].documents] == ["Company Profile.pdf"]

    edwards = plan_package(project, {12}, root, None, brand="EDWARDS")
    assert [d.name for d in {s.number: s for s in edwards.sections}[12].documents] == ["UL listing.pdf"]

    # No brand known: only what COMMON (or the old flat layout) holds.
    none = plan_package(project, {12}, root, None)
    assert [d.missing_reason for d in {s.number: s for s in none.sections}[12].documents] == ["Not in the submittal builder."]


def test_a_recorded_datasheet_is_taken_before_the_library_is_searched(client, db_session, tmp_path):
    """SL2-65D3D-CGL-M has no sheet of its own and its sheet calls it
    SL2MNM65D3D, so no filename or text match finds it: the equipment table
    records which sheet it is on, and the package takes that."""
    from app.models import Project
    from app.services import equipment_currents
    from app.services.datasheet_library import DatasheetLibrary

    _login_admin(client)
    pid = client.post("/projects", json={"ep_number": "40411", "project_name": "P", "design_sheets": []}).json()["id"]
    client.put(f"/projects/{pid}/boq", json=[
        {"system_code": "ELS", "manufacturer": "MENVIER", "catalog_no": "SL2-65D3D-CGL-M", "description": "Emergency light", "quantity": "73"},
        {"system_code": "ELS", "manufacturer": "MENVIER", "catalog_no": "SL210DI", "description": "Pictogram", "quantity": "3"},
    ])
    project = db_session.get(Project, pid)
    folder = tmp_path / "MENVIER"
    _pdf(folder / "SL2-42D3D-CGL-M.pdf", ["SL2MNM42D3D SL2MNM65D3D"])
    libraries = {"MENVIER": DatasheetLibrary("MENVIER", folder)}

    # Without the record the sheet is not found.
    assert datasheet_documents(project, libraries, "ELS")[0].missing_reason == "No datasheet in the manufacturer's library."

    equipment_currents.upsert(db_session, part_no="SL2-65D3D-CGL-M", source="assigned by the platform owner",
                              manufacturer="MENVIER", datasheet={"datasheet_library": "MENVIER", "datasheet_path": "SL2-42D3D-CGL-M.pdf",
                                                                  "datasheet_pages": [], "datasheet_match": "mapped", "datasheet_sha256": None})
    documents = datasheet_documents(project, libraries, "ELS", links=equipment_currents.index(db_session))
    assert documents[0].name == "SL2-42D3D-CGL-M.pdf" and documents[0].covers == ["SL2-65D3D-CGL-M"]
    assert documents[1].missing_reason == "No datasheet in the manufacturer's library."


def test_a_built_package_is_filed_in_the_project_folder_and_entered_in_the_register_and_the_log(client, db_session, tmp_path, monkeypatch):
    """The package goes to 02- Material Submittals/FA/R0/, and the tab, the
    index and the log know it at once -- no scan, no model."""
    import app.routers.submittal as submittal_router
    from app.models import DocumentReading, ProjectDocument, ProjectSubmittal

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login_admin(client)
    root = tmp_path / "EP-30785"
    root.mkdir()
    project_id = client.post("/projects", json={
        "ep_number": "30785", "project_name": "Binghatti Titania", "source_folder_path": str(root), "design_sheets": [],
    }).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[{"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-CPU",
                                                     "description": "Central Processor Module", "quantity": "1", "manufacturer": "EDWARDS"}])

    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [1, 8], "system_code": "FAS", "revision": "R0"})
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    filed = root / "02- Material Submittals" / "FA" / "R0" / "EP-30785 - Material Submittal - FA - R0.pdf"
    assert filed.is_file() and filed.read_bytes() == resp.content
    assert resp.headers["X-Package-Filed"].replace("%20", " ") == "02- Material Submittals/FA/R0/EP-30785 - Material Submittal - FA - R0.pdf"
    assert resp.headers["X-Package-Reference"] == "EP-30785-MAS-FA"

    row = db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).one()
    assert (row.role, row.reference, row.revision, row.status, row.system_code, row.state) == ("submittal_form", "EP-30785-MAS-FA", "R0", "UR", "FAS", "fresh")
    reading = db_session.get(DocumentReading, row.reading_id)
    assert reading.kind == "submittal_form" and reading.reading["reference"] == "EP-30785-MAS-FA" and reading.calls == 0
    register = db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project_id).one()
    assert (register.reference, register.revision, register.status.value, register.system_code) == ("EP-30785-MAS-FA", "R0", "under_review", "FAS")
    logs = client.get(f"/projects/{project_id}/logs").json()
    assert [(m["reference"], m["revision"]) for m in logs["material_submittals"]] == [("EP-30785-MAS-FA", "R0")]

    # R1 of the same submittal: its own folder, the register moves to R1, the index holds both.
    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [1, 8], "system_code": "FAS", "revision": "R1"})
    assert resp.status_code == 200
    assert (root / "02- Material Submittals" / "FA" / "R1" / "EP-30785 - Material Submittal - FA - R1.pdf").is_file()
    db_session.refresh(register)
    assert register.revision == "R1"
    assert db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count() == 2

    # file=false: the download only, nothing written.
    resp = client.post(f"/projects/{project_id}/submittal/package",
                       json={"sections": [1, 8], "system_code": "FAS", "revision": "R2", "file": False})
    assert resp.status_code == 200 and "X-Package-Filed" not in resp.headers
    assert not (root / "02- Material Submittals" / "FA" / "R2").exists()


def test_a_model_with_a_slash_or_a_wildcard_digit_is_the_part_the_boq_quotes(client, db_session, tmp_path):
    """"APS6A/230" is one part number, not APS6A and 230; "757-XA-SS70" on
    the sheet covers 757-3A-SS70 and 757-7A-SS70. Both used to come back
    blank on the package although the sheet had them."""
    from app.services.submittal_package import read_origins

    library = _library(tmp_path)
    _coo_sheet(tmp_path, [
        ["", "APS6A/230", "Auxiliary power supply, 6Amps, 230V", "", "CHINA", "NETHERLAND"],
        ["", "757-XA-SS70", "Speaker/strobe", "", "CHINA", "NETHERLAND"],
        ["", "STI-1230/STI-3002", "Stopper/ Gasket", "", "USA", "NETHERLAND"],
    ])

    origins = read_origins(library)

    assert origins["APS6A230"] == ("CHINA", "NETHERLAND")
    assert origins["7573ASS70"] == ("CHINA", "NETHERLAND") and origins["7577ASS70"] == ("CHINA", "NETHERLAND")
    assert origins["STI1230"] == ("USA", "NETHERLAND") and origins["STI3002"] == ("USA", "NETHERLAND")   # the pair still


def test_a_scanned_spelling_of_a_part_finds_its_origin_under_the_catalogue_s(client, db_session, tmp_path):
    """A BOQ line still holding the scan's "SIGA-AASO" (S for 5, O for 0)
    gets SIGA-AA50's origin; a part the sheet does not know stays blank."""
    from app.services.submittal_package import build_country_of_origin

    _login_admin(client)
    line = lambda part, desc: {"system_code": "FAS", "group_heading": PANEL, "catalog_no": part,  # noqa: E731
                               "description": desc, "quantity": "1", "manufacturer": "EDWARDS"}
    project = _db_project(db_session, _project(client, lines=[
        line("SIGA-AASO", "Intelligent Audio Amplifier - 50 Watt"),
        line("3-CABSB", "Backbox, black. Supports five Local Rail Modules."),
        line("4-XYZ", "Mystery module"),
    ]))
    library = _library(tmp_path)
    _coo_sheet(tmp_path, [
        ["", "SIGA-AA50", "Intelligent Audio Amplifiers", "", "MEXICO", "NETHERLAND"],
        ["", "3-CAB5B", "Backbox, black", "", "CANADA", "NETHERLAND"],
    ])

    doc = build_country_of_origin(project, library, "FAS")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    assert "MEXICO" in text and "CANADA" in text
    assert "to be completed before issue" in text      # 4-XYZ, still unknown


def test_a_revision_already_prepared_is_refused_unless_replaced_and_deleting_takes_the_file_with_it(client, db_session, tmp_path, monkeypatch):
    import app.routers.submittal as submittal_router
    from app.models import ProjectDocument, ProjectSubmittal

    library = _library(tmp_path)
    monkeypatch.setattr(submittal_router, "_submittal_library", lambda: library)
    monkeypatch.setattr(submittal_router, "get_libraries", lambda *_: {"EDWARDS": _datasheets(tmp_path)})
    _login_admin(client)
    root = tmp_path / "EP-30786"
    root.mkdir()
    project_id = client.post("/projects", json={
        "ep_number": "30786", "project_name": "Titania", "source_folder_path": str(root), "design_sheets": [],
    }).json()["id"]
    client.put(f"/projects/{project_id}/boq", json=[{"system_code": "FAS", "group_heading": PANEL, "catalog_no": "4-CPU",
                                                     "description": "Central Processor Module", "quantity": "1", "manufacturer": "EDWARDS"}])
    body = {"sections": [1, 8], "system_code": "FAS", "revision": "R0"}
    assert client.post(f"/projects/{project_id}/submittal/package", json=body).status_code == 200
    filed = root / "02- Material Submittals" / "FA" / "R0" / "EP-30786 - Material Submittal - FA - R0.pdf"
    assert filed.is_file()

    # R0 again: refused, with the way out.
    again = client.post(f"/projects/{project_id}/submittal/package", json=body)
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert (detail["code"], detail["reference"], detail["revision"], detail["next_revision"]) == ("already_prepared", "EP-30786-MAS-FA", "R0", "R1")
    assert detail["filed"].endswith("EP-30786 - Material Submittal - FA - R0.pdf")

    # Replacing: the same file, written again; still one index row.
    before = filed.stat().st_mtime_ns
    assert client.post(f"/projects/{project_id}/submittal/package", json={**body, "replace": True}).status_code == 200
    assert filed.is_file() and filed.stat().st_mtime_ns >= before
    assert db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id, ProjectDocument.state != "removed").count() == 1

    # The next revision is welcome.
    assert client.post(f"/projects/{project_id}/submittal/package", json={**body, "revision": "R1"}).status_code == 200
    assert (root / "02- Material Submittals" / "FA" / "R1" / "EP-30786 - Material Submittal - FA - R1.pdf").is_file()

    # Deleting the submittal from the register: both filed revisions go from the folder, the index says removed, the register is empty.
    register = client.get(f"/projects/{project_id}/submittals").json()["items"]
    assert len(register) == 1 and register[0]["revision"] == "R1"
    gone = client.delete(f"/projects/{project_id}/submittals/{register[0]['id']}")
    assert gone.status_code == 200, gone.text
    result = gone.json()
    assert result["reference"] == "EP-30786-MAS-FA" and result["register_rows"] == 1 and len(result["files"]) == 2
    assert not filed.exists() and not (root / "02- Material Submittals" / "FA" / "R1" / "EP-30786 - Material Submittal - FA - R1.pdf").exists()
    assert {r.state for r in db_session.query(ProjectDocument).filter(ProjectDocument.project_id == project_id)} == {"removed"}
    assert db_session.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project_id).count() == 0
    assert client.get(f"/projects/{project_id}/logs").json()["material_submittals"] == []
    assert client.get(f"/projects/{project_id}/submittals/map").json()["submittals"] == 0

    # By reference, from the Logs tab: nothing left under it now.
    assert client.post(f"/projects/{project_id}/submittals/delete", json={"reference": "EP-30786-MAS-FA"}).status_code == 404
    # R0 can be prepared again after the deletion.
    assert client.post(f"/projects/{project_id}/submittal/package", json=body).status_code == 200
