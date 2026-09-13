# Selective AI assistance: what was implemented

Mode: IMPLEMENT (13 September 2026). Companion to `LLM_ASSISTANCE_PLAN.md`
(the plan) and `REVIEW_FIXES.md` (the deterministic fixes that preceded it).

## 1. What changed

Everything is behind `AI_ENABLED` (default `false`). With it off, the
platform makes no model call, constructs no SDK client, and behaves as
before except that reads are now recorded with structured issues and
coverage.

| Area | Files | What |
|---|---|---|
| Issue codes, router, outcomes, coverage | `app/extraction/issues.py` | `IssueCode`, `ROUTING` (per code: severity, local repair, LLM-eligible, human required, task), `ENABLED_TASKS`, `Outcome`, `Coverage`, `outcome_for` |
| Design-sheet read reports what it could not settle | `app/services/design_sheet_extractor.py` | `extract_design_sheet()` returns lines + coverage + issues + failure; a row whose quantity could not be parsed becomes `QUANTITY_OR_UNIT_PARSE_FAILURE` with the cell's bbox instead of vanishing; a page with no layout is `UNPROCESSED_PAGE_OR_REGION`; `PARSER_VERSION`; `extract_boq_lines()` unchanged for callers |
| DRF read reports blanks and low-confidence fields | `app/services/drf_extractor.py` | `DrfExtractionResult.issues`: `MISSING_REQUIRED_FIELD` (never sent to a model) and `AMBIGUOUS_OCR` below `AI_OCR_REVIEW_CONFIDENCE` with the cell bbox; `PARSER_VERSION` |
| One provider, two vendors | `app/ai/provider.py` | `AiProvider` protocol; `OpenAiProvider` (OpenAI SDK, `response_format` json_schema strict, token-parameter fallback, `quota` told apart from `rate_limit` and never retried, survives an absent key); `ClaudeProvider` (Anthropic SDK, `output_config` JSON schema, effort from settings); both: concurrency semaphore, normalised errors, usage incl. cached and reasoning tokens where reported; `NullProvider`; `RecordingProvider` for tests; `PROMPT_VERSION` |
| Evidence builders | `app/ai/evidence.py` | one per task: cell crop + row; DRF field crop; sheet first-page text + DRF marked rows. Document text fenced as data. Size caps. |
| Proposal schema and validation | `app/ai/proposals.py` | pydantic `Proposal`; explicit JSON schema (`SCHEMA_VERSION`); `validate()`: target allow-list, source region must have been sent, type rules, independent-OCR agreement for cell reads, candidate-set bound for classification, classification is never self-validated |
| Result cache | `app/ai/cache.py` + table `result_cache` | key = sha256 of {scope, document sha256, evidence fingerprint, task, context, parser, prompt, schema, model, policy}; TTL; access follows the document hash; `InFlight` de-duplication |
| Budgets | `app/ai/budget.py` | per-call input/output caps, calls per document, calls per project per day (from the usage table), cost per job, elapsed time, escalations; reserve before, reconcile after; `BudgetExceeded(limit)` |
| Pipeline | `app/extraction/pipeline.py` | `record_design_sheet_run`, `assist_run` / `assist_project` (G-H-I-J), `ask()` (cache -> in-flight -> budget -> provider -> escalation once on invalid response -> validate -> cache), `independent_readings`, `suggest_sheet_system`, `suggest_drf_field`, `accept_issue` / `reject_issue` |
| Storage | `app/models.py`, `alembic/versions/b42f592e597c_*.py` | `extraction_runs`, `extraction_issues`, `ai_proposals`, `ai_usage`, `result_cache` (additive; no existing table changed) |
| Endpoints | `app/routers/extraction.py` | `GET /projects/{id}/extraction`; `GET .../issues/{id}/evidence.png`; `POST .../issues/{id}/accept` and `/reject`; `POST .../assist`; `GET /admin/ai/usage` |
| Integration | `app/routers/projects.py` | `boq/ensure` records a run per sheet and, with AI on, runs assistance after releasing the extraction lock (a failure there is a warning, never a failed read); `resolve` returns `ai_suggestions` for unlabelled sheets on multi-system DRFs and low-confidence fields |
| Config | `app/core/config.py`, `.env.example`, `requirements.txt` | `AI_*` settings; `anthropic==1.5.0` |
| UI | `frontend/src/components/ExtractionReview.tsx`, `ProjectBoqPage.tsx`, `ReviewProjectForm.tsx`, `lib/types.ts` | rows needing review with the cell image, the proposal and its validation state, Add line / Not an item, "Ask AI to read the cells", partial-page and budget notices; resolve-time suggestions shown beside the form |
| Tests | `tests/test_ai_assist.py` (22) | see §6 |

## 2. Tasks that now avoid model calls (by construction)

