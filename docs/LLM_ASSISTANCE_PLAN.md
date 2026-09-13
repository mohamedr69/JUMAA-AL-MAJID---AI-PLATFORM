# Selective LLM assistance: implementation plan

Status (13 September 2026, later the same day): steps 1-3 and 5 of §11 are
implemented behind `AI_ENABLED` with Claude as the provider -- see the
"Selective AI assistance" section of the README and `docs/AI_IMPLEMENTATION.md`.
Step 4 (live evaluation) and step 6 (enabling layout interpretation) are open.

Original mode: REVIEW_ONLY. This was the plan for adding LLM
assistance to the Engineering Project Platform so that Python keeps every
routine, numeric and authoritative task and a model is called only for
unresolved interpretation, within a budget, with its proposals validated
against source evidence before a human sees them.

Date: 13 September 2026. Basis: the repository as it stands after the
company-library move and the ten-project review fixes (`docs/REVIEW_FIXES.md`).

---

## 1. What the application does today

### 1.1 Facts established by inspection

- **There is no LLM anywhere.** No SDK in `requirements.txt` or
  `package.json`, no client, prompt, model name, retry or usage code in
  `backend/app` or `frontend/src`. The "AI Quick Actions" on the Compliance
  page are disabled placeholders. There is no AGENTS.md or CLAUDE.md.
- The current call count is therefore **zero**, and the baseline for
  "token savings" is: add assistance without adding calls that the
  deterministic pipeline already answers.
- The repository is not under git; there are no unrelated working-tree
  changes to preserve beyond the files themselves.
- Outcomes are signalled as **free-text warnings and exceptions**
  (`DrfExtractionResult.warnings`, `DesignSheetExtractionError`,
  `WorkbookReadError`, `boq_extraction_warnings`), not structured codes.
- Existing caches: `routers/compliance.py` (15-minute in-memory dict per
  project), `services/datasheet_library.py` (gzip index keyed by file
  size+mtime under `backend/.cache`), `services/log_scan_jobs.py`
  (in-memory scan state per folder). None is keyed by content hash, tenant
  or version.
- Background work: daemon threads only (`log_scan_jobs`, library warm-up).
  No persisted job id or state; a refresh re-polls in-memory state.
- Persistence: SQLAlchemy models in `app/models.py` -- Project,
  ProjectDesignSheet, ProjectSystem, ProjectBoqItem, ProjectBoqRevision
  (immutable snapshots), ProjectDesign (VE JSON), DesignRule (versioned),
  ProjectSubmittal(+Event). There is no table for extraction runs,
  proposals or usage.

### 1.2 Call-flow map (deterministic, as it stands)

| User action | Endpoint | Modules | Interpretation-heavy step | Today's outcome on failure |
|---|---|---|---|---|
| Find project | `POST /projects/resolve` | `ep_resolver.resolve_project` -> `drf_extractor.extract_drf_fields` | grid-line geometry + Tesseract per cell; systems table by row position; notes block by banner | warning strings; blank fields; unlabelled sheets |
| Create project | `POST /projects` | `projects.create_project` (+ `_validate_source_paths`) | none | 422 on path mismatch |
| Open BOQ first time | `POST /projects/{id}/boq/ensure` | `design_sheet_extractor.extract_boq_lines` per sheet | rule-count layout (6 or 4 rules), per-box OCR strips, row pairing | `DesignSheetExtractionError` per sheet -> stored warning; sheet yields nothing |
| Re-read | `POST /projects/{id}/reextract` | `reextraction.reextract_project` | same extractors; diff on letters/digits | report only |
| Batteries | `GET .../design/battery`, `POST .../fill-currents` | `battery_calculation`, `datasheet_library.find`, `datasheet_currents.read_part_current` | placing a current figure by geometry on a datasheet page | `missing_parts`, lower-bound load |
| Amplifier | `POST .../design/ve/import` | `ve_workbook_reader.read_amplifier_workbook` | headings-not-positions reading; headerless layout accepted only if watts agree | `WorkbookReadError` (EP-16884 refused) |
| Compliance | `GET .../compliance` | `spec_finder.find_specs` | CSI number + title keywords; zip members; page ranges | 40 s walk; 15-min cache |
| Submittals | `POST .../submittals/scan`, `GET .../logs` | `submittal_scanner`, `document_control` | form text + stamp OCR; title blocks | "under review" when unreadable |
| Package | `POST .../submittal/package` | `submittal_package.plan_package/build_package` | none (assembly) | `PackageBuildError` names the stage |
| Exports | `.../export.xlsx`, `.../export.pdf` | `boq_export`, `battery_export`, `battery_pdf` | none | -- |

