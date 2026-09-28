# M2 - Compatibility report

> **Correction 2026-09-28 (Review 01, R5).** Sections 1-6 are the submission as reviewed. Section 7 adds the promotion gate, the normal-path fixtures, the clone repair with the corrected reader (form-reading records included) and the downstream deltas. The claim in section 1 that G-01 is "not reachable from the changed code" is withdrawn: G-01 exists on the ordinary path; what the correction establishes is that the default path feeds it no new decision method.

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


## 7. Correction 2026-09-28: promotion gate, normal path, downstream deltas

### 7.1 The boundary

`Settings.extraction_promote_observations` (env `EXTRACTION_PROMOTE_OBSERVATIONS`), default **false**, read by `document_control.read_open_pdf(promote=None)` and so by every caller (`document_sync.extract`, `read_pdf_full`, the repair tool, the reader processes).

| Observation | Default path (off) | Promoted path (on) |
|---|---|---|
| Decision read from a drawn frame or a highlight (M2 method) | candidate in `decision_candidates`, flag `decision_method_unpromoted`, observation `decision_unpromoted`; status stays as the accepted methods read it (UR when none) | record status |
| Decision read from a filled box or a stamp annotation (accepted reader) | record status (unchanged behaviour) | record status |
| Conflicting marks (any methods) | UR + both candidates + `decision_conflict` | same |
| Mark on a sheet whose revision is the folder's while it prints another | UR + candidate + `decision_revision_unvalidated` | same (the association is not an extraction fact) |
| Untracked-discipline cover / sheet | observation `cover_untracked` / `drawing_sheet` (no record) | cover record with `system_code None`, `raw_system` (M2 behaviour) |
| Scanned transmittal | observation `transmittal` (the sample records inside it) | `samples` records (M2 behaviour) |
| Reply page folded into a submission | record (as accepted in M2's D-EXT-6; a reply row is never a register row) | same |
| Page ledger, attempt, stale, flags, observations | always written inside `extracted` | same |

Tests: `test_a_drawn_frame_decision_is_held_as_a_candidate_unless_promoted`, `test_the_default_settings_do_not_promote`, `test_an_untracked_cover_is_an_observation_by_default_and_a_record_when_promoted`, `test_a_mark_on_a_folder_revision_sheet_that_prints_another_revision_is_held_not_assigned`, `test_extraction_m2::test_a_scanned_transmittal_is_read_off_its_ocr_and_a_receipt_is_not_an_approval` (default -> observation, promoted -> record).

### 7.2 The normal route, traced and exercised

`document_processing.run` -> `read_task`/`read_pdf_full` -> `document_sync.process` (writes `project_documents.extracted`, mirrors, dependencies) -> `submittal_reader.check` (`run@480`, only when a submittal form changed) -> `project_state.reconcile_actions` (`@488`) -> `shop_drawings.reconcile` (`@496`). Isolated fixtures through this route (`tests/test_extraction_m2_review.py`, R5 block):

| Fixture | What normal processing wrote | What stayed |
|---|---|---|
| Manual submittal status `approved` + corrected BOQ line, then a re-read of a cover with a drawn frame (default path) | the reading (frame as candidate, status UR) | the submittal's manual status and revision (the map is not redrawn when no form changed: G-01 not triggered), the BOQ line's quantity and `origin = corrected` |
| Engineer-confirmed drawing revision, then a reading that says otherwise | the reading | the confirmed revision, its `confirmed_by_id` and `source = engineer` (`test_drawings_module` guards the same) |
| Folder R1 vs printed 00 with a consultant mark | record R1 (folder), `printed_revision 00`, status UR, candidate held | no status assigned to R1 |
| Mangled second cover (`-0042` vs `-FA-0042`) | two records kept apart (G-M2-6 unchanged) | no merge by guess |

**G-01 still exists**: `submittal_reader.check` -> `sync_register` overwrites an engineer status when it runs. Not changed here (M4). On the default path the decision inputs that reach it come from the same methods the accepted reader used; the M2 methods are candidates only.

### 7.3 Downstream deltas, population (124 documents, isolated, `evidence/r3__population_comparison.json`)

| Comparison | Identical | Status changed | Records added/removed | Explanation |
|---|---|---|---|---|
| default `.3` vs M2 submission `.2` | 84 | 24 (22 drawn-frame/highlight decisions held as candidates; 2 folder-revision holds: 120, 122 page 2) | 16 removed from `records` (11 fire-fighting covers, 3 OCR-mangled second covers of FA-0042 - 575/780/845, G-M2-6 - and 2 scanned transmittals 729/730) - all present as observations | the gate, off |
| promoted `.3` vs default `.3` | 86 | 22 restored | 16 restored | the gate, on |
| promoted `.3` vs M2 `.2` | all but 120/122 page 2 (held on both paths) | 2 | 0 | D-REV-2 only |

Status totals: M2 `.2` {UR 44, rejected 67, ANN 25}; `.3` default {UR 62, rejected 54, ANN 3} on 106 documents with records; `.3` promoted {UR 46, rejected 65, ANN 25}. Flags in the default run: `decision_method_unpromoted` 22, `decision_revision_unvalidated` 2, `reference_incomplete` 1 (687); no `decision_conflict` in the population.

### 7.4 Downstream deltas, clone repair with the corrected reader (default path)

| Project | Repair manifest | Tables changed (snapshot) | Documents with changed records, all sources | Roles changed | States / attempts / coverage after | Stored records vs the M2 submission's repair (`repair2`, `.2`) |
|---|---|---|---|---|---|---|
| EP-30088 (project 4) | run 1: 522 selected, 484 repaired, 6 skipped, 32 failed (D-EXT-10), 677.3 s; run 2 on the 32 failed rows: 32 repaired, 0 failed, 13.8 s | `document_dependencies` (174 of 306 rows), `project_documents` (516 of 525 rows); project fields changed: False | 165 of 525 (records 151 -> 201; form-reading records 9 -> 5, 4 documents with changed form-reading records) | 0 | {'fresh': 525}; attempts 0, stale 0; coverage {'null': 9, 'complete': 429, 'bounded': 87} | {'identical': 447, 'changed:status': 39, 'records_removed': 32, 'records_added': 4} |
| EP-30784 (project 1) | run 1: 356 selected, 349 repaired, 4 skipped, 3 failed (D-EXT-10), 707.6 s; run 2 on the 3 failed rows: 3 repaired, 0 failed, 0.3 s | `document_dependencies` (222 of 384 rows), `project_documents` (352 of 359 rows); project fields changed: False | 21 of 359 (records 393 -> 400; form-reading records 2 -> 2, 0 documents with changed form-reading records) | 0 | {'fresh': 359}; attempts 0, stale 0; coverage {'null': 7, 'complete': 295, 'bounded': 57} | {'records_removed': 3, 'identical': 329, 'records_added': 1, 'changed:status': 23} |

Snapshots: `scripts/business_snapshot.py` before/after (every business table; hash), `scripts/golden_records.py --from-db` before/after (form-reading records excluded, as the tool does) **and** a direct dump of every `project_documents.extracted` of both projects before/after (`repair_r3__records_before/after.json`: records of every source, form-reading records apart, attempt, stale, coverage, observations, flags). Flags after: project 4 {'decision_method_unpromoted': 44, 'reference_incomplete': 1}, project 1 {'decision_revision_unvalidated': 23}; observation kinds after: project 4 {'decision_unpromoted': 66, 'consultant_comments': 209, 'cover_untracked': 36, 'drawing_sheet': 47, 'decision_conflict': 1, 'transmittal': 2}, project 1 {'transmittal': 3, 'consultant_comments': 4, 'drawing_sheet': 50}. Status counts (all sources): project 4 {'UR': 143, 'rejected': 4, 'RR': 3, 'ANN': 1} -> {'UR': 95, 'rejected': 101, 'ANN': 5}; project 1 {'UR': 281, 'ANN': 35, 'rejected': 77} -> {'UR': 265, 'ANN': 43, 'rejected': 92}.

**Form-reading records.** Four EP-30088 material submittal forms (634, 639, 670, 674) held the AI form reading's own record in `records` (status RR on three, UR on one) because the page reader had read nothing off their pages. With bounded page discovery (D-EXT-9) the reader now reads the form's own submittal record on page 2 and the reply pages behind it, so by the existing stand-in rule (`document_sync.record_for_the_log`: the model's record stands for the form only when nothing was read off the page) the form-reading record leaves `records`; the row's mirrored reference / revision / status keep the form reading's values (`kept_form_reading`) and `extracted.form` is unchanged. For a consumer building the log from `records`, three forms move from RR to the page's UR. This is the existing rule applied to pages that are now visited, not a new rule; it is a business-visible delta and is flagged for the owner (option not taken here: keep the model's record beside the page's when the page record carries no decision). The five other forms keep their form-reading record. **Conflicts on real data**: one document of the clone (441, `FF-0047-03-COMMENTED-B.pdf`) carries a filled box on C and a drawn frame on B; it is stored as a `decision_conflict` observation with both marks and no decision, as R2 requires.

Reading the last column: `identical` = the same stored records as the M2 submission's repair; `changed:status` = a decision the gate holds back (candidate kept) or a folder-revision hold; `records_removed` = an untracked-discipline cover, an OCR-mangled second cover or a scanned transmittal held as an observation; `records_added` would be new records, none expected. Form-reading records (`source = submittal form`, written by the AI form reader, never by the parser) must not change under an extract-only repair with the model off.

### 7.5 What is and is not established

Established: the default path adds no decision method, cover kind or transmittal kind to `records` beyond the accepted reader's; every M2 observation is present, inspectable and gated; manual values and confirmed identities survive normal processing in the fixtures; the extract-only repair on the clone writes `project_documents` and `document_dependencies` only. Not established: behavioural equivalence of the promoted path with any consumer (that is the promotion decision, with this evidence); register-level before/after (Logs, Drawings) was again not executed on the clone; G-01 remains.


## 8. Correction 2 (Review 02): identity, precedence, clone

- **Reading identity** is (content sha256, `PARSER_VERSION`, `profile`). `document_processing.parser_current` checks all three; `_previous_sha`, the known-content map, `read_task`'s unchanged shortcut and `_copy_reading` depend on it; the repair tool's `parser-outdated` selection includes a profile mismatch and `apply_row` writes the profile. A reading without a profile (every live reading) is not current under either profile: it is re-read when its file next changes or when the repair tool is run, never reused as a current reading. Engineer-confirmed values are not touched by a profile switch.
- **Withdrawn precedence** (a downstream delta): the reader no longer lets an OCR-read stamp override a ticked box or a printed status; the two are a conflict (status UR, both candidates). EP-30784's emergency lighting sample (BBY006-GME-SAR-EL-LI-0001: "Approved as Noted (B)" ticked, "(C) Revise & Resubmit" stamped) reads UR with `decision_conflict` where it read `rejected` before. Restoring the stamp's precedence needs an explicit owner policy.
- **Population deltas** (124 documents): Review 02 default vs Review 01 default {'identical': 124}; promoted vs default {'identical': 86, 'changed:status': 22, 'records_added': 16} (`evidence/r4__population_comparison_r4.json`, examples inside).
- **Clone repair** (fresh copy of the read-only clone, default profile, `evidence/r4__repair_r4_comparison.json`): tables changed are `project_documents` and `document_dependencies` only; roles changed 0; form-reading records and the top-level mirrors as in the table in `M2-REVIEW-RESPONSE.md` (Review 02); every observation stored as JSON (no serialisation failure).


## 9. Correction 3 (Review 03): retained records, mixed readings, the SAR case

- A bounded reading's carried records are **retained evidence**, not current observations: each keeps its source hash, parser, profile and time (or None), flagged `carried_unvisited` and, as its provenance says, `carried_unverified` / `carried_other_profile`; its decision is a candidate (method `retained`) until its bytes and profile are the current ones. `extracted.retained` summarises them. A reading with other-profile retained records is a mixed reading: `parser_current` is False, it is not reused for its hash nor copied to a duplicate file, the repair tool selects it; it is replaced when the row is next processed or repaired with a reader that visits the page or under the profile that read it.
- **SAR case (stamp over tick)**: old result `rejected` (the OCR stamp won implicitly), new result UR with `decision_conflict` and both candidates; the raw conflict is kept apart from any domain resolution, of which none is authorised; the owner decision needed is stated in the response. Affected consumer: the samples register built from records (UR instead of rejected for the R0 sample once re-read).
- Clone repair under the Review 03 writer: `evidence/r5__repair_r5_comparison.json` (the table in the response).
