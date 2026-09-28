# M2 - Acceptance report: Extraction Reliability

> **Correction 3 (Independent Review 03) is at the very end and supersedes Correction 2's table; Correction 2 supersedes everything before it.**
>
> **Correction 2026-09-28 (Independent Review 01: CHANGES REQUIRED).** The sections below are the M2 submission as reviewed, kept as history. The corrected state, evidence and verdict are in the section "Correction 2026-09-28" at the end and in `M2-REVIEW-RESPONSE.md`. Statements superseded there: section 1 (source version), section 2 (test results), section 3 (Golden counts), section 4 (population IDs: the four outside the manifest are 24, 122, 313, 316; 22 is inside), section 6 and the opening sentence's "no restart" (see the operations timeline), section 8 ("first M3 prerequisite": the Golden review was completed within M2), section 9 (verdict).

Submitted for independent review on 2026-09-28 (local; the work ran on the evening of 2026-09-27 and into the night). Verdict proposed at the end. Nothing was run against the live database, cache, workers or AI; no migration, backfill, restart or live repair. The M1 acceptance qualifications (Review 03) were carried: the 86 generic references were treated as a review population, "no record expected" was not treated as truth, and drawing attribution was left to M3.

## 1. Reviewed source version

| Item | Value |
|---|---|
| HEAD | `13eb73ce85e5ac55c14303efe9d8511090ace723` (unchanged throughout) |
| Working tree | the pre-existing dirty tree of M1's acceptance plus M2's own changes; `evidence/before_manifest.json` (107 dirty files hashed at start) and `evidence/before_tracked.diff` separate the two |
| M2-only diff | `evidence/m2_changes.diff` (pre-M2 versions reconstructed from HEAD + the pre-M2 diff for tracked files, and by reversing the exact edits for the two pre-existing untracked test files; every reconstruction verified against the pre-M2 snapshot hashes, two differing only by line-ending convention: `evidence/m2_changes_provenance.json`) |
| Files changed by M2 | `backend/app/services/document_control.py`, `backend/app/services/document_sync.py`, `backend/app/services/transmittals.py`, `backend/app/ai/sheet_reader.py` (one assignment), `backend/tests/test_ai_sheet_reader.py` (one assertion), `backend/tests/test_extraction_repair.py` (one expectation), `backend/tests/test_repair_tool.py` (two expectations); new `backend/tests/test_extraction_m2.py`; documentation under `docs/milestones/M2/` |
| Versions after M2 | `PARSER_VERSION parse-2026-09-28.2`, `BOX_VERSION box-3`; `INDEX_VERSION`, `EVIDENCE_VERSION`, `RULES_VERSION`, `PROMPT_VERSION` unchanged |
| Source hashes after M2 | `evidence/M2-EVIDENCE-MANIFEST.json` (`source_hashes_after_m2`) |

## 2. Test commands and results (isolated, `backend/tests/conftest.py`)

| Run | Command (from `backend/`) | Result | Evidence |
|---|---|---|---|
| Baseline before edits | `venv/Scripts/python -m pytest <16 focused suites> -q -p no:cacheprovider --junitxml=...` | **158 passed, 1 failed** (`test_document_sync.py::test_a_read_that_fails_keeps_the_previous_result_and_is_marked`, diagnosed as D-EXT-1, not waived) | `evidence/baseline_focused.xml`, `.log` |
| Final, focused + consumer suites | same 16 suites + `test_extraction_m2.py`, `test_document_classification_pilot.py`, `test_submittal.py`, `test_submittal_ai.py`, `test_project_state.py`, `test_drawings_module.py`, `test_battery_api.py` | **270 passed, 0 failed, 0 errors** in 285 s | `evidence/final_focused.xml`, `.log` |
| New regressions | `tests/test_extraction_m2.py` (13 tests) | all pass; they cover: hyphen-wrapped reference and the numeric-continuation guard; untracked-discipline cover kept with `raw_system`, sheet pages not; framed / highlighted option read, whole-row frame, two frames, black rule and a comment box mentioning an option rejected; scanned transmittal via OCR (with and without the banner) and receipt-not-approval, deduplication with the Word original, integration through `read_open_pdf`; printed revision and revision source; new fields in the stored record and old records loading; reader defect raising vs unopenable file noted; folded reply page kept | `evidence/final_focused.xml` |
| Existing suites relied on for BOQ reliability | `test_boq_extraction_v2` (time budget stops between calls and state is kept; resume asks only pending rows; a read cut mid-page resumes from that page; failure while saving leaves the project unextracted; CLI timeout is a timeout), `test_boq_geometry_v2`, `test_boq_selective_v2` (batches bounded, answers matched by row id, strip failure leaves rows pending, no batch without time), `test_boq_verification_v2` (provider failure keeps the line; two explicit not-founds remove it), `test_boq_corrections_v2`, `test_ai_sheet_reader` (a sheet read before is not read again; without the model the sheet is recorded as not read - now with `state == "failed"`) | pass before and after | same |
| Failure/interruption cases exercised | interrupted processing keeps finished documents and resumes the rest; one bad document does not stop the others; worker restart does not re-read finished documents; a job left by a dead worker is recovered; a stop during the AI stage leaves unread forms pending; an OCR failure on one page is noted and the batch continues (`test_file_sync_v2_processing`, `test_document_processing_v2`); reader crash keeps the previous reading and marks the row failed (`test_document_sync`) | pass | same |

