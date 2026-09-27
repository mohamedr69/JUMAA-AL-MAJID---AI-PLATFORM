# M2 - Acceptance report: Extraction Reliability

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
