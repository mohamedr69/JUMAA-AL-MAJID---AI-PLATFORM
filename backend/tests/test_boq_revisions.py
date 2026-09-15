from io import BytesIO

from openpyxl import load_workbook

from app.models import RoleEnum
from app.schemas_project import ProjectBoqItemIn
from app.services.boq_revisions import compare_boq

from .conftest import login, make_user
from .test_projects import _login_admin, _payload_without_documents, _valid_project_payload


def line(description, quantity="1", **fields) -> ProjectBoqItemIn:
    return ProjectBoqItemIn(system_code="FAS", description=description, quantity=quantity, **fields)


# --- compare_boq ---


def test_compare_reports_added_removed_and_changed_lines():
    before = [line("CPU", "1", catalog_no="4-CPU"), line("Battery", "2"), line("Sounder", "40")]
    after = [line("CPU", "1", catalog_no="4-CPU"), line("Sounder", "42"), line("Isolator", "5")]

    changes = compare_boq(before, after)
    assert [(c.kind, (c.after or c.before).description) for c in changes] == [
        ("changed", "Sounder"),
        ("added", "Isolator"),
        ("removed", "Battery"),
    ]
    assert changes[0].fields == ["quantity"]
    assert (changes[0].before.quantity, changes[0].after.quantity) == ("40", "42")


def test_compare_ignores_equal_prices_and_order():
    before = [line("Sounder", unit_price="250.00"), line("Detector")]
    after = [line("Detector"), line("Sounder", unit_price="250")]
    assert compare_boq(before, after) == []


def test_cleaning_an_ocr_artifact_is_a_change_to_the_same_line():
    """Case, spacing and punctuation don't stop two versions of a line
    pairing up -- but tidying them is still an edit, and must show."""
    before = [line("Surface Mounted Emergency Light |", catalog_no="/ SIGA PS")]
    after = [line("Surface Mounted Emergency Light", catalog_no="SIGA-PS")]

    changes = compare_boq(before, after)
    assert [(c.kind, c.fields) for c in changes] == [("changed", ["catalog_no", "description"])]


def test_compare_treats_a_new_part_number_as_a_different_line():
    """Swapping the part is replacing the item, not editing it."""
    changes = compare_boq([line("Detector", catalog_no="SIGA-PS")], [line("Detector", catalog_no="SIGA-HRS")])
    assert sorted(c.kind for c in changes) == ["added", "removed"]


def test_compare_pairs_repeated_lines_one_to_one():
    """One of two identical lines going is one removal, not two -- nor none."""
    before = [line("Filler plate"), line("Filler plate")]
    changes = compare_boq(before, [line("Filler plate")])
    assert [c.kind for c in changes] == ["removed"]


def test_compare_keeps_systems_apart():
    fas = ProjectBoqItemIn(system_code="FAS", description="Battery 12V 7Ah", quantity="2")
    els = ProjectBoqItemIn(system_code="ELS", description="Battery 12V 7Ah", quantity="2")
    assert sorted(c.kind for c in compare_boq([fas], [els])) == ["added", "removed"]


# --- API ---


def _boq(quantity="2") -> list[dict]:
    return [
        {"system_code": "FAS", "catalog_no": "4-CPU", "description": "Central Processor Module", "quantity": "1"},
        {"system_code": "FAS", "catalog_no": "12V65A", "description": "Battery, 12 V @ 65 AH", "quantity": quantity},
    ]


def _project_with_boq(client, ep: str) -> int:
    _login_admin(client)
    pid = client.post("/projects", json=_payload_without_documents(ep)).json()["id"]
    client.put(f"/projects/{pid}/boq", json=_boq())
    return pid


def test_issue_revisions_numbered_from_rev_00(client):
    pid = _project_with_boq(client, "37000")

    first = client.post(f"/projects/{pid}/boq/revisions", json={"note": " Issued for approval "})
    assert first.status_code == 201
    assert first.json()["label"] == "Rev 00"
    assert first.json()["note"] == "Issued for approval"
    assert first.json()["line_count"] == 2
    assert first.json()["issued_by_name"]

    client.put(f"/projects/{pid}/boq", json=_boq(quantity="4"))
    second = client.post(f"/projects/{pid}/boq/revisions", json={})
    assert second.json()["label"] == "Rev 01"

    listed = client.get(f"/projects/{pid}/boq/revisions").json()
    assert [r["label"] for r in listed] == ["Rev 01", "Rev 00"]  # newest first


def test_issuing_an_unchanged_boq_is_refused(client):
    """Also what stops a double-click issuing the same BOQ twice."""
    pid = _project_with_boq(client, "37100")
    assert client.post(f"/projects/{pid}/boq/revisions", json={}).status_code == 201

    again = client.post(f"/projects/{pid}/boq/revisions", json={})
    assert again.status_code == 409
    assert "Rev 00" in again.json()["detail"]


