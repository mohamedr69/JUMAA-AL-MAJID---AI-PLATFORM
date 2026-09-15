# Accuracy, user experience, data handling and AI: what was implemented

Branch `accuracy-improvements`, 15 September 2026. This is the record against the
improvement plan of the same date (Releases 1–4). For each item: what changed,
where it lives, and how it is checked. Companion to `AI_IMPLEMENTATION.md`
(the AI layer this builds on) and `REVIEW_FIXES.md`.

## Operating notes first

- **Migrations back up the database.** Starting the backend with migrations
  pending takes a SQLite backup through the backup API into `backend/backups/`,
  verifies it (integrity check and matching row counts per table), and only then
  migrates. A backup that fails verification stops the migration.
  Administrators can take and test-restore backups at **AI & backups**
  (`/admin/system`).
- **Existing projects are not rewritten.** `scripts/repair_report.py` reports
  what a re-read would change. It is read-only unless `--write`. With `--write`
  it records a re-read candidate that an engineer reviews on the project's
  Re-read page; the BOQ changes only through that review.
- **AI stays off unless `AI_ENABLED=true`.** A project can refuse AI entirely
  (Project Info → AI use: Not allowed). A task can be switched off on the server
  with `AI_DISABLED_TASKS`.

## Release 1 — source identity, provenance, typed values, safe edits

| Plan item | Implementation | Checked by |
|---|---|---|
| Typed parsing with explicit rejection | `app/extraction/values.py`: quantity, decimal, money, voltage, current, power and revision parsers return `raw`, `value`, `status` (ok / empty / ambiguous / rejected) and the rule applied. No first-digit coercion. | `tests/test_values.py` |
| Provenance on every BOQ line | `project_boq_items` columns: extraction run, document SHA-256, page, region, raw values, OCR confidence, parser version, values as extracted, editor and time, origin (extracted / corrected / ai_accepted / review_accepted / manual / legacy). A save keeps provenance by line id. | `tests/test_boq_provenance.py` |
| Document intake gate | `app/services/document_intake.py`: each DRF and sheet is checked for readable pages, printed page numbering, EP number and source folder. Findings are blocked or warning; acknowledgements are recorded (some need a manager, some cannot be acknowledged). | `tests/test_document_intake.py`; all 14 real projects (see Release 2) |
| Readiness and completion blockers | `app/services/readiness.py`: documents, sheet revisions, coverage, unresolved rows, AI review, BOQ lines, re-read, battery and compliance. Issuing a BOQ revision returns 409 `not_ready` while any BOQ check is blocked or unknown. | `tests/test_document_intake.py`, `tests/test_boq_revisions.py` |
| No silent overwrites | `If-Match` / `X-Resource-Version` on project details, BOQ, battery and VE. Compliance rows carry the value they were edited from. A stale write returns 409 `stale_write` and the page offers to reload. | `tests/test_stale_writes.py` |
| Non-destructive re-read | `app/services/boq_candidates.py`: a re-read is a candidate compared line by line (exact / probable pairing). Every change needs a decision, a snapshot is taken before apply, and snapshots can be restored. UI: `ProjectBoqRereadPage.tsx`. | `tests/test_boq_candidates.py` |
| BOQ status strip and source badges | `ProjectBoqPage.tsx`: source file, parser version, coverage, unresolved and changed counts; per-line origin badge with provenance; filters; card view on narrow screens. | manual, `tsc` |

## Release 2 — OCR, identity, calculations, existing projects

| Plan item | Implementation | Checked by |
|---|---|---|
| Canonical buildings, catalog normalisation | `app/extraction/identity.py`: one canonical building name with aliases. Catalog cleaning for matching only (a leading £/€ read as E, §/$ as S); the source spelling is kept and the reason recorded. Part-library match with its reason. | `tests/test_identity.py` |
| Multi-pass quantity reads | `design_sheet_extractor.py`: pages straightened (OSD + deskew search). Each suspect quantity cell gets independent passes (padded, 3× upscaled, Otsu-binarised; single-line digits, single character, greyscale line). A read is kept only when it is confident and a pass agrees; otherwise it becomes a review row with the crop. `PARSER_VERSION = 2026-09-15.2`. | `tests/test_design_sheet_extractor.py` incl. the live EP-30208 sheet |
| Calculation hashes and completeness | `app/services/calc_integrity.py`: input and result hashes for battery and VE. VE reports `complete` and the reasons it is not. Automatic no-load parts must be confirmed or rejected, and the decision is stored. A workbook with uncalculated formulas is reported. | `tests/test_calc_integrity.py` |
| Existing-project workflow | `app/services/repair_report.py`, `scripts/repair_report.py` | run against a copy of the database |

Real-data results:
- EP-30208 reads 93 lines plus 4 review rows (700 units), checked against the page images.
- EP-30784 EML reads 12 lines and FAS 73.
- Intake across all 14 projects flags only two:
  - EP-31112: outside the source folder.
  - EP-30058: EP number in the filename mismatched; incomplete ELS sheet.
- EP-30175 has superseded revisions attached, which doubles its quantities; the repair report shows this.

