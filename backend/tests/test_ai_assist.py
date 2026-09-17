"""Selective AI assistance: routing, evidence, validation, cache, budget.

Every test here uses `RecordingProvider` -- a scripted stand-in that
counts calls and keeps what it was asked. Nothing in this file is a
measurement of a live model; the live evaluation is a separate, opt-in
exercise described in docs/LLM_ASSISTANCE_PLAN.md.
"""

import json
import threading
from pathlib import Path

import pymupdf
import pytest

from app.ai import cache as result_cache
from app.ai import provider as provider_module
from app.ai.budget import BudgetExceeded, JobBudget, Limits
from app.ai.proposals import Proposal, parse, validate
from app.ai.provider import ImagePart, NullProvider, RecordingProvider, TextPart
from app.core.config import get_settings
from app.extraction import pipeline
from app.extraction.issues import Coverage, Issue, IssueCode, Outcome, PageCoverage, llm_task_for, outcome_for
from app.models import AiProposal, AiUsage, ExtractionIssue, ExtractionRun, Project, ProjectDesignSheet, RoleEnum, User
from app.services import design_sheet_extractor
from app.services.design_sheet_extractor import (
    DesignSheetExtraction,
    ExtractedBoqLine,
    DesignSheetExtraction,
    ExtractedBoqLine,
    extract_boq_lines,
    extract_design_sheet,
)

from .conftest import login, make_user
from .test_design_sheet_extractor import requires_tesseract

settings = get_settings()


# --- fixtures ---------------------------------------------------------------------