Everything the platform did before: project resolution and path
validation, DRF grid OCR, design-sheet reads (including the multi-building
case), quantity parsing and its deterministic fallbacks, system-code
aliases and single-system inference, revision defaults, BOQ save/diff/
snapshot, battery and amplifier arithmetic and thresholds, datasheet
current lookup, specification search, submittal scanning, package
assembly, every export. A complete supported sheet produces a run with
outcome `VALID` and zero calls (tested).

Issues that are never sent to a model, whatever the flag:
`MISSING_REQUIRED_FIELD`, `MISSING_ENGINEERING_EVIDENCE`,
`AMBIGUOUS_REVISION`, `CONFLICTING_SOURCE_VALUES`, `SOURCE_ROOT_MISMATCH`,
`UNSUPPORTED_DOCUMENT`. `UNRECOGNIZED_TABLE_LAYOUT` and
`UNPROCESSED_PAGE_OR_REGION` are routed as eligible but not in
`ENABLED_TASKS`: they are recorded for review and no call is made.

## 3. Exact conditions that trigger a call

All of: `AI_ENABLED=true`; the issue's code maps to a task in
`ENABLED_TASKS`; the evidence builder could render the region and the
bundle is under `MAX_EVIDENCE_BYTES`; no cached result for the key; no
identical request in flight; every budget limit has room.

| Trigger | Task | Evidence sent | Result |
|---|---|---|---|
| A design-sheet row read with a catalog number or item-like description whose quantity cell could not be parsed | `read_cell` | PNG crop of that cell (+28 px margin, <=420 px wide), the row's description / catalog / group / raw OCR text | `validated` if an independent Tesseract re-read (psm 7/11/6) or the raw read maps to the same value; else `needs_human_review` |
| At resolve: a design sheet with no code while the DRF marks systems mapping to two or more codes | `classify_system` | first-page text of the sheet (text layer or OCR of the top 40 %, <=4 000 chars), the DRF's marked rows, the candidate codes | always `needs_human_review` (a suggestion), rejected if outside the candidate set |
| At resolve: a DRF field read below `AI_OCR_REVIEW_CONFIDENCE` (60) | `read_field` | PNG crop of the field's cell, the field name, the OCR read | `validated` on independent agreement, else `needs_human_review` |

Not sent, ever: other pages, other projects, the BOQ, database rows,
contact fields, credentials. The evidence test asserts the part list and
byte size.

## 4. Cache and budget behaviour

- Cache key components: scope, document SHA-256, evidence fingerprint
  (hash of every part sent), task, task context, `PARSER_VERSION`,
  `PROMPT_VERSION`, `SCHEMA_VERSION`, model id, `VALIDATION_POLICY_VERSION`.
  A change to any produces a new key (tested). Never filename or EP number.
- A hit costs no call, is logged as `cache_hit=true`, and is re-judged
  against the current validation rules and the reviewed value before it
  can be applied.
- Rejected proposals are not cached; `insufficient_evidence` and
  `needs_human_review` are, so a document is not re-asked the same
  question.
- `InFlight(key)`: simultaneous identical requests share one call (tested
  with three threads).
- Budgets: reserve `estimate_input_tokens` and the output cap under a lock
  before the call; reconcile with the provider's reported usage after.
  Limits: input/output per task, calls per document, calls per project per
  day (counted from `ai_usage`, so it survives restarts), cost per job,
  elapsed seconds per job, escalations per document. A trip marks the
  issue `starved` with the limit's name and the run `BUDGET_EXHAUSTED`
  (tested).
- Retries: transport and rate-limit errors are retried by the SDK
  (`AI_MAX_RETRIES`); an `invalid_response` is escalated once to the
  standard tier within the escalation budget; `refused`, `auth` and
  insufficient evidence are not retried. A browser refresh re-reads stored
  state; `assist` skips issues already proposed, resolved or starved.

## 5. Quality and token measurements

**Not measured.** No live model call has been made from this codebase:
the machine used for implementation has no `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` or `ant auth login` profile, and the test suite uses
`RecordingProvider` throughout. The numbers below are what the code
records, not results:

- `ai_usage` rows carry input, output, cached and reasoning tokens as the
  SDK reports them (null where it does not), estimated cost from the
  configured prices, latency and outcome; `GET /admin/ai/usage` reports
  calls, cache hits, token totals, estimated cost, median and p95 latency.
- The deterministic baseline is known: EP-30208 reads 92 of 97 rows with
  zero calls; its 5 unread rows are exactly the `read_cell` population for
  a first live evaluation.

Measurement gaps: extraction completeness and field accuracy under
assistance, missed-error rate, human-review rate, tokens and cost per
accepted result, model escalation rate, and calibration of
`AI_OCR_REVIEW_CONFIDENCE` beyond the ten-DRF sample. The live evaluation
in `LLM_ASSISTANCE_PLAN.md` §13 is the next step and needs a key and the
engineer's ground truth for the six unreadable sheets.