Nothing in this map should change owner. The plan adds a side channel for
the interpretation-heavy cells only, after the deterministic step has run
and failed in a way that is *interpretation*, not *software*.

### 1.3 Where interpretation is actually unresolved on the ten reviewed projects

Confirmed from the stored data and the review:

| Project | Unresolved today | Nature |
|---|---|---|
| EP-30175, 31055, 30387, 30977, 31725 | "Could not find a line-item table" on 6 design sheets | unrecognised layout (rule count not 4 or 6, or no numeric quantities) |
| EP-30208 | 5 of 97 rows dropped: quantity cell unreadable | ambiguous OCR of a digit |
| EP-30977, EP-24601 | generic "Design.pdf" on a DRF marking two systems | ambiguous system |
| EP-30175, EP-31055 | 128 / 39 BOQ lines from two systems' sheets, indistinguishable after the fact | historical; needs human |
| EP-31112 | contractor read "7 AELIA TEK" on EP-30208, "ee ee ee" plot (now dropped) | OCR noise -- already deterministic |
| EP-29495 | 4 parts with no datasheet in the library (FireKey, Compaq, 3-CABSB, SIGA-AASO) | missing evidence, not interpretation |
| EP-16884 (README) | amplifier workbook lists one total per circuit with no tap per type | unsupported input; a model cannot supply taps |

So the genuine LLM-eligible classes are: **unrecognised table layout**,
**ambiguous OCR of a specific cell with the image at hand**, **ambiguous
system for a generic sheet** (as a *classification proposal*, reviewer
decides), **free-text scope notes** (already extracted deterministically;
a model may only normalise), and **contextual description comparison**
(BOQ revision diff pairing, submittal material-to-datasheet "mentions it"
matches). Everything else in the review was software or data and has been,
or must be, fixed in Python.

---

## 2. Responsibility boundaries applied to actual modules

| Python owns (unchanged or strengthened) | Module |
|---|---|
| Source root, selected candidate, path containment | `ep_resolver`, `projects._validate_source_paths` |
| File discovery, ranking, content hash | `ep_resolver` (+ new `hashing.py`) |
| Rule/grid detection, OCR strips, quantity parsing | `drf_extractor`, `design_sheet_extractor` |
| Required-field, unit, completeness validation | new `extraction/validation.py` |
| Revision selection default + reviewer override | `ep_resolver.mark_superseded`, review form |
| All arithmetic | `battery_calculation`, `ve_calculation` |
| Snapshots, diffs | `boq_revisions`, `reextraction` |
| Permissions | `deps.require_role` |
| PDF/Excel assembly | `battery_pdf`, `boq_export`, `submittal_package` |
| Cache, budgets, retries, jobs | new `ai/cache.py`, `ai/budget.py`, `jobs/` |

An LLM may propose, never decide:
- a table's column roles and row segmentation for an unrecognised layout
  (given the page image region and the rule geometry Python found);