## Release 3 — jobs, knowledge eligibility, compliance readiness, data controls

| Plan item | Implementation | Checked by |
|---|---|---|
| Progress and cancellation | `background_jobs` table, `app/services/jobs.py`, `app/routers/jobs.py`. Re-read and intake run as jobs with progress and cancel. Jobs interrupted by a restart are marked failed on start. UI: `useJob`, `JobProgress`. | `tests/test_jobs_and_eligibility.py` |
| Knowledge-base eligibility repair | `app/knowledge/eligibility.py`: summary, review queue, verdicts. A verified mapping counts as high confidence and the importer honours verdicts. UI: `KnowledgeEligibilityPanel`. | `tests/test_jobs_and_eligibility.py` |
| Compliance recheck and readiness | Reviewed rows are included in recheck. A changed specification file triggers recheck. Approval is withdrawn with an audit entry. `readiness_of` counts unanswered, candidate, recheck, AI-pending, reviewed and autofilled rows; the score is shown on the statement. | `tests/test_compliance_knowledge.py` |
| Foreign keys and deletion | SQLite FK enforcement on every connection. Migrations switch it off outside their transaction for batch rebuilds. Projects are deleted child-first. | `tests/test_data_controls.py` |
| AI project policy | `projects.ai_policy` (allowed / blocked), checked in extraction assist, compliance autofill/review/ask and details check. | `tests/test_data_controls.py` |
| Contacts, uploads, exports, logs | Contact details are masked for viewers. Uploads are checked by content, not extension. Every export is written to the activity log. Request IDs and JSON logs. | `tests/test_data_controls.py` |
| Backups | `app/routers/backups.py`: list, take, verify by restoring into a scratch database; UI on `/admin/system`. | `tests/test_data_controls.py` |
| Accessibility | focus-visible outlines, reduced-motion, mobile section selector, keyboard movement between BOQ rows. | manual |

## Release 4 — AI evaluation, feedback loop, bounded tasks, controlled deployment

### Feedback loop: proposals measured against engineers' decisions

When an issue is accepted, rejected or superseded, every proposal on it records
`outcome` (accepted / corrected / rejected / abstained / superseded), the value
decided and when (`pipeline.record_outcomes`).

`app/ai/metrics.py` groups proposals by task, prompt version and model:

| Metric | Meaning |
|---|---|
| precision | values taken exactly as proposed ÷ proposals decided |
| validated precision | the same for proposals marked `validated`; each miss is listed as a **false validation** |
| recall | issues settled with the proposed value ÷ issues settled with a value |
| abstention rate | proposals with no value (model or validator) ÷ proposals |
| correction rate | corrected ÷ (accepted + corrected) |

Usage (calls, cache hits, tokens, cost, latency, errors) comes from `ai_usage`.
Shown at `GET /admin/ai/metrics` and on **AI & backups**. There is no
"confidence" label anywhere: only these measured rates.

### Evaluation set and harness (`app/ai/evaluation.py`, `scripts/ai_eval.py`)

- `ai_eval.py build` (or the button on the admin page) adds every reviewed cell
  reading with an engineer's value to `backend/evaluations/cases/read_cell.jsonl`.
  It calls no model. The folder holds values from real documents and is not in git.
- `ai_eval.py run [--limit N]` sends each case through the live evidence builder,
  validation and independent OCR check, bypassing the cache and project budget.
  It saves a report with prompt, schema and parser versions, model, precision,
  recall, abstention rate, false validations, tokens and cost.
- `ai_eval.py report` prints the latest report per task and the gates.

### Gated tasks

A task that is routed as eligible but not enabled outright (today
`table_layout`) switches on only when a report for the **current prompt version**
passes its gate:
- at least 50 scored cases
- precision at least 98%
- no false validations

`table_layout` has no evidence builder yet, so no report can pass and it stays off.
That is the plan's "bounded catalog/layout tasks gated until the evaluation set
exists". Enabled crop tasks stay bounded as before: `read_cell` sends one cell crop
and its row, `read_field` one DRF field, `classify_system` the sheet's first-page
words and the DRF's marked systems.

### Controlled deployment

`AI_DISABLED_TASKS` (comma separated) switches a task off on the server. The
router stops offering it and any call returns "switched off" without reaching
the provider. It is listed on the admin page.

### Prompt-injection defences (`app/ai/guard.py`)

1. **Fences cannot be closed from inside.** Every text part is sent as
   `<label>…</label>` through `guard.fence`, which makes tag-shaped sequences in
   the document inert. All three providers use it. `PROMPT_VERSION` was bumped, so
   earlier cached answers are not reused.
2. **Instruction-like wording is flagged.** Examples: "ignore previous
   instructions", "you are now", "respond with status validated", "reveal the
   system prompt", role markup. The call still runs, but a proposal made from
   flagged text is never `validated`. It goes to the engineer with a warning, and
   the flags are stored on the proposal and on the compliance audit entry.
3. **Answers are bounded by the task.** Validation already refused a target,
   region or value type the task does not allow. Values containing links, markup
   or instructions are now also rejected. Compliance remarks and notes containing
   them are dropped (the response itself is one of the fixed words). A free-text
   clause answer containing them is not shown.

