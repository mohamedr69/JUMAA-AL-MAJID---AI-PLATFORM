# M1 - Acceptance Report: Data Requirements, Ownership & Current-State Map

Correction revision 2026-09-27 (evening, local) after Independent M1 Reviews 01 and 02 (both CHANGES REQUIRED; Review 02 left two bounded corrections, A generic-reference measures and B rejected-no-load semantics, plus counting consistency). Documentation and read-only inspection only.
Nothing in application code, tests, migrations, configuration, dependencies, launch scripts, business data, the historical audit package or the independent review files was changed.

## 1. Deliverables

| File | Content | sha256 (as generated in this revision) |
|---|---|---|
| `docs/milestones/M1/M1-DATA-OWNERSHIP.csv` | 155 field rows x 22 columns (schema of the task) | 3cfb923f74126d2011d7c9674e2177f9cac164590cd0e95eae38c075529669f6 |
| `docs/milestones/M1/M1-DATA-OWNERSHIP.md` | Reading guide, storage notation, override state, target authorities, per-module tables | 5201d0a67c3c778e12b91bbdeb7c916d2340fe6230fc4f761e2e290c45241e1a |
| `docs/milestones/M1/M1-CONSUMER-COVERAGE.md` | 22 module/subview rows -> field IDs; functionality state | 9b9e4a6f253d1a14a02871ddf59d6288152f46ee795794d7a15e54fb7c07b3af |
| `docs/milestones/M1/M1-DUPLICATION-AND-READ-SIDE-EFFECTS.md` | 14 read-time producers (counting unit: distinct producer, consumer paths listed inside), 9 duplicate-producer groups, inference paths, R1 section | aef61a03d29a90cde997982752366d1ff2c879fe563b031d8730bc47d7919507 |
| `docs/milestones/M1/M1-TARGET-OWNERSHIP-DECISIONS.md` | 15 decisions (14 SETTLED outright + D-08 SPLIT) with the status vocabulary SETTLED / IMPLEMENTATION CHOICE / DEFERRED / OPTIONAL REQUEST / GAP | 62882dad8a7c029e30b7cdc1fd1b969645f2f49e3fa5d37e2223a228851c2db7 |
| `docs/milestones/M1/M1-ACCEPTANCE-REPORT.md` | this file | - |
| `docs/milestones/M1/M1-REVIEW-RESPONSE.md` | one row per R1-R7: action, changed references, verification, limitation, disposition | - |
| `docs/milestones/M1/evidence/M1-QUERIES.sql` | every SELECT/PRAGMA used, with the date regex and the two date-metric definitions | see evidence folder |
| `docs/milestones/M1/evidence/M1-EVIDENCE.json` | timestamped results: population, counts, ID sets, schema, alembic, total_changes | see evidence folder |
| `docs/milestones/M1/evidence/M1-SOURCE-HASHES.json` | HEAD, git status, sha256 of every source file the documents cite (fresh, this revision) | see evidence folder |

Regenerating the five generated files gives a new timestamp line and therefore new hashes; the evidence JSON carries its own collection time.

## 2. Evidence snapshot

- Repository `ep-platform` (submodule of `dev`), branch master, HEAD `13eb73ce85e5ac55c14303efe9d8511090ace723`, unchanged since the first M1 revision and since the independent review.
- Working tree: the same dirty tree the review recorded (82 `git status --porcelain` lines, the last being `?? docs/milestones/`). This task added files only under `docs/milestones/M1/`. No reset, clean, stash or checkout was run.
- Source hashes: `evidence/M1-SOURCE-HASHES.json` lists the sha256 of every backend/frontend file cited by the six documents, hashed in this revision. The fourteen source files the review hashed (`M1-REVIEW-EVIDENCE.json`) can be compared against it directly. The original start/end manifests of the first revision lived in a session scratchpad and are not part of this package; that earlier no-change claim is therefore reported, not reproducible, and is superseded by the fresh hashes here.
- Database: `backend/ep_platform.db` (95,461,376 bytes), the `DATABASE_URL=sqlite:///./ep_platform.db` of `backend/.env` resolved from `backend/`. Opened with `?mode=ro` and `PRAGMA query_only=1`; journal mode wal; `total_changes` 0 (recorded in the evidence JSON). Whether the running service is bound to this exact file was not observed at runtime. Alembic head `a5b6c7d8e9f0`.
- Historical packages read only and unchanged: the earlier M1 audit (`~/.codex/.../M1/`, snapshot 2026-09-27T16:19:43Z) and Independent Review 01 (`~/.codex/.../master-roadmap/reviews/M1-review-01/`, 2026-09-27T17:07:57Z).

## 3. Counts with their units