- a digit for one unreadable quantity cell (given that cell's image crop
  plus its row's description and neighbours);
- which of N candidate systems a generic sheet belongs to (given the
  sheet's first-page text and the DRF's marked rows);
- a normalised reading of the notes block lines (given the OCR text and
  the crop);
- whether two BOQ descriptions are the same item (given both strings).

An LLM must not: fill a blank quantity with a number it did not read from
supplied evidence; pick the governing revision; change `source_folder_path`;
compute Ah or W; set a submittal status; write to reviewed values.

---

## 3. Pipeline design against the existing code

The flow A-J from the brief maps onto one new orchestration layer that
wraps the existing extractors without rewriting them.

```
extraction/run.py            orchestrates A-J for one document
  A identify        -> DocumentIdentity(project_id, sheet_id, sha256, pages, parser_version)
  B cache           -> result_cache.get(key)  (validated/approved results only)
  C parse           -> existing extract_boq_lines / extract_drf_fields, extended to
                       return a Coverage record and per-page issues (not only warnings)
  D validate        -> validation.py: required fields, quantity typing, coverage
  E classify        -> router.py: issues -> IssueCode with severity, eligibility
  F local repair    -> existing deterministic fallbacks (sparse OCR read, aliases,
                       single-system inference, superseded marking) -- already in code
  G LLM             -> ai/provider.py within ai/budget.py, evidence from ai/evidence.py
  H validate        -> proposals.py: schema + evidence + contradiction checks
  I preserve        -> issues left NEEDS_HUMAN / NEEDS_INTERPRETATION stay on the run
  J save            -> ExtractionRun + ExtractionIssue + AiProposal + AiUsage rows
```

### 3.1 Outcomes (one enum, stored on the run)

`VALID`, `VALID_PARTIAL`, `NEEDS_INTERPRETATION`, `NEEDS_HUMAN_DECISION`,
`MISSING_SOURCE`, `UNSUPPORTED_INPUT`, `PROCESSING_FAILURE`, `BUDGET_EXHAUSTED`.

Rules that keep them honest:
- A sheet whose pages are all `detected` but not all `processed` cannot be
  `VALID`; it is `VALID_PARTIAL` at best (EP-30208 before the fix would have
  been `VALID_PARTIAL` with 3 pages' second boxes `skipped`).
- A page with a detected rule layout that yielded zero rows is
  `UNPROCESSED_PAGE_OR_REGION`, never an empty success.
- `boq_extracted_at` stays the idempotency stamp; a run that ends
  `BUDGET_EXHAUSTED` or `NEEDS_INTERPRETATION` still stamps (no infinite
  retry) but the run row says why, and "Re-run" is explicit.

### 3.2 Coverage record

Added to `ExtractedBoqLine`'s sibling: `Coverage(pages=[PageCoverage(page,
regions=[RegionCoverage(kind=table|banner|address, top, bottom, status,
reason, rows_accepted, rows_dropped)])])`. `design_sheet_extractor._read_page`
already knows every extent and band; it needs to *report* them instead of
returning lines only. `drf_extractor` reports blocks: field table, scope,
systems, notes -- each `processed | skipped(reason) | failed`.

### 3.3 Quantities

`ExtractedBoqLine.quantity` is a string today. Add a typed view in
`validation.py` -- `Quantity(kind: numeric|lot|blank|unresolved, value,
unit, raw)` -- without changing the stored column; aggregation in
`boq_export` already separates numeric from "Lot" and must keep doing so.
`unresolved` (a row the OCR read but could not parse) becomes an issue
with the cell crop attached, instead of the row being dropped silently.
The five EP-30208 rows are exactly this class.

---

## 4. Attention router (`app/extraction/router.py`, pure Python)

| Code | Detection rule (module) | Severity | Local repair | LLM | Human | Minimum context | Proposal validation |
|---|---|---|---|---|---|---|---|
| `MISSING_REQUIRED_FIELD` | DRF field absent after `_read_value` (`drf_extractor`) | high for plot/client/contractor | sparse-read fallback (done) | no -- form field, engineer types it | yes | -- | -- |
| `UNPROCESSED_PAGE_OR_REGION` | page has a layout but a region yielded 0 rows; or `_find_layout` None on a page that has ink (`design_sheet_extractor`) | high | none | **yes** (layout interpretation) | yes if LLM declines/insufficient | page image at 150 dpi, rule x positions, neighbouring page's headings | rows must carry page+region; quantities must be read from the region text the model returns with bbox; row count sanity vs. detected text lines |
| `UNRECOGNIZED_TABLE_LAYOUT` | `_find_layout` None on every page (`DesignSheetExtractionError`) | high | none | **yes**, one call per sheet first page, escalate per page only if accepted | yes | first page image + word boxes (`page.get_text("words")`) | as above; plus `_looks_like_line_items` on the result |
| `AMBIGUOUS_OCR` | quantity cell unparsable, or DRF value confidence < calibrated threshold with alnum content (`_clean_quantity` None, `_read_value` conf) | medium | `QUANTITY_CONFUSIONS`, psm-11 read (done) | **yes**, cell crop only | yes if still unresolved | cell crop (+ row description text) | proposal must be a digit string; must match one of top-k OCR alternatives or be echoed with the crop bbox; never accepted for a cell the model calls "unclear" |
| `AMBIGUOUS_SYSTEM` | sheet `system_guess` None and `infer_single_system` None (`projects.resolve`) | medium | aliases + single-system inference (done) | **yes**, classification among DRF-marked codes only | yes -- reviewer picks in the form | sheet first-page text (first 60 lines), DRF marked rows | proposed code must be in the candidate set; shown as suggestion, not applied |
| `CONFLICTING_SOURCE_VALUES` | reextraction `differs` on a reviewed field; two sheets of one system give different rows | medium | letters/digits compare (done) | may **summarise** the difference | yes | both values + sources | narrative only; no field write |
| `AMBIGUOUS_REVISION` | >1 sheet per system with no `declared_revision` or equal revisions (`mark_superseded`) | high | highest declared revision default (done) | may summarise differences | **yes, always** | filenames, dates, first-page text | cannot select |
| `QUANTITY_OR_UNIT_PARSE_FAILURE` | `Quantity.kind == unresolved`; "Lot"-like word not in `WORD_QUANTITIES` | medium | word list | **yes** for the cell crop | yes | cell crop | as AMBIGUOUS_OCR |
| `SOURCE_ROOT_MISMATCH` | `_validate_source_paths` (done) | high | none | **no** | yes (re-select) | -- | -- |
| `UNSUPPORTED_DOCUMENT` | non-PDF/XLSX; PDF with 0 pages; workbook with no taps (`WorkbookReadError` classes) | high | none | no | yes | -- | -- |
| `MISSING_ENGINEERING_EVIDENCE` | part without a current after `fill-currents`; battery brand not in library; no datasheet for a submittal part | high | library lookup (done) | **no** -- retrieve the datasheet or ask the engineer | yes | -- | -- |
| `OCR_NOISE_DROPPED` (info) | `_is_ocr_noise` true (done) | info | done | no | no | -- | -- |

Confidence: Tesseract word confidence and any model self-score are inputs
to the router, never the sole acceptance criterion. Thresholds are set from
the labelled fixtures in §9 (the ten DRFs, EP-30208, the two workbooks) and
recorded in `validation_policy_version`.

---

## 5. Evidence builder (`app/ai/evidence.py`)

One builder per eligible code, returning `Evidence(task, issue, parts=[...],
schema, byte_estimate)` where each part is one of: `ImageRegion(page,
bbox, dpi, png_bytes)`, `Text(label, text, source_ref)`, `Facts(dict)`.

Limits (config, defaults): one page image at 150 dpi max, cropped to the
region's bbox with 40 px margin; text parts capped at 4 KB each; at most 3
neighbouring headings; project facts = EP number, marked systems, sheet
code candidates. Never: other pages, other projects, the BOQ, the DB row,
credentials, contact fields (name/phone/email are not needed for any
eligible task and are excluded by allow-list).

Document text is quoted inside a delimited data block with an instruction
that it is data; the provider call uses a fixed system prompt and no
tools. The output schema is enforced by the provider's structured-output
feature where available, and re-validated locally regardless.

If the builder cannot reach the minimum context (e.g. the page image cannot
be rendered), the issue stays `NEEDS_HUMAN_DECISION` with reason
`insufficient_evidence`; no call is made.

---

## 6. Proposal schema and validation (`app/ai/proposals.py`)

```python
class SourceRef(BaseModel):
    document_sha256: str
    page: int | None
    sheet: str | None
    region: tuple[float, float, float, float] | None   # bbox in page points, or cell "B12"

