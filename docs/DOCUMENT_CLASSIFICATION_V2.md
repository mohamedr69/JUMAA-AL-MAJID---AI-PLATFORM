# Document Classification & Routing V2 -- backward-compatible foundation

Working notes for the task of 27 September 2026 (Tasks.txt, Task 1). Part A
is the as-is audit, written before any source was changed. Part B is the
design and what each phase changed. Nothing here migrates a consumer.

## A. As-is ownership and dependency map (branch state 2026-09-27)

### A1. The flow

```
File Sync (worker "sync", app.services.document_sync.sync)
  listing(): a stat per PDF / Word-in-Transmittal-folder      -- nothing opened
  first sync: document_intake.run() indexes the DRF + Design Sheets (role drf / design_sheet)
  new file  -> ProjectDocument(role = "transmittal" if Word in a Transmittal folder else "document",
                               state = "pending")               -- the sync never classifies content
  changed   -> state = "pending" (previous reading kept)
  removed   -> state = "removed", mark_stale()
  intake rows -> _watch_intake(): sha256 + mark_stale only
  -> document_processing.enqueue()  (one process_documents job per project)
  -> shop_drawings.reconcile() when nothing is pending

Document Processing (worker "documents", app.services.document_processing.run)
  pending_rows() in priority() order (path words + role; never a reader)
  read_task() in a reader process: stat, 64 KB readiness read, sha256
     unchanged sha       -> row fresh, nothing opened
     sha already read    -> {"duplicate_of"}: _copy_reading() (role + extracted + reading_id copied
                            from the other row -- CONTENT identity, path-blind)
     else document_sync.extract(): PDF opened once
        classify_text(first page text, path, relative)      -- THE role decision (content + path)
        document_control.read_open_pdf() -> ControlledDocument records (+ OCR)
  worker: document_sync.process() writes extracted = {"records", "notes"} and reference/revision/status
          from records[0]; a submittal_form gets submittal_reader.read_form() (AI, stored by content in
          document_readings) and apply_form_reading() -> extracted["form"], record_for_the_log()
  _after_reading(): mark_stale(), depend() (submittal / compliance / log), settle_from()
  AI stage for forms not yet read; then submittal_reader.check() (the map + register) when a form
  changed; project_state.reconcile_actions(); shop_drawings.reconcile()
```

### A2. Who owns what