def test_a_cleaned_description_can_be_issued_but_a_reordering_cannot(client):
    """The server's "nothing changed" check is the comparison the Revisions
    page shows, so the two agree on what is issuable."""
    pid = _project_with_boq(client, "37150")
    client.post(f"/projects/{pid}/boq/revisions", json={})

    client.put(f"/projects/{pid}/boq", json=list(reversed(_boq())))
    assert client.post(f"/projects/{pid}/boq/revisions", json={}).status_code == 409

    cleaned = _boq()
    cleaned[1]["description"] = "Battery, 12V @ 65AH"
    client.put(f"/projects/{pid}/boq", json=cleaned)
    assert client.post(f"/projects/{pid}/boq/revisions", json={}).json()["label"] == "Rev 01"


def test_an_empty_boq_cannot_be_issued(client):
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload("37200")).json()["id"]
    assert client.post(f"/projects/{pid}/boq/revisions", json={}).status_code == 400


def test_an_issued_revision_does_not_follow_later_edits(client):
    pid = _project_with_boq(client, "37300")
    client.post(f"/projects/{pid}/boq/revisions", json={})

    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "description": "Everything replaced"}])

    rev = client.get(f"/projects/{pid}/boq/revisions/0").json()
    assert [i["description"] for i in rev["items"]] == ["Central Processor Module", "Battery, 12 V @ 65 AH"]
    assert rev["items"][1]["quantity"] == "2"


def test_compare_revision_to_revision_and_to_current(client):
    pid = _project_with_boq(client, "37400")
    client.post(f"/projects/{pid}/boq/revisions", json={})
    client.put(f"/projects/{pid}/boq", json=_boq(quantity="4"))
    client.post(f"/projects/{pid}/boq/revisions", json={})
    client.put(f"/projects/{pid}/boq", json=_boq(quantity="4")[:1])

    between = client.get(f"/projects/{pid}/boq/compare", params={"from_rev": 0, "to_rev": 1}).json()
    assert (between["from_label"], between["to_label"]) == ("Rev 00", "Rev 01")
    assert [(c["kind"], c["fields"]) for c in between["changes"]] == [("changed", ["quantity"])]

    since = client.get(f"/projects/{pid}/boq/compare", params={"from_rev": 1}).json()
    assert since["to_label"] == "Current"
    assert [(c["kind"], c["before"]["catalog_no"]) for c in since["changes"]] == [("removed", "12V65A")]

    assert client.get(f"/projects/{pid}/boq/compare", params={"from_rev": 9}).status_code == 404


def test_export_a_revision(client):
    pid = _project_with_boq(client, "37500")
    client.post(f"/projects/{pid}/boq/revisions", json={})
    client.put(f"/projects/{pid}/boq", json=[{"system_code": "FAS", "description": "Later line"}])

    resp = client.get(f"/projects/{pid}/boq/revisions/0/export.xlsx")
    assert resp.status_code == 200
    assert 'filename="EP-37500 BOQ Rev 00.xlsx"' in resp.headers["content-disposition"]
    fas = load_workbook(BytesIO(resp.content))["FAS"]
    assert fas["A2"].value.startswith("Bill of Quantities — Rev 00, issued ")
    # the revision's lines, not the BOQ as it is now
    assert fas["E6"].value == "Central Processor Module"

    assert client.get(f"/projects/{pid}/boq/revisions/3/export.xlsx").status_code == 404


def test_viewers_read_revisions_but_cannot_issue(client, db_session):
    pid = _project_with_boq(client, "37600")
    client.post(f"/projects/{pid}/boq/revisions", json={})
    client.put(f"/projects/{pid}/boq", json=_boq(quantity="9"))
    client.post("/auth/logout")

    make_user(db_session, "viewer7@ep-platform.com", RoleEnum.viewer)
    login(client, "viewer7@ep-platform.com")
    assert client.get(f"/projects/{pid}/boq/revisions").status_code == 200
    assert client.get(f"/projects/{pid}/boq/revisions/0").status_code == 200
    assert client.get(f"/projects/{pid}/boq/compare", params={"from_rev": 0}).status_code == 200
    assert client.post(f"/projects/{pid}/boq/revisions", json={}).status_code == 403


def test_revisions_go_away_with_the_project(client, db_session):
    from app.models import ProjectBoqRevision

    pid = _project_with_boq(client, "37700")
    client.post(f"/projects/{pid}/boq/revisions", json={})
    assert client.delete(f"/projects/{pid}").status_code == 204
    assert db_session.query(ProjectBoqRevision).filter_by(project_id=pid).count() == 0