class ProposedChange(BaseModel):
    target: str            # "boq_line:<run_id>:<page>:<region>:<row>" | "drf_field:plot_number" | "sheet_system:<sheet_id>"
    value: str | int | float | dict
    source: SourceRef
    reason: str            # <= 200 chars

class Proposal(BaseModel):
    task_id: str
    status: Literal["proposed", "insufficient_evidence", "needs_human_review"]
    proposed_changes: list[ProposedChange] = []
    source_references: list[SourceRef] = []
    unresolved_issues: list[str] = []
    prompt_version: str
    schema_version: str
```

Validation, in order, all in Python:
1. Schema/types (pydantic).
2. `target` matches the task's allow-list (a quantity task may only target
   that row's quantity).
3. `source` refers to a document/page/region the evidence actually
   contained (the builder records what it sent; a reference outside it is
   rejected).
4. Type rules: quantity is `\d+` or a `WORD_QUANTITIES` member; system code
   in candidate set; DRF field non-empty and not `_is_ocr_noise`.
5. Independent check: for OCR tasks, the proposed digits must appear in a
   Tesseract re-read of the same crop with `--psm 7` or `--psm 11`
   alternatives, or in `QUANTITY_CONFUSIONS` mapping of the raw read;
   otherwise status becomes `needs_human_review` with the proposal kept as
   a *suggestion*. For layout tasks, `_looks_like_line_items` and the
   region's OCR word count must bound the row count (rows <= text lines).
6. Contradiction: a target whose reviewed value exists (engineer edited the
   BOQ line, `PUT /projects/{id}`) is never written; the proposal is stored
   with `state=superseded_by_review`.
7. Result: `AiProposal.state in {proposed, validated, rejected(reason),
   accepted(by, at), superseded_by_review}`. Only `accepted` moves data, and
   only through the existing edit endpoints (BOQ PUT, project PUT, sheet
   PATCH), which keep their permission checks.

---

## 7. Provider abstraction (`app/ai/provider.py`)

One module, one class, no per-feature clients.

```python
class AiProvider(Protocol):
    def complete(self, request: AiRequest) -> AiResponse: ...