| Fact | Owner (creates / assigns) | Where |
|---|---|---|
| ProjectDocument rows, intake roles `drf` / `design_sheet` | `document_intake.run` (project.drf_document_path, project.design_sheets) | document_intake.py:344 |
| ProjectDocument rows, index roles | `document_sync.sync` (new file: `document` or `transmittal`) | document_sync.py:839 |
| `role` from content (`submittal_form` / `spec` / `document` / `transmittal`) | `document_sync.classify_text` called from `extract` in a reader process; written by `document_processing.run` (`row.role = role`); copied from the duplicate's row for same-content files | document_sync.py:159, document_processing.py:301 |
| `state` (pending / processing / fresh / failed / stale / removed) | sync (pending, removed, stale for intake rows); processing (processing, fresh, failed, pending on stop); `retry_failed` | document_sync.py:830-870, document_processing.py:290-380 |
| `extracted["records"]` | `document_control.read_open_pdf` -> `parse_page` (+ `_merge`); transmittals.read_transmittal for Word; `record_for_the_log` adds the form's own record when the page gave none | document_control.py:624, document_sync.py:273 |
| record `category` | `parse_page`: `submittals` (MAS/MAR), `samples` (SAR), `drawings` (SDW/DWG/SD, DRAW_REF title blocks, drawing schedules with source="drawing schedule"), `reply` (REPLY_SHEET, source="reply"); transmittals: see transmittals.py | document_control.py:633-715 |
| record `source` | "document" (default), "drawing schedule", "reply", "submittal form", "transmittal" | same |
| `reference`, `revision`, `status` on the row | `process()`: records[0]; overridden by `apply_form_reading` for a form (the model's reference/revision, status via `submittal_replies.answers_another_revision` + `_code`) | document_sync.py:326-420 |
| revision of a record | `parse_page`: REV on the page, else `-Rn` suffix, else the folder (drawings only), else R0 (owner's rule of 2026-09-24) | document_control.py:676-688 |
| system of a record | `parse_page`: title/layout keywords, then reference infixes (-FA-/-LI-/-FRC-), then page keywords; forms: `submittal_reader._system_code` (model, then folder, then wording) | document_control.py:696-706 |
| floor | `floor_name(title)` then the file stem | document_control.py:713 |
| consultant status of a submittal | `submittal_reader.check` -> `build_map` (readings + `submittal_replies.on_file/for_revision/vetted`) -> `sync_register` writes ProjectSubmittal / revisions; the logs re-read filed replies in `log_records` | submittal_reader.py:590-666, document_sync.py:1196 |
| drawing status / revisions | `shop_drawings.reconcile` from `log_records` (records with category drawings, `is_shop_drawing`, `drawings_in_scope`), `_reconcile_system`, candidates, engineer confirmations | shop_drawings.py:144 |
| what is a shop drawing | `document_control.is_shop_drawing`: category drawings AND no folder named enquiry / tender / ifc / issued for construction; plus `system_rules.drawings_in_scope(project)` | document_control.py:362 |
| stale dependencies | `depend` / `mark_stale` / `settle` / `settle_from` on DocumentDependency (boq, details, compliance, submittal, log) | document_sync.py:179-265 |
| IFC drawings | uploads through `/projects/{id}/ifc-drawings` into their own tables (IfcDrawing ...); never ProjectDocument rows; `GIVEN_TO_US` keeps folder IFC sheets out of the shop-drawing log | routers/ifc_boq.py, document_control.py:359 |

### A3. Consumers and what they read

| Consumer | Reads | Notes |
|---|---|---|
| File Sync page (`/documents/sync-files`, `sync-summary`, `status`) | `ProjectDocument.state`, `extracted["notes"]`, `role`, `relative_path` | `SyncFileOut` = name, path, status, reason, role |
| Logs (`log_records`) | every row's `extracted["records"]` (category, status, reference, revision, path), `submittal_replies.on_file` over rows in received/approved folders, `is_shop_drawing`, `drawings_in_scope` | domain-derived, not role-derived |
| Drawings tab (`shop_drawings`) | `log_records` + ProjectShopDrawing tables + IFC floors | category drawings is evidence; records with source "drawing schedule" excluded from `shop_all` |
| Material Submittal register | `submittal_reader.check` over rows with role `submittal_form` + `on_file` replies; ProjectSubmittal tables | the AI reading is by content hash |
| Home / project state | `project_state.summary` over submittals, records, shop drawings | domain records |
| Compliance | rows with role `spec` (`_after_reading` marks compliance stale), `spec_finder` | |
| Documents intake panel (`/documents/intake`, readiness) | rows with role drf / design_sheet only (`DocumentIntakeOut`) | the "general rows in intake UI" issue is out of scope (section 36) |
| BOQ | ProjectDesignSheet + `sheet_reader` (design_sheet rows are only watched by the sync) | untouched |
| Priority | `document_processing.priority(relative, role, size)` | path words, no reader |
| Old-rules requeue | `ProjectDocument.index_version != INDEX_VERSION` | must not be bumped by this task |

### A4. Intentional look-alikes (not redundant)

- `document_sync.classify` (opens the file; kept for callers with no page text) vs `classify_text` (the one decision, from the page `extract` already has).
- `submittal_scanner.read_form` (deterministic OCR form read, used by the scanner/tests) vs `submittal_reader.read_form` (the model's reading, stored by content).
- `document_control.is_shop_drawing` vs `shop_drawings.is_shop_drawing_record` (a re-export).
- `submittal_replies.for_revision` (reply filed beside a revision) vs `answers_another_revision` / `carried_over` (comments printed on a resubmission are the earlier revision's).
- `record_for_the_log` (the model's form as a record when the page gave none) vs `parse_page` (the page's own record wins in `combine`).

### A5. Tests that protect each behaviour

`test_file_sync_v2.py` (completion order, limits, stall, progress, telemetry),
`test_file_sync_v2_processing.py` (the two halves, no heavy work in a sync,
duplicates, stop/resume, retry), `test_document_processing_v2.py` (the read
task, timing, OCR tiers), `test_document_sync.py` (roles, records, replies,
dependencies, logs), `test_document_intake.py` (intake rows, findings),
`test_document_control.py` (parse_page, decisions, drawings, replies),
`test_submittal_ai.py` / `test_submittal_one_per_system.py` (map, register),
`test_drawings_module.py` (reconcile, candidates, IFC vs shop), and the
BOQ suites (`test_ai_sheet_reader.py`, `test_boq_*_v2.py`).

### A6. Compatibility risks found

1. Role is decided in a reader *process* from page-1 text; a classification
   assessment must run in the worker after the row is written, from the
   stored `extracted`, never in the reader.
2. `_copy_reading` copies role and records by content hash regardless of
   the destination folder (section 18): the new assessment must be made
   from the destination row's own relative path.
3. `log_records` and `shop_drawings.reconcile` read every row's records:
   adding rows or columns is safe; changing `extracted` is not.
4. `INDEX_VERSION` requeues every project when bumped: the classification
   has its own `RULES_VERSION`.
5. `SyncFileOut` is consumed by the File Sync page: new fields must be
   optional with `None` defaults.
6. `DocumentDependency.stale` is set only by `mark_stale` on content change
   or removal: classification writes never touch it.

## B. What was built (2026-09-27)

### B1. Architecture

`app/services/document_classification.py` -- pure rules plus a thin
persistence layer; nothing in it changes a role, a state, a record, a
status, a revision, a dependency or a register.

- `hint(relative_path, filename, role, intake_role, intake_system)`: the
  fast metadata hint (stage `hint`, strength `weak`; `supported` / `strong`
  only for the intake association of the DRF and the Design Sheets).
  Path words as the readers already use them; whole folder names for IFC /
  tender as `document_control.is_shop_drawing` does; the system from the
  file name's reference infix or the folder, never inside the type.
- `assess(row)`: from the row's stored `extracted` (records, the form
  reading, the notes) and its role, laid against the hint: `supported`,
  `ambiguous` (two kinds of content, or a content-supported path
  suggestion the content contradicts and no compatible pairing explains),
  `unknown` (read, nothing on it), or the hint when the row is unprocessed
  or online-only. Components are kept (a reply sheet, a decision, a
  schedule inside a submittal package).
- `context_fingerprint(row, project)`: normalised relative path + role +
  intake association + project; `is_current(entry, row, fingerprint)`:
  same content hash, same context, same `RULES_VERSION`.
- `record(db, project, row, assessment, source)`: one history row per
  assessment; the earlier current row is superseded unless
  `engineer_confirmed`, in which case the automatic one is stored already
  superseded. Flushes only; the caller's transaction decides.
- `hint_rows` (the sync), `assess_row` (the processing), `backfill` (the
  job), `metrics` (the evaluation): each guarded by `enabled()` and
  wrapped so a failure is logged and nothing else changes.

Hooks (all under `DOCUMENT_CLASSIFICATION_V2`, default off):

- `document_sync.sync`: the rows this sync recorded (new, changed, and the
  watched intake rows) get a hint after the loop, before the same commit.
  Nothing opened, hashed or asked; `counts["classification_hints"]`.
- `document_processing.run`: `_classify(db, project, row, counts)` after a
  row's own commit (and after a deferred form's AI reading), in its own
  transaction; a failure rolls back only itself. `counts["classified"]`,
  `counts["classification_ms"]`.
- `routers/documents.py`: `SyncFileOut.classification` (null unless the
  feature is on and an assessment is stored), `GET
  /projects/{id}/documents/classification`, `GET .../classification/metrics`,
  `POST /projects/{id}/jobs/classify-documents` (409 when off).

### B2. Taxonomy actually supported

| Type | Content evidence today | Hint-only |
|---|---|---|
| DRF, DESIGN_SHEET | the intake association (strong) | name |
| MATERIAL_SUBMITTAL | role `submittal_form`, record category `submittals`, the model's form reading | folder / name words |
| SAMPLE_APPROVAL | record category `samples` (SAR) | `sar`, `sample` |
| SHOP_DRAWING | record category `drawings` with source `document`, outside enquiry / tender / IFC folders | drawing words |
| IFC_DRAWING | a drawing record under an IFC / issued-for-construction folder | the folder |
| DRAWING_SCHEDULE (added) | record source `drawing schedule` | schedule words |
| TRANSMITTAL | role `transmittal` (Word in a Transmittal folder) + its sample records | folder |
| COMMENT_RESPONSE | record category `reply` (REPLY_SHEET) | reply / comment words |
| CONSULTANT_DECISION | a decision read off a record (a stamp, a marked box) or the form reading's consultant reply -- always a component alongside the submission it sits with | received / approved folders |
| SPECIFICATION | role `spec` and no controlled record | spec words, CSI numbers |
| DATASHEET, CERTIFICATE | none: never above a hint | catalogue / datasheet / certificate words |
| OTHER | a drawing given to us under enquiry / tender | -- |
| UNKNOWN | read, nothing on it, nothing in the path | -- |

Discipline is a field, always null in this phase: no code today reads it.

### B3. Compatibility matrix (what the classification does and does not touch)

| Existing | Classification | Untouched |
|---|---|---|
| role drf / design_sheet | DRF / DESIGN_SHEET, strong, from the intake association; the rows are hinted, never read | intake, BOQ, Project Info |
| role submittal_form | MATERIAL_SUBMITTAL candidate; strong only with the form reading and a submittals record | `submittal_reader`, map, register |
| role spec | SPECIFICATION unless the content says otherwise (then ambiguous) | compliance discovery, `spec_locations` |
| role transmittal | TRANSMITTAL | Word / sample behaviour |
| category submittals / samples / reply / drawings | evidence only; a reply never implies a decision; a drawing record never becomes SHOP_DRAWING under an IFC / tender folder, nor a schedule | identity, revision, reply, `is_shop_drawing`, reconcile |
| IFC domain | IFC uploads are not ProjectDocument rows; folder IFC sheets classify IFC_DRAWING | IFC tables |
| conflicting evidence | `ambiguous` / `conflicting` | routing |
| duplicate content | the index copies the reading; the assessment is made from the destination row's own path | `_copy_reading` |

### B4. Persistence

Table `document_classifications` (migration `f4a5b6c7d8e9`), model
`DocumentClassification`: project_id, document_id, content_sha256,
context_fingerprint, rules_version, stage, primary_type, component_types,
evidence_strength, evidence, evidence_sources, reason, system_code,
discipline, source (hint / assessment / backfill / engineer),
engineer_confirmed, confirmed_by_id, assessment (the full dict),
created_at, superseded_at. Current = `superseded_at IS NULL`; history is
kept. Existing rows need no backfill; nothing else reads the table.
`INDEX_VERSION` is untouched; `RULES_VERSION = "classify-2026-09-27.1"`.

### B5. Files changed

- new: `app/services/document_classification.py`,
  `alembic/versions/f4a5b6c7d8e9_document_classifications.py`,
  `tests/test_document_classification_v2.py`
- `app/models.py` (+`DocumentClassification`), `app/core/config.py`
  (+`document_classification_v2`), `app/services/document_sync.py`
  (`sync`: hinted rows; `sync_files`: the optional field),
  `app/services/document_processing.py` (`_classify` + two call sites),
  `app/routers/documents.py` (schemas, two GETs, one POST).

### B6. Tests and commands

```
venv\Scripts\python -m pytest -q tests/test_document_classification_v2.py
venv\Scripts\python -m pytest -q tests/test_file_sync_v2.py tests/test_file_sync_v2_processing.py tests/test_document_processing_v2.py tests/test_document_sync.py tests/test_document_intake.py tests/test_document_control.py tests/test_submittal_ai.py tests/test_submittal_one_per_system.py tests/test_drawings_module.py tests/test_boq_extraction_v2.py
```

Test map: TEST 1 / 2 / 8 / 15 / 16 -> `test_legacy_outputs_are_identical_with_the_feature_off_and_on`
(parametrised off / on, the same project, a fresh database each) and
`test_what_each_document_of_the_folder_is_assessed_as`; TEST 3 ->
`test_the_sync_hint_reads_no_file_hashes_nothing_and_asks_no_model`; TEST 4 / 6 /
7 -> the folder test; TEST 5 -> `test_a_decision_printed_on_a_resubmission_...`;
TEST 9 -> `test_duplicate_content_keeps_the_legacy_reading_and_gets_its_own_context`;
TEST 10 -> `test_a_form_filed_among_specifications_is_ambiguous_...`; TEST 11 ->
`test_a_classification_error_leaves_the_processing_successful`; TEST 12 / 18 ->
`test_backfill_uses_stored_data_only_and_marks_nothing_stale`; TEST 13 / 14 ->
`test_the_drf_and_the_design_sheets_are_classified_from_their_intake_association_only`;
TEST 17 -> `test_turning_the_feature_off_leaves_the_legacy_path_working_and_the_metadata_stored`;
plus three unit tests of the rules.

### B7. Enable / disable / backfill

- Enable: `DOCUMENT_CLASSIFICATION_V2=true` in `backend/.env` (or the
  environment), restart the API and the workers (`start-backend.bat`).
  From then on each sync hints its new and changed rows and each
  processing job assesses what it writes.
- Disable: set it to `false` (or remove it) and restart. Every path
  behaves as before; stored assessments stay and the listing shows none.
- Backfill: with the feature on, `POST /projects/{id}/jobs/classify-documents`
  (a job the Jobs panel follows and can stop; rows already current are
  skipped, so a stopped run resumes). Stored data only: no file, no OCR,
  no model, no dependency, no reconcile.

### B8. Performance (EP-30784, 359 indexed rows, this PC)

| | Before | After (feature on) |
|---|---|---|
| File Sync discovery | a stat per file, ~0.7 s | + `hint` 41 ms for 359 rows (115 us per row), pure functions; the writes ride the sync's own commit |
| Document processing | unchanged readers | + `assess` 47 ms for 359 rows (132 us per row) + one small transaction per row (23 ms for 8 assessments in the tests) |
| AI calls / OCR / PDF opens for classification | -- | 0 / 0 / 0 |

Measured with the module's functions over the project's real stored rows
(nothing written). The feature is off on this PC's database.

### B9. Consumer migration readiness (report only; nothing migrated)

| Consumer | Source of truth today | What classification could offer | Readiness |
|---|---|---|---|
| Documents / intake panel | project + intake rows (role drf / design_sheet) | filter the index by type; keep general rows out of the intake list | good after evaluation; the known intake-list issue stays for its own task |
| File Sync page | `ProjectDocument.state` + notes | the optional `classification` field is already there for display | ready as display only |
| Drawings tab | `shop_drawings` records + `log_records` + IFC floors | candidate discovery (rows of type SHOP_DRAWING with no record), debugging | do not replace domain logic |
| Material Submittal register | map + replies + revisions | candidate routing (rows of type MATERIAL_SUBMITTAL the role missed) | do not replace revision / status logic |
| Compliance | role spec + `spec_finder` | rows of type SPECIFICATION the role missed, and rows the role calls spec that are not (see defects) | evaluation first |
| Logs, Home | domain records | none | none required |
| Priority order | path words + role | a hint could order by type | evaluation first |

### B10. Real-project evaluation numbers (EP-30784, assessments computed from stored rows, not written)

documents 359; UNKNOWN 3; ambiguous 18; mixed-component files 246
(replies and decisions inside submission packages, schedules); type
agreement with the existing role 49 of 165 roles that map to a type,
conflicts 116 -- 110 of them shop drawings the legacy role calls `spec`
(see defects), 6 replies filed under MS folders; path-only (hint stage)
100; content-supported 238; classification latency 0.13 ms per row.
No accuracy figure: there is no engineer-verified truth set yet.

### B11. Known limitations

- DATASHEET and CERTIFICATE never rise above a hint: no reader produces
  content evidence for them.
- CONSULTANT_DECISION is always a component; which revision it applies
  to is the register's question and is not answered here.
- A content-supported type conflicting with the path is reported as
  ambiguous; the compatible pairings are a fixed list.
- Discipline is never populated.
- The engineer override is prepared in the model (`source = "engineer"`,
  `engineer_confirmed`, `confirmed_by_id`) but has no endpoint yet.

### B12. Separately identified defects (not fixed here)

1. `document_processing.run`, the duplicate-content branch: a
   `read_task` result of `{"duplicate_of": ...}` carries no `unchanged`
   key, and `if result["unchanged"]:` raises `KeyError`, failing the whole
   processing job the moment a copy of already-read content arrives in a
   later sync. Only `read_task` is unit-tested for duplicates.
2. `spec_finder.looks_like_a_spec` takes the six-digit drawing numbers in
   EP-30784's shop drawing file names (`...-010025.pdf`) for CSI section
   numbers: 110 shop drawings carry role `spec` -- read last by
   `priority`, and each marks the compliance page's spec locations for a
   new search (`_after_reading`).
3. `test_document_sync.py::test_a_read_that_fails_keeps_the_previous_result_and_is_marked`
   fails on the current branch with or without this feature: since
   Document Processing V2, `document_sync.extract` turns a reader
   exception into a "could not read" note and the row ends `fresh` with
   its previous reading replaced by an empty one, where the test (and the
   docstring's promise) expects `failed` with the previous reading kept.
4. The legacy register keeps one row per form content across projects:
   a second project holding the same form bytes gets no register row.

## C. Pilot hardening and the EP-30880 pilot (2026-09-27, rules `classify-2026-09-27.2`)

### C1. Four safety defects, fixed

1. **A failed classification write damaged the File Sync transaction.**
   `hint_rows` ran inside the sync's transaction, before its commit; a
   flush refused by the database left the session in a failed state, the
   exception was caught, and the sync's own `commit()` then raised -- the
   index writes, the pending states and the processing job were lost.
   Now the sync commits its own writes first and the hints run after, in a
   transaction of their own; every `record()` write is a savepoint
   (`begin_nested`), so a refused row leaves the session usable and the
   next row is tried; the failure is logged once with identifiers
   captured before the flush. The same savepoint protects the processing
   hook and the backfill. Test: `rules_version = None` (NOT NULL) makes
   every INSERT fail in the database; the sync, its states, its job and
   the session survive (`test_document_classification_pilot.py`, the
   first two tests).
2. **Metadata read as content.** `assess` put the role alone (spec,
   transmittal) into the content list and reported SPECIFICATION /
   TRANSMITTAL as *supported*, TRANSMITTAL even *strong*, with nothing
   read off the pages. Every answer now carries a `basis`:
   `intake_association` (the project's own DRF / Design Sheet, named as
   such -- the source label is `intake_association`, never content),
   `metadata` (path words, name, legacy role), `content` (stored records
   and readings) or `none`. Only content supports a type; a role-only
   answer stays a hint. Each component of a mixed file carries its own
   basis (`component_support`).
3. **A changed file showed its old assessment as current.** The sync
   marks a changed file `pending` and leaves the old hash and the old
   reading on the row until it is read again; `is_current` compared the
   hash only, so the old content-based answer read as current, and a
   backfill re-labelled the old reading as evidence for the new file.
   Freshness now also binds the row's processing state at assessment
   (`assessment.source_state`): `current` only when rules, context,
   content hash *and* state match; otherwise `source_changed`,
   `context_changed` or `rules_changed`, shown as such. `assess` uses the
   stored reading only for a `fresh` row; a pending, processing or failed
   row gets the hint with the reason written in. History is kept.
4. **A file merely named "Design Sheet" bypassed its content.** `assess`
   returned early for any DRF / DESIGN_SHEET hint; now only for the
   intake association. A name-only hint is weighed against the content
   and a disagreement is `ambiguous` / `conflicting` with the content
   reported and the name kept as a metadata component.

Also: at most one current assessment per document is now refused by the
database (partial unique index `uq_document_classifications_one_current`,
migration `a5b6c7d8e9f0`; `record` closes the old row before it opens the
new one); the backfill names every failure in its result
(`eligible = assessed + skipped + failed`); the listing and the metrics
carry `basis`, `freshness`, `component_support`, `needs_review` and
`review_reasons`; the metrics count only current rows of non-removed
documents and report `duplicate_current`.

### C2. Read-only inspector

Project > File Sync > "Classification (diagnostic)", collapsed at the
bottom of the page: file, legacy role and state, primary type, the other
components with their basis, stage, strength and basis, sources, system,
reason (evidence on click), freshness and rules version, when and by
what, engineer confirmation, needs-review flag with its reasons. Filters:
all / supported / hint / ambiguous / unknown / stale / needs review /
unassessed, document type, conflicting evidence only, search. It reads
`GET /projects/{id}/documents/classification` and `/metrics` and writes
nothing; it starts nothing.

SQL for the same view: `backend/scripts/classification_inspection.sql`.
A read-only business snapshot with a stable hash:
`backend/scripts/business_snapshot.py <project_id> [out.json]`.

### C3. Rollback

1. Remove `DOCUMENT_CLASSIFICATION_V2=true` from `backend/.env` (or set it
   to `false`).
2. `start-backend.bat` (it stops this project's API, workers and orphaned
   children first). Confirm with `GET /health`: `flags.document_classification_v2`
   is `false` in the process that answers.
3. Leave `document_classifications` as it is: the history is metadata,
   read by nothing else; the sync, the processing and every page work
   without it, and the classify-documents endpoint answers 409.
4. Do not delete assessments to disable the feature; do not revert
   business data (none was changed).

### C4. The EP-30880 pilot (2026-09-27, 18:32-18:35 local)

Pilot: EP-30880, internal `project_id` 2. Chosen because the task names
it and it passes the hard criteria (stable, no active job, migration
current, no infrastructure errors); it is small -- 6 indexed documents:
the DRF, two Design Sheets, three material submittal forms, all fresh,
3 with stored readings -- so it exercises the intake association and the
content-supported form paths only. It has no `document`, `spec`,
`transmittal` or drawing rows, so the metadata-only, conflicting and
changed-file paths are proven by the tests, not by this pilot. EP-30784
(`project_id` 1, 359 rows, every role) is the recommended next pilot.

Runtime that ran it: the API child (PID 3724, started 18:33:32 local,
`backend\venv\Scripts\python.exe` 3.12.10, root and models under this
backend, revision 13eb73ce85e5, `/health` flags
`document_classification_v2: true`, rules `classify-2026-09-27.2`),
started by `start-backend.bat` after the `.env` change; the three workers
started with it (18:33:25). Database: `backend/ep_platform.db`
(`DATABASE_URL` in `.env`), migration head `a5b6c7d8e9f0`. Backup, taken
and verified by the API's own startup migration step:
`backend/backups/ep_platform-before-a5b6c7d8e9f0-20260927-143217.db`
(integrity check ok).

Baseline: 6 documents, 6 eligible, roles drf 1 / design_sheet 2 /
submittal_form 3, all `fresh`; 0 classification rows; business snapshot
hash `a3bc0cb5…ba4fdc` (`scripts/business_snapshot.py 2`, 12 tables).

| Run | Job | Result | Duration |
|---|---|---|---|
| first backfill (`POST /projects/2/jobs/classify-documents`, admin login) | 20 | eligible 6 = assessed 6 + skipped 0 + failed 0 | 1.0 s end to end (job itself 25 ms) |
| second, identical (idempotency) | 21 | eligible 6 = assessed 0 + skipped 6 + failed 0 | 1.0 s |

After both runs: 6 rows in `document_classifications`, 6 current, 0
superseded, 0 duplicate current (the unique index is in place), every
row on a project-2 document with type, stage, basis, sources, reason and
rules version populated. Metrics: supported 6, hint 0, ambiguous 0,
unknown 0, not current 0, needs review 0, metadata-only 0,
content-supported 3, intake association 3, rules .2. The listing, the
sync-files listing and the SQL query return the same six.

| File | Role | Type | Stage | Basis / sources | System | Freshness | Review |
|---|---|---|---|---|---|---|---|
| EP-30880 DRF.pdf | drf | DRF | supported, strong | intake_association | – | current | no |
| EP-30880 ELM Design.pdf | design_sheet | DESIGN_SHEET | supported, strong | intake_association | ELS | current | no |
| EP-30880 FAS Design.pdf | design_sheet | DESIGN_SHEET | supported, strong | intake_association | FAS | current | no |
| Material Submittal - ELS - R0.pdf | submittal_form | MATERIAL_SUBMITTAL | supported, strong | content: role, path, form_reading, records | ELS | current | no |
| Material Submittal - FA - R0.pdf | submittal_form | MATERIAL_SUBMITTAL | supported, strong | content: role, path, form_reading, records | FAS | current | no |
| Material Submittal - FRC - R0.pdf | submittal_form | MATERIAL_SUBMITTAL | supported, strong | content: role, path, form_reading, records | FRC | current | no |

No metadata-only hint, no ambiguous or conflicting case, no stale, no
failure or skip in this population: the six are the two cases the rules
are surest of. Business snapshot after both runs: the same hash
`a3bc0cb5…ba4fdc`; `ai_usage`, `document_readings`, `result_cache` and
every business table hold the same row counts as the pre-pilot backup:
no model was asked, no file was read, no sync or processing ran.

Known limitations: the pilot population is too small and too uniform to
say anything about accuracy (no Golden Truth exists; none is claimed);
the engineer override has no endpoint; discipline is never populated;
the inspector is a diagnostic list, not a review workflow. Defects
reported earlier (B12) stand unfixed.

## D. Extraction and classification repair after the source audit (2026-09-28)

Audit: `~/.codex/visualizations/2026/09/27/01a0e218-.../document-validation/`
(validation-report.md, findings F1-F6). This section records what was
repaired in code, what was proven on the isolated clone, and what was
*not* applied to the live database.

### D1. AS-IS: the flow and its consumers

File Sync (`document_sync.sync`) stats the folder and writes `pending`
rows. Document processing (`document_processing.run`, the documents
worker) hashes each row (`read_task`), reads the PDF in a reader process
(`document_sync.extract` -> `document_control.read_open_pdf` ->
`parse_page` per page, `boxed_decision`, OCR where the gate allows) and
writes `extracted = {records, notes, form?}` plus `reference / revision /
status` from the first record; the model reads material submittal forms
later (the AI stage). `_after_reading` marks dependencies, `_classify`
writes the classification. Then `shop_drawings.reconcile` and
`submittal_reader.check` rebuild the registers from `log_records`.

| Consumer (tab) | Service / API | Legacy selector | Status owner | Classification use (2026-09-28) |
|---|---|---|---|---|
| Documents (intake) | `document_intake`, `/documents/status`, `/documents/drf`, `/design-sheets` | role `drf` / `design_sheet` (intake association) | intake gate | none; shadow only (`intake`) |
| BOQ, Proposed Materials, Calculations | `boq_*`, `/extraction`, `/materials`, design routers | the DRF / Design Sheets via intake; BOQ readings by content sha | BOQ revisions, engineer corrections | none |
| Compliance Statement | `/compliance`, `spec_finder`, `project.spec_locations` | role `spec` | compliance statements | none; shadow only (`compliance`) |
| Material Submittals | `/submittals/map`, `submittal_reader.check`, `submittal_filing` | role `submittal_form` + `reference` | `project_submittals` (engineer / AI / replies) | none; shadow only (`submittal_map`) |
| Shop Drawings / Drawings | `/drawings/*`, `shop_drawings.reconcile` | records of category `drawings` that `is_shop_drawing` (folder rule) | `shop_drawing_revisions` (engineer / AI / replies) | none; shadow only (`logs.drawings`) |
| Logs | `/logs`, `document_sync.log_records` -> `document_control.combine` | every stored record (category, reference, revision) | the records + replies | none; shadow only (`logs.*`) |
| Replies (map, drawings) | `submittal_replies.on_file`, `_apply_reply` | any row under a received / approved folder | as above | none; shadow only (`replies`) |
| Home, Register, Readiness | `/state`, `/logs`, `readiness` | the above | -- | none |
| File Sync > Classification (diagnostic) | `/documents/classification[/metrics]` | -- | -- | read-only inspector |

Business invariants kept: `ProjectDocument.role` is never rewritten in
bulk; extracted records are corrected only through the reviewed repair
path (`scripts/repair_extraction.py`), with the model's form reading, the
row's manual fields and the classification history kept; classification
writes only `document_classifications`; IFC / tender drawings stay apart
from shop drawings (`is_shop_drawing`, the IFC / tender folder rules);
the intake association is authoritative for the DRF and Design Sheets;
one current assessment per document; every classification write is a
savepoint; `hint` is not content, `supported` is not approval,
`ambiguous` is for a person. No consumer is migrated
(`app/services/document_routing.py`, `MIGRATED` all false): the selector
records the disagreement and returns the legacy answer.

### D2. What was fixed in code

Extraction (`document_control.py`, `PARSER_VERSION = parse-2026-09-28.1`):

- F1: `is_date_shaped` rejects `6-Mar-2026` and the like (a number, a
  month, a year) as a reference; `reference_candidates` lists every
  reference on the page with its kind (`serial`, `sheets`, `other`) and
  `first_reference` gives the first that is not a date. A real reference
  with a numeric prefix (`123-MAR-001`) is kept.
- F2: the grammar keeps the sheets a reference lists past a slash
  (`...-SD-MEP/FA-100,101,102,104&105`, `.../FA-104 A~104 M`, `.../EM-104 B`,
  `.../FA-104-M`); the raw form is stored, and `ControlledDocument.listed`
  carries the sheets a submission cover lists.
- `submission_cover` reads the contractor's cover (SHOP DRAWING / MATERIAL
  / SAMPLE SUBMITTAL heading, the form's labels) as the submission: the
  numbered reference of the heading's kind (the labelled "No:" one when
  the form keeps one), `Rev:`, the listed sheets, the description line,
  the system from the description or the reference infix. An instruction
  mentioning "MAR Approval" in the comments changes nothing.
- A reply page behind a cover that names the cover's number, a listed
  sheet or sheets of the same series is folded into the submission
  (`_answers`), not registered as a reply of its own; `combine` also lets
  a reply settle a submission by a listed sheet. A reply naming another
  series or another contractor's number is not attached.
- F5: `annotated_decision` reads a frame drawn round an option (a stamp,
  square, ink or highlight annotation over exactly one option) as the
  decision; `boxed_decision` falls back to it; `BOX_VERSION = box-2`.
  Unmarked option lists still settle nothing; two frames say nothing.
- The OCR gate also reads a scanned first or second page whatever the
  file is called (bounded to two pages, cached by content).
- `extracted["parser_version"]` is stored; `document_processing._previous_sha`
  and the duplicate-content reuse ignore a reading made by an earlier
  parser, so an unchanged file cannot keep a known-obsolete reading once
  its row is processed again. INDEX_VERSION is unchanged: nothing is
  re-read project-wide by itself.

Page evidence (`content_evidence.py`, `EVIDENCE_VERSION = evidence-2026-09-28.2`):
the first three pages' text layers (plus the reader's cached OCR of a
scanned page; nothing rendered here) are scanned for generic kinds:
shop drawing / material / sample submittal, transmittal, receipt,
consultant comments sheet, contractor reply, review status, datasheet,
catalogue, certificate, load schedule, vendor list, scope matrix,
compliance statement, specification, single-line diagram, title block,
method statement, DRF, plus printed project codes and author cues.
Each finding keeps page, excerpt, method, rule, grade, version and content
hash, stored as `extracted["evidence"]` by processing (and by the repair).
Findings are graded: `title` (the kind's own words near the top of page
1), `strong` (the kind's own words anywhere) or `weak` (a phrase that
often goes with the kind: "product data" in a specification's submittals
clause, "connected load" on a single-line diagram, a title block's
"Drawing No"). Legend lines ("TDS - Technical Data Sheet") are ignored.
Only title and strong findings name a type; a weak one is an evidence
line. Among reference kinds a title beats the rest.

Classification (`document_classification.py`, `RULES_VERSION = classify-2026-09-28.4`):

- F3: the file name outranks the folder; under a submittal folder a
  datasheet, certificate or catalogue keeps its own type with
  MATERIAL_SUBMITTAL as a metadata component (package membership); the
  role `submittal_form` no longer reads "the reader found a form"; a
  fresh `form.is_submittal = false` is negative evidence (the role does
  not make it a form; with nothing else the answer is UNKNOWN with the
  package as a component).
- Page evidence is content, weighed after the readers' records; a record
  whose reference is a date is not evidence and is flagged; a reading
  by an earlier parser is flagged. Reference kinds (datasheet,
  certificate, load schedule, vendor list, scope matrix, compliance
  statement, specification, drawing) are components of a controlled
  document (a submission, a package, a transmittal, a reply, a decision)
  and never make it ambiguous; only among themselves can two of them.
- F4: new types LOAD_SCHEDULE, VENDOR_LIST, SCOPE_MATRIX,
  COMPLIANCE_STATEMENT and DRAWING (provenance not established); DATASHEET
  and CERTIFICATE can now be supported by the pages.
- F6 / F7: a printed project code that differs from the indexed project
  is a flag (nothing moved); a consultant comments sheet is a
  CONSULTANT_DECISION document and never a contractor reply, and every
  CONSULTANT_DECISION carries the review reason "nothing is applied"; a
  receipt is proof of delivery only; a name the pages contradict (a
  "compliance statement" that reads as specification sections) is
  ambiguous with a source flag; a role the content disagrees with is
  flagged (the role stands).

API / UI: `DocumentClassificationRowOut.extracted` (reference, revision,
status, categories, parser version and currency, form reading, notes,
error, pages read); `ClassificationOut.flags` and `evidence_pages`;
metrics `flagged`. The inspector shows the extracted column, source
flags, page evidence with links to the page (the existing
`/logs/file` mechanism and its permissions), processing errors, and
filters for source flags, processing errors and outdated readings.

### D3. The repair tool

`backend/scripts/repair_extraction.py` is a dry run by default. Selection
by `--project` + `--select date-references,truncated-references,parser-outdated,all`
or `--ids`; never project-wide by accident (a selection needs a project).
Per row: re-read with the current parser (no model; the reader's own
OCR, cached), compare with the stored records, and write a manifest
entry (old / new reference, revision, status, records, evidence kinds,
changed fields, reasons, skip reason or error). `--apply` re-checks the
file's size and time and the row's state and hash before each write,
keeps the model's form reading, the manual fields and the history, marks
and settles dependencies, commits per row; `--reassess` classifies the
repaired row again; `--reconcile` runs the shop drawing reconciliation
for the project afterwards; `--rollback manifest.json` restores the
recorded readings where the row still holds what the repair wrote.
Running the same repair again selects nothing (or reports `unchanged`).
Tests: `tests/test_repair_tool.py`. Point it at a clone with
`DATABASE_URL=sqlite:///<clone>` and a scratch `CACHE_ROOT`.

### D4. What the isolated clone showed (2026-09-27 evening, local)

Clone: `backup_sqlite` of the live database (the SQLite backup API, so
the WAL is included) into the scratchpad, `DATABASE_URL` pointed at it,
`CACHE_ROOT` at a scratch page cache. No worker ran against it; nothing
enqueued; no model asked (`ai_usage` 202 rows before and after, the same
as live). The live database, the live page cache and the live processes
(API and workers of 18:33) were not touched: live still holds
`parser_version` null on all 890 rows, rules `classify-2026-09-27.2` on
534 current rows, and the 17 date references.

Repair runs (`scripts/repair_extraction.py`, manifests in the scratchpad):

| Run | Selection | Selected | Result | Time |
|---|---|---|---|---|
| dry run, EP-30088 | date-references, truncated-references | 102 | 102 would_repair | 204 s |
| apply, EP-30088 | + parser-outdated, --reassess --reconcile | 522 | 522 repaired | 490 s |
| apply, EP-30784 | parser-outdated, --reassess --reconcile | 356 | 356 repaired | 754 s |
| apply, EP-30880 | parser-outdated, --reassess | 3 | 3 repaired | 1 s |
| apply, 11 FF covers by id | (the fire-fighting covers: no record, stale row reference cleared) | 11 | 11 repaired | 5 s |
| apply, 24 form rows by id | (the model's log record re-added after a tool fix, see below) | 24 | 14 repaired, 10 unchanged | 18 s |

Two tool defects found on the clone and fixed before anything else: the
first version re-read the six Word transmittals with the PDF parser and
emptied their records (rolled back from the manifests; the tool now
skips transmittal rows and files it cannot read), and it dropped the log
record the model's form reading stands in for when the page gives none
(now re-added as processing does; EP-30880's logs went 3 -> 0 -> 3).

After the repair and a classification backfill under rules `.4`:

- F1: 0 date-shaped references remain (17 before). 434/436/437/578/783/848
  read as their covers: `ICC-DLRC-SPM-SD-MEP-FA-0054` R1 rejected (the
  annotation-framed C), `…-0055` R1 rejected, `…-0128` R0 rejected; the
  11 fire-fighting covers (440–463) yield no record, as the platform's
  systems do not include fire fighting, and their stale row reference is
  cleared; all 17 classify SHOP_DRAWING / supported.
- F2: 0 rows hold the truncated `ICC-DLRC-SPM-SD-MEP` (84 before); EP-30088
  has 34 distinct drawing references (7 before); the CRS reply 868 keeps
  `…-SD-MEP/FA-100,101,102,104&105`.
- Confirmed cases: 646, 682 DATASHEET supported (MATERIAL_SUBMITTAL a
  metadata component; flag: role submittal_form vs datasheet); 653, 702
  CERTIFICATE supported; 706 CONSULTANT_DECISION supported (review reason:
  nothing applied); 711 LOAD_SCHEDULE, 719 SCOPE_MATRIX, 728 VENDOR_LIST
  supported with the role-spec flag; 717 DRAWING hint (its page names no
  kind strongly); 729 TRANSMITTAL supported with SAMPLE_APPROVAL, 730
  SAMPLE_APPROVAL supported (its OCR misses the "DOCUMENT TRANSMITTAL"
  header), both flagged "prints EP-30058, indexed under EP-30088"; 620
  SPECIFICATION ambiguous with the name flag; 621 MATERIAL_SUBMITTAL hint
  with the project-code flag; 640, 868 COMMENT_RESPONSE supported; 688,
  690 DATASHEET hint (scanned, nothing read); EP-30880 unchanged: one
  DRF, two DESIGN_SHEET, three MATERIAL_SUBMITTAL supported.

Coverage and stages (current rows, no duplicates; distinct documents):

| Project | Docs | Classified | Supported | Hint | Ambiguous | Unknown | Flagged |
|---|---|---|---|---|---|---|---|
| EP-30088 | 525 | 525 | 246 | 252 | 9 | 18 | 41 |
| EP-30784 | 359 | 359 (3 before) | 255 | 80 | 22 | 2 | 152 |
| EP-30880 | 6 | 6 | 6 | 0 | 0 | 0 | 0 |

EP-30784's 3/359 was a coverage gap only: the project was processed with
the flag off and never backfilled; nothing was missing from its
extraction. The remaining ambiguous cases are real disagreements (a
datasheet filed under a drawings folder, a compliance statement that
reads as a specification, a load schedule under IFC). The 152 flags on
EP-30784 are mostly "the legacy role is spec; the content reads as shop
drawing / datasheet" (the 110 shop drawings `looks_like_a_spec` misnames,
defect B12.2) and the printed project codes EP-30627 / EP-29941 on
reference copies of other projects' submittals.

No accuracy percentage is claimed: the labelled set is the audit's
confirmed cases (22 documents), reviewed for type only, and duplicates
by content are counted once above.

Registers before / after on the clone (`scripts/consumer_shadow.py`):

- EP-30880: identical hash (logs 3, submittals 3).
- EP-30088: submittals unchanged (3); logs 42 -> 63 and shop drawings
  8 -> 34: the collapsed `ICC-DLRC-SPM-SD-MEP` identity split into the
  34 submissions it stood for, each with its own revision and the
  decision read off its cover (rejected where the framed C was found);
  the 17 date "submittals" are gone from the submittal log. This is the
  intended repair of F2 and needs a person's confirmation of the register
  before the live registers follow it.
- EP-30784: submittals unchanged (5), logs and shop drawings the same
  count (147 / 52) but 14 R1 revisions moved from under review (one from
  approved as noted) to not approved. Cause: the current reader's
  region OCR reads a "(C) Revise & Resubmit" stamp on the R1 sheets
  themselves (the stamp is on page 2 of each resubmission file, as the
  cached OCR text shows); the live readings predate that OCR improvement
  of 2026-09-27 and never saw it. This is not caused by the parser
  repair; it is what re-reading under the current reader gives, and an
  engineer must confirm those R1 decisions (or the FAVE R1 sheets'
  stamps) before EP-30784 is re-read live.

Shadow comparison (legacy selector vs current classification, supported
only; `MIGRATED` all false, nothing routed from it):

| Consumer | EP-30088 legacy / shadow / both | EP-30784 legacy / shadow / both | Verdict |
|---|---|---|---|
| intake | 3 / 3 / 3 | 3 / 3 / 3 | agrees; could migrate, no benefit |
| logs.drawings | 33 / 173 / 33 | 58 / 179 / 58 | shadow ⊇ legacy: the covers and sheets without a system record; not migrated |
| logs.submittals | 24 / 31 / 24 | 8 / 20 / 8 | shadow ⊇ legacy; deferred |
| submittal_map | 14 / 31 / 10 | 14 / 20 / 11 | 4 / 3 legacy rows the content calls datasheet / catalogue; deferred |
| compliance | 18 / 9 / 9 | 144 / 14 / 14 | 130 EP-30784 "specs" are shop drawings by content (B12.2); deferred, a fix for `looks_like_a_spec` first |
| replies | 277 / 8 / 2 | 43 / 24 / 4 | the legacy rule is the received folder; the two say different things; deferred |

Migrated consumers: none. Deferred: all, for the reasons above.

### D5. Live application -- not done; how to do it

Nothing below was run against the live database or the live processes.

1. Stop the workers and the API through `stop-backend.bat` (the API would
   otherwise process with the old code); back up with the startup
   mechanism or `backup_sqlite` (no schema change is needed: no migration).
2. Restart through `start-backend.bat` so the workers load
   `PARSER_VERSION parse-2026-09-28.1`, `EVIDENCE_VERSION …28.2` and rules
   `classify-2026-09-28.4`; confirm with `/health` and the worker fingerprints.
3. Dry run, then apply, the narrow repair for EP-30088 (internal id 4):
   `venv\Scripts\python scripts\repair_extraction.py --project 4 --select date-references,truncated-references --manifest repair-p4.json`
   then the same with `--apply --reassess --reconcile`. Expected: 102
   rows repaired, 0 date references, 0 truncated references, the shop
   drawing register of EP-30088 rebuilt to 34 drawings; keep the manifest
   (it is the rollback: `--rollback repair-p4.json`).
4. Re-read the rest (`--select parser-outdated`) project by project only
   after an engineer has confirmed, from the clone's manifests, the R1
   decisions the current OCR reads on EP-30784's FAVE sheets. Until then
   those rows are re-read only when their files change or their rows are
   processed again (the parser version forces it).
5. Backfill the classification per project (`POST /projects/{id}/jobs/classify-documents`),
   EP-30784 first (its 356 unassessed rows).
6. Rollback: `--rollback <manifest>` restores the readings; disabling
   `DOCUMENT_CLASSIFICATION_V2` leaves the history stored; no consumer
   reads the classification, so nothing else changes.

Source questions for an engineer (from the flags): the 24 EP-30088 files
that print EP-30058 and the 5 that print EP-29484; EP-30784's 6 files
printing EP-30627 and 3 printing EP-29941; `COMPLIANCE STATEMENT.pdf`
(620) whose pages are specification sections; the FAVE R1 stamps; the
110 shop drawings named as specifications by `looks_like_a_spec`.

### D6. Tests run (2026-09-27 evening)

New: `tests/test_extraction_repair.py` (dates, slash sheets, the
submission cover, the MAR-approval comment, replies folded by listed
sheet, `combine` by listed sheet, the framed option, page evidence and
its bound), `tests/test_classification_evidence.py` (the confirmed cases'
shapes: F3 attachments, negative form reading, F4 kinds, the scanned
transmittal with a receipt, consultant comments vs reply, the framed
decision component, a date record, a contradicted name, two kinds,
graded evidence, records first), `tests/test_repair_tool.py` (preview,
apply once, unchanged again, rollback, changed file and pending row
skipped, transmittal and unreadable file skipped),
`tests/test_document_routing.py` (nothing migrated; missing / stale /
ambiguous never removes a document).

One session, all affected suites: document control, extraction repair,
classification v2 / pilot / evidence, repair tool, routing, document sync,
processing v2, File Sync v2, transmittals, submittal scanner / package /
submittal / AI / one-per-system, worker runtime, migrations, BOQ
provenance, IFC platform: **286 passed, 1 skipped, 1 failed**. The
failure is `test_document_sync.py::test_a_read_that_fails_keeps_the_previous_result_and_is_marked`,
failing on the branch before this work (B12.3). Frontend: `tsc -b` and
`vite build` pass; the inspector was not opened in a browser in this
session.
