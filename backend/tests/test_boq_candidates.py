"""Re-reading the Design Sheets never replaces the BOQ: it compares, the
engineer decides each change, and the BOQ as it was is kept."""

import app.routers.projects as projects_router
from app.models import BoqSnapshot
from app.services.design_sheet_extractor import ExtractedBoqLine

from .test_projects import _login_admin, _valid_project_payload


def _line(catalog, description, quantity, group="Main Panel"):
    return ExtractedBoqLine(catalog_no=catalog, description=description, quantity=quantity, group_heading=group,
                            confidence=90.0, page=1)


FIRST_READ = [
    _line("4-CPU", "Central Processor Module", "1"),
    _line("SIGA-PS", "Photoelectric smoke detector", "120", "Field Devices"),
    _line("SIGA-CT1", "Single input module", "14", "Field Devices"),
    _line("SIGA-HFS", "Heat detector", "30", "Field Devices"),
]
SECOND_READ = [
    _line("4-CPU", "Central Processor Module", "1"),                          # unchanged
    _line("SIGA-PS", "Photoelectric smoke detector", "126", "Field Devices"),  # quantity changed
    _line("SIGA-CT1", "Single input monitor module", "14", "Field Devices"),   # same part, wording differs
    _line("SIGA-CC1", "Synchronised output module", "6", "Field Devices"),      # new
    # SIGA-HFS no longer read
]


def _stub(monkeypatch, reads):
    state = {"lines": reads}
    monkeypatch.setattr(projects_router, "extract_boq_lines", lambda path: list(state["lines"]))
    return state


def _editable(item):
    keys = ("id", "system_code", "group_heading", "manufacturer", "catalog_no", "description", "quantity", "unit",
            "unit_price", "total_price", "remarks")
    return {k: item.get(k) for k in keys}


def _project_with_boq(client, monkeypatch, ep="61001"):
    state = _stub(monkeypatch, FIRST_READ)
    _login_admin(client)
    pid = client.post("/projects", json=_valid_project_payload(ep)).json()["id"]
    body = client.post(f"/projects/{pid}/boq/ensure").json()
    assert len(body["items"]) == 4
    return pid, body, state


