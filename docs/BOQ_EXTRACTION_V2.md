# BOQ Extraction Accuracy & Latency V2

Working notes for the task of 27 September 2026. Part A is the as-is report,
written before any source was changed; part B is the phase plan and what each
phase changed.

## A. As-is (branch state on 2026-09-27, before this task)

### A1. Execution trace

Design Sheet (a scanned PDF: EP-30784's FAS and EML sheets have **no text
layer**, one image per page, 595x842 pt, rendered at 300 dpi = 2480x3507 px)

1. **Entry** `routers/projects.py::ensure_project_boq` (POST `/projects/{id}/boq/ensure`).
   If `project.boq_extracted_at` is set, the stored BOQ is returned. Otherwise,
   when the model is available and any sheet has no stored reading, a
   `boq_read` `BackgroundJob` is started (`_start_boq_read`, thread of the API,
   `jobs.start`) that calls `_extract_boq`.
2. **Per sheet** `_extract_boq` → `_read_design_sheet` → `ai/sheet_reader.py::read_design_sheet`
   (the deterministic reader `services/design_sheet_extractor.py` no longer
   reads sheets since the owner's 2026-09-17 decision; its page rendering and
   quantity parsers are still used).
3. **Primary read** `sheet_reader.read_document` → `_read_page(tier="small")`:
   each page is scaled to 1600 px wide and cut into bands of 1100 px with 180
   px overlap (an A4 scan = 3 bands). One AI call per band, task
   `read_sheet_page`, schema = rows with kind/quantity/catalog_no/description/
   readable/box. Rows are assigned to the band whose "own zone" holds the row's
   centre; boxes are mapped back to page pixels.
4. **Second read** the same pages, same bands, `tier="standard"`, task
   `read_sheet_page_second` — a full second AI read of every page, always.
5. **Stored reading** one `DocumentReading` per document SHA-256 + prompt
   version: `{"pages": [...], "second": [...]}`, `status` = completed unless a
   *primary* page failed. Readings are permanent (36,500 days) and shared
   across projects.
