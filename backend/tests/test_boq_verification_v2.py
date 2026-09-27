"""BOQ Extraction V2, phase 1, the AI check: a line the model could not be
asked about (a timeout, the provider, a reply with no word on it) is never
removed; only two explicit not-founds remove one."""

from __future__ import annotations

from app.models import AiVerification

from .test_ai_verification import FIRST_READ, FRESH_READ, _project, _rows, recording, sheets  # noqa: F401


class _Failure(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


FIRST_STRIP = _rows(("1", "4-CPU", "Central Processor Module"), ("126", "SIGA-PS", "Photoelectric smoke detector"),
                    ("14", "SIGA-CT1", "Single input module"), ("6", "SIGA-CC1", "Synchronised output module"))


def _run_check(client, db_session, sheets, recording, ep: str, find_answers: list) -> tuple[dict, dict]:
    pid = _project(client, sheets, ep=ep)
    sheets["lines"] = FRESH_READ
    recording.answers = [FIRST_STRIP] + find_answers
    job = client.post(f"/projects/{pid}/jobs/ai-verify?scope=boq")
    assert job.status_code == 202 and job.json()["status"] == "succeeded", job.text
    # The job wrote through a session of its own: end this one's snapshot first.
    db_session.rollback()
    record = (db_session.query(AiVerification).filter(AiVerification.project_id == pid, AiVerification.scope == "boq")
              .order_by(AiVerification.id.desc()).first())
    assert record is not None and record.status == "completed", (record.status if record else None, record.error if record else None)
    lines = {i["catalog_no"]: i for i in client.get(f"/projects/{pid}/boq").json()}
    return record.summary, {**{i["id"]: i for i in record.items}, "lines": lines}


def test_a_line_the_model_could_not_look_for_is_kept(client, db_session, sheets, recording):
    """SIGA-HFS is not in the fresh read; both looks for it time out. It
    stays, unresolved, with the timeout as the reason."""
    summary, items = _run_check(client, db_session, sheets, recording, "63001", [_Failure("timeout"), _Failure("timeout")])
    assert summary["removed"] == 0 and summary["unresolved"] == 1
    assert "SIGA-HFS" in items["lines"] and items["lines"]["SIGA-HFS"]["quantity"] == "30"
    held = next(i for i in items.values() if isinstance(i, dict) and i.get("held") and i["held"].get("catalog_no") == "SIGA-HFS")
    assert held["outcome"] == "unresolved" and held["reason_code"] == "AI_TIMEOUT"
    assert held["find"] == {"first": "timeout", "second": "timeout"}


def test_a_reply_with_no_word_on_the_line_is_not_a_not_found(client, db_session, sheets, recording):
    summary, items = _run_check(client, db_session, sheets, recording, "63002", [{"lines": []}, {"lines": []}])
    assert summary["removed"] == 0 and "SIGA-HFS" in items["lines"]
    held = next(i for i in items.values() if isinstance(i, dict) and i.get("held") and i["held"].get("catalog_no") == "SIGA-HFS")
    assert held["find"] == {"first": "invalid_response", "second": "invalid_response"}


def test_a_provider_failure_on_the_second_look_keeps_the_line(client, db_session, sheets, recording):
    summary, items = _run_check(client, db_session, sheets, recording, "63003",
                                [{"lines": [{"id": "c5", "found": False, "quantity": "", "catalog_no": "", "page": 1}]},
                                 _Failure("transport")])
    assert summary["removed"] == 0 and "SIGA-HFS" in items["lines"]
    held = next(i for i in items.values() if isinstance(i, dict) and i.get("held") and i["held"].get("catalog_no") == "SIGA-HFS")
    assert held["find"] == {"first": "not_found", "second": "provider_error"} and held["reason_code"] == "AI_PROVIDER_ERROR"


def test_two_explicit_not_founds_remove_the_line(client, db_session, sheets, recording):
    not_found = {"lines": [{"id": "c5", "found": False, "quantity": "", "catalog_no": "", "page": 1}]}
    summary, items = _run_check(client, db_session, sheets, recording, "63004", [not_found, not_found])
    assert summary["removed"] == 1 and "SIGA-HFS" not in items["lines"]