def _ruled_sheet(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """A one-box sheet in the six-rule layout. A row whose quantity is "?"
    is drawn with an unreadable mark instead of a number."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    columns = [40, 100, 220, 430, 500, 570]
    y = 60
    page.insert_text((60, y), "Qty.", fontsize=8)
    page.insert_text((110, y), "Catalog No.", fontsize=8)
    page.insert_text((230, y), "Description", fontsize=8)
    top = y + 12
    y = top + 6   # the first row clear of the top rule, as on a printed sheet
    for qty, catalog, description in rows:
        y += 18
        if qty == "?":
            page.draw_line((58, y - 6), (70, y + 2), width=1.2)   # a scribble, not a digit
        else:
            page.insert_text((60, y), qty, fontsize=9)
        page.insert_text((110, y), catalog, fontsize=9)
        page.insert_text((230, y), description, fontsize=9)
    # The column rules run most of the page, as a real sheet's do: the
    # layout probe looks for them in the middle of the page.
    bottom = max(y + 8, 760)
    for x in columns:
        page.draw_line((x, top), (x, bottom), width=1)
    page.draw_line((columns[0], top), (columns[-1], top), width=1)
    page.draw_line((columns[0], bottom), (columns[-1], bottom), width=1)
    doc.save(path)
    doc.close()
    return path


# Two-digit quantities: a lone "1" beside a rule is the one thing Tesseract
# reads as a bar, and this fixture is about the pipeline, not the OCR.
GOOD_ROWS = [("10", "VX-3004F", "Voice Evacuation Frame"), ("20", "PC-1860BS-C", "Ceiling Speaker"),
             ("30", "RM-300X", "Remote Microphone"), ("25", "RM-210F", "Remote Microphone Extension"),
             ("12", "SC-630M", "Horn Speaker"), ("40", "MP-032B", "Monitor Panel")]


def _proposal(target: str, value: str, region: str = "cell", status: str = "proposed") -> dict:
    return {"task_id": "read_cell", "status": status,
            "proposed_changes": [{"target": target, "value": value, "source": {"page": 1, "region": region},
                                  "reason": "digit visible"}] if status == "proposed" else [],
            "source_references": [{"page": 1, "region": region}], "unresolved_issues": []}


@pytest.fixture()
def recording(monkeypatch):
    provider = RecordingProvider()
    provider_module.set_provider(provider)
    yield provider
    provider_module.set_provider(None)


@pytest.fixture()
def ai_on(monkeypatch):
    """The model on, for the cell-level assistance these tests are about.
    The read runs in the request: as a job it would run in a thread that
    cannot see the test database."""
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(jobs_router, "RUN_INLINE", True)
    yield
    monkeypatch.setattr(settings, "ai_enabled", False)


# What the sheet read returns for each drawn sheet, by path. The read is the
# model's (app.ai.sheet_reader) and is scripted here: a line per row with a
# quantity, and a row to review -- with its region on the page, for the
# evidence crop -- per row drawn with a scribble instead of one.
SCRIPTED: dict[str, list[tuple[str, str, str]]] = {}
_PX = design_sheet_extractor.RENDER_DPI / 72   # points -> pixels at the render DPI
_COLUMNS = [40, 100, 220, 430, 500, 570]


def _scripted_read(rows: list[tuple[str, str, str]]) -> DesignSheetExtraction:
    result = DesignSheetExtraction(reader="ai", coverage=Coverage(pages=[PageCoverage(page=1, processed=True)]))
    y = 72 + 6
    dropped = 0
    for qty, catalog, description in rows:
        y += 18
        span = (int(_COLUMNS[0] * _PX), int(_COLUMNS[1] * _PX))
        bounds = (int((y - 12) * _PX), int((y + 4) * _PX))
        if qty == "?":
            # Rows to review are numbered among themselves, as the read numbers them.
            dropped += 1
            result.issues.append(Issue(
                IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE, page=1, region=(span[0], bounds[0], span[1], bounds[1]),
                target=f"boq_line:1:{dropped}",
                detail={"description": description, "catalog_no": catalog, "group_heading": None, "raw_quantity": None,
                        "quantity_parse": None, "alternates": [], "building": None,
                        "ai_reading": {"quantity": "", "catalog_no": catalog, "description": description},
                        "reason": "the model could not read the quantity with confidence", "reader": "ai"},
            ))
            continue
        result.lines.append(ExtractedBoqLine(
            catalog_no=catalog, description=description, quantity=qty, group_heading=None, confidence=92.0, page=1,
            raw_quantity=qty, y_px=y * _PX, quantity_span=span, row_bounds=bounds,
            table_span=(int(_COLUMNS[0] * _PX), int(_COLUMNS[-1] * _PX)),
            quantity_parse=design_sheet_extractor._parse_quantity(qty).to_dict(),
            ai_reading={"quantity": qty, "catalog_no": catalog, "description": description},
        ))
    return result


@pytest.fixture(autouse=True)
def scripted_sheet_read(monkeypatch):
    from app.ai import sheet_reader

    monkeypatch.setattr(sheet_reader, "read_design_sheet",
                        lambda db, project, sheet, **kwargs: _scripted_read(SCRIPTED[sheet.document_path]))


def _project_with_sheet(db, tmp_path, rows, code="PAVA") -> tuple[Project, Path]:
    sheet = _ruled_sheet(tmp_path / f"EP-1 {code} Design.pdf", rows)
    SCRIPTED[str(sheet)] = list(rows)
    user = db.query(User).first() or make_user(db, "e@x.com", RoleEnum.design_engineer)
    project = Project(ep_number="40001", project_name="Test", created_by_id=user.id)
    project.design_sheets = [ProjectDesignSheet(system_code=code, document_path=str(sheet))]
    db.add(project)
    db.commit()
    db.refresh(project)
    return project, sheet


# --- routing: pure rules, no model --------------------------------------------------


def test_the_router_never_sends_software_or_evidence_problems_to_a_model():
    for code in (IssueCode.SOURCE_ROOT_MISMATCH, IssueCode.UNSUPPORTED_DOCUMENT,
                 IssueCode.MISSING_ENGINEERING_EVIDENCE, IssueCode.AMBIGUOUS_REVISION,
                 IssueCode.MISSING_REQUIRED_FIELD, IssueCode.CONFLICTING_SOURCE_VALUES):
        assert llm_task_for(code) is None, code
    assert llm_task_for(IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE) == "read_cell"
    assert llm_task_for(IssueCode.AMBIGUOUS_SYSTEM) == "classify_system"
    # Layout interpretation is routed but not enabled until an evaluation set exists.
    assert llm_task_for(IssueCode.UNRECOGNIZED_TABLE_LAYOUT) is None


def test_outcomes_keep_a_partial_read_from_passing_as_complete():
    coverage = Coverage(pages=[PageCoverage(1, processed=True), PageCoverage(2, processed=False, reason="no layout")])
    assert outcome_for(coverage, [], lines=40) == Outcome.VALID_PARTIAL
    assert outcome_for(Coverage(pages=[PageCoverage(1, processed=True)]), [], lines=40) == Outcome.VALID
    assert outcome_for(Coverage(), [], lines=0, failure="boom") == Outcome.PROCESSING_FAILURE
    cell = Issue(IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE, page=1, region=(0, 0, 1, 1), target="boq_line:1:1")
    assert outcome_for(Coverage(pages=[PageCoverage(1, processed=True)]), [cell], lines=5) == Outcome.NEEDS_INTERPRETATION
    human = Issue(IssueCode.AMBIGUOUS_REVISION)
    assert outcome_for(Coverage(pages=[PageCoverage(1, processed=True)]), [human], lines=5) == Outcome.NEEDS_HUMAN_DECISION


# --- the deterministic path: zero calls ----------------------------------------------


def test_a_complete_supported_sheet_makes_zero_model_calls(client, db_session, tmp_path, recording, ai_on):
    project, _ = _project_with_sheet(db_session, tmp_path, GOOD_ROWS)
    login(client, settings.default_admin_email, settings.default_admin_password)

    body = client.post(f"/projects/{project.id}/boq/ensure").json()

    assert len(body["items"]) == 6 and body["warnings"] == []
    assert recording.calls == 0
    runs = client.get(f"/projects/{project.id}/extraction").json()["runs"]
    assert runs[0]["outcome"] == "VALID" and runs[0]["issues"] == []


@requires_tesseract
def test_an_unreadable_quantity_becomes_an_issue_not_a_missing_row(tmp_path):
    sheet = _ruled_sheet(tmp_path / "s.pdf", GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome")])

    result = extract_design_sheet(sheet)

    assert len(result.lines) == 6
    dropped = [i for i in result.issues if i.code == IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE]
    assert len(dropped) == 1
    issue = dropped[0]
    assert issue.page == 1 and issue.region is not None and issue.target.startswith("boq_line:1:")
    assert issue.detail["catalog_no"] == "PC-1860BS-C"
    assert result.outcome == Outcome.NEEDS_INTERPRETATION
    # The plain function keeps its contract.
    assert len(extract_boq_lines(sheet)) == 6


def test_unprocessed_pages_trigger_attention_even_when_rows_look_valid(tmp_path):
    doc = pymupdf.open()
    _ruled_sheet(tmp_path / "one.pdf", GOOD_ROWS)
    with pymupdf.open(tmp_path / "one.pdf") as good:
        doc.insert_pdf(good)
    blank = doc.new_page(width=595, height=842)
    blank.insert_text((100, 300), "Continued on next sheet -- table scanned as an image", fontsize=10)
    doc.save(tmp_path / "two.pdf")
    doc.close()

    result = extract_design_sheet(tmp_path / "two.pdf")

    assert result.coverage.unprocessed_pages == [2]
    assert any(i.code == IssueCode.UNPROCESSED_PAGE_OR_REGION and i.page == 2 for i in result.issues)
    assert result.outcome == Outcome.VALID_PARTIAL


def test_unsupported_and_missing_documents_are_outcomes_not_zero_rows(tmp_path):
    missing = extract_design_sheet(tmp_path / "nowhere.pdf")
    assert missing.outcome == Outcome.UNSUPPORTED_INPUT and missing.lines == []
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    bad = extract_design_sheet(tmp_path / "bad.pdf")
    assert bad.failure and bad.outcome == Outcome.UNSUPPORTED_INPUT


# --- evidence: only what the issue needs ---------------------------------------------


def test_an_ambiguous_cell_sends_only_that_cell_and_its_row(client, db_session, tmp_path, ai_on):
    provider = RecordingProvider([_proposal("boq_line:1:1", "7")])
    provider_module.set_provider(provider)
    try:
        project, _ = _project_with_sheet(db_session, tmp_path, GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome")])
        login(client, settings.default_admin_email, settings.default_admin_password)
        client.post(f"/projects/{project.id}/boq/ensure")

        assert provider.calls == 1
        request = provider.requests[0]
        labels = [p.label for p in request.parts]
        assert labels == ["task", "cell", "row"]
        image = next(p for p in request.parts if isinstance(p, ImagePart))
        assert 200 < len(image.png) < 60_000, "a small crop, not a page"
        row = next(p for p in request.parts if isinstance(p, TextPart) and p.label == "row")
        assert "PC-1860BS-C" in row.text and "Firedome" in row.text
        # Nothing about the project's people travels with a cell.
        assert "@" not in row.text and "Test" not in row.text
        assert request.task == "read_cell" and request.max_output_tokens == settings.ai_max_output_tokens_per_task
    finally:
        provider_module.set_provider(None)


# --- validation: the model's answer is not the truth -----------------------------------


def test_a_proposal_for_another_target_or_region_is_rejected():
    proposal = Proposal.model_validate(_proposal("boq_line:1:2", "7"))
    verdict = validate(proposal, task="read_cell", allowed_target="boq_line:1:1", sent_regions={"cell"})
    assert verdict.state == "rejected" and "target" in verdict.reason

    proposal = Proposal.model_validate(_proposal("boq_line:1:1", "7", region="page"))
    verdict = validate(proposal, task="read_cell", allowed_target="boq_line:1:1", sent_regions={"cell"})
    assert verdict.state == "rejected" and "region" in verdict.reason


def test_a_cell_reading_is_validated_only_by_an_independent_reading():
    proposal = Proposal.model_validate(_proposal("boq_line:1:1", "74"))
    agreed = validate(proposal, task="read_cell", allowed_target="boq_line:1:1", sent_regions={"cell"},
                      independent_readings={"74", "7A"})
    assert agreed.state == "validated" and agreed.value == "74"
    alone = validate(proposal, task="read_cell", allowed_target="boq_line:1:1", sent_regions={"cell"},
                     independent_readings={"14"})
    assert alone.state == "needs_human_review" and alone.value == "74"
    nonsense = Proposal.model_validate(_proposal("boq_line:1:1", "seventy"))
    assert validate(nonsense, task="read_cell", allowed_target="boq_line:1:1", sent_regions={"cell"}).state == "rejected"
    assert parse({"task_id": "x"}).state == "rejected"


def test_a_classification_is_a_suggestion_bounded_by_the_drf():
    proposal = Proposal.model_validate({**_proposal("sheet_system:s.pdf", "PAVA", region="sheet"), "task_id": "classify_system"})
    ok = validate(proposal, task="classify_system", allowed_target="sheet_system:s.pdf", sent_regions={"sheet"},
                  allowed_values={"PAVA", "FAS"})
    assert ok.state == "needs_human_review" and ok.value == "PAVA"
    outside = validate(proposal, task="classify_system", allowed_target="sheet_system:s.pdf", sent_regions={"sheet"},
                       allowed_values={"FAS", "VES"})
    assert outside.state == "rejected"


# --- cache and de-duplication ---------------------------------------------------------


def test_a_validated_cache_hit_makes_zero_model_calls(client, db_session, tmp_path, ai_on):
    provider = RecordingProvider([_proposal("boq_line:1:1", "7")])
    provider_module.set_provider(provider)
    try:
        rows = GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome")]
        project, sheet = _project_with_sheet(db_session, tmp_path, rows)
        login(client, settings.default_admin_email, settings.default_admin_password)
        client.post(f"/projects/{project.id}/boq/ensure")
        assert provider.calls == 1

        # A second project reading the same bytes: the same evidence, the same key.
        other = Project(ep_number="40002", project_name="Twin", created_by_id=project.created_by_id)
        other.design_sheets = [ProjectDesignSheet(system_code="PAVA", document_path=str(sheet))]
        db_session.add(other); db_session.commit(); db_session.refresh(other)
        client.post(f"/projects/{other.id}/boq/ensure")

        assert provider.calls == 1
        hits = db_session.query(AiUsage).filter(AiUsage.cache_hit.is_(True)).count()
        assert hits == 1
        proposals = db_session.query(AiProposal).order_by(AiProposal.id).all()
        assert [p.from_cache for p in proposals] == [False, True]
    finally:
        provider_module.set_provider(None)


def test_a_changed_document_gets_a_different_key_and_an_unchanged_one_the_same():
    common = dict(scope="default", task="read_cell", context={"target": "boq_line:1:1"}, parser_version="p1",
                  prompt_version="v1", schema_version="1", model="m")
    a = result_cache.cache_key(document_sha256="aaa", evidence_fingerprint="e1", **common)
    same = result_cache.cache_key(document_sha256="aaa", evidence_fingerprint="e1", **common)
    changed_doc = result_cache.cache_key(document_sha256="bbb", evidence_fingerprint="e1", **common)
    changed_parser = result_cache.cache_key(document_sha256="aaa", evidence_fingerprint="e1", **{**common, "parser_version": "p2"})
    changed_prompt = result_cache.cache_key(document_sha256="aaa", evidence_fingerprint="e1", **{**common, "prompt_version": "v2"})
    assert a == same
    assert len({a, changed_doc, changed_parser, changed_prompt}) == 4


def test_simultaneous_identical_requests_make_one_call(client, db_session):
    from app.ai.evidence import Evidence
    from app.ai.provider import AiRequest
    from app.ai.proposals import PROPOSAL_SCHEMA

    provider = RecordingProvider([_proposal("boq_line:1:1", "7")] * 4, delay_s=0.3)
    request = AiRequest(task="read_cell", system="s", parts=[TextPart("task", "t")], schema=PROPOSAL_SCHEMA,
                        max_output_tokens=100)
    evidence = Evidence(request=request, sent_regions={"cell"}, crop_png=None, fingerprint="fp")
    budget = JobBudget(limits=Limits.from_settings(), calls_today_before=0)
    results = []

    def go():
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            verdict, *_ = pipeline.ask(db, project_id=None, run_id=None, document_sha256="doc", evidence=evidence,
                                       context={}, budget=budget, provider=provider, allowed_target="boq_line:1:1",
                                       independent={"7"})
            results.append(verdict.state)
        finally:
            db.close()

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert provider.calls == 1
    assert results == ["validated"] * 3


# --- budgets ------------------------------------------------------------------------------


def test_budget_limits_are_enforced_before_the_call_and_reconciled_after():
    limits = Limits(max_input_tokens_per_task=1000, max_output_tokens_per_task=100, max_calls_per_document=2,
                    max_calls_per_project_per_day=10, max_cost_per_job=1.0, max_elapsed_s_per_job=60,
                    max_escalations_per_document=1, price_input=5.0, price_output=25.0, price_cached=0.5)
    budget = JobBudget(limits=limits, calls_today_before=0)
    reservation = budget.reserve(500, 100)
    assert reservation == pytest.approx((500 * 5 + 100 * 25) / 1_000_000)
    actual = budget.reconcile(reservation, 480, 20, 0)
    assert actual == pytest.approx((480 * 5 + 20 * 25) / 1_000_000) and budget.reserved_cost == pytest.approx(0)
    budget.reserve(500, 100)
    with pytest.raises(BudgetExceeded) as third:
        budget.reserve(500, 100)
    assert third.value.limit == "calls_per_document"
    with pytest.raises(BudgetExceeded) as big:
        JobBudget(limits=limits, calls_today_before=0).reserve(5000, 100)
    assert big.value.limit == "input_tokens_per_task"
    with pytest.raises(BudgetExceeded) as daily:
        JobBudget(limits=limits, calls_today_before=10).reserve(10, 10)
    assert daily.value.limit == "calls_per_project_per_day"


def test_budget_exhaustion_preserves_the_unresolved_issue(client, db_session, tmp_path, ai_on, monkeypatch):
    provider = RecordingProvider([_proposal("boq_line:1:1", "7"), _proposal("boq_line:1:2", "9")])
    provider_module.set_provider(provider)
    monkeypatch.setattr(settings, "ai_max_calls_per_document", 1)
    try:
        rows = GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome"), ("?", "RM-300X", "Remote Microphone Two")]
        project, _ = _project_with_sheet(db_session, tmp_path, rows)
        login(client, settings.default_admin_email, settings.default_admin_password)
        client.post(f"/projects/{project.id}/boq/ensure")

        run = client.get(f"/projects/{project.id}/extraction").json()["runs"][0]
        states = sorted(i["state"] for i in run["issues"])
        assert provider.calls == 1
        assert states == ["proposed", "starved"]
        assert run["outcome"] == "BUDGET_EXHAUSTED" and run["budget_exhausted"] == "calls_per_document"
    finally:
        provider_module.set_provider(None)


# --- nothing is written without an engineer ---------------------------------------------


def test_accepting_adds_the_row_and_a_second_accept_does_not_duplicate(client, db_session, tmp_path, ai_on):
    provider = RecordingProvider([_proposal("boq_line:1:1", "74")])
    provider_module.set_provider(provider)
    try:
        project, _ = _project_with_sheet(db_session, tmp_path, GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome")])
        login(client, settings.default_admin_email, settings.default_admin_password)
        before = len(client.post(f"/projects/{project.id}/boq/ensure").json()["items"])
        issue = client.get(f"/projects/{project.id}/extraction").json()["runs"][0]["issues"][0]
        assert issue["state"] == "proposed"
        assert issue["proposals"][0]["value"] == "74"
        # The proposal alone changed nothing.
        assert len(client.get(f"/projects/{project.id}/boq").json()) == before

        added = client.post(f"/projects/{project.id}/extraction/issues/{issue['id']}/accept", json={"value": "74"})
        assert added.status_code == 200 and added.json()["quantity"] == "74"
        assert len(client.get(f"/projects/{project.id}/boq").json()) == before + 1

        again = client.post(f"/projects/{project.id}/extraction/issues/{issue['id']}/accept", json={"value": "74"})
        assert again.status_code == 409
        assert len(client.get(f"/projects/{project.id}/boq").json()) == before + 1
        assert client.get(f"/projects/{project.id}/extraction/issues/{issue['id']}/evidence.png").status_code == 200
    finally:
        provider_module.set_provider(None)


def test_an_engineer_s_own_line_is_never_overwritten_by_an_accept(client, db_session, tmp_path, ai_on):
    provider = RecordingProvider([_proposal("boq_line:1:1", "74")])
    provider_module.set_provider(provider)
    try:
        project, _ = _project_with_sheet(db_session, tmp_path, GOOD_ROWS + [("?", "PC-1860BS-C", "Ceiling Speaker Firedome")])
        login(client, settings.default_admin_email, settings.default_admin_password)
        items = client.post(f"/projects/{project.id}/boq/ensure").json()["items"]
        issue = client.get(f"/projects/{project.id}/extraction").json()["runs"][0]["issues"][0]
        # The engineer types the line in themselves, with their own quantity.
        typed = [{k: v for k, v in i.items() if k not in ("id", "position")} for i in items]
        typed.append({"system_code": "PAVA", "group_heading": None, "manufacturer": None, "catalog_no": "PC-1860BS-C",
                      "description": "Ceiling Speaker Firedome", "quantity": "80", "unit": None,
                      "unit_price": None, "total_price": None, "remarks": None})
        assert client.put(f"/projects/{project.id}/boq", json=typed).status_code == 200

        refused = client.post(f"/projects/{project.id}/extraction/issues/{issue['id']}/accept", json={"value": "74"})

        assert refused.status_code == 409 and "already holds" in refused.json()["detail"]
        lines = client.get(f"/projects/{project.id}/boq").json()
        assert [l["quantity"] for l in lines if l["catalog_no"] == "PC-1860BS-C" and "Firedome" in l["description"]] == ["80"]
    finally:
        provider_module.set_provider(None)


def test_invalid_proposals_never_reach_the_project(client, db_session, tmp_path, ai_on):
    """A proposal that names the wrong target, or is not a quantity, is
    recorded as rejected and the issue stays open."""
    from app.ai.evidence import Evidence
    from app.ai.provider import AiRequest
    from app.ai.proposals import PROPOSAL_SCHEMA

    provider = RecordingProvider([_proposal("boq_line:9:9", "74")])
    request = AiRequest(task="read_cell", system="s", parts=[TextPart("task", "t")], schema=PROPOSAL_SCHEMA, max_output_tokens=100)
    evidence = Evidence(request=request, sent_regions={"cell"}, crop_png=None, fingerprint="fp2")
    verdict, data, cached, _model, key = pipeline.ask(
        db_session, project_id=None, run_id=None, document_sha256="d", evidence=evidence, context={},
        budget=JobBudget(limits=Limits.from_settings(), calls_today_before=0), provider=provider,
        allowed_target="boq_line:1:1", independent={"74"},
    )
    assert verdict.state == "rejected"
    # A rejected answer is not cached either: the next run asks again.
    assert result_cache.get(db_session, key, project_id=None, ttl_days=90) is None


def test_missing_engineering_evidence_is_not_filled_in(client, db_session, tmp_path, recording, ai_on):
    """A part with no datasheet stays a missing current: the router has no
    task for it, so the battery page's fill-currents makes no model call."""
    from app.routers import design as design_router

    monkeypatch_libraries = lambda: {}  # noqa: E731
    design_router._datasheet_libraries = monkeypatch_libraries
    user = db_session.query(User).first()
    project = Project(ep_number="40003", project_name="B", created_by_id=user.id)
    db_session.add(project); db_session.commit(); db_session.refresh(project)
    login(client, settings.default_admin_email, settings.default_admin_password)
    boq = [{"system_code": "FAS", "group_heading": "Fire Alarm Control Panel", "manufacturer": "EDWARDS",
            "catalog_no": None, "description": "EST4 panel. Includes:", "quantity": "1", "unit": None,
            "unit_price": None, "total_price": None, "remarks": None},
           {"system_code": "FAS", "group_heading": "Fire Alarm Control Panel", "manufacturer": "EDWARDS",
            "catalog_no": "4-XYZ", "description": "Mystery module", "quantity": "1", "unit": None,
            "unit_price": None, "total_price": None, "remarks": None}]
    assert client.put(f"/projects/{project.id}/boq", json=boq).status_code == 200
    client.post(f"/projects/{project.id}/design/battery/fill-currents")
    battery = client.get(f"/projects/{project.id}/design/battery").json()

    assert recording.calls == 0
    assert any("4-XYZ" in p["missing_parts"] for p in battery["panels"])


def test_no_vendor_client_is_built_while_assistance_is_off(monkeypatch):
    """With assistance off the provider is the NullProvider: no vendor SDK
    client is constructed, so the calculation and export paths cannot depend
    on one being available."""
    built = []
    monkeypatch.setattr(settings, "ai_enabled", False)
    monkeypatch.setattr(provider_module, "ClaudeProvider", lambda: built.append("claude"))
    monkeypatch.setattr(provider_module, "OpenAiProvider", lambda: built.append("openai"))
    monkeypatch.setattr(provider_module, "ClaudeCodeProvider", lambda: built.append("claude-code"))
    provider_module.set_provider(None)
    try:
        assert isinstance(provider_module.get_provider(), NullProvider)
        assert built == []
    finally:
        provider_module.set_provider(None)


@pytest.mark.parametrize(
    ("provider_name", "expected"),
    [("openai", "OpenAiProvider"), ("gpt", "OpenAiProvider"), ("claude", "ClaudeProvider"),
     ("anthropic", "ClaudeProvider"), ("claude-code", "ClaudeCodeProvider"), ("groq", "NullProvider"),
     ("nonsense", "NullProvider")],
)
def test_the_provider_setting_picks_the_vendor(monkeypatch, provider_name, expected):
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_provider", provider_name)
    monkeypatch.setattr(settings, "ai_api_key", "test-key")
    provider_module.set_provider(None)
    try:
        assert type(provider_module.get_provider()).__name__ == expected
    finally:
        provider_module.set_provider(None)


def test_a_spent_balance_is_reported_as_quota_and_is_not_retried(monkeypatch):
    """OpenAI reports an exhausted balance as a 429. Treated as a rate limit
    it would be retried until the budget ran out; it is its own error and is
    never retried."""
    import openai

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", "test-key")
    provider_module.set_provider(None)
    provider = provider_module.OpenAiProvider()

    class Response:
        status_code = 429
        headers: dict = {}
        request = None

    def spent(**_kwargs):
        raise openai.RateLimitError(
            "Error code: 429 - {'error': {'code': 'credit_balance_exhausted', 'type': 'insufficient_quota'}}",
            response=Response(), body=None,
        )

    monkeypatch.setattr(provider._client.chat.completions, "create", spent)
    request = provider_module.AiRequest(task="read_cell", system="s", parts=[TextPart("task", "t")],
                                        schema={"type": "object"}, max_output_tokens=50)

    response = provider.complete(request)

    assert response.error == "quota" and response.retryable is False
    assert "credits" in (response.error_detail or "")
    provider_module.set_provider(None)


def test_an_uncredentialed_provider_answers_instead_of_raising(monkeypatch):
    """Enabled but with no key: the call must come back as an `auth` error,
    not as the SDK's own exception part-way through a read."""
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider_module.set_provider(None)
    try:
        provider = provider_module.OpenAiProvider()
        assert provider.ready is False
        request = provider_module.AiRequest(task="read_cell", system="s", parts=[TextPart("task", "t")],
                                            schema={"type": "object"}, max_output_tokens=50)
        response = provider.complete(request)
        assert response.error == "auth" and "AI_API_KEY" in (response.error_detail or "")
    finally:
        provider_module.set_provider(None)


def test_the_null_provider_answers_insufficient_evidence():
    from app.ai.provider import AiRequest
    from app.ai.proposals import PROPOSAL_SCHEMA

    request = AiRequest(task="read_cell", system="s", parts=[TextPart("task", "t")], schema=PROPOSAL_SCHEMA, max_output_tokens=10)
    response = NullProvider().complete(request)
    assert response.ok and response.data["status"] == "insufficient_evidence"


def test_the_diagnostics_view_reports_usage(client, db_session):
    db_session.add(AiUsage(project_id=None, task="read_cell", model="m", input_tokens=500, output_tokens=40,
                           cached_input_tokens=0, estimated_cost=0.0035, latency_ms=800, cache_hit=False, outcome="ok"))
    db_session.add(AiUsage(project_id=None, task="read_cell", model="m", estimated_cost=0, latency_ms=0, cache_hit=True, outcome="ok"))
    db_session.commit()
    login(client, settings.default_admin_email, settings.default_admin_password)
    body = client.get("/admin/ai/usage").json()
    assert body["calls"] == 1 and body["cache_hits"] == 1 and body["input_tokens"] == 500
    assert body["median_latency_ms"] == 800 and body["estimated_cost"] == pytest.approx(0.0035)


def test_claude_code_runs_the_cli_on_the_subscription(monkeypatch, tmp_path):
    """The subscription provider: the schema and system prompt go to the CLI,
    an image is written beside the call for the Read tool, the API key is
    kept out of its environment, and `structured_output` is the answer."""
    import subprocess

    from app.ai.provider import AiRequest, ClaudeCodeProvider, ImagePart, TextPart

    cli = tmp_path / "claude.exe"
    cli.write_bytes(b"")
    monkeypatch.setattr(settings, "ai_claude_cli", str(cli))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    seen = {}

    def fake_run(args, input, cwd, env, **kwargs):  # noqa: A002
        seen.update(args=args, input=input, env=env, files=sorted(p.name for p in Path(cwd).iterdir()))
        reply = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"answer": "ok"},
                 "result": "{\"answer\":\"ok\"}", "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100,
                                                            "cache_read_input_tokens": 0, "output_tokens": 7},
                 "modelUsage": {"claude-sonnet-5": {}}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(reply), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = ClaudeCodeProvider()
    assert provider.ready
    response = provider.complete(AiRequest(task="t", system="Be brief.", parts=[TextPart("clause", "Hello"), ImagePart("page", b"png")],
                                           schema={"type": "object"}, max_output_tokens=100))
    assert response.ok and response.data == {"answer": "ok"} and response.model == "claude-sonnet-5"
    assert response.usage.input_tokens == 103 and response.usage.output_tokens == 7
    args = seen["args"]
    assert args[args.index("--system-prompt") + 1] == "Be brief." and args[args.index("--json-schema") + 1] == '{"type": "object"}'
    assert args[args.index("--tools") + 1] == "Read" and seen["files"] == ["image-1.png"]
    assert "<clause>\nHello\n</clause>" in seen["input"] and "image-1.png" in seen["input"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]

    def logged_out(args, **kwargs):
        reply = {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}
        return subprocess.CompletedProcess(args, 1, stdout=json.dumps(reply), stderr="")

    monkeypatch.setattr(subprocess, "run", logged_out)
    failed = provider.complete(AiRequest(task="t", system="s", parts=[TextPart("a", "b")], schema={}, max_output_tokens=10))
    assert failed.error == "auth" and "Not logged in" in failed.error_detail