@dataclass
class AiRequest:
    task: str                     # router code
    prompt_version: str
    schema: type[BaseModel]
    parts: list[Part]
    model_tier: Literal["small", "standard"]
    max_output_tokens: int
    timeout_s: float
    idempotency_key: str          # = cache key

@dataclass
class AiResponse:
    text_or_json: str
    usage: Usage                  # input, output, cached_input, reasoning -- each Optional
    model: str
    latency_ms: int
    error: AiError | None         # normalized: transport | rate_limit | invalid_response | refused
```

- Model names, context limits and prices are **configuration**
  (`AI_MODEL_SMALL`, `AI_MODEL_STANDARD`, `AI_PRICE_*` per million tokens),
  read from `.env`; the plan does not hardcode any advertised name. Before
  wiring, check the provider's current documentation for the structured
  output feature and usage field names.
- Escalation rule (Python, not the model): small tier first for
  `AMBIGUOUS_OCR` and `AMBIGUOUS_SYSTEM`; standard tier for
  `UNRECOGNIZED_TABLE_LAYOUT`/`UNPROCESSED_PAGE_OR_REGION`; escalate small
  -> standard once when the response is `invalid_response` or fails
  validation step 5, never on the model's own request.
- Concurrency: a process-wide semaphore (`AI_MAX_CONCURRENCY`, default 2);
  in-flight de-duplication by `idempotency_key` (second identical request
  waits for the first's result).
- Retries: transport/rate-limit -> up to `AI_MAX_RETRIES` (3) with
  exponential backoff and jitter; `invalid_response` -> one re-ask with the
  validation error appended; `refused`/`insufficient_evidence` -> no retry.
- Cancellation: honour the job's cancel flag between calls; a single call
  is bounded by its timeout.
- A `NullProvider` (returns `insufficient_evidence` for everything) is the
  default when `AI_ENABLED=false`; every deterministic path must pass with
  it -- that is the rollback.

---

## 8. Result cache (`app/ai/cache.py` + table `result_cache`)

Key = SHA-256 over the canonical JSON of:

```
{ "scope": <tenant or "default">,          # single-tenant today; field present for later
  "document_sha256": ..., "page": ..., "region_hash": <sha of the evidence bytes>,
  "task": <router code>,
  "context": { "system_candidates": [...], "marked_rows": [...] },   # only what the task uses
  "parser_version": design_sheet_extractor.PARSER_VERSION,
  "prompt_version": ..., "schema_version": ...,
  "model": <model id when the task is interpretive>,
  "policy_version": validation.POLICY_VERSION }