| Measure (unit) | Value |
|---|---|
| Indexed documents (project_documents rows) | 890 |
| Documents with an extracted JSON payload | 881 |
| Documents with at least one extracted record | 367 |
| Extracted records (sum of `extracted.records` lengths) | 547 |
| Documents whose top-level `reference` is date-shaped (IDs) | 17: 434, 436, 437, 440, 444, 448, 452, 454, 457, 458, 459, 460, 461, 463, 578, 783, 848 |
| Documents with any date-shaped `extracted.records[*].reference` (IDs) | 18: the 17 above plus 687 (top-level reference is a real drawing number; its third record is `24-Mar-22`) |
| Distinct normalized paths of those 18 documents | 18 (no duplicate path; the earlier "duplicate path" explanation was wrong) |
| Generic reference `ICC-DLRC-SPM-SD-MEP`, three separate measures (document IDs; sets in the evidence JSON `generic_reference`) | top-level `reference` equals it exactly: 84; top-level `reference` starts with it: 91 (the 7 extra are 771-776 and 784, full drawing references such as `...-MEP-FA-0054`, NOT truncation defects); any `extracted.records[*].reference` equals it exactly: 86 (the earlier audit's measure; the 2 beyond the top-level set are 434 and 436, whose top-level reference is a date but whose records carry the generic string) |
| Current classification rows (superseded_at null) | 534 of 890; all `classify-2026-09-27.2` (ambiguous 17, hint 358, supported 134, unknown 25) |
| Documents with `extracted.parser_version` / `extracted.evidence` | 0 / 0 |
| Submittal status-history rows, all source `ai_check` | 12 (no manual row locally, so the G-01 overwrite is shown from code, not from a lost edit) |
| Current `part.current` design rules with `data.auto=true` | 29 of 29 |

The date regex, both date definitions and the three generic-reference queries are in `evidence/M1-QUERIES.sql`; the ID sets are in `evidence/M1-EVIDENCE.json` (collected 2026-09-27T18:02:39Z). Compared with the review snapshots (17:07:57Z and Review 02) no count moved. The earlier audit's 86 (16:19:43Z, `build_baseline.py`, key `documents_with_generic_reference_prefix`) is the embedded-exact measure despite its key name, and it reproduces exactly. Source scope matters: the top-level `reference` column is the first record's reference copied by `document_sync.process@326`; records inside `extracted.records` can differ from it (434 and 436) or carry a full reference the top level does not (none locally). An equal generic reference is a review indicator until the cover or sheet establishes the identity; a prefixed full reference is not a defect.

## 4. Coverage totals

| Measure | Count |
|---|---|
| Required modules covered | 12 of 12 |
| Module groups in the CSV | 22 (the 12 split by subview, plus cross-cutting Classification) |
| Fields (rows) | 155 (was 145; 10 rows added by splitting contracts: PRJ.status, BAT.design_inputs, BAT.quoted, BAT.selected, BAT.settings_effective, AMP.design_inputs, PWR.design_inputs, FWS.edits, FWS.material_choice, MAT.added_item, CMP.statement_inputs, CMP.approval, EQC.engineering_rule, EQC.part_current_rule; removed BAT.quoted_selected, BAT.settings_overridden, EQC.design_rule (IDs no longer exist)) |
| Fields by status | CURRENT 138; READ_SIDE_EFFECT 13; DUPLICATE_DERIVATION 2; LEGACY 1; NOT_IMPLEMENTED 1 |
| UNVERIFIED cells | 0 (every earlier UNVERIFIED cell was settled statically or restated as a gap) |
| Shared canonical facts in the ownership guide | 9 |
| Duplicate-producer groups | 9 |
| Read-time producers on ordinary reads (unit: distinct producer; every consumer path listed inside its producer's row) | 14 (the battery calculation with six consumer paths and the floor-schedule ingest with two are one row each) |
| Target decisions | 15 = 14 SETTLED outright + D-08 SPLIT (part (a) current requirement metadata SETTLED, part (b) matrix feature DEFERRED); implementation choices inside D-04, D-05, D-07; optional request inside D-10; gaps G-01, G-02, G-03, G-04 |
| Consumer rows | 22 |
| Evidence references validated | see M1-REVIEW-RESPONSE.md R7 (AST definition-line check over all seven documents) |

## 5. Checks performed

1. Static tracing of every function chain the review named: manual submittal writer, `check` -> `sync_register` (assignment, revision removal and deletion branches, and its four callers), `get_statement` -> `_statement_out` -> `recheck` and its other callers, `create_project` / `update_project` / `ProjectDetailsIn`, `fill_battery_currents` and every `save_rule_version` caller, `_battery_calculation` and `calc_integrity.panel_inputs`, `latest_map` / `check` map storage and the map GET's `changed=False`, `get_submittal_reply` -> `_consultant_words` / `_reply_context`, `drawing_detail`, every `_catch_up` caller, `_read_from_folder` (material choice loss), `datasheet_currents` imports (no model).
2. Read-only SQLite queries in `evidence/M1-QUERIES.sql`, results in `evidence/M1-EVIDENCE.json`.
3. Strict reference validation: every `path::Symbol@line` and `module.symbol@line` in the seven documents parsed against the source with `ast` (definition line must equal the cited line; class attributes checked inside the class body; TS declarations within two lines); markdown links resolved; CSV parsed (22 columns, unique IDs, no blank cells); consumer IDs resolve; no field ID mentioned in any document is missing from the CSV.
4. Source hashes recorded for every cited file (`evidence/M1-SOURCE-HASHES.json`).

## 6. Checks not performed

- Application tests were not run; the future regression cases listed under MS.status and in the response are proposals, not executed tests.
- No endpoint called, no job enqueued, no module imported, no sync/processing/extraction/OCR/AI/repair/backfill/reconciliation/migration/restart.
- Runtime binding of the live service to the inspected database file was not observed.
- The historical clone results quoted in the M2 handoff (repair runs, shadow comparison) come from `docs/DOCUMENT_CLASSIFICATION_V2.md` section D4; their manifests are in a session scratchpad, not in this package, so they are **reported, unverified** here.

## 7. Reconciliation with the earlier M1 audit (16:19:43Z package)

Already found by the earlier audit and only restated here: read-time processing on BOQ ensure, battery GET persistence and the mount-time fill, floor-schedule and IFC-comparison sync, compliance specification discovery, drawings/logs catch-up, datasheet library refresh, state/actions reconciliation, project open (`get_project`), Home/readiness/materials reaching the battery calculation; legacy `/logs` drawings and the drawings register as separate consumers; classification coverage 534/890 with 356 unassessed rows on EP-30784; parser_version and evidence absent on all rows.

Extensions in this map: package planning and package build also reach the battery calculation (submittal router lines 1191-1194 and 1357); the compliance statement recheck on GET/approve/export (found by Independent Review 01); the reply editor's PDF page read for scanned replies; the register overwrite by `sync_register` (G-01); the floor-schedule material choice lost on re-read; `list_intake` returning every project document; the design-rule writers behind the mount-time fill; project lifecycle as a non-DRF fact; field-level rows for Cause & Effect, Samples and Estimation.

Corrections to the first M1 revision (this package's own earlier text): the "17 not 18, duplicate path" explanation was wrong (the difference is top-level vs embedded scope, document 687); 547 is a record count, not a document count; engineer-status protection on submittals was described as existing (it is not); `DOC.parser_version` was cited as a model attribute (it is a JSON key); `PRJ.status` was attributed to DRF extraction; `drawing_detail` was listed as a catch-up caller; the reply chain was written as `_reply_context -> _consultant_words`; several `document_control` line numbers were stale.

## 8. Decisions and gaps

- Product decisions required before M1 acceptance: **none**.
- Implementation choices deferred to the implementing milestone: D-04 drawing attribution attribute, D-05 effective-system persistence, D-07 sample record shape (M4).
- Deferred feature: D-08(b) the Cause & Effect matrix (absent; the current requirement label is owned by the Requirements service).
- Optional request, inactive: D-10 pure recompute on GET.
- Gaps carried to M3/M4: G-01 `sync_register` overwrites engineer submittal status; G-02 no classification confirmation endpoint; G-03 no sample override record; G-04 the automatic current fill re-sets a host (included-in-module) part to no-load even after an engineer rejected the no-load setting, because the rejection test carries `and not host` (`fill_battery_currents@319`, line 355) - to be confirmed as intended or fixed in M4; documented, not changed.

## 9. Verdict

READY FOR INDEPENDENT M1 RE-REVIEW: every R1-R7 finding has an evidence-backed response (M1-REVIEW-RESPONSE.md); no current-state protection, owner or storage is described as implemented without a traced write path; the reproducible SQL, ID sets and fresh hashes are in `evidence/`; the strict reference validation passed on the final files (result recorded in the response, R7). Acceptance and permission to advance remain with the independent review workflow.

---

## 10. M2 handoff (Extraction Reliability)

Scope note: M2 concerns extraction, not classification or tab migration. Everything below is evidence at recorded versions, not acceptance. Clone results are **reported, unverified** (their artifacts are not in this package).

### Confirmed extraction defects (local database, readings under the old parser)

| Id | Defect | Evidence (units stated) | Fix status in the dirty tree |
|---|---|---|---|
| F1 | Date-shaped strings read as references | 18 documents with a date-shaped embedded record reference (17 also at top level; 687 embedded only), all EP-30088; ID sets in `evidence/M1-EVIDENCE.json`. The Golden/repair candidate set is the 18, not the 17 | `document_control.is_date_shaped`, `reference_candidates`; repair tool selection `date-references` (selects on the row reference: 687 must be added by id); tests `test_extraction_repair.py` (not run in M1) |
| F2 | Reference truncated at the system infix (`ICC-DLRC-SPM-SD-MEP`) | Review population = the 86 documents with an embedded record reference equal to the generic string (84 of them also at top level; 434 and 436 only in records). The 7 further documents whose top-level reference merely starts with the string (771-776, 784) are full drawing references and are not candidates. ID sets in `evidence/M1-EVIDENCE.json`. EP-30088 register collapsed 34 drawings into 7 | `_SHEETS` slash grammar, `submission_cover`; repair selection `truncated-references` selects on the row reference, so 434 and 436 must be added by id; clone result 34 distinct references (reported) |
| F3 | Reply folded to the wrong revision / listed sheet | `combine@1247` reply fold by sheet overlap; 18 open reply_unmatched issues (source ai) locally | `_answers@1155` sheet-number overlap; `ControlledDocument.listed` |
| F4 | Decision stamps on annotations (Stamp/Square/Ink/Highlight) not read | EP-30088 covers 434/436/437 framed "C" | `annotated_decision@641`, BOX_VERSION box-2 |
| F5 | Word transmittals re-read by the PDF parser emptied records (tool defect) | found on the clone (reported) | repair tool skips role transmittal |
| F6 | A signed transmittal receipt is not a consultant decision | sample status logic (SMP.status) | not a code change: rule recorded; needs Golden labels |
| F7 | `looks_like_a_spec@136` names 110 EP-30784 shop drawings as specs (role spec) | compliance legacy 144 vs content 14 on the clone (reported) | open; affects role, therefore compliance discovery |
| F8 | Region OCR now reads "(C) Revise & Resubmit" on 14 EP-30784 R1 sheets that the stored readings call under review | clone re-read (reported) | needs engineer confirmation before any live re-read |
| F9 | Any live re-read or repair that triggers `submittal_reader.check` rewrites the material submittal register from the map (G-01) | traced in MS.status | evaluate every repair/backfill on isolated data until the writer is guarded (M4) |

### Source evidence available / missing

- Available locally: 890 registry rows, 881 with extracted JSON, 367 with records (547 records) under the old parser; cached OCR text per content sha (page cache); form readings (DocumentReading) for the forms the model read; 12 ai_check status-history rows.
- Missing: no `parser_version`/`evidence` on any live row; no Golden Truth labels checked against original documents (only the 22 audit-confirmed type labels, and those were reviewed for type only); no engineer confirmation of EP-30784 R1 decisions; zero floor schedules, IFC drawings, compliance statements, proposed materials or manual submittal edits locally, so those paths have no local fixture beyond tests; clone manifests not in the repository.

### Existing fixes and tests an M2 reviewer should inspect first

- `backend/app/services/document_control.py` (PARSER_VERSION `parse-2026-09-28.1`), `content_evidence.py` (`evidence-2026-09-28.2`), `document_sync.py` / `document_processing.py` plumbing (`parser_current@197` re-read), `scripts/repair_extraction.py` (dry-run default, manifest rollback), `scripts/consumer_shadow.py`.
- Tests: `backend/tests/test_extraction_repair.py`, `test_classification_evidence.py`, `test_repair_tool.py`, `test_document_routing.py`, plus `test_document_control.py`, `test_document_sync.py` (one pre-existing failure: `test_a_read_that_fails_keeps_the_previous_result_and_is_marked`). None run in M1.
- Docs: `docs/DOCUMENT_CLASSIFICATION_V2.md` sections D1-D6 (clone results, live procedure not executed).

### Cases needing Golden labels (independently checked originals)

- The 18 date-reference documents (IDs above), including 687 and the 11 fire-fighting covers 440-463 (no record expected).
- EP-30784 FAVE R1 sheets (14) whose stamp the current OCR reads as C.
- The CRS reply 868 with the listed-sheet reference `ICC-DLRC-SPM-SD-MEP/FA-100,101,102,104&105`.
- Files printing another project's code (EP-30088: 24 print EP-30058, 5 print EP-29484; EP-30784: 6 print EP-30627, 3 print EP-29941) - counts reported from the clone flags.
- Transmittal/SAR pair 729/730 and the six Word transmittals.
- `COMPLIANCE STATEMENT.pdf` (620) whose pages read as specification sections.
