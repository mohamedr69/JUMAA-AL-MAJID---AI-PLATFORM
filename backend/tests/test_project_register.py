"""The project register: read from the workbook, and watched for edits."""

import time

import pytest
from openpyxl import Workbook, load_workbook

from app.services import project_register as register

HEADERS = [
    "DRF No.", "SE/ E #", "Job No.", "Scope", "Project Name", "Client Name", "Consultant",
    "MEP Contractor", "Project Started", "System", "Product Name", "Action Date",
    "Material Approval Status", "Project Status", "Design Engineer", "YEAR",
    "Quotation / LOI Details", "Project Value", "File Location", "Remarks", "O&M",
]


def _row(ep, name, system, product, status, engineer, action):
    """One register row, in the workbook's column order."""
    filled = [None] * len(HEADERS)
    filled[1], filled[4], filled[9], filled[10] = ep, name, system, product
    filled[13], filled[14], filled[11] = status, engineer, action
    return filled


@pytest.fixture()
def register_file(tmp_path, monkeypatch):
    """A small register of our own, so the tests do not depend on the
    company's workbook being reachable from this machine."""
    from datetime import datetime

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(HEADERS)
    # One job, two systems: the fire alarm and the emergency light, which
    # is how the register holds a job with both.
    sheet.append(_row("EP-100", "Tower One", "FAS", "EDWARDS", "On Going", "Ameer", datetime(2026, 9, 1)))
    sheet.append(_row("EP-100", "Tower One", "ELM", "Menvier", "On Going", "Ameer", datetime(2026, 9, 3)))
    # A job shared between two engineers, in different divisions.
    sheet.append(_row("EP-200", "Mall Two", "CCTV", "HIKVISION", "Completed", "Sohail", datetime(2026, 8, 1)))
    sheet.append(_row("EP-200", "Mall Two", "FAS", "EDWARDS", "On Hold", "Ameer", datetime(2026, 7, 1)))
    # Not a job: the register uses "N/A" where there is no EP number.
    sheet.append(_row("N/A", "No number", "FAS", "EDWARDS", "On Going", "Ameer", datetime(2026, 6, 1)))
    # Not an engineer: an office, which the manager asked be left out.
    sheet.append(_row("EP-300", "Office job", "FAS", "EDWARDS", "On Going", "Abudhabi Office", datetime(2026, 5, 1)))

    path = tmp_path / "register.xlsx"
    workbook.save(path)
    workbook.close()
    monkeypatch.setattr(register, "register_path", lambda: path)
    monkeypatch.setattr(register, "_cache", None)
    return path


def test_rows_are_one_job_newest_first(register_file):
    jobs = register.projects()
    assert [j.ep_number for j in jobs] == ["EP-100", "EP-200", "EP-300"]

    tower = jobs[0]
    assert tower.systems == ["FAS", "ELM"]
    # The newest action across the job's rows is what "latest" orders by.
    assert tower.last_action.date().isoformat() == "2026-09-03"
    # "Menvier" and "EDWARDS" are recorded one way, as brands are everywhere.
    assert tower.products == ["EDWARDS", "MENVIER"]


def test_a_job_without_an_ep_number_is_not_a_job(register_file):
    assert all(j.ep_number != "N/A" for j in register.projects())


def test_divisions_follow_the_engineer_not_the_system(register_file):
    """EP-200 has a CCTV row and a fire alarm row. It is Sohail's in ELV
    and Ameer's in fire alarm, because a division is its engineers."""
    by_key = {d.key: d for d in register.divisions()}
    fire = by_key[register.FIRE_ALARM]
    elv = by_key[register.ELV]

    assert [e.name for e in fire.engineers] == ["Ameer"]
    assert [e.name for e in elv.engineers] == ["Sohail"]
    # EP-200 counts in both, so the totals are larger than the job count.
    assert fire.total == 2 and elv.total == 1


def test_an_office_is_not_an_engineer(register_file):
    """"Abudhabi Office" carries EP-300 in the register and is left out, so
    that job has nobody -- it must not invent a twenty-fourth engineer."""
    everyone = {e.name for d in register.divisions() for e in d.engineers}
    assert "Abudhabi Office" not in everyone


def test_the_workbook_is_watched_for_edits(register_file):
    """The register is edited while the page is open, so the reader keys
    its cache on the file's timestamp and size and reads again when they
    move. `revision` is what an open page polls; it must move too."""
    before = register.revision()
    assert register.projects()[0].project_name == "Tower One"

    workbook = load_workbook(register_file)
    workbook["Sheet1"].cell(row=2, column=5).value = "Tower One Renamed"
    # Far enough apart that the filesystem records a different timestamp.
    time.sleep(1.1)
    workbook.save(register_file)
    workbook.close()

    assert register.revision() != before
    assert register.projects()[0].project_name == "Tower One Renamed"


def test_a_register_that_is_not_there_says_so(tmp_path, monkeypatch):
    """Missing is not empty: a page shown an empty register would read it
    as a company with no jobs."""
    missing = tmp_path / "nowhere.xlsx"
    monkeypatch.setattr(register, "_cache", None)
    monkeypatch.setattr(register.get_settings(), "project_register", str(missing), raising=False)
    with pytest.raises(register.RegisterUnavailable):
        register.register_path()
    assert register.revision() is None


def test_the_roster_folds_one_person_entered_twice(register_file):
    """The register spells one engineer "Wahab" and "wahab"; they are one
    person and the roster's spelling wins."""
    assert register.engineer_of("wahab") is register.engineer_of("Wahab")
    assert register.engineer_of("Wahab").name == "Wahab"
    assert register.engineer_of("Abudhabi Office") is None
    # Someone the roster has not met is not an engineer it can place, but
    # the caller keeps their name rather than dropping the job.
    assert register.engineer_of("Someone New") is None