```

- Never filename or EP number.
- Stored value: the *validated* proposal (or `insufficient_evidence`) plus
  its state. `accepted` state is on `AiProposal`, not the cache; a cache hit
  returns a validated proposal that still goes through contradiction check
  (step 6) before it can be applied, since the reviewed value may have
  changed since.
- Invalidation is by key change: any of the components changing produces
  a new key; old rows are pruned by age (`AI_CACHE_TTL_DAYS`, default 90).
- Access: a hit is served only if the caller has read access to the
  project the cached document belongs to (`document_sha256` -> project via
  `ProjectDesignSheet`).
- Deterministic results (a valid extraction with no issues) are cached by
  the same key minus prompt/model, so an unchanged sheet is not re-OCR'd
  on `reextract` unless `parser_version` changed. This is where most of the
  saving comes from: OCR time, not tokens.
- Provider prompt caching: enable if the provider supports it for the fixed
  system prompt; do not pad; do not rely on it for correctness.

---

## 9. Budgets (`app/ai/budget.py`)

Config (`.env`, all with defaults; `AI_ENABLED=false` by default):

| Setting | Default | Scope |
|---|---|---|
| `AI_MAX_INPUT_TOKENS_PER_TASK` | 6 000 | one call |
| `AI_MAX_OUTPUT_TOKENS_PER_TASK` | 800 | one call |
| `AI_MAX_CALLS_PER_DOCUMENT` | 12 | one sheet/DRF run |
| `AI_MAX_CALLS_PER_PROJECT_PER_DAY` | 60 | rolling |
| `AI_MAX_COST_PER_JOB` | 0.50 (currency unit) | estimated, from configured prices |
| `AI_MAX_RETRIES` | 3 | transport |
| `AI_MAX_ELAPSED_S_PER_JOB` | 120 | wall clock |
| `AI_MAX_ESCALATIONS_PER_DOCUMENT` | 2 | small -> standard |

Mechanics: estimate input tokens from evidence bytes (text: len/4; image:
provider formula from its docs, configurable) and **reserve** against the
job's remaining budget under a lock before the call; reconcile with the
provider's reported usage after; refund the difference. Reservation state
lives in the `ai_jobs` row so concurrent workers see one number.

When any limit trips: the call is not made, the issue stays
`NEEDS_INTERPRETATION` with reason `budget_exhausted`, the run outcome is
`BUDGET_EXHAUSTED` if any issue was starved, and partial results are saved.
"Retry" is a user action on the run, not automatic.

Usage rows (`ai_usage`): run_id, task, model, input/output/cached/reasoning
tokens (nullable), estimated cost, latency, cache_hit, escalated, outcome.

---

## 10. Data model additions (Alembic migration, additive only)

- `extraction_runs`: id, project_id, document kind, document_sha256,
  sheet_id/DRF path, parser_version, policy_version, outcome, coverage
  JSON, started/finished, triggered_by.
- `extraction_issues`: run_id, code, severity, page, region, target,
  detail JSON, state (open | repaired | proposed | resolved_by_human |
  rejected), resolved_by, resolved_at.
- `ai_proposals`: issue_id, cache_key, prompt/schema/model versions,
  proposal JSON, validation JSON, state, accepted_by/at.
- `ai_usage`: as §9.
- `result_cache`: key, kind (deterministic | proposal), value JSON,
  document_sha256, project_id, created_at, last_hit_at.
- `ai_jobs`: id, project_id, kind, status (queued | running | succeeded |
  failed | cancelled | budget_exhausted), progress JSON, reservations JSON,
  created_by, created_at, updated_at, error.

No existing table changes. Existing projects are untouched; nothing is
migrated; `boq_extracted_at` semantics stay.

---

## 11. Where each change lands (concrete)

| Change | File(s) | Size |
|---|---|---|
| Structured issues + coverage from the design-sheet extractor (return `ExtractionResult(lines, coverage, issues)`; keep `extract_boq_lines` as a thin wrapper for callers/tests) | `services/design_sheet_extractor.py` | medium |
| Same for the DRF extractor (issues per block, cell crops retained in memory for the run) | `services/drf_extractor.py` | small-medium |
| `PARSER_VERSION` constants | both extractors, `ve_workbook_reader`, `datasheet_currents` | trivial |
| `extraction/validation.py` (Quantity typing, coverage rules, POLICY_VERSION) | new | small |
| `extraction/router.py` (table in §4 as code) | new | small |
| `extraction/run.py` (A-J orchestration; called from `boq/ensure`, `resolve`, `reextract`) | new | medium |
| `ai/provider.py`, `ai/evidence.py`, `ai/proposals.py`, `ai/cache.py`, `ai/budget.py`, `ai/usage.py`, `ai/null_provider.py` | new package | medium |
| `jobs/` (persisted job rows; `log_scan_jobs` migrates onto it later, not now) | new, small | small |
| Migration `xxxx_extraction_runs_and_ai.py` | `alembic/versions` | small |
| Endpoints: `GET /projects/{id}/extractions/{run}` (issues, coverage, proposals), `POST .../issues/{id}/accept|reject`, `POST .../runs/{run}/retry`, `GET /admin/ai/usage` | `routers/extraction.py`, `routers/admin_ai.py` | medium |
| UI: BOQ page banner (stage, N rows need review, M pages unprocessed, "AI suggested 3 quantities -- review"), issue list with the cell crop and Accept/Reject; review form already shows sheet choices; admin diagnostics page | `frontend/src/pages/ProjectBoqPage.tsx`, new `ExtractionIssues.tsx`, `AdminAiUsagePage.tsx` | medium |
| Config: `AI_ENABLED`, provider key, model tiers, prices, budgets | `core/config.py`, `.env.example` | small |

Order of delivery (each a reviewable change with tests):
1. Issues + coverage + PARSER_VERSION in the two extractors, with the
   outcome enum; UI banner reads them. **No LLM yet.** This alone makes
   partial extraction visible and is the regression base.
2. Deterministic result cache keyed by content hash (OCR reuse on
   `reextract`).
3. `ai/` package with `NullProvider`, router, evidence builder, proposal
   validation, budget, usage tables -- all testable with mocks; flag off.
4. Real provider behind `AI_ENABLED=true` on a dev machine; live
   evaluation set (§13); thresholds calibrated.
5. UI accept/reject; admin usage view.
6. Only then: enable for `AMBIGUOUS_OCR` in production; layout tasks after
   the evaluation shows they add rows that validation confirms.

---

## 12. Tests (mapping to the brief's §14)

All under `backend/tests/test_ai_routing.py`, `test_ai_cache_budget.py`,
`test_extraction_outcomes.py`, using a `RecordingProvider` mock that
returns scripted proposals and counts calls.

| Requirement | Test |
|---|---|
| Complete supported document -> zero calls | synthetic 6-rule sheet (existing fixture builder in `test_design_sheet_extractor`) through `run.py` with `RecordingProvider`; assert `calls == 0`, outcome `VALID` |
| Validated cache hit -> zero calls | run twice on the same bytes; second run hits `result_cache`; `calls == 0`; OCR function patched to count invocations == 0 on the second run |
| Ambiguous region sends only necessary evidence | one unreadable quantity cell; assert the request has exactly one `ImageRegion` (that cell's bbox) and one `Text` (its row), byte size under the cap, no contact fields |
| Unprocessed pages trigger attention despite valid rows | two-box page with the second box's rules broken so `_table_extents` misses it: rows from box 1 are well formed; coverage marks a region `skipped`; outcome `VALID_PARTIAL`; issue `UNPROCESSED_PAGE_OR_REGION` |
| Changed document invalidates cache | append a byte to the PDF; key differs; run again -> parse runs |
| Unchanged document not reprocessed | `reextract` on unchanged sheet -> cache hit, OCR count 0 |
| Simultaneous identical requests -> one call | two threads, same key, provider sleeps 0.2 s; `calls == 1` |
| Budget exhaustion preserves status | `AI_MAX_CALLS_PER_DOCUMENT=1` with two issues: second stays `NEEDS_INTERPRETATION`, outcome `BUDGET_EXHAUSTED`, first proposal saved |
| Invalid/unsupported proposals cannot overwrite | proposal targets another row / a reviewed line / cites a page not sent -> `rejected`, BOQ unchanged |
| Missing engineering evidence not invented | part without datasheet: router yields `MISSING_ENGINEERING_EVIDENCE`, `calls == 0`, `missing_parts` unchanged |
| Retry/refresh does not duplicate mutations | `POST .../runs/{run}/retry` twice while running -> second returns the same job id; accept an issue twice -> one BOQ change, second is 409 |
| Calculators/exports independent of LLM | `AI_ENABLED=false` + `NullProvider`: battery arithmetic fixtures 250.312944 / 491.765232 Ah, amplifier 496.25 W and the 49.9 W boundary, PDF pagination test -- all pass (these tests already exist; the new test asserts the provider was never constructed) |

Existing regressions kept: `test_review_fixes.py` (46), the live-archive
checks for EP-30208 (92/97 rows, buildings kept apart), the ten DRFs
(notes, PA row, EP-31725 plot), and the EP-30175 / EP-24601 arithmetic.

---

## 13. Measurement plan

Same fixtures, two pipelines (flag off = current; flag on = proposed):

- Fixtures: the ten reviewed DRFs; EP-30208 (4 pages, 9 buildings, 97/700);
  the six unreadable sheets (30175 x2, 31055, 30387, 30977, 31725) with a
  hand-made row count for each (to be produced by an engineer -- **needed
  and not available today**); EP-29495 and EP-30784 sheets as "must stay
  zero-call" controls; EP-24601 and EP-29495 amplifier workbooks.
- Metrics per run, from `ai_usage` and the run rows: model calls;
  input/output/cached/reasoning tokens (null where the provider does not
  report them); estimated cost from configured prices (labelled estimated
  until reconciled with a provider invoice); median and p95 latency;
  cache-hit rate; share of issues resolved without a call; escalation
  rate; rows extracted vs. source count; field accuracy against the
  hand-checked values; missed-error rate (rows the pipeline marked VALID
  that the hand count says are wrong); human-review rate; tokens and cost
  per accepted change.
- Reported as a table in `docs/AI_EVALUATION.md` after step 4 of §11. No
  percentage is promised in advance. The success criterion is: rows and
  fields recovered that validation confirms, at or below the budget, with
  the missed-error rate not above the current pipeline's.

Measurement gaps today: no ground truth for the six unreadable sheets;
no provider chosen, so no usage fields to reconcile; no labelled set for
calibrating OCR-confidence thresholds beyond the samples in
`docs/REVIEW_FIXES.md`.

---

## 14. Dependencies, risks, rollout

Dependencies: provider SDK and key (choice open); Alembic migration;
`pymupdf` for region crops (present); Tesseract (present) for the
independent re-read in validation step 5.

Risks and mitigations:
- A model "reads" a digit that is not there -> validation step 5 requires
  agreement with an OCR alternative or explicit human review; never
  auto-applied to a reviewed row.
- Layout proposals inflate row counts -> rows bounded by OCR text lines;
  `_looks_like_line_items`; coverage marks the region `proposed` not
  `accepted` until a human accepts.
- Cost creep from repeated opens -> `boq_extracted_at` stamp, result cache,
  in-flight de-duplication, per-project daily cap.
- Prompt injection from document text -> data-block quoting, no tools,
  schema-only output, allow-listed targets.
- Personal data leakage -> evidence allow-list excludes contact fields;
  tests assert it.
- Regression in the deterministic path -> `NullProvider` default; all
  existing tests run with the flag off in CI.

Rollout: steps 1-3 of §11 ship with the flag off (no behaviour change
except structured issues in the UI); step 4 on one workstation against the
live evaluation set; step 6 enables one task class at a time.

---

## 15. Human decisions required before implementation

1. Provider and the two model tiers (and therefore the price table and
   usage field names).
2. Budget defaults in §9 and who can raise them (admin only?).
3. Whether an accepted proposal writes through the existing edit endpoints
   as the accepting engineer (recommended) or as a system user.
4. Ground truth for the six currently unreadable design sheets (an
   engineer's row counts) -- without it the layout task cannot be
   evaluated and should not be enabled.
5. Whether `AMBIGUOUS_SYSTEM` suggestions may appear on the review form
   (suggested, unticked) or only after creation on the Documents page.
6. Retention period for `ai_usage` and `result_cache`.
