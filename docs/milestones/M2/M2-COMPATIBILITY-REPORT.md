# M2 - Compatibility report

What the M2 extraction changes do and do not touch, shown on a disposable clone. Nothing here was run against the live database, the live cache or the live workers. Evidence files are under `evidence/` (hashes in `evidence/M2-EVIDENCE-MANIFEST.json`).

## 1. Preserved contracts

| Contract | Status | How it is shown |
|---|---|---|
| Legacy processing role (`ProjectDocument.role`) | unchanged: `document_sync.classify_text` untouched; the extract-only repair changed 0 roles on 878 re-read rows | `evidence/repair2_comparison.json` (`roles_changed: 0` for both projects) |
| Record category / `extracted.records` shape | additive only: three new keys per record (`raw_system`, `printed_revision`, `revision_source`); every existing key unchanged; older stored records still load (`ControlledDocument(**data)` in `document_sync.log_records`, defaults apply) | `test_extraction_m2.py::test_the_new_fields_travel_with_the_stored_record`; consumer suites (`test_submittal`, `test_project_state`, `test_drawings_module`, `test_document_classification_v2`, `test_classification_evidence`) pass unchanged |
| Revision values | unchanged: the owner's settled rule (printed first, folder where the sheet prints none) is untouched; only the provenance is now recorded | `::test_the_printed_revision_is_kept_as_printed_beside_the_revision_the_folder_gave` |
| Business status ownership | unchanged: extraction still writes only `project_documents` (reading, mirrored reference/revision/status of the first record) and dependency rows; registers, reconciliation, submittal map, actions, BOQ, calculations, current-fill policy, effective-system rules and approval rules were not edited | clone snapshot: only `project_documents` and `document_dependencies` changed (section 3) |
| Manual overrides and domain identities | untouched: `project_submittals`, `project_submittal_revisions`, `project_shop_drawings`, `shop_drawing_revisions`, `shop_drawing_candidates`, `drawing_issues`, `project_boq_items`, `project_actions`, `project_building_floors`, project fields: 0 rows changed | section 3 |
| Folder names as proof | no new use; the one existing use (revision from the R-folder) is now labelled `revision_source = "folder"` | `M2-DEFECTS-AND-FIXES.md` D-REV-1 |
| G-01 (`sync_register` overwrites engineer status) | not reachable from the changed code: the repair tool never calls `submittal_reader.check`; `--reassess` and `--reconcile` were off; extract-only runs leave `project_submittals` untouched (0 rows changed) | section 3; `scripts/repair_extraction.py` reconcile branch calls `shop_drawings.reconcile` only |
| G-04 | untouched (no current-fill change) | diff |
| Classification (M3) and routing | untouched: `document_classification.py`, `document_routing.py`, `content_evidence.py` not edited; `DOCUMENT_CLASSIFICATION_V2` flag semantics and `MIGRATED` (all false) unchanged; classification suites pass | diff; `test_document_classification_v2`, `test_document_routing`, `test_classification_evidence` |
| Parser/box versioning | `PARSER_VERSION` `parse-2026-09-28.1` -> `.2` (every stored reading is "outdated" and is re-read on its next processing, as designed by `document_processing.parser_current`); `BOX_VERSION` `box-2` -> `box-3` (cached box readings are recomputed; OCR cache keys unchanged, so OCR text is reused) | `document_control.py` header comment |
| Failure semantics | a reader defect now raises and the row keeps its previous reading with `state=failed` and the error; an unopenable / online-only file is still a note; an OCR failure on one page is still a note | `test_document_sync::test_a_read_that_fails_keeps_the_previous_result_and_is_marked` (was failing), `test_document_processing_v2::test_an_unreadable_file_is_still_a_document_with_the_reason_noted`, `::test_an_ocr_failure_on_one_page_is_noted_and_the_batch_continues`, `test_extraction_m2` failure tests |

## 2. Clone and isolation settings

