# M2 - Baseline (before any M2 edit)

> Correction note (M2 Review 01, 2026-09-28): the checkout reviewed independently is commit `ed7d221df24dbdeac3ababed5de3e128fed0c588` (the dirty tree described below was committed there on 2026-09-28 00:27 local, with the database snapshot); the Review 01 correction is the uncommitted diff on top of it (`evidence/r3__m2_review01_changes.diff`). The four population documents outside the 120-case manifest are **24, 122, 313 and 316** (22 is a manifest case). See `M2-REVIEW-RESPONSE.md`.

Recorded 2026-09-27 (evening, local) on the accepted M1 snapshot's checkout. Everything below was gathered before the first M2 code change; the reproducible artifacts are under `docs/milestones/M2/evidence/`.

## 1. Source and environment

| Item | Value |
|---|---|
| Repository / HEAD | `ep-platform` (submodule of `dev`), HEAD `13eb73ce85e5ac55c14303efe9d8511090ace723`, branch master |
| Dirty tree at start | 82 `git status --porcelain` lines (the pre-existing Document Processing V2, BOQ V2, Classification V2 and repair work, uncommitted, plus `docs/milestones/`). 107 dirty files hashed in `evidence/before_manifest.json`; the tracked diff at start is `evidence/before_tracked.diff`. None of it is M2 work and none was reset, cleaned or overwritten. |
| Versions at start | `document_control.PARSER_VERSION = parse-2026-09-28.1`, `BOX_VERSION = box-2`, `document_sync.INDEX_VERSION = index-2026-09-24.4`, `content_evidence.EVIDENCE_VERSION = evidence-2026-09-28.2`, `document_classification.RULES_VERSION = classify-2026-09-28.4`, `sheet_reader.PROMPT_VERSION = read-sheet-2026-09-16.1`, `design_sheet_extractor.PARSER_VERSION = 2026-09-15.2` |
| Test environment | `backend/venv` Python 3.12.10, pymupdf 1.28.2, tesseract available (`submittal_scanner.ocr_available() = True`); `backend/tests/conftest.py` isolates DATABASE_URL (in-memory), CACHE_ROOT, LIBRARY_ROOT, UPLOADS_ROOT, AI (off), libraries, knowledge import, sync workers |
| Standalone scripts | do not inherit pytest fixtures: every isolated run in M2 set `DATABASE_URL` (clone), `CACHE_ROOT`, `LIBRARY_ROOT`, `UPLOADS_ROOT`, `AI_ENABLED=false`, `DATASHEET_LIBRARIES={}`, `PROJECTS_ROOT=` and the autodetect/import-on-start flags before importing app code (`evidence/run_parser.py`, `evidence/run_parser_after2.py`, `evidence/run_parser_final.py`) |
| Live data | `backend/ep_platform.db` untouched. Clone taken with the SQLite backup API (WAL included) from a `mode=ro` connection: `m2_clone_20260927T181957Z.db`, 95,461,376 bytes, 890 project_documents in both; a second copy `m2_clone2_repair.db` for the extract-only repair run. Both live in the session scratchpad (not committed; hashes in the acceptance report). Long OneDrive paths need the `\\?\` prefix: 92 of 359 EP-30784 files are unreachable without it (all reachable with it) |

## 2. Existing implementation, classified

| Area | Symbols inspected (current lines) | Classification | Basis |
|---|---|---|---|
| Reference grammar | `document_control.REF`, `reference_candidates@395`, `first_reference@409`, `is_date_shaped@368` | verified working for the date defect (F1) and the truncation defect (F2) on the current dirty tree; **defective** for a reference wrapped at a hyphen on a scan (687 page 1: `...-PJW-ZZZ` instead of `...-PJW-ZZZ-ZZZ-1004`) | isolated run of the parser on the originals (`evidence/parser_population.json`): 0 date-shaped and 0 generic references remain in the 115-document population |
| Submission cover | `submission_cover@540`, `Cover`, `_LABEL_LINE` | verified working on 434/436/437/409/415 (serial, listed sheets, description, Rev) | visual + text layer |
| Decisions | `read_decision@761`, `boxed_decision@700`, `annotated_decision@641`, `_is_mark@597` | verified for stamp annotations (434/436/437) and filled boxes; **defective** for a frame drawn into the page (409: green stroked rectangle -> UR) and a highlight-sized fill (440: orange fill) | `get_drawings()` inspection in `evidence/render2_report.json` |
| Page parsing | `parse_page@826` | **defective**: a drawings cover of a discipline the platform does not track (fire fighting, 11 covers 440-463) returns no record at all, although the page carries a number, a revision, a listed sheet and a decision | actual run: 11 documents "no records now" |
| Reader loop | `read_open_pdf@1028`, `_answers@1155`, `combine@1247` | reply folding, listed sheets, superseded chains verified by existing tests (`test_extraction_repair.py`); **defective failure semantics**: a reader exception is swallowed as an "unreadable" note and an empty reading replaces the previous one (baseline test failure below) | `test_document_sync.py::test_a_read_that_fails_keeps_the_previous_result_and_is_marked` |
| Scanned PDFs | `read_open_pdf` OCR path, `transmittals.read_transmittal@135` | **missing**: a scanned copy of a document transmittal filed as a PDF (729/730) yields nothing; only Word transmittals in a transmittal folder are read | OCR text dumped in isolation (`evidence/`) |
| Revision | `REV@417`, `FOLDER_REV@425`, `folder_revision@455`, `title_block@787` | verified working per the settled owner rule (printed first, folder where the sheet prints none); **unverified/incomplete**: the positional title block's printed revision ("00" after the sheet size) is not captured, and the origin of a revision is not recorded (the R1 folder file 120 page 2 prints 00) | text layer of 120/22 |
| Processing integration | `document_sync.extract@430`, `process@326`, `document_processing.read_task@129`, `parser_current@197`, `run@208` | verified: parser version forces re-read; failed read keeps previous result at the `run` level (`row.state=FAILED`) **but** `extract` never lets the failure through (see above) | code read + failing test |
| Content evidence | `content_evidence.scan_pdf@147` (kinds, author cues, project codes) | implemented, unverified for the new component observations; not changed in M2 (classification is M3) | code read |
| BOQ extraction | `design_sheet_extractor.extract_design_sheet@768`, `sheet_reader` (bands, second reading, close-up, time budget, resume), `extraction/pipeline.record_design_sheet_run@76`, `row_geometry`, `identity`, `issues` | verified by existing tests: resume after a cut mid-page, only pending rows re-asked, failure while saving leaves the project unextracted, CLI timeout is a timeout, close-up failure leaves the row for review, part-number identity (prefix is not the same part), geometry witnesses, selective verification batches, cache reuse, provider failure keeps the line, two explicit not-founds remove a line (`test_boq_extraction_v2.py`, `test_boq_geometry_v2.py`, `test_boq_selective_v2.py`, `test_boq_verification_v2.py`, `test_ai_sheet_reader.py`) - all passing in the baseline | baseline run |
| BOQ persistence/review | `boq_candidates`, `boq_provenance`, `boq_corrections`, `reextraction`, `page_cache` | implemented and tested (`test_boq_corrections_v2.py`, candidates/snapshots in `boq_review`); not changed in M2 | baseline run |
| Evaluation utilities | `scripts/repair_extraction.py` (dry-run default; `--apply`, `--reassess`, `--reconcile` separate; skips transmittal rows; reconcile calls `shop_drawings.reconcile` only, never `submittal_reader.check`), `scripts/golden_records.py` (`--from-db` captures stored output, excludes form-reading records: a behavioural baseline, not Golden truth), `scripts/consumer_shadow.py`, `scripts/business_snapshot.py`, `scripts/boq_metrics.py` | verified by reading; used on clones only | code read |

## 3. Baseline tests (before edits)

Focused suites run in the isolated pytest environment (`evidence/baseline_focused.xml`, `.log`): `test_document_control`, `test_extraction_repair`, `test_repair_tool`, `test_document_sync`, `test_document_processing_v2`, `test_boq_extraction_v2`, `test_boq_geometry_v2`, `test_boq_selective_v2`, `test_boq_corrections_v2`, `test_boq_verification_v2`, `test_ai_sheet_reader`, `test_classification_evidence`, `test_document_routing`, `test_document_classification_v2`, `test_file_sync_v2`, `test_file_sync_v2_processing`.

Result: **158 passed, 1 failed** in 175 s.

The failure, `tests/test_document_sync.py::test_a_read_that_fails_keeps_the_previous_result_and_is_marked`, was diagnosed, not waived: the test makes `document_control.read_open_pdf` raise; `document_sync.extract` catches every exception and returns an empty reading with the note "Could not read ...", so `process` writes that empty reading as `fresh` over the previous good one. Actual: `row.state == "fresh"`, expected `failed` with the previous `extracted` kept. This is an M2 acceptance matter (a failed re-read must retain the last successful result and expose the failure) and is fixed in M2 (see M2-DEFECTS-AND-FIXES.md, D-EXT-1).

## 4. Populations (from the accepted M1 snapshot, resolved through the registry on the clone)

| Population | Documents | Notes |
|---|---|---|
| Embedded date-shaped references | 18 (434, 436, 437, 440, 444, 448, 452, 454, 457, 458, 459, 460, 461, 463, 578, 687, 783, 848) | 687 only has it embedded (page 9); all EP-30088 |
| Embedded-exact generic `ICC-DLRC-SPM-SD-MEP` | 86 | includes 434 and 436 (their page-2 reply record) |
| Prefix-only controls | 7 (771-776, 784) | full drawing references; must stay unchanged |
| Word transmittals | 6 (343, 344, 345, 346, 889, 890) | read by the transmittal reader, not the PDF parser |
| Named cases | 868 (CRS reply list), 729/730 (scanned transmittals), 620 (spec named Compliance Statement), 687 (reference copy of other projects' approvals), 409/415 (EML covers), 120/122/313/316/22/24 (EP-30784 R1 files) | 22 and 120 are manifest cases; 24, 122, 313, 316 are read for the revision cases only (population 124 = 120 + 4) |
| Deduplicated union | 115 documents (population run), 130 with the named EP-30784 cases | every id accounted for in the manifest; 0 missing files with the long-path prefix |

Stored-reading facts at baseline (clone, read-only): 890 documents, 881 with extracted JSON, 367 with records, 547 records; `parser_version` and `evidence` absent on all rows (live readings predate both).

## 5. Constraints honoured

No live processing, sync, worker restart, migration, repair or backfill. No live AI (scripted/absent providers only). Source documents opened read-only (pymupdf, `mode=ro` SQLite). Extract-only repair on the second clone only, with `--reassess` and `--reconcile` off (G-01: `submittal_reader.check` is never reached by the repair tool). Word transmittals are skipped by the repair tool and read by their own reader in the isolated runs.