## 6. Tests run

`backend/tests/test_ai_assist.py` (22 tests, all mocked):

| Brief requirement | Test |
|---|---|
| complete supported document -> zero calls | `test_a_complete_supported_sheet_makes_zero_model_calls` |
| validated cache hit -> zero calls | `test_a_validated_cache_hit_makes_zero_model_calls` |
| ambiguous region sends only necessary evidence | `test_an_ambiguous_cell_sends_only_that_cell_and_its_row` |
| unprocessed pages trigger attention despite valid rows | `test_unprocessed_pages_trigger_attention_even_when_rows_look_valid` |
| changed document invalidates cache | `test_a_changed_document_gets_a_different_key_and_an_unchanged_one_the_same` |
| unchanged documents not reprocessed | same test (same key), plus the cache-hit test |
| simultaneous identical requests -> one call | `test_simultaneous_identical_requests_make_one_call` |
| budget exhaustion preserves unresolved status | `test_budget_exhaustion_preserves_the_unresolved_issue`, `test_budget_limits_are_enforced_before_the_call_and_reconciled_after` |
| invalid/unsupported proposals cannot overwrite | `test_invalid_proposals_never_reach_the_project`, `test_a_proposal_for_another_target_or_region_is_rejected`, `test_an_engineer_s_own_line_is_never_overwritten_by_an_accept` |
| missing engineering evidence not invented | `test_missing_engineering_evidence_is_not_filled_in`, router test |
| retry/refresh does not duplicate mutations | `test_accepting_adds_the_row_and_a_second_accept_does_not_duplicate` |
| calculators/exports independent of LLM | `test_calculators_and_exports_do_not_construct_a_provider`, `test_the_null_provider_answers_insufficient_evidence`; the existing battery, amplifier and export tests run with the flag off |

Plus: routing table, outcome ordering, unsupported/missing documents,
classification bounds, the diagnostics endpoint. Full-suite results are in
the delivery message. Frontend: `tsc -b && vite build` passes; `oxlint`
reports only pre-existing warnings.

## 6b. Switching vendor (added after the first delivery)

`AI_PROVIDER` chooses: `openai` / `gpt` -> `OpenAiProvider`, `claude` /
`anthropic` -> `ClaudeProvider`, anything else -> `NullProvider`. Nothing
above the provider changes: the same evidence builders, schema, validation,
cache keys (the model id is a key component, so switching vendor or model
rolls the cache over by itself), budgets and usage rows.

Three differences the OpenAI SDK forced, all handled in the provider:

- **It refuses to construct a client without a credential**, where the
  Anthropic SDK waits for the first call. Caught, so "no key" is a status
  the page shows rather than an exception raised mid-read.
- **The token parameter moved.** `max_completion_tokens` on recent models,
  `max_tokens` on older ones. The first call for a model tries the newer
  name and falls back once, remembering the answer. **Unverified live** --
  see below.
- **A spent balance arrives as a 429.** Treated as a rate limit it would be
  retried until the budget ran out. It is reported as `quota`, which is
  never retried, and the message names the fix.

**The account supplied has no credits** (`credit_balance_exhausted`), so
while the key authenticates and lists models, no completion has run. What
is verified: model listing, provider selection, credential handling, error
mapping and the failure path end to end (`scripts/ai_selftest.py` reports
the quota error correctly, and the assist endpoint degrades cleanly). What
is **not** verified: that `gpt-5.4-mini` accepts an image with a strict
JSON schema, which token parameter it wants, and the reading quality on
real cells. Run `python scripts/ai_selftest.py --all` once credits are
added; it names the models that pass.

## 7. Limitations and human decisions

- **No live evaluation yet** (§5). Do not enable in production before it.
- **Layout interpretation is off** (`ENABLED_TASKS`) until the six
  unreadable sheets have engineer-counted ground truth.
- **Model and prices are settings.** The models default to `gpt-5.4-mini` /
  `gpt-5.4`, chosen from the account's own model list but not yet exercised.
  Prices default to zero, so the cost column reads zero and the per-job cost
  cap does not bind until they are set from the vendor's pricing page.
- **Resolve-time suggestions are shown, not applied**, and are not
  persisted as proposals (there is no project yet); their usage is logged
  with no project id.
- **No background jobs**: assistance runs synchronously after the BOQ read
  (bounded by `AI_MAX_ELAPSED_S_PER_JOB`) and on `POST .../assist`.
  Persisted jobs with progress remain open from the plan.
- **Existing projects**: runs exist only for reads made after this change.
  A re-read (`POST .../reextract`) still reports only; it does not create a
  run. Recording runs on re-extraction is a small follow-up.
- Decisions needed: enable in production or not; budget defaults; whether
  resolve-time suggestions should also be persisted; the evaluation set.