Not run: the full test suite beyond the listed suites; any live model call; the frontend build (no frontend change).

## 3. Golden evidence

`M2-GOLDEN-MANIFEST.json` (120 cases + the BOQ case): per case the registry identity (id, relative path, sha256, role, pages, format), the populations it belongs to, the label with its provenance, the independent text-layer facts, the stored reading (old parser), the pre-M2 dirty-tree reader's result (`parse-2026-09-28.1`), the M2 reader's result (`parse-2026-09-28.2`) and per-field verdicts.

| Measure | Value |
|---|---|
| Cases | 120 documents (18 date + 86 generic + 7 prefix controls + 6 Word transmittals + named cases, deduplicated) + 1 BOQ scan case |
| Labels independently source-checked (page images rendered with pymupdf, read by the author) | 17 documents: 434, 436, 437, 578, 783, 848, 440, 409, 771, 784, 868, 729, 730, 687, 620, 120, 22 (+ BOQ page 2 for TP606 / TP434) |
| Labels derived from the original's text layer by an independent regex (visual check pending) | 103 |
| Engineer-confirmation-pending cases | 687 (scanned tick and truncated OCR reference), the EP-30784 R1-folder sheets (which revision the C stamp answers), the 103 text-layer labels, the BOQ fixture countersignature |
| Reference verdicts (after) | 106 match, 1 mismatch (687: OCR interleaves `Rev.02` and a stray digit at the line break - G-M2-1b), 6 unlabelled (drawing sheets and the CRS copy 597: not covers, no independent expectation), 7 prefix controls unchanged |
| Status verdicts on visually checked cases | 13 match, 1 mismatch (687: scanned tick - G-M2-1) |
| Revision / category / system / listed / revision_source on visually checked cases | all match |
| 620 | 0 records, as expected (specification pages) |
| Date-shaped references left in the 18 | 0 (already 0 under the pre-M2 dirty-tree reader; confirmed on the originals) |
| Exact generic references left in the 86 | 0 (same; the originals' own text layers carry the serial numbers) |
| BOQ | TP606 = 491, TP434 = 142 confirmed on the scan; the platform's stored EP-30784 BOQ (old reader) holds TP606 twice and other errors (`evidence/boq_metrics_platform_p1.json`); a fresh no-model read is recorded as not read (`state failed` after D-VAL-1) |

Accuracy is stated only on the visually checked cases; the text-layer-derived cases are reported as counts, not as accuracy.

## 4. Document / page / row accounting

Unit rules: one outcome per unit, exclusive; counts sum to the eligible population.

| Level | Population | Outcomes |
|---|---|---|
| Documents, Golden population run (final parser, cold cache, idle machine) | 124 eligible (120 manifest cases + 4 EP-30784 R1 files 22, 24, 313, 316 read for the revision cases; 4 of the 6 Word transmittals overlap the manifest count) | succeeded with records 123 (119 manifest + 4), succeeded with no records 1 (620, expected), failed 0, skipped 0, unresolved 0 |
| Documents, extract-only repair on the clone, EP-30088 | 522 eligible (525 rows minus 3 intake rows) | succeeded (repaired) 520, skipped 2 (Word transmittals: read by their own reader), failed 0, unresolved 0; 624.4 s |
| Documents, extract-only repair on the clone, EP-30784 | 356 eligible (359 minus 3 intake rows) | succeeded (repaired) 352, skipped 4 (Word transmittals: read by their own reader), failed 0, unresolved 0; 589.1 s |
| Pages | the reader visits pages until its own stop rules (12 pages for reply search, 12 OCR pages); pages not visited are noted on the document ("only the first 12 pages were checked", "OCR limit reached") - 4 such notes in the population run; no page visited is dropped without a note (an OCR failure is a note, a reader failure is now a failed document) | see `evidence/parser_population_after.json` notes |
| Rows (BOQ) | not re-extracted in M2 (no model); the stored BOQ vs Golden: 74 golden FAS rows, 76 stored lines, 3 golden rows missing, 6 false auto-accepts, 1 wrong quantity (`evidence/boq_metrics_platform_p1.json`) - stored business data, reported, not repaired | |
| Content-validation uncertainty (separate from execution) | 103 text-layer labels pending visual check; 6 unlabelled sheets; 1 reference and 1 status mismatch on 687; EP-30784 R1 association pending | |

## 5. Measured performance (same files, same machine)

| Set | Before (`parse-2026-09-28.1`) | After (`parse-2026-09-28.2`) | Note |
|---|---|---|---|
| 89 documents cold in both runs, excluding the fire-fighting covers | 169.2 s | 163.5 s | no material change |
| 9 fire-fighting covers (cold in both) | 0.0 s | 29.7 s | new, legitimate work: their drawing page is read now that the cover is a record (before, "no record on page 1" stopped the read) |
| 17 named documents | 6.3 s (warm OCR cache, read once before in that cache) | 38.0 s (cold) | not comparable; listed for completeness |
| OCR / AI attempts | OCR through the scratch page cache; 0 AI calls in every M2 run | | |

The earlier after-runs were slower because the repair chain ran on the same machine at the same time; only the final run above was taken on an idle machine (`evidence/timing_comparison.json`). No latency improvement is claimed.

## 5b. Same code, same result

The repair chain and the isolated population parse ran as separate processes with separate scratch caches; for the 118 Golden-population documents the repair also re-read, the records it wrote and the records the population parse produced are identical (118 of 118, `evidence/repair_vs_population_crosscheck.json`), so the clone comparison and the manifest describe the same reader.

## 6. Data effects

None on live data. On the repair clone: `project_documents` (readings, mirrored reference/revision/status, `last_processed_at`) and `document_dependencies` (stale flags, log dependencies) only; every register, BOQ, action, floor, project field and role unchanged (M2-COMPATIBILITY-REPORT.md, section 3).

## 7. Limitations

- Golden labels: 17 visual, 103 text-layer; no engineer sign-off anywhere; the BOQ fixture is re-checked by the author, not countersigned.
- 687: reference and decision unresolved (OCR); the AI form reading carries both.
- EP-30784 R1 sheets: the stamp is read, the revision it answers is not an extraction fact (M4 + engineer).
- Second covers read by OCR can carry mangled numbers (G-M2-6).
- Register-level before/after (Logs, Drawings) was not executed on the clone; the earlier session's clone figures remain reported/unverified.
- The read-side OCR scope is unchanged (first two scanned pages, 12 OCR pages per document).

## 8. M3 handoff

- Extraction now exposes, per record: `raw_system`, `printed_revision`, `revision_source`, reply pages as records, scanned-transmittal sample records, framed/highlighted decisions. Classification (M3) can read them; nothing routes on them yet.
- Attribution inputs available: `content_evidence` project-code findings (729/730/687 print other projects' codes), `raw_system` for untracked disciplines, reply records with their listed sheets.
- Cases for M3 evidence grading: 687 (scanned tick), 575/780/845 (OCR-mangled second cover), 620 (specification named as a statement), 597/868 (CRS copies).
- Golden countersignature and the 103 visual checks are the first M3 prerequisite; the manifest is the vocabulary.

## 9. Verdict

**READY FOR INDEPENDENT M2 REVIEW**, with the limitations in section 7 stated as such: the confirmed in-scope defects (D-EXT-1..6, D-REF-1, D-REV-1, D-VAL-1) are fixed and regression-tested; critical references, revisions, dates and decisions match the source-checked Golden labels except the two 687 items recorded as unresolved; no page, row or document vanishes silently on failure (a reader failure is now a failed document with its previous reading kept); resume and idempotency are covered by the existing suites, all passing; manual corrections and business contracts are untouched on the clone; tests and evidence refer to the hashed source version. Acceptance, and permission to start M3, remain with the independent review.


## Correction 2026-09-28 (after Independent Review 01)

### C1. Source version

| Item | Value |
|---|---|
| Reviewed commit | `ed7d221df24dbdeac3ababed5de3e128fed0c588` (clean at review; contains the M2 submission and the database snapshot) |
| Correction | uncommitted diff on top of it: `evidence/r3__m2_review01_changes.diff` (6 application/script files, 4 test files, this documentation); working tree at the start of the correction hashed in `evidence/r3__before_manifest.json` |
| Versions | `PARSER_VERSION parse-2026-09-28.3`, `BOX_VERSION box-4`; `design_sheet_extractor.PARSER_VERSION` unchanged (`2026-09-15.2`: coverage-only change, no row reading changed); no migration |
| Hashes | `evidence/M2-EVIDENCE-MANIFEST.json` (`source_hashes_after_correction`, every evidence file, the M2 documents, scratch-only artifacts, clones; the submission's manifest kept under `m2_submission_2026_09_28`) |

### C2. Tests

**317 tests, 309 passed, 7 skipped, 1 failed, 0 errors** in 308.4 s (skips: live-archive BOQ sheets), short scratch path (`evidence/r3__final_r3.xml`, `.log`; command in `M2-REVIEW-RESPONSE.md` section 1). New: `tests/test_extraction_m2_review.py` (21), `test_design_sheet_extractor.py::test_an_inked_band_between_two_tables_is_a_skipped_region_not_a_silent_gap`. The reviewer's five probes pass on the final code (`evidence/r3__probe__probe_results.json`). The one failure, `tests/test_ai_sheet_reader.py::test_the_first_read_runs_as_a_job_the_page_follows` (a BOQ read run as a background job thread; `FOREIGN KEY constraint failed` on `project_boq_items.extraction_run_id` inside the job, or `UnmappedInstanceError: NoneType` in the full run), is **not attributable to the correction**: it fails identically on a clean worktree of the reviewed commit `ed7d221` with the same interpreter (3 of 3 runs alone; `evidence/r3__flaky_1.log` is the same failure on this tree), passed in the first full run of this correction (`r3__final_r3_run1.xml`) and in the M2 submission's run, and passes when its module runs together with `tests/test_boq_extraction_v2.py` (20 passed, same code, same hour). It is order/timing dependent in untouched BOQ job code (`app/routers/projects.py::_extract_boq`, `app/services/jobs.py`), not diagnosed here, and listed as a limitation for the re-review. A first full run on the code before the observation-JSON fix (D-EXT-10) is kept as `evidence/r3__final_r3_run1.xml` (316 tests, all passing, 392.6 s); it did not contain the persistence test that catches the defect.

### C3. Golden evidence (manifest regenerated after final code and labels)

| Measure | Value |
|---|---|
| Cases | 120 documents + 1 BOQ case (unchanged denominator) |
| Source-checked labels | 17 page-image checks (2026-09-27) + **111 text-PDF cases checked on header/recommendation-row crops (2026-09-28)**, 111 of 111 agreeing; 3 scans (687, 729, 730) page-image checked; **6 Word transmittals still label-pending** |
| 687 | reference: bounded cell OCR recovers the full number; the parser's record stays truncated and **flagged `reference_incomplete`**; decision: mark analysis **unresolved** (B darkest, not separable from the shaded D box), status UR; both visible in every verdict table |
| Reference verdicts, promoted path | 106 match, 1 incomplete (687), 13 with no independent reference expectation (drawing sheets, CRS copies, specification, Word transmittals) |
| Reference verdicts, default path | 93 match, 13 held as observations (11 untracked-discipline covers, 2 scanned transmittals), 1 incomplete (687) |
| Status verdicts, promoted path | 112 match, 1 unresolved (687); 120/122 page 2: mark held (revision association unvalidated) |
| Status verdicts, default path | 77 match, 22 held (drawn-frame/highlight candidate agrees with the label), 11 held as cover observations, 2 held as transmittal observations, 1 unresolved (687) |
| Population IDs outside the manifest | 24, 122, 313, 316 (22 is a manifest case); 120 + 4 = 124 |
| BOQ | current deterministic reader on the FAS/EML originals with page and row accounting: FAS 74 golden -> 73 lines, 69 matched by part, quantity 68/69, 1 row unread in an unruled band (now a skipped region), 1 title row dropped with an issue; EML 12 -> 12, 11 by part, quantities all right by position. No model. Current-reader defects D-BOQ-open-1..5 open (`M2-DEFECTS-AND-FIXES.md`) |

### C4. Document / page / row accounting (corrected reader, cold cache, both paths)

| Level | Population | Outcomes |
|---|---|---|
| Documents, default path | 124 | 106 with records, 13 observations only (11 untracked covers, 2 scanned transmittals), 1 no records (620, expected), 0 failed, 0 skipped |
| Documents, promoted path | 124 | 119 with records, 1 no records (620), 0 failed, 0 skipped |
| Pages (ledger) | 124 documents | 351 visited, 35 skipped on 8 documents (scan/OCR/reply-search limits, each with the reason), 0 failed; outcomes 116 complete, 8 bounded |
| Rows (BOQ, originals) | FAS 74 + EML 12 golden rows | see C3; the FAS band `y 513-559` on page 2 is listed as a skipped region and a note |
| Content uncertainty (separate from execution) | 687 reference incomplete / decision unresolved; 6 Word labels pending; EP-30784 R1 association held (not assigned) |

### C5. Performance (identical work, both cold)

124 documents: 263.8 s (`parse-2026-09-28.2`) -> 243.7 s (`parse-2026-09-28.3`, default path); largest single increase 3.8 s (433). No latency claim beyond this workload; the promoted run was warm-cache and is not compared.

### C6. Data effects

None on live data (the live database is byte-identical to the committed snapshot: `evidence/r3__live_db_readonly_check.json`). On the correction's repair clone (`clone_r3.db`, default path): project 4: run 1 522 selected / 484 repaired / 32 failed on the observation JSON defect D-EXT-10, run 2 on those 32: 32 repaired / 0 failed, tables changed ['document_dependencies', 'project_documents'], roles changed 0, form-reading records 9 -> 5 (4 documents changed); project 1: run 1 356 selected / 349 repaired / 3 failed on the observation JSON defect D-EXT-10, run 2 on those 3: 3 repaired / 0 failed, tables changed ['document_dependencies', 'project_documents'], roles changed 0, form-reading records 2 -> 2 (0 changed); vs the M2 submission's repair: project 4 {'identical': 447, 'changed:status': 39, 'records_removed': 32, 'records_added': 4}, project 1 {'records_removed': 3, 'identical': 329, 'records_added': 1, 'changed:status': 23}; details in `M2-COMPATIBILITY-REPORT.md` section 7.

### C7. Operations

The submission's "no restart" was wrong for the snapshot step: `stop-backend.bat` was run at ~00:20 on 2026-09-28 before the commit and `start-backend.bat` was launched afterwards without confirming health. Corrected timeline, what was observed independently and the separate recovery checklist: `M2-REVIEW-RESPONSE.md` section 2, `M2-RECOVERY-CHECKLIST.md`. During the correction nothing was stopped, started, migrated or processed live.

### C8. Verdict (supersedes section 9)

**READY FOR INDEPENDENT M2 RE-REVIEW**, with the real-model BOQ verification portion **BLOCKED BY MISSING EVIDENCE** (no model call permitted), the current-reader BOQ misreads reported open, G-01 unchanged and documented, six Word labels pending, no engineer sign-off claimed. Acceptance and any live promotion remain with the independent review and the owner.


## Correction 2 (2026-09-28, after Independent Review 02) -- supersedes C1-C8 and sections 1-9

| Item | Value |
|---|---|
| Source | commit `ed7d221df24dbdeac3ababed5de3e128fed0c588`; the Review 01 and Review 02 corrections uncommitted on top (`evidence/r4__m2_review02_changes.diff` = the whole uncommitted diff; hashes in `evidence/M2-EVIDENCE-MANIFEST.json`) |
| Versions | `PARSER_VERSION parse-2026-09-28.4`, `BOX_VERSION box-4`, `design_sheet_extractor.PARSER_VERSION 2026-09-28.1`; no migration |
| Tests | **337 tests, 330 passed, 7 skipped, 0 failed, 0 errors** in 402.6 s (`evidence/r4__final_r4.xml`); new `tests/test_extraction_m2_review02.py` (16), `tests/test_job_thread_sessions.py` (1), `tests/test_design_sheet_extractor.py` (+3); the test harness now uses a file database (`tests/conftest.py`), the diagnosed cause of the BOQ job test's failures |
| Reviewer probes (Review 02) | all five meet the intended contract on the final code: `evidence/r4__probe__independent_probes_after.json` |
| Golden | 120 cases: 111 text-PDF crop-checked, 3 scans page-checked, 6 Word transmittals labelled from their own binary text (this correction); verdicts for parse .4 on both profiles in `M2-GOLDEN-MANIFEST.json` (`verdict_counts_parse_4`) |
| Documents (124, exclusive) | default {'succeeded_with_records': 110, 'succeeded_observations_only': 13, 'succeeded_no_records': 1, 'total': 124}; promoted {'succeeded_with_records': 123, 'succeeded_no_records': 1, 'total': 124}; the four outside the manifest: 24, 122, 313, 316 |
| Pages (default) | {'visited': 351, 'skipped': 23, 'failed': 0, 'total': 374, 'ocr': {'attempted': 269, 'failed': 0, 'skipped_budget': 12}, 'document_outcomes': {'complete': 116, 'bounded': 8}} |
| BOQ, deterministic, originals | FAS: 69 lines accepted with part/quantity accuracy 1.00/1.00, group 0.985, band row read, 6 rows for review (TP606 cut digit; four uncertain parts; the title row), outcome NEEDS_INTERPRETATION; EML: 12 lines, quantity 1.00; open reader defects listed in the response |
| Clone repair (default profile) | project 4: {'repaired': 516, 'skipped': 6}; project 1: {'repaired': 352, 'skipped': 4}; roles changed 0; details in `M2-COMPATIBILITY-REPORT.md` section 8 |
| Live data | untouched by this correction (no stop/start, no live processing, no repair, no model). **Observed at the end**: the backend (API + three workers) is running from this checkout, started outside this session; the live database file has been written by its startup (no document processing since 2026-09-27) and no longer matches the committed snapshot -- `M2-REVIEW-RESPONSE.md`, operations note; `evidence/r4__live_check_r4.json` |
| Verdict | **READY FOR INDEPENDENT M2 RE-REVIEW**; code defects remaining and missing evidence listed separately in `M2-REVIEW-RESPONSE.md` (Review 02 section) |


## Correction 3 (2026-09-28, after Independent Review 03) -- supersedes Correction 2's table

| Item | Value |
|---|---|
| Source | commit `ed7d221df24dbdeac3ababed5de3e128fed0c588`; Review 01+02+03 corrections uncommitted (`evidence/r5__m2_review03_changes.diff` = the whole uncommitted backend diff; hashes in `evidence/M2-EVIDENCE-MANIFEST.json`) |
| Versions | unchanged: `parse-2026-09-28.4`, box-4, sheet reader `2026-09-28.1`; no migration |
| Tests | **344 tests, 337 passed, 7 skipped, 0 failed, 0 errors** in 422.2 s (`evidence/r5__final_r5.xml`); new `tests/test_extraction_m2_review03.py` (5), `test_design_sheet_extractor.py` (+2) |
| Reviewer probes | 5 inherited + 2 new (repeat_bounded, promoted_carried_as_default) meet the intended contracts: `evidence/r5__probe__*` |
| Carried records | keep their own provenance (`retained`), their uncertainty and a withheld decision until their bytes and profile are the current ones; a mixed reading is never current or copied |
| BOQ, deterministic, originals | FAS 69 lines accepted (part/quantity 1.00), 6 review rows; EML 10 lines, 2 review rows incl. the multiline `+SL231`/`+SL23I` identity, outcome NEEDS_INTERPRETATION (not VALID); SIGA-OSHD-FC recorded as printed, 6538-G5 an owner layout decision |
| Clone repair | project 4 {'repaired': 516, 'skipped': 6}; project 1 {'repaired': 352, 'skipped': 4}; roles changed 0; retained: p4 {'documents': 0, 'other_profile': 0, 'unverified': 0}, p1 {'documents': 0, 'other_profile': 0, 'unverified': 0} |
| Live data | untouched by this correction; the backend found running at the end of Review 02 was neither used nor stopped |
| Verdict | **READY FOR INDEPENDENT M2 RE-REVIEW**; owner decisions and missing evidence listed in `M2-REVIEW-RESPONSE.md` (Review 03) |