def test_a_reread_compares_and_applies_only_what_the_engineer_takes(client, db_session, monkeypatch):
    pid, body, state = _project_with_boq(client, monkeypatch)
    # The engineer puts in a price and types a line the sheet does not have.
    rows = [_editable(i) for i in body["items"]]
    rows[1]["unit_price"] = "55.00"
    rows.append({"system_code": "FAS", "description": "Commissioning", "quantity": "Lot"})
    saved = client.put(f"/projects/{pid}/boq", json=rows, headers={"If-Match": str(body["version"])})
    assert saved.status_code == 200
    before = client.get(f"/projects/{pid}/boq").json()

    state["lines"] = SECOND_READ
    built = client.post(f"/projects/{pid}/boq/candidates")
    assert built.status_code == 201, built.text
    candidate = built.json()
    # Nothing in the BOQ moved.
    assert client.get(f"/projects/{pid}/boq").json() == before

    kinds = {(c["kind"], c["match"], (c["after"] or c["before"])["catalog_no"] or (c["after"] or c["before"])["description"])
             for c in candidate["changes"]}
    assert ("unchanged", "exact", "4-CPU") in kinds
    assert ("changed", "exact", "SIGA-PS") in kinds
    assert ("changed", "probable", "SIGA-CT1") in kinds
    assert ("added", None, "SIGA-CC1") in kinds
    assert ("removed", None, "SIGA-HFS") in kinds
    assert ("removed", None, "Commissioning") in kinds
    summary = candidate["summary"]
    assert (summary["old_lines"], summary["new_lines"], summary["probable"]) == (5, 4, 1)
    assert candidate["decisions_needed"] == 5

    by_catalog = {((c["after"] or c["before"])["catalog_no"] or (c["after"] or c["before"])["description"]): c
                  for c in candidate["changes"]}
    base = f"/projects/{pid}/boq/candidates/{candidate['id']}"
    # Every change needs a decision.
    partial = client.post(f"{base}/apply", json={"decisions": {by_catalog["SIGA-PS"]["id"]: "accept"}})
    assert partial.status_code == 400 and "need a decision" in partial.json()["detail"]

    decisions = {
        by_catalog["SIGA-PS"]["id"]: "accept",
        by_catalog["SIGA-CT1"]["id"]: "keep",
        by_catalog["SIGA-CC1"]["id"]: "accept",
        by_catalog["SIGA-HFS"]["id"]: "accept",       # take the removal
        by_catalog["Commissioning"]["id"]: "keep",    # the engineer's own line stays
    }
    applied = client.post(f"{base}/apply", json={"decisions": decisions})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == {"changed": 1, "added": 1, "removed": 1, "kept": 2, "sources_updated": 1}

    after = {i["catalog_no"] or i["description"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    assert set(after) == {"4-CPU", "SIGA-PS", "SIGA-CT1", "SIGA-CC1", "Commissioning"}
    assert after["SIGA-PS"]["quantity"] == "126" and after["SIGA-PS"]["unit_price"] == "55.00"   # price kept
    assert after["SIGA-CT1"]["description"] == "Single input module"                            # kept as it was
    assert after["SIGA-CC1"]["origin"] == "extracted" and after["SIGA-CC1"]["manufacturer"] == "EDWARDS"
    assert after["Commissioning"]["origin"] == "manual"

    # The BOQ as it was is kept, and can be put back.
    snapshots = client.get(f"/projects/{pid}/boq/snapshots").json()
    assert len(snapshots) == 1 and snapshots[0]["lines"] == 5
    assert db_session.query(BoqSnapshot).count() == 1
    assert client.get(base).json()["status"] == "applied"
    assert client.post(f"{base}/apply", json={"decisions": decisions}).status_code == 400

    restored = client.post(f"/projects/{pid}/boq/snapshots/{snapshots[0]['id']}/restore")
    assert restored.status_code == 200
    back = {i["catalog_no"] or i["description"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    assert back["SIGA-PS"]["quantity"] == "120" and "SIGA-HFS" in back and "SIGA-CC1" not in back
    assert back["SIGA-PS"]["origin"] == "extracted" and back["SIGA-PS"]["unit_price"] == "55.00"
    # Restoring was itself snapshotted.
    assert len(client.get(f"/projects/{pid}/boq/snapshots").json()) == 2


def test_a_reread_built_before_a_save_cannot_be_applied(client, monkeypatch):
    pid, body, state = _project_with_boq(client, monkeypatch, ep="61002")
    state["lines"] = SECOND_READ
    candidate = client.post(f"/projects/{pid}/boq/candidates").json()
    decisions = {c["id"]: "accept" for c in candidate["changes"] if c["kind"] != "unchanged"}

    rows = [_editable(i) for i in client.get(f"/projects/{pid}/boq").json()]
    rows[0]["remarks"] = "checked"
    client.put(f"/projects/{pid}/boq", json=rows)

    assert client.get(f"/projects/{pid}/boq/candidates/{candidate['id']}").json()["stale"] is True
    refused = client.post(f"/projects/{pid}/boq/candidates/{candidate['id']}/apply", json={"decisions": decisions})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "stale_write"


def test_a_new_reread_supersedes_the_pending_one_and_a_discarded_one_changes_nothing(client, monkeypatch):
    pid, body, state = _project_with_boq(client, monkeypatch, ep="61003")
    first = client.post(f"/projects/{pid}/boq/candidates").json()
    second = client.post(f"/projects/{pid}/boq/candidates").json()
    assert client.get(f"/projects/{pid}/boq/candidates/{first['id']}").json()["status"] == "superseded"
    assert second["summary"]["unchanged"] == 4 and second["decisions_needed"] == 0
    assert client.post(f"/projects/{pid}/boq/candidates/{second['id']}/discard").status_code == 204
    assert len(client.get(f"/projects/{pid}/boq").json()) == 4


def test_invalid_quantities_are_refused_at_the_boundary(client, monkeypatch):
    pid, body, _state = _project_with_boq(client, monkeypatch, ep="61004")
    rows = [_editable(i) for i in body["items"]]
    for bad in ("12.5", "-3", "2 x 10"):
        rows[0]["quantity"] = bad
        resp = client.put(f"/projects/{pid}/boq", json=rows)
        assert resp.status_code == 422, bad
    rows[0]["quantity"] = "1,250"
    rows[1]["quantity"] = "10 Nos"
    out = client.put(f"/projects/{pid}/boq", json=rows).json()
    assert out[0]["quantity"] == "1250" and (out[1]["quantity"], out[1]["unit"]) == ("10", "Nos")
