"""The compatibility selector: every consumer keeps its legacy answer; the
classification is a shadow beside it, and a missing, stale, ambiguous or
unknown classification never removes a document from a consumer."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import document_routing as routing


def _row(role="document", records=None, relative="x/a.pdf"):
    return SimpleNamespace(role=role, relative_path=relative, filename=relative.rsplit("/", 1)[-1],
                           extracted={"records": records or []})


def _entry(primary_type, stage="supported"):
    return SimpleNamespace(primary_type=primary_type, stage=stage)


def test_nothing_is_migrated_and_the_legacy_answer_is_effective():
    assert all(value is False for value in routing.MIGRATED.values())
    form = _row(role="submittal_form")
    s = routing.select("submittal_map", form, _entry("DATASHEET"), "current")
    assert s.legacy is True and s.shadow is False and s.agree is False and s.effective is True
    assert "not applied" in s.why
    agree = routing.select("submittal_map", form, _entry("MATERIAL_SUBMITTAL"), "current")
    assert agree.agree is True and agree.effective is True


def test_missing_stale_ambiguous_or_unknown_classification_never_removes_a_document():
    spec = _row(role="spec")
    for entry, freshness in ((None, None), (_entry("LOAD_SCHEDULE"), "source_changed"), (_entry("LOAD_SCHEDULE", "ambiguous"), "current"),
                             (_entry("UNKNOWN", "unknown"), "current")):
        s = routing.select("compliance", spec, entry, freshness)
        assert s.shadow is None and s.agree is None and s.effective is True and "legacy selector stands" in s.why


def test_the_legacy_selectors_are_the_consumers_own():
    drawing = _row(records=[{"category": "drawings", "source": "document", "path": "05- Drawings/L01.pdf"}], relative="05- Drawings/L01.pdf")
    given = _row(records=[{"category": "drawings", "source": "document", "path": "00- IFC/L01.pdf"}], relative="00- IFC/L01.pdf")
    assert routing.legacy_selects("logs.drawings", drawing) and not routing.legacy_selects("logs.drawings", given)
    assert routing.legacy_selects("logs.submittals", _row(records=[{"category": "submittals"}]))
    assert routing.legacy_selects("intake", _row(role="drf")) and not routing.legacy_selects("intake", _row(role="spec"))
    assert routing.legacy_selects("replies", _row(relative="02- MS/FA/R0/Received/reply.pdf"))
    assert not routing.legacy_selects("replies", _row(relative="02- MS/FA/R0/Submitted/form.pdf"))
    s = routing.select("replies", _row(relative="02- MS/FA/R0/Received/datasheet.pdf"), _entry("DATASHEET"), "current")
    assert s.legacy is True and s.shadow is False and s.effective is True, "the folder rule stands; the disagreement is recorded"