| Item | Value |
|---|---|
| Source of truth left alone | `backend/ep_platform.db` (live), the live page cache, uploads, libraries, workers |
| Read-only clone | `m2_clone_20260927T181957Z.db`: SQLite backup API from a `mode=ro` connection (WAL included), 95,461,376 bytes, 890 documents; all isolated parser runs pointed `DATABASE_URL` at it and never wrote |
| Repair clone | `m2_clone5_repair.db`: a second backup of the read-only clone; the only database any M2 script wrote to |
| Environment for every standalone script | `DATABASE_URL` (clone), `CACHE_ROOT` (fresh scratch folder per run), `LIBRARY_ROOT`, `UPLOADS_ROOT` (scratch), `AI_ENABLED=false`, `DATASHEET_LIBRARIES={}`, `PROJECTS_ROOT=`, `PROJECTS_ROOT_AUTODETECT=false`, `ARCHIVE_*` empty, `COMPLIANCE_KNOWLEDGE_*` off, `DOCUMENT_CLASSIFICATION_V2=false`, `SYNC_FILE_WORKERS=0`; set before any `app` import; scripts run as separate processes (settings reach them through the environment) |
| Repair invocation | `scripts/repair_extraction.py --project 4 --select date-references,truncated-references,parser-outdated --apply --manifest ...` then `--project 1 --select parser-outdated --apply --manifest ...`; no `--reassess`, no `--reconcile`; OCR through the scratch cache; the model never called |
| Source documents | opened read-only through the app's long-path opener; never moved, renamed or written |
| Tests | pytest with `backend/tests/conftest.py` isolation (in-memory database, temp cache/library/uploads, AI off, scripted providers) |

## 3. Before/after on the repair clone (extract-only)

Snapshots: `scripts/business_snapshot.py` before and after (tables, project fields; hash), `scripts/golden_records.py --from-db` before and after (stored records per document, form-reading records excluded).

| Project | Repair manifest | Documents compared | Documents with changed records | Records before -> after | Roles changed |
|---|---|---|---|---|---|
| EP-30088 (project 4) | 522 selected, 520 repaired, 2 skipped (a transmittal is read by the transmittal reader, not the par), 0 failed, 624.4 s | 522 | 188 | 151 -> 234 | 0 |
| EP-30784 (project 1) | 356 selected, 352 repaired, 4 skipped (a transmittal is read by the transmittal reader, not the par), 0 failed, 589.1 s | 356 | 46 | 393 -> 400 | 0 |

Tables changed by the extract-only repair, per project (every other table of the snapshot identical, project fields identical):

| Project | Table | Rows before -> after | Rows changed | Fields changed (rows) |
|---|---|---|---|---|
| project 4 | `document_dependencies` | 164 -> 335 | 203 | updated_at (203), last_validated_sha256 (171), project_id (171), dependent_id (171), reason (171), id (171), source_document_id (171), stale (171), dependent_type (171) |
| project 4 | `project_documents` | 525 -> 525 | 520 | extracted (520), last_processed_at (520), status (173), reference (171), revision (70) |
| project 1 | `document_dependencies` | 383 -> 386 | 224 | updated_at (224), last_validated_sha256 (3), project_id (3), dependent_id (3), reason (3), id (3), source_document_id (3), stale (3), dependent_type (3) |
| project 1 | `project_documents` | 359 -> 359 | 352 | extracted (352), last_processed_at (352), reference (3), revision (3), status (3) |

Stored-record differences (documents with changed records, records before -> after, by marker):

| Project | Marker | Before | After |
|---|---|---|---|
| project 4 | records (form-reading records excluded) | 142 | 225 |
| project 4 | documents with records | 130 | 200 |
| project 4 | date-shaped references (F1) | 18 | 0 |
| project 4 | exact generic references (F2) | 86 | 0 |
| project 4 | drawings records with system None (untracked discipline / OCR-mangled cover) | 0 | 36 |
| project 4 | sample records read from scanned PDF transmittals | 0 | 3 |
| project 4 | decisions evidenced by a framed/boxed option label | 0 | 165 |
| project 4 | records carrying a printed revision | 0 | 0 |
| project 4 | records by status | {'UR': 138, 'rejected': 4} | {'rejected': 133, 'UR': 56, 'ANN': 36} |
| project 4 | records by category | {'drawings': 89, 'submittals': 42, 'reply': 8, 'samples': 3} | {'drawings': 186, 'reply': 8, 'submittals': 25, 'samples': 6} |
| project 4 | revision_source | {'null': 142} | {'cover': 188, 'default': 23, 'folder': 7, 'printed': 1, 'null': 6} |
| project 1 | records (form-reading records excluded) | 391 | 398 |
| project 1 | documents with records | 223 | 226 |
| project 1 | date-shaped references (F1) | 0 | 0 |
| project 1 | exact generic references (F2) | 0 | 0 |
| project 1 | drawings records with system None (untracked discipline / OCR-mangled cover) | 0 | 0 |
| project 1 | sample records read from scanned PDF transmittals | 0 | 7 |
| project 1 | decisions evidenced by a framed/boxed option label | 0 | 0 |
| project 1 | records carrying a printed revision | 0 | 155 |
| project 1 | records by status | {'UR': 279, 'ANN': 35, 'rejected': 77} | {'UR': 243, 'ANN': 41, 'rejected': 114} |
| project 1 | records by category | {'drawings': 320, 'submittals': 17, 'reply': 34, 'samples': 20} | {'samples': 27, 'drawings': 320, 'submittals': 17, 'reply': 34} |
| project 1 | revision_source | {'null': 391} | {'null': 85, 'folder': 121, 'printed': 118, 'default': 73, 'cover': 1} |