6. **Row parsing / matching** `combine()`: primary item rows are paired with
   second-read rows by `_match`: same `identity.part_key(catalog_no)` scores
   2 + description ratio, else description `SequenceMatcher` ratio ≥ 0.8; a
   candidate must be within 320 px vertically. Sections/headings set a running
   `section`/`heading` that every following item inherits ("last heading
   wins", no interval logic).
7. **Quantity agreement** `verification.agree_quantity` on the two readings
   (quantity only; catalog identity is not required to agree).
   - agree → BOQ line, confidence 92.
   - dispute or one-sided → one **close-up** AI call (task
     `read_sheet_row_close_up`, standard tier, strip around the box); the
     close-up settles for whichever reading it agrees with (confidence 90/85),
     else the row goes to review.
   - neither readable → review.
   - `run.exhausted` (budget) → review, reason "the AI budget ran out".
   - rows found only by the second read: a close-up must agree with the
     second reading's quantity, else the row is **silently dropped**.
8. **Review rows** become `Issue(QUANTITY_OR_UNIT_PARSE_FAILURE, target="boq_line:{page}:{ordinal}")`
   with detail (description, catalog_no, group_heading, raw_quantity,
   alternates, ai_reading, reason). The reason is free text.
9. **Persistence** `_extract_boq`: (a) UPDATE projects SET boq_extracted_at
   WHERE NULL, **commit**; (b) `pipeline.record_design_sheet_run` per sheet
   (ExtractionRun + ExtractionIssue rows), **commit per run**; (c) append
   `ProjectBoqItem`s, `boq_version += 1`, **commit**. Then (outside the lock)
   `document_sync.register_intake_dependencies` and `assist_project`
   (`read_cell` proposals on open issues, its own budget).
10. **Completion flag** = `boq_extracted_at` (set in step 9a, before any line
    exists) plus `BackgroundJob.status` = succeeded when `work` returns.
11. **Later: AI check** `ai/verification.py::verify_boq` (job `ai_verify`,
    auto-started after the read when `ai_verify_auto`): builds a
    `BoqCandidate` by re-reading the sheets (`boq_candidates.build` →
    `sheet_reader` again, reading reused), pairs held lines with fresh rows,
    reads rows blind in strips of 16 (`verify_boq_rows`), a second strip read
    (8 rows, standard tier) when unsettled, close-ups for up to 12 rows, and
    `_find_lines` (`verify_boq_find`, 3 pages per call) for held lines the
    fresh read did not yield. Applies through `boq_candidates.apply` after a
    snapshot.

### A2. Every AI call (per sheet of P pages, B bands per page; EP-30784 FAS: P=2, B=3)

| Stage | Task | Tier | Calls | Measured latency (claude-code CLI, EP-30784) |
|---|---|---|---|---|
| Primary read | `read_sheet_page` | small (sonnet) | P×B = 6 | avg 119 s |
| Second read | `read_sheet_page_second` | standard (opus) | P×B = 6 | avg 107 s |
| Close-ups | `read_sheet_row_close_up` | standard | one per disputed / one-sided row | avg 7.7 s (68 calls) |
| Cell assistance | `read_cell` | small | one per open issue (separate budget) | avg 5.3 s (16 calls) |
| AI check strips | `verify_boq_rows` | small then standard | ⌈rows/16⌉ + ⌈unsettled/8⌉ | 140 s (sonnet) / 40 s (opus) |
| AI check close-ups | `verify_boq_row_close_up` | standard | ≤ 12 | — |
| AI check find | `verify_boq_find` | small then standard | ⌈pages/3⌉ per system | — |

EP-30784 (2026-09-26 run 2): 6 + 6 band reads ≈ 22.6 min, then close-ups;
the 30-minute `ai_read_max_elapsed_s` tripped during the second read of
page 2 (stored as `"failed": "budget: elapsed_time"` inside `reading.second`)
and during the close-ups: 42 lines, 34 review rows, all 34 with the reason
"the AI budget ran out before the row could be settled" although every one of
them carries a readable primary quantity in `ai_reading`.

### A3. Timeout / budget checks

- `JobBudget.reserve` (ai/budget.py) before every call: elapsed time
  (`ai_read_max_elapsed_s` 1800 s for the read, `ai_verify_max_elapsed_s`
  3600 s for the check), input/output token ceilings, calls per document
  (60 / 80), calls per project per day (600 / 240, or `AI_MAX_CALLS_PER_PROJECT_PER_DAY`
  = 150 from `.env`), cost per job. It is checked only at the moment a call is
  about to be made; nothing looks ahead.
- Provider timeouts: CLI `ai_cli_timeout_s` 300 s per call (`subprocess.run`
  timeout → `AiResponse(error="transport")`); SDK `ai_timeout_s` 60 s with
  `ai_max_retries` 3 inside the SDK.
- Retries: none in `assist._call`; `pipeline.ask` escalates once to the
  standard tier on `invalid_response`.
- `_Run.exhausted` is set from `session.exhausted` after any call trips the
  budget; from then on `_read_page` returns a failed page and `combine`
  sends every remaining row to review.

### A4. Where a row can be dropped or an item removed (confirmed in code)

| Where | What happens | Verdict |
|---|---|---|
| `sheet_reader.read_document` L361–L408 | `status` is "completed" unless a *primary* page failed; a failed second read (`"failed": "budget: elapsed_time"` on page 2 of the FAS sheet) still stores a completed reading, and `existing.reading.get("second") is not None` then reuses it forever, so the second read of page 2 is never made again. | **Root cause 1** (incomplete read stored as complete). |
| `sheet_reader._match` L424–L448 | A row pairs on description similarity ≥ 0.8 when part numbers differ or are missing; `combine` then compares quantities only, so `SIGA-OSD-FCN 2396` can be "verified" by a second-read row `SIGA-HRD-FCN 2396` if the descriptions read alike. | **Root cause 2** (identity not required). |
| `sheet_reader.combine` L631–L644 | Rows only the second read found: `continue` when `run.exhausted`, when the close-up fails (None), or when it disagrees → the row vanishes from lines *and* review. | **Root cause 3** (silent loss). |
| `sheet_reader.combine` L597–L599 | On budget exhaustion every remaining row is a review row with reason "budget ran out"; the primary quantity is kept in `ai_reading` but the issue is worded as an unsettled quantity. | Semantics wrong (A2 numbers). |
| `verification._find_lines` L625–L648 | Returns only `found` lines; a timeout, provider error or empty reply is indistinguishable from "not on the sheet". | **Root cause 4** feeds L919. |
| `verification._verify_boq` L919–L920 | `elif not a1 and not a2 and not run.exhausted: outcome = "removed"` → `decisions[key] = "accept"` on a `removed` change → `boq_candidates.apply` deletes the line. Only `run.exhausted` guards it; a transport error, an `invalid_response`, a refusal or a per-call CLI timeout does not set `exhausted`. | **Root cause 4** (AI failure removes an item). |
| `verification._read_rows` L520–L525 | A strip answer missing a label sets `ai1`/`ai2` to None; `settle` then treats None as "no reading" — not a removal, but an unresolved outcome with the held value kept. | acceptable |
| `pipeline.assist_run` | `starved` issues stay; nothing removed. | acceptable |
| `provider.ClaudeCodeProvider.complete` L532 | `tempfile.TemporaryDirectory` cleanup raises `PermissionError: [WinError 32]` when `claude.exe` still holds an image; the exception escapes `complete()`, through `assist._call` and `read_document`, and the sheet is recorded "Not read: the AI read failed (PermissionError…)" (ExtractionRun 1) or the check fails (AiVerification 2). | **Root cause 5**. |
| `projects._extract_boq` L885–L925 | `boq_extracted_at` committed first, runs committed one by one, lines committed last; a failure between leaves a project "extracted" with no lines, and `ensure_project_boq` never reads again. | **Root cause 6**. |

### A5. Where the run can be marked complete

- `DocumentReading.status = "completed"` in `read_document` (see root cause 1).
- `Project.boq_extracted_at` in `_extract_boq` step 9a — before lines exist.
- `BackgroundJob.status = "succeeded"` in `jobs.execute` when `work` returns —
  regardless of budget exhaustion; the job result carries `lines` and
  `warnings` only.
- `ExtractionRun.outcome` (`issues.outcome_for`): VALID / VALID_PARTIAL /
  NEEDS_INTERPRETATION / … / BUDGET_EXHAUSTED — the only place the budget is
  visible, and `budget_exhausted` is set only by `assist_run`, not by the read.
- `AiVerification.status = "completed"` in `_finish`, also when `run.exhausted`.

There is no state for "partial" or "timed out" anywhere a page reads it.

### A6. Cache / checkpoint behaviour

- `result_cache` (ai/cache.py): key = scope + document SHA + evidence
  fingerprint (SHA of the parts, image bytes hashed) + task + parser/prompt/
  schema versions + model. Every sheet-reader and verification call goes
  through it with a 36,500-day TTL; identical band and close-up images are
  therefore replayed at no cost — this is the only "checkpoint" the close-up
  stage has, and it holds only while the crop bytes are identical.
- `DocumentReading` is the page-level checkpoint for the two page reads, but
  with the status defect above. No row-level state is stored; `combine`
  recomputes everything on every open, calling the model again for any
  close-up whose crop is not cached.
- `_reading_key` in verification (document SHA, page, region, kind, model,
  prompt) — a row-level cache for the strip reads.

### A7. Row-matching rules today

- Sheet read: `_match` above (part key, else description ratio ≥ 0.8, within
  320 px).
- AI check: `boq_candidates.compare` pairs held and fresh lines by
  `_key` / probable matches; `agree_catalog` = same alphanumerics, or the same
  after the part library settles OCR confusions (S/5, O/0).
- `identity.part_key` and `identity.clean_catalog` already normalise
  case, separators and edge junk; they are used for matching only where a
  catalog number exists on both sides.

### A8. Transaction boundaries

`_extract_boq`: three commits (stamp; each run; lines). `record_design_sheet_run`
commits internally. `verification._verify_boq`: `_start` commit,
`boq_candidates.build` commits, `apply` commits after snapshot, `_finish`
commits. `assist_run` commits per issue. `jobs.execute` commits the job row
separately from the work's own commits.

### A9. Golden fixture

`tests/fixtures/boq_ep30784.json` was generated from the dev database
(header says so): 79 lines, OCR text kept as read, `TP606 = 49` where the
sheet reads 491 (the database read on 2026-09-27 holds 491 on both TP606
lines). It is used by `test_battery_calculation.py` as a realistic BOQ input
only. It is not a truth set.

### A10. Numbers to beat (EP-30784, FAS + EML)

Run 2 (2026-09-26, cold): 42 lines, 34 review rows, budget `elapsed_time`,
≈ 30 min. Runs 4/6 (2026-09-27, readings reused): 70 + 12 lines, 6 review
rows, resolved by the AI check (2 calls, 83 readings reused). Total AI calls
on the project so far: 15 + 7 band reads, 68 close-ups, 16 cell reads, 4
strip reads.

## B. Plan

Assumptions stated up front:

- The sheets are scans without a text layer, so "PDF text coordinates"
  (section 8 of the task) come from OCR word boxes (Tesseract TSV at the
  extractor's 300 dpi) and from the table rules `design_sheet_extractor`
  already detects. Where OCR cannot place a row the model's own row box is
  the geometry, marked as such in the confidence.
- The owner's 2026-09-17 decision (AI-only extraction) is superseded by this
  task's "deterministic first" for *row geometry and confidence*; the model
  still reads the rows, and no line is written from OCR text alone.

### Phase 1 — safety / state (this change set)

1. `DocumentReading.reading["stages"]`: per page `{"primary": ..., "second": ...}`
   ∈ complete | failed | pending, plus `"rows"`: the settle stage per row id
   (`p{page}r{n}` primary rows, `p{page}s{n}` second-only rows) with its
   outcome; `status` ∈ completed | partial | failed. A reading is completed
   only when every stage of every page is complete.
2. `read_document` resumes: a partial reading gets only its pending stages
   read; nothing complete is read again.
3. `combine` never drops a row: second-only rows whose close-up fails or is
   skipped become review rows (reason `VERIFICATION_NOT_COMPLETED` or
   `QUANTITY_CONFLICT`); budget-exhausted rows keep their primary value with
   reason `TIME_BUDGET_EXHAUSTED`; every review issue carries a structured
   `reason_code`, `row_id`, `source_stage`, `primary` and `verification`.
4. Identity is required for agreement: `_match` pairs by normalised part
   number first; a description-only pair is `PART_NUMBER_CONFLICT` when the
   part numbers differ, and never a line.
5. `verification._find_lines` returns an explicit outcome per line
   (`found` / `not_found` / `timeout` / `provider_error` / `invalid_response`
   / `budget_exhausted`); removal only on two explicit `not_found`s.
6. `ExtractionRun.state` ∈ pending | running | partial | completed | timed_out
   | failed | cancelled, set from the reading's stages; `budget_exhausted`
   set by the read.
7. `_extract_boq` writes the stamp, the runs, the lines and the version in
   one transaction; `record_design_sheet_run(commit=False)`.
8. Provider temp-folder cleanup isolated: bounded retries, then a warning and
   a deferred sweep; never an exception out of `complete()`.
9. Budget look-ahead: `JobBudget.remaining_s()`; a close-up batch is not
   started when less than one call's typical duration remains.

#### Phase 1 -- what changed (2026-09-27)

- `app/ai/provider.py`: the CLI call's folder is made with `mkdtemp` and
  released in a `finally` by `release_folder` (five retries, then deferred to
  `sweep_deferred_folders` at the next call); a provider timeout is its own
  error kind, `timeout`, retryable.
- `app/ai/budget.py`: `remaining_s()` and `has_time_for(seconds)`.
- `app/core/config.py`: `AI_READ_BAND_RESERVE_S` (90) and
  `AI_READ_CLOSE_UP_RESERVE_S` (20): a page band or a close-up is not started
  with less than that left of `AI_READ_MAX_ELAPSED_S`.
- `app/extraction/issues.py`: `ReviewReason` codes and `PROCESS_REASONS`.
- `app/models.py` + migration `d2e3f4a5b6c7`: `extraction_runs.state`.
- `app/ai/sheet_reader.py`: `stages` / `pending_stages` / `reading_status`
  on the stored reading; `read_document` resumes pending page readings and
  keeps what it read on a stop; `same_item` (part-number identity) and
  `_match` on it; `combine` checkpoints every row's outcome in
  `reading["settled"]`, keeps a row whose close-up failed or was never made
  as a pending review row with its first reading, never drops a second-only
  row, refuses to settle on a different part number, assigns groups by the
  heading in force at the row's position, and sets `result.state` /
  `budget_exhausted`; review issues carry `reason_code`, `row_id`,
  `source_stage`, `primary`, `verification`, `pending`, `bbox`.
- `app/ai/verification.py`: `_find_lines` returns an explicit outcome per
  line; a removal needs two answered not-founds; a held line the fresh read
  left pending is not looked for and is kept; `_Run.last_error`.
- `app/routers/projects.py`: the stamp, the runs, the lines and the version
  are one transaction (`record_design_sheet_run(commit=False)`); the read
  job's result carries `state`.
- Tests: `tests/test_boq_extraction_v2.py` (12) and
  `tests/test_boq_verification_v2.py` (4); the removal scenario in
  `test_ai_verification.py` now answers with explicit not-founds.

#### Phase 1 -- EP-30784 result (2026-09-27, `bench/boq-metrics-before.json`, `bench/boq-metrics-phase1.json`)

Golden Truth: `tests/fixtures/boq_ep30784_golden_v1.json`, 74 FAS + 12 EML
rows transcribed from the scans (`boq_ep30784.json` is reclassified as a
regression fixture). Scored with `scripts/boq_metrics.py`.

| FAS sheet (74 golden rows) | Held BOQ before (run 6 + AI check) | Phase 1 read |
|---|---|---|
| lines / review rows | 76 / 0 | 76 / 0 |
| row detection recall / precision | 0.959 / 0.934 | 1.000 / 0.974 |
| part number / quantity / pair accuracy | 1.000 / 0.986 / 0.986 | 1.000 / 1.000 / 1.000 |
| group accuracy | 0.986 | 0.986 |
| false auto-accept rate | 0.079 (6 lines) | 0.026 (2 lines) |
| false removal rate | 0.041 (3 rows lost) | 0.000 |
| AI calls this run | -- | 3 band reads (page 2, second reading) + 0 close-ups; 71 s |
| timeouts | -- | 0 |

EML sheet: 12/12 on every metric, both before and after, 0 calls.

What the before-BOQ got wrong (all from the AI check's "added" rows and the
read cut short on 2026-09-26): SIGA-SD quantity 4 (the parent row's 4),
three lines whose part number and description belong to adjacent rows
(SIGA-CC1 / SIGA-CC2A, TP606 / TP434, 27193-11 / 757A-WB), two duplicated
panel components, and SIGA-CC2A, 757A-WB, GRSW-10 missing. The Phase 1 read
has none of these. Its two false accepts are one row each reported by two
adjacent page bands (4-COMREL and 3-SDDC2 at the band-1/band-2 boundary,
70 px apart in the model's drifting boxes); the one wrong group is
6538-G5 "Call for Assistance Kit" under the Booster Power Supply heading.
Both are Phase 2 (row association, group intervals).

The read that produced the 42 / 34 result of 2026-09-26 would now end as
`timed_out` with 34 pending review rows carrying their primary quantities
and reason `TIME_BUDGET_EXHAUSTED`, and a re-read would resume with the
second reading of page 2 alone -- which is what the Phase 1 run did: 3
calls instead of the 12 band reads + 68 close-ups of the original.

Not done in Phase 1: the frontend still words the review rows the old way
(Phase 4); no engineer-facing "Resume" -- the re-read job is the resume.

### Phase 2 -- deterministic accuracy (2026-09-27)

- `app/extraction/row_geometry.py` (new): Tier 0. The page's column rules
  (`design_sheet_extractor._find_layout`) and Tesseract's lines down each
  column strip, laid against every row the model read: `part_number_exact`,
  `part_number_near` (scan confusions I/1, S/5, O/0 folded; a code cut at
  the column edge; a code wrapped over two lines), `quantity_inside_expected_column`
  (the quantity column, or the inline "( n )" of a component row),
  `same_row_alignment` (the row located by its part number or wording within
  160 px of the model's drifting box), `description_alignment`,
  `single_quantity_candidate`, `neighbor_conflict`, `part_number_disagreement`,
  `quantity_disagreement`. Weighted sum in [0, 1]; levels from
  `BOQ_CONFIDENCE_HIGH` (0.85) / `BOQ_CONFIDENCE_LOW` (0.5). A number in a
  description or a part number is never a quantity candidate.
- `sheet_reader.band_duplicates`: a row reported by two adjacent page bands
  (different own zones, both within the overlap of their boundary, same item
  and quantity) is one row. `resolve_groups`: heading intervals; a row with
  its own part number and column quantity after a panel's inline-quantity
  components has an unresolved group -> review row `GROUP_UNRESOLVED`, never
  the last heading.
- The evidence travels with the line (`ExtractedBoqLine.evidence`,
  `raw_values.evidence`), the review issue and the row checkpoint.
- Tests: `tests/test_boq_geometry_v2.py` (10).

EP-30784, every row re-scored with the geometry (`bench/boq-metrics-phase2.json`):
FAS 74 / 74 rows, recall 1.0, precision 1.0, part 1.0, quantity 1.0, pair
1.0, false auto-accept 0.0, false removal 0.0 (the two band duplicates are
gone); group 0.986 (6538-G5, then resolved as a review row in phase 3);
EML 12 / 12. Geometry levels before the near-match: FAS 68 high / 6 low,
EML 7 high / 5 low -- all of the lows OCR confusions (4-CABI6D, SIGA-AAS0,
G1IARN, a cut-off SIGA-OSHD-FC, wrapped EML codes, TP606's "49]"), the
model's values right on every one.

### Phase 3 -- selective AI (2026-09-27)

- `AI_READ_FULL_SECOND_PASS` (default false): no second full-page reading.
  The second stage is owed only while it is on; a stored second reading is
  still used when it is there.
- `combine`: a row with a partner reading that agrees -> line (92); a row
  the geometry rates high -> line on the first reading (confidence = the
  score); otherwise the row goes to Tier 1: `_verify_rows`, a strip per row,
  `AI_VERIFY_ROWS_PER_CALL` (8) rows to a call by the small tier, each strip
  labelled with its row id and the answer matched by row id (never by
  order); Tier 2 (`_close_up`, the standard tier) only where Tier 1 disputes
  or cannot read the row; the engineer where the two do not agree with the
  first reading. Both tiers require the same item (`same_item`) and the
  same quantity.
- Row verification cache: `_row_verification_key` = document SHA + page +
  bbox to 10 px + hash of the first reading's values + model + prompt
  version + tier, through `app.ai.cache`; a strip read or close-up made
  before is not made again, even after the row checkpoint is cleared.
- Budget: `has_time_for(2 x AI_READ_CLOSE_UP_RESERVE_S)` before every
  batch, `has_time_for(AI_READ_CLOSE_UP_RESERVE_S)` before every close-up;
  a batch not started leaves its rows pending with `TIME_BUDGET_EXHAUSTED`.
- Tests: `tests/test_boq_selective_v2.py` (7); the legacy tests keep the
  second pass on through their fixture.

EP-30784, the selective read on a detached copy of each stored reading
with its second reading removed (`bench/boq-phase3-ep30784.json`):

| | FAS (74 golden) | EML (12 golden) |
|---|---|---|
| lines / review | 73 / 1 (`GROUP_UNRESOLVED`, 6538-G5) | 12 / 0 |
| wrong lines | 0 | 0 |
| settled by | geometry 70, tier 1 2, tier 2 1 | geometry 11, tier 2 1 |
| AI calls | 1 strip call (25.5 s) + 1 cached close-up | 1 strip (10.4 s) + 1 close-up (11.0 s) |
| wall | 31 s (incl. Tesseract) | 23 s |

Against the original reader on the same sheets: 12 band reads + 68
close-ups and 30 min to 42 lines and 34 review rows, versus 3 band reads
(already stored) + 3 verification calls and under a minute to 85 lines
and 1 review row, with 0 wrong lines. Batch sizes 5 / 8 / 10 were not
benchmarked against each other: on these sheets at most 3 rows needed a
strip, so one call at any size.

### Phase 4 -- UX / evaluation (2026-09-27)

- `frontend/src/components/ExtractionReview.tsx`: each row shows "Read as"
  (the first reading's quantity and part, the geometry level), "Verification"
  (not completed / disagrees on the quantity or part, with the verifying
  reading's values / could not read), "Reason" (the code worded, e.g. "The
  processing time budget was reached"), the heading question for an
  unresolved group; a process reason offers "Accept <qty>" as read, "Edit"
  and "Not an item"; a read that did not finish offers "Resume the read"
  (the re-read job, which resumes only what is pending). `ExtractionRun.state`
  in `types.ts`.
- `app/models.py` `BoqCorrection` + migration `e3f4a5b6c7d8` +
  `app/services/boq_corrections.py`: a review row accepted or rejected and a
  machine-read line's sheet values edited each record the document hash,
  page, bbox, row id, the first reading, the verification, the evidence,
  the reason code, the engineer's final values and the processor version.
  Hooked in `pipeline.accept_issue` / `reject_issue` and
  `boq_provenance.rebuild_items`. Test: `tests/test_boq_corrections_v2.py`.
- Metrics: `scripts/boq_metrics.py` (section 26), the golden set
  `tests/fixtures/boq_ep30784_golden_v1.json`.

### Remaining problems / not done

- The golden set was transcribed by Claude from the scans; the owner
  should countersign it (a second pair of eyes on 86 rows).
- Batch-size benchmark (5 / 8 / 10 rows per call) needs a sheet with more
  uncertain rows than EP-30784 has now.
- `GROUP_UNRESOLVED` sends an otherwise settled row to review (as the task
  asks); if that proves noisy, a line with group null and a flag is the
  alternative.
- The BOQ page's own banner and the AI check's summary still use the old
  wording; only the review panel was rewritten.
- The AI check (`verification.verify_boq`) still reads every row in strips
  of 16 by the small tier; it was made safe (phase 1), not selective.