No model has tools that write, upload or delete. The Claude Code provider gets
only the Read tool, and only when an image is attached.

### Budget display

`GET /projects/{id}/ai/budget` returns calls in the last 24 hours against the
per-project limit, cost, and the per-job limits. The rows-to-review panel shows
the calls left and disables "Ask AI" when none remain.

Tests: `tests/test_ai_evaluation.py` (20) covers fencing, flagging (and not
flagging ordinary specification wording), no validation from flagged text,
link/markup rejection, compliance remark dropping, outcome recording, metrics,
endpoints, harness scoring, case merging, gates and the server switch.

## AI verification — the AI checks and settles the BOQ and Project Info

Added on request: the engineer should not have to review rows one by one. The
Design Sheets are the source of the BOQ; the DRF is the source of Project Info.
`app/ai/verification.py` checks both and applies what it confirms.

**How a value is settled.** Each value is read by independent sources:

- the value held
- a fresh OCR read with the current parser
- the AI reading the scan itself, blind (it sees the image, not the values)

| Situation | Result |
|---|---|
| AI agrees with the held value | confirmed |
| AI agrees with the OCR | corrected to that value |
| Otherwise | a second reading by the larger model (`AI_MODEL_STANDARD`) |
| Still unsettled | a close-up reading of that single row |
| No two readings agree | unresolved: the held value stays and the item is listed |

- **Removing needs full agreement.** A BOQ line the sheet no longer yields is
  looked for on the pages, and removed only when neither AI reading finds it.
  A Project Info value is cleared, or a system removed, only when every reading
  agrees.
- **Unreadable is not the same as blank.** An empty or unreadable reading counts
  as no reading at all. Whatever the DRF image does not show (a half-page scan)
  is "not checked" and left alone.
- **Headings are not added.** A row the OCR dropped whose AI readings agree it
  has no quantity is recognised as a heading.
- **Lines typed in by hand are left alone.**

**Applying and undoing.**
- The BOQ changes through the re-read machinery, after a snapshot.
- Project Info changes through the same path as an edit, including carrying a
  brand change into BOQ lines.
- Each run is stored in `ai_verifications` with what every source said.
  **Undo these changes** reverts a run in one step, as long as nothing was saved
  since.
- Each BOQ line shows the verdict as a badge (AI ✓ / AI fixed / AI added /
  AI unsure). The verdict clears when someone edits a value the sheet carries.

**When it runs.**
- Automatically, the first time a project's BOQ or Project Info page is opened
  by someone who can edit it (`AI_VERIFY_AUTO`).
- On **Check again**.
- Limits: `AI_VERIFY_MAX_CALLS` per run, `AI_VERIFY_MAX_CALLS_PER_DAY` per
  project, `AI_VERIFY_MAX_ELAPSED_S`.
- Projects set to "AI use: Not allowed" are never checked.
- Readiness shows whether each check is current.

**Measured on real data** (a copy of the database, Claude subscription through
Claude Code):

| Project | Result | Cost |
|---|---|---|
| EP-30208 BOQ | 50 stored lines became 97: 24 confirmed, 26 corrected (quantities and OCR-damaged part numbers such as `£232 301H` → `E-232 301H`), 47 added, 1 heading recognised, 0 unresolved. 97 is the count previously verified by hand against the page images. | 8 AI calls, ~2 minutes |
| EP-30784 Project Info | 14 confirmed; missing Other Information filled from the DRF (OCR and AI agreed) | 1 call, 39 s |
| EP-30208 Project Info (half-page JPG DRF) | 10 confirmed; Systems table and Other Information not on the image, left unchanged and marked not checked | 2 calls |

Tests: `tests/test_ai_verification.py` (10).

## Acceptance gates: where each stands

| Gate (plan §6) | Status |
|---|---|
| Document identity: 100% or blocked | Intake blocks unreadable, mismatched or out-of-folder documents; issuing is refused while blocked. |
| Page completeness: 100% or blocked | Printed page numbering and coverage checks; an unprocessed page blocks readiness. |
| Catalog number: exact or engineer-reviewed canonical match | Source spelling kept; canonical match shown with its reason. A mismatch against the part library is a review item. |
| Equipment quantity: exact integer, no silent coercion | Typed parser; ambiguous or rejected reads become review rows. |
| System/building assignment explicit and reviewable | Canonical building on each line with filter; ambiguous system is an issue. |
| Battery current: source and rule recorded, else lower bound | Hashes, `incomplete_reasons`, no-load confirmations. |
| VE watts reproducible | Input/result hashes; `complete` with reasons; uncalculated workbook reported. |
| Compliance: source-backed, reviewed, invalidated on change | Recheck on BOQ, scope, knowledge or specification-file change; approval withdrawn; readiness score. |
| AI: measured precision and abstention, no unbounded confidence | Outcome-based metrics and evaluation reports; gates on new tasks. |

**Still open:**
- A labelled evaluation set only grows as engineers review cells; no report has been run against a live provider yet.
- `table_layout` and catalog assistance have no evidence builders and stay gated off.
- EP-30175's superseded attachments need an engineer's decision through the repair workflow.