Attribution. The repair re-reads every row whose reading predates the current parser, so the stored-record delta combines (a) the pre-M2 dirty-tree reader `parse-2026-09-28.1` (F1 date references, F2 truncated references, cover parsing, region OCR of stamps) with (b) the M2 changes. The M2-only share is measured on the 115-document Golden population read by both parsers (`evidence/parser_population.json` vs `evidence/parser_population_after.json`): 77 identical; 22 status changed only (framed / highlighted / B option); 11 record where none (untracked-discipline cover); 3 status changed and a second-cover record added; 2 records added (kept reply page / second cover). Markers that exist only after M2: records with `system_code None` + `raw_system` (untracked-discipline covers), `revision_source` / `printed_revision` on every record, sample records read from scanned PDF transmittals, reply pages kept behind the submission they answer.

Register-level effect (what Logs / Drawings would build from the records): not executed in M2. Reconciliation (`shop_drawings.reconcile`) and the submittal map were deliberately not run on the clone; the earlier session's clone results for these (34 EP-30088 drawings, 14 EP-30784 R1 decisions) remain reported, unverified. The stored-record delta above is the input those consumers would see; its business interpretation (which revision a C stamp answers, whether an OCR-mangled second cover is the same submission) is M4 work and is flagged in the manifest ambiguities.

## 4. Role and manual-override checks

- Roles: 0 changed on the clone (both projects).
- Manual submittal overrides: no local manual status rows exist (all 12 history rows are `ai_check`), so G-01 could not be exercised on real data; the repair never reaches `sync_register` (traced), and `project_submittals` / `project_submittal_revisions` are byte-identical before and after.
- Drawing confirmations: no `confirmed_by_id` set locally; `project_shop_drawings` and `shop_drawing_revisions` identical before and after.
- BOQ manual edits: `project_boq_items` identical; BOQ code untouched.
- Intake rows (DRF, Design Sheets) are excluded from the repair by design (`INTAKE_ROLES`).

## 5. Observed effects to review (not defects of the change, but visible differences)

- Untracked-discipline covers now read: 36 `drawings` records with `system_code None` on EP-30088 (fire-fighting, architecture/pump-room covers, one OCR-mangled second cover of FA-0042); 0 before. No register consumes them.
- Reply pages behind a submission are kept: reply records 8 -> 8 on EP-30088 and 34 -> 34 on EP-30784 (the old readings held them as records; the .1 reader had dropped the folded ones).
- Status counts move with the stamp/frame evidence now read: EP-30088 {'UR': 138, 'rejected': 4} -> {'rejected': 133, 'UR': 56, 'ANN': 36}; EP-30784 {'UR': 279, 'ANN': 35, 'rejected': 77} -> {'UR': 243, 'ANN': 41, 'rejected': 114}. On EP-30784 the changes are page-2 sheets whose consultant stamp the region OCR reads (F8): revision by folder, `printed_revision` 00 recorded, engineer confirmation still required.
- Scanned PDF transmittals now give sample records: 3 on EP-30088, 7 on EP-30784; `transmittals.number` counts a Word original and its signed PDF copy once.
- The first version of two M2 rules produced wrong readings that the clone comparison caught before acceptance and that are now tested against: a coloured comment box mentioning "approved" read as an approval (8 EP-30784 records), and a hyphen-joined `...-SD-MEP-` / `0042` read as a serial. Neither is in the final run.

## 6. Unresolved impacts and gates

- **No live promotion in M2.** The live readings stay under the old parser until the platform's own processing re-reads them (parser version) or a repair is run under M5 rules. This report is the compatibility gate for that step: the extract-only path touches `project_documents` and `document_dependencies` only.
- **Engineer confirmation still required before any live re-read of EP-30784** (F8 / G-M2-2): the page-2 C stamps now read as decisions on the folder-derived R1 while the sheet prints 00; the raw facts are complete, the association is not an extraction decision.
- **Second covers read by OCR** (G-M2-6) can carry a mangled number; the relationship layer (M4) must pair them with the text cover by page adjacency, not by string equality.
- **Golden countersignature pending**: 17 of 120 cases visually checked; 103 labelled from the text layer only; 687's reference and decision remain unresolved; the BOQ fixture rows re-checked but not countersigned.
