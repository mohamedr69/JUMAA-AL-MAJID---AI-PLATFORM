"""Stale saves of the design inputs and of compliance answers are refused."""

from app.core.config import get_settings

from .test_compliance import _clear_cache
from .test_compliance_knowledge import SPEC_BODY, _project_with_boq  # noqa: F401 -- fixtures imported below
from .test_compliance_knowledge import knowledge, no_ai  # noqa: F401
from .test_compliance_statements import spec_pdf
from .test_projects import _login_admin

settings = get_settings()


def test_a_stale_battery_design_save_is_refused(client):
    _login_admin(client)
    pid = client.post("/projects", json={"ep_number": "70001", "project_name": "P", "systems": [], "design_sheets": []}).json()["id"]
    loaded = client.get(f"/projects/{pid}/design/battery").json()
    assert loaded["design_version"] == 0
    first = client.put(f"/projects/{pid}/design/battery", json={"panels": {}}, headers={"If-Match": "0"})
    assert first.status_code == 200, first.text
    assert first.json()["design_version"] == 1
    stale = client.put(f"/projects/{pid}/design/battery", json={"panels": {}}, headers={"If-Match": "0"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_write"
    assert client.put(f"/projects/{pid}/design/battery", json={"panels": {}}, headers={"If-Match": "1"}).status_code == 200


def test_a_compliance_answer_changed_underneath_is_not_overwritten(client, db_session, tmp_path, monkeypatch, knowledge, no_ai):  # noqa: F811
    monkeypatch.setattr(settings, "uploads_root", str(tmp_path / "uploads"))
    _clear_cache()
    _login_admin(client)
    folder = tmp_path / "EP-30784"
    project_id = _project_with_boq(client, db_session, folder)
    spec_pdf(folder / "Specification" / "283111 - FIRE DETECTION.pdf", body=SPEC_BODY)
    spec = client.get(f"/projects/{project_id}/compliance").json()["systems"][0]["specs"][0]
    statement = client.post(f"/projects/{project_id}/compliance/prepare",
                            json={"system_code": "FAS", "path": spec["path"], "member": spec["member"],
                                  "first_page": spec["first_page"], "last_page": spec["last_page"]}).json()
    base = f"/projects/{project_id}/compliance/statements/{statement['id']}"
    row = next(r for r in statement["rows"] if r["ref"] == "2.1.B")

    # Engineer A saves an answer.
    first = client.patch(base, json={"rows": [{"id": row["id"], "response": "Comply", "remark": "A",
                                               "base_response": row["response"] or "", "base_remark": row["remark"] or ""}]})
    assert first.status_code == 200, first.text
    # Engineer B, whose page still shows the old empty row, saves over it.
    stale = client.patch(base, json={"rows": [{"id": row["id"], "response": "Not Applicable", "remark": "B",
                                               "base_response": row["response"] or "", "base_remark": row["remark"] or ""}]})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_write" and "2.1.B" in detail["clauses"]
    after = next(r for r in client.get(base).json()["rows"] if r["id"] == row["id"])
    assert (after["response"], after["remark"]) == ("Comply", "A")
    # A save without a base (a script) still goes through.
    assert client.patch(base, json={"rows": [{"id": row["id"], "remark": "C"}]}).status_code == 200
