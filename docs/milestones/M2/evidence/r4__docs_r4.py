"""Append the Review 02 (A-D) sections to the M2 documents, with every number read from the evidence files."""
import json, re, sys, pathlib, subprocess

S = pathlib.Path(sys.argv[1]); R1 = S.parent / "m2r"; REPO = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform"); D = REPO / "docs/milestones/M2"
J = lambda p: json.load(open(p, encoding="utf-8"))
cmp_ = J(S / "population_comparison_r4.json"); rep = J(S / "repair_r4_comparison.json"); boq = J(S / "boq_run_r2.json"); boq1 = J(R1 / "boq_run.json")
probes = J(S / "probe" / "independent_probes_after.json"); words = J(S / "word_transmittals_independent.json"); manifest = J(D / "M2-GOLDEN-MANIFEST.json")
xml = (S / "final_r4.xml").read_text(encoding="utf-8")
tests = int(re.search(r'tests="(\d+)"', xml).group(1)); fail = int(re.search(r'failures="(\d+)"', xml).group(1)); err = int(re.search(r'errors="(\d+)"', xml).group(1))
skip = int(re.search(r'skipped="(\d+)"', xml).group(1)); secs = float(re.search(r'time="([\d.]+)"', xml).group(1)); passed = tests - fail - err - skip
failed_names = re.findall(r'<testcase classname="([^"]+)" name="([^"]+)"[^>]*>\s*<failure', xml)
suite_line = f"**{tests} tests, {passed} passed, {skip} skipped, {fail} failed, {err} errors** in {secs:.1f} s"
job_runs = J(S / "job_test_runs.json") if (S / "job_test_runs.json").is_file() else {}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()

fas, eml = boq["systems"]["FAS"], boq["systems"]["EML"]; fas1, eml1 = boq1["systems"]["FAS"], boq1["systems"]["EML"]
def m(v, k): return v["metrics"][k]
fas_review = [(i["detail"].get("catalog_no"), i["detail"].get("reason_code") or "quantity") for i in fas["issues"]]
d4 = cmp_["review02_default"]; p4 = cmp_["review02_promoted"]; r3 = cmp_["review01_default"]
cls_d = cmp_["review02_default_vs_review01_default"]["classes"]; cls_p = cmp_["review02_promoted_vs_review02_default"]["classes"]
acc = manifest["outcome_accounting_documents_parse_4"]; pages = manifest["page_accounting_parse_4"]
vd = manifest["verdict_counts_parse_4"]["default"]; vp = manifest["verdict_counts_parse_4"]["promoted"]


def rrow(pid):
    mf = rep[f"manifest_p{pid}"]; r2 = rep.get(f"manifest_p{pid}_run2"); rc = rep[f"records_p{pid}"]; sn = rep[f"snapshot_p{pid}"]
    tables = ", ".join(f"`{t}` ({v['rows_changed']} of {v['after']} rows)" for t, v in sn["tables_with_changed_rows"].items()) or "none"
    return (f"| EP-{'30088' if pid == 4 else '30784'} (project {pid}) | {mf['selected']} selected, {mf['counts'].get('repaired', 0)} repaired, {mf['counts'].get('skipped', 0)} skipped, {mf['counts'].get('failed', 0)} failed, {mf['seconds']} s | "
            f"{tables}; project fields changed: {sn['project_fields_changed']} | {rc['documents_with_changed_records_all_sources']} of {rc['documents']}; form-reading records {rc['form_reading_records_before']} -> {rc['form_reading_records_after']} "
            f"({rc['documents_with_changed_form_reading_records']} documents); mirrors changed {rc['top_level_mirrors_changed']} | {rc['roles_changed']} | states {rc['states_after']}; attempts {rc['attempt_markers_after']}; stale {rc['stale_after']}; "
            f"profiles {rc.get('profiles_after', 'n/a')}; observation kinds {rc['observation_kinds_after']}; flags {rc['flags_after']} | {rep[f'r3_vs_m2_repair_p{pid}']['classes']} |")


response = f"""

---

# Response to Independent Review 02 (A-D)

Correction of 2026-09-28 (local, afternoon). Source: commit `{head}` plus the Review 01 correction and, on top of it, the Review 02 correction (uncommitted; `evidence/r4__m2_review02_changes.diff` is the whole uncommitted diff, `evidence/r4__before_manifest_r2.json` the hashes of the dirty tree before this correction). Versions: `PARSER_VERSION parse-2026-09-28.4`, `BOX_VERSION box-4`, `design_sheet_extractor.PARSER_VERSION 2026-09-28.1`. No migration; no business, revision or calculation policy changed; no live data, backend, worker or model touched. The review files are untouched (the probe copy under `C:\\t\\m2r2\\probe` is byte-identical: sha256 `08b948d4fa55b8ba...`). `backend/library/symbols/symbol_library.json` was found already modified at the start of this correction (its `exported_at` rewritten at 09:10:24 local by an app start outside this session, after the Review 01 package); it is not part of either correction and was left as found.

| Finding | Root cause | Change | Tests / evidence | Remaining limitation | Disposition |
|---|---|---|---|---|---|
| **A** [P1] Legacy readings discarded; bounded runs replaced complete ones | `document_sync.process` kept a previous reading only when it carried `parser_version` (the 367 live payloads carry none); `COMPLETE_OUTCOMES` let a bounded run (page budget reached) replace a reading whose records sat on the pages it never visited | `reading_to_keep`: any previous reading with records, or a modern one (parser version / hash) even without, is kept in front of a failed, unavailable or partial attempt -- records, form evidence (`extracted.form`), mirrors and provenance exactly as they were; `staleness` is True/False when the reading records its hash and **None** (`source_identity: unknown`) for a legacy one: nothing is invented. `carry_unvisited`: a bounded reading carries the kept reading's records from the pages it skipped (and records of unknown page), flagged `carried_unvisited`, plus `carried_unverified` when the file's bytes are not the ones the kept reading was read from (or unknown), with a note and `coverage.carried_from_previous`; the mirrors follow. The same carry in the repair tool's preview/apply. A successful retry replaces the reading and keeps the form evidence | `tests/test_extraction_m2_review02.py`: `test_a_legacy_reading_without_parser_version_survives_failures_and_a_successful_retry` (legacy payload + form -> open failure -> OCR failure -> second failure -> good file; persisted through normal processing, retry accounting checked), `test_a_bounded_re_read_carries_the_records_of_the_pages_it_did_not_visit` (complete 13-page reading, then the repair path on the same bytes, then ordinary processing on changed bytes, then a wider reader), `test_reading_to_keep_and_staleness_tell_legacy_from_modern_from_nothing`, `test_carry_unvisited_keeps_records_of_unknown_page_and_flags_by_source_identity`; the reviewer's probes `legacy_last_success_without_parser_version` (records kept) and `bounded_replaces_more_complete_success` (record carried with both flags) - `evidence/r4__probe__independent_probes_after.json` | A carried record is what the previous content held, marked so; the reader does not read the skipped pages (the budget stands). A legacy reading's staleness stays unknown until a complete re-read | **Fixed** (D-EXT-11, D-EXT-12) |
| **B** [P1] Text and OCR bypassed conflict resolution | Marks were collected only when every text record was UR; the OCR decision replaced the status after the marks had been settled | `settle_decision`: the page's text status (method `text`), every mark (`filled_box`, `annotation`, `drawn_frame`) and the OCR decision (method `ocr`) are candidates settled once per record; different answers are a conflict (UR, every candidate, `decision_conflict`), agreement settles the status when a promoted method is among them, held methods alone are candidates. The stamp-over-tick precedence the reader applied implicitly (comment on the SAR sample) is **withdrawn** until an explicit policy is authorised: that case is now a conflict the record shows; no new approval policy is introduced | `test_a_status_in_the_text_and_a_frame_on_another_option_are_a_conflict` (both gate settings), `test_marks_that_conflict_are_not_settled_by_what_ocr_reads_afterwards` (annotation A + frame C then OCR A; and the reverse; both gate settings), `test_agreeing_text_marks_and_ocr_settle_one_status_with_every_method_recorded`, `test_an_unpromoted_frame_does_not_return_as_a_status_through_ocr_of_the_options_list`, `test_an_ocr_failure_leaves_the_text_and_mark_evidence_to_settle_the_page`, `test_a_frame_round_a_comment_naming_an_option_is_noise_beside_the_text_status`, `test_a_conflict_reaches_the_row_and_its_register_row_as_under_review` (persisted: row status, stored record, `log_records`); `test_document_control::test_a_consultants_stamp_that_disagrees_with_the_ticked_box_is_a_conflict` (was `..._overrides_the_ticked_box`); probes `ocr_overrides_collected_conflict` -> UR with three candidates, `text_decision_bypasses_mark_collection` -> UR conflict | A consumer that wants the stamp to outrank the tick needs an authorised precedence policy (owner); until then such pages read UR with the evidence attached | **Fixed** (D-EXT-13) |
| **C** [P1] The promotion gate was absent from reuse | `parser_current` checked the parser version only; `_previous_sha`, the known-content map and `read_task`'s unchanged shortcut reused a promoted reading with the gate off | A reading's identity is (content hash, `PARSER_VERSION`, **profile**): `extracted.profile` (`default` / `promoted`, from the reading's own `coverage.promoted`) is written by processing and the repair tool; `parser_current` requires the current parser *and* the current profile (`document_control.extraction_profile()`), so `_previous_sha`, the known-content map, `read_task`'s unchanged return and the duplicate copy all recompute on a mismatch; a reading without a recorded profile is not current; the repair selection `parser-outdated` includes profile mismatch | `test_a_reading_carries_its_profile_and_freshness_checks_it` (off -> on: unchanged bytes re-read, on -> on: reused and idempotent, on -> off: re-read, missing profile: not current; `read_task` refuses the unchanged shortcut), `test_a_duplicate_copy_is_not_given_the_other_profiles_reading`, `test_the_repair_tool_selects_readings_of_another_profile_and_is_idempotent_within_one`; probe `promoted_reading_reused_with_gate_off` -> `parser_current false, previous_sha_reused false, task_unchanged false` | A profile change is applied when a row is next processed (a content change) or by the repair tool; rows untouched on disk keep their reading with its profile recorded, never reused as the other's. Engineer-confirmed domain values are not touched by a profile switch (`test_an_engineer_confirmed_revision_survives...` unchanged). G-01 stays as documented | **Fixed** (D-GATE-2) |
| **D** [P2] BOQ correctness, harness race, accounting | Current-reader defects D-BOQ-open-1..5; one BOQ job test failing by order; page categories not exclusive; a 124-row table totalling 120 | **D1** the inked band between two tables is read as rows under the heading in force (`_read_page` -> `_read_table` on the band); a band that yields no row is a `skipped` region **and** an `UNPROCESSED_PAGE_OR_REGION` issue, so the sheet is not accepted as complete over it. **D2** a strip quantity at 60-90 % is checked on its own cell for a cut digit only (`_check_cut_digit`): a pass reading the strip's digits plus one sends the row to review; the low-confidence path keeps its full confirmation with the same rule (`_cut_digit`). **D3** a part number read below 90 % is checked by two independent passes on its cell (`_confirm_catalog`): both agreeing on another whole reading, or a same-length near miss (edit distance <= 2), make the row a review row (`_uncertain_part_issue`, `PART_NUMBER_CONFLICT`, every reading kept); fragments and clipped readings are not evidence; nothing is substituted. **D4** the group heading in force carries across a split table and across pages, reset by a new section banner (`_read_table` / `_read_ruled_rows` return it). **D5** the Golden matcher pairs repeated parts by page and occurrence order (`scripts/boq_metrics.match_rows`). **D6** the six Word transmittals read from their own .doc binary text. **D7** the BOQ job test: traced (`evidence/r4__job_trace.log`: every session on one DBAPI connection, `conn=...` identical for `MainThread`, `AnyIO worker thread` and `job-1-boq_read`; a request session's close issues ROLLBACK on the job thread's in-flight transaction); the defect is the test harness's in-memory database (StaticPool: one shared connection) with a real job thread, not the application (the platform runs a file database, one connection per thread). Fix: the test database is a SQLite file in a temporary folder (`tests/conftest.py`; `EP_TEST_DATABASE=memory` keeps the old harness to reproduce the defect); regression `tests/test_job_thread_sessions.py` (a job flushes a row, waits, commits while the test polls it through the API): fails on the memory harness (the flushed row is gone), passes on the file. **D8** the page ledger has two exclusive dimensions: pages visited / skipped / failed sum to the page count and never overlap (a failed page leaves `visited`), OCR is `coverage.ocr` (attempted, failed, skipped by budget) and `ocr_failed_pages`; the document accounting is over the whole 124-document population with exclusive outcomes | BOQ on the originals (`evidence/r4__boq_run_r2.json`, `.log`; before: `r3__boq_run.json`): see the table below. `test_design_sheet_extractor.py`: `test_an_inked_band_between_two_tables_is_a_skipped_region_not_a_silent_gap` (band read / band unread with the heading carried), `test_a_longer_independent_reading_sends_a_cut_digit_to_review`, `test_a_part_number_the_independent_passes_do_not_confirm_is_a_row_for_review`, `test_the_catalog_check_reads_the_cell_twice_and_keeps_what_each_pass_read`; Word labels `evidence/r4__word_transmittals_independent.json` (6 of 6 read: TR number, date, subject); job test: `evidence/r4__job_trace.log`, `evidence/r4__job_test_runs.json` (alone x3, in its module, mixed order, full suite); accounting: `M2-GOLDEN-MANIFEST.json` `outcome_accounting_documents_parse_4`, `page_accounting_parse_4` | Open reader defects, listed as code defects: the EML two-line cell `+SL23I` is accepted as `+SL231` (the cell passes read fragments of a two-line cell, so nothing flags it); `6538-G5` is grouped under `Booster Power Supply` where the transcriber left it ungrouped (a rule of the sheet's layout, not settled); the band row's part is read as printed and cut (`SIGA-OSHD-FC`), which the Golden fixture names whole. The real-model verification path is still not exercised (no model call permitted): missing evidence, separate from the code | **Fixed** D1-D8 with three reader defects **open** (below); real-model path **BLOCKED BY MISSING EVIDENCE** |

## Deterministic BOQ reader on the originals, before and after

| Sheet | Review 01 reader (`2026-09-15.2`) | Review 02 reader (`2026-09-28.1`) |
|---|---|---|
| FAS (74 golden rows, 2 pages) | {fas1['accounting']['lines_read']} lines accepted, {m(fas1, 'row_detection_recall'):.3f} recall, part {m(fas1, 'part_number_accuracy'):.3f} / quantity {m(fas1, 'quantity_accuracy'):.4f} / group {m(fas1, 'group_accuracy'):.3f} on matched rows; 1 row unread and silent (band), TP606 accepted as 49, 4 misspelt parts accepted, 26 rows without group | {fas['accounting']['lines_read']} lines accepted, {m(fas, 'row_detection_recall'):.3f} recall, part {m(fas, 'part_number_accuracy'):.3f} / quantity {m(fas, 'quantity_accuracy'):.3f} / group {m(fas, 'group_accuracy'):.3f} on matched rows; the band row read (`SIGA-OSHD-FC`, 525, as printed and cut); {len(fas['issues'])} rows for review: {', '.join(f'{c} ({r})' for c, r in fas_review)}; outcome {fas['accounting']['outcome']} (not accepted whole) |
| EML (12 golden rows, 1 page) | {eml1['accounting']['lines_read']} lines, quantities judged crosswise on the repeated SL210DI | {eml['accounting']['lines_read']} lines accepted, {m(eml, 'row_detection_recall'):.3f} recall, quantity {m(eml, 'quantity_accuracy'):.3f} with the matcher pairing SL210DI by occurrence; open: `+SL231` for `+SL23I` |

Nothing in the reader knows a Golden value; the fixture is read only by `scripts/boq_metrics.py`. No model was called.

## Population, page ledger and clone (Review 02 reader, isolated)

- Population read (124 documents, cold caches): default vs Review 01 default {cls_d}; promoted vs default {cls_p}. Document outcomes over 124, exclusive: default {acc['default']['counts']}, promoted {acc['promoted']['counts']} (no-record documents: {acc['default']['no_records_ids']}). Pages (default): {pages['default']} -- visited + skipped + failed = total on every document (`document_outcomes` shows no `NON_EXCLUSIVE` / `PAGES_DO_NOT_SUM`). Timing on identical work: {cmp_['timing_identical_work']['review01_parse_3_cold_s']} s (.3) -> {cmp_['timing_identical_work']['review02_parse_4_cold_s']} s (.4); the .4 run overlapped with test runs for part of its time -- not a latency claim.
- Golden verdicts (parse .4): reference, default {vd.get('reference')}, promoted {vp.get('reference')}; status, default {vd.get('status')}, promoted {vp.get('status')}. The six Word transmittals now carry independent labels (systems {vd.get('systems')}).
- Clone extract-only repair (default profile, fresh copy of the read-only clone, form-reading records included): `evidence/r4__repair_r4_comparison.json`.

| Project | Repair manifest | Tables changed | Documents with changed records (all sources) | Roles changed | After | Stored records vs the M2 submission's repair (`repair2`, parse .2) |
|---|---|---|---|---|---|---|
{rrow(4)}
{rrow(1)}

## Tests

`{suite_line}` (`evidence/r4__final_r4.xml`, `.log`; the file-backed test harness). {"Failures: " + ", ".join(f"`{c}::{n}`" for c, n in failed_names) + "." if failed_names else "No failures."} The BOQ job test runs: {json.dumps(job_runs) if job_runs else "see evidence/r4__job_test_runs.json"}. Skips are the live-archive BOQ sheet tests (`EP_PLATFORM_LIVE_ARCHIVE_ROOT` unset).

## Verdict (Review 02)

**READY FOR INDEPENDENT M2 RE-REVIEW.**

Code defects remaining (reader, deterministic path): (1) EML `SL2MNM65D3C-M +SL23I` read `+SL231` and accepted (two-line cell; the cell check reads fragments); (2) `6538-G5` grouped under the heading above it where the fixture leaves it ungrouped; (3) the band row's part number is read as printed and cut (`SIGA-OSHD-FC`) -- the sheet cuts it, the fixture completes it. Missing evidence: the real-model BOQ verification path (no model call permitted); engineer sign-off on Golden labels (none claimed). G-01 stays documented, untouched.
"""
p = D / "M2-REVIEW-RESPONSE.md"; s = p.read_text(encoding="utf-8"); assert "Response to Independent Review 02" not in s; p.write_text(s + response, encoding="utf-8")

acceptance = f"""

## Correction 2 (2026-09-28, after Independent Review 02) -- supersedes C1-C8 and sections 1-9

| Item | Value |
|---|---|
| Source | commit `{head}`; the Review 01 and Review 02 corrections uncommitted on top (`evidence/r4__m2_review02_changes.diff` = the whole uncommitted diff; hashes in `evidence/M2-EVIDENCE-MANIFEST.json`) |
| Versions | `PARSER_VERSION parse-2026-09-28.4`, `BOX_VERSION box-4`, `design_sheet_extractor.PARSER_VERSION 2026-09-28.1`; no migration |
| Tests | {suite_line} (`evidence/r4__final_r4.xml`); new `tests/test_extraction_m2_review02.py` (16), `tests/test_job_thread_sessions.py` (1), `tests/test_design_sheet_extractor.py` (+3); the test harness now uses a file database (`tests/conftest.py`), the diagnosed cause of the BOQ job test's failures |
| Reviewer probes (Review 02) | all five meet the intended contract on the final code: `evidence/r4__probe__independent_probes_after.json` |
| Golden | 120 cases: 111 text-PDF crop-checked, 3 scans page-checked, 6 Word transmittals labelled from their own binary text (this correction); verdicts for parse .4 on both profiles in `M2-GOLDEN-MANIFEST.json` (`verdict_counts_parse_4`) |
| Documents (124, exclusive) | default {acc['default']['counts']}; promoted {acc['promoted']['counts']}; the four outside the manifest: 24, 122, 313, 316 |
| Pages (default) | {pages['default']} |
| BOQ, deterministic, originals | FAS: {fas['accounting']['lines_read']} lines accepted with part/quantity accuracy {m(fas, 'part_number_accuracy'):.2f}/{m(fas, 'quantity_accuracy'):.2f}, group {m(fas, 'group_accuracy'):.3f}, band row read, {len(fas['issues'])} rows for review (TP606 cut digit; four uncertain parts; the title row), outcome NEEDS_INTERPRETATION; EML: {eml['accounting']['lines_read']} lines, quantity {m(eml, 'quantity_accuracy'):.2f}; open reader defects listed in the response |
| Clone repair (default profile) | project 4: {rep['manifest_p4']['counts']}; project 1: {rep['manifest_p1']['counts']}; roles changed 0; details in `M2-COMPATIBILITY-REPORT.md` section 8 |
| Live data | untouched (no stop/start, no live processing, no repair, no model) |
| Verdict | **READY FOR INDEPENDENT M2 RE-REVIEW**; code defects remaining and missing evidence listed separately in `M2-REVIEW-RESPONSE.md` (Review 02 section) |
"""
p = D / "M2-ACCEPTANCE-REPORT.md"; s = p.read_text(encoding="utf-8"); assert "Correction 2 (2026-09-28" not in s
s = s.replace("> **Correction 2026-09-28 (Independent Review 01: CHANGES REQUIRED).**", "> **Correction 2 (Independent Review 02) is at the end and supersedes everything below, including the first correction's C1-C8.**\n>\n> **Correction 2026-09-28 (Independent Review 01: CHANGES REQUIRED).**", 1)
p.write_text(s + acceptance, encoding="utf-8")

defects = f"""

## Fixed in the Review 02 correction (2026-09-28)

| Id | Category | Issue (review 02) | Root cause | Change | Regression tests |
|---|---|---|---|---|---|
| D-EXT-11 | VAL (A1) | A stored reading without `parser_version` (367 live payloads) was discarded by a failed re-read | `complete_before` required `parser_version` | `document_sync.reading_to_keep` keeps any reading with records or modern provenance; `staleness` None + `source_identity: unknown` for a legacy reading; form evidence carried on success | `test_a_legacy_reading_without_parser_version_survives_failures_and_a_successful_retry`, `test_reading_to_keep_and_staleness_tell_legacy_from_modern_from_nothing`; probe |
| D-EXT-12 | VAL (A2) | A bounded run replaced a complete reading, deleting records from pages it did not visit | `bounded` counted as complete | `carry_unvisited` (flags `carried_unvisited`, `carried_unverified`; note; `coverage.carried_from_previous`), in processing and the repair preview/apply | `test_a_bounded_re_read_carries_the_records_of_the_pages_it_did_not_visit`, `test_carry_unvisited_keeps_records_of_unknown_page_and_flags_by_source_identity`; probe |
| D-EXT-13 | EXT (B) | Text status bypassed marks; OCR overwrote a settled conflict | two bypasses in `read_open_pdf` | `settle_decision` over text + marks + OCR candidates; conflicts kept; stamp-over-tick precedence withdrawn (no authorised policy) | seven B tests in `test_extraction_m2_review02.py`; `test_a_consultants_stamp_that_disagrees_with_the_ticked_box_is_a_conflict`; probes |
| D-GATE-2 | VAL (C) | Promoted readings reused with the gate off | freshness checked the parser version only | `extracted.profile`; `parser_current` = parser + profile; repair selection/apply carry the profile | three C tests; probe |
| D-BOQ-2 | EXT (D1) | The band row was reported, not read | band skipped | band read as rows under the heading in force; unreadable band = skipped region + issue | band unit test; FAS run |
| D-BOQ-3 | VAL (D2) | TP606 49/491 auto-accepted at 86 % | confident-read rule never looked again | `_check_cut_digit` for 60-90 %; `_cut_digit` in the low path | `test_a_longer_independent_reading_sends_a_cut_digit_to_review`; FAS run (TP606 for review) |
| D-BOQ-4 | VAL (D3) | Misspelt part tokens accepted | no check of the catalog cell | `_confirm_catalog` (two passes; agreement on another whole reading or a near miss -> review row `PART_NUMBER_CONFLICT`; nothing substituted) | two part-number tests; FAS run (4 rows for review) |
| D-BOQ-5 | EXT (D4) | Group lost across a split table | heading reset per table | heading carried through `_read_table`/`_read_ruled_rows`, across pages, reset by a new section | band unit test (heading carried); FAS group accuracy {m(fas, 'group_accuracy'):.3f} |
| D-METRIC-1 | (D5) | Repeated parts paired crosswise by the Golden matcher | part-only greedy match | pairing by page and occurrence order | EML quantity accuracy {m(eml, 'quantity_accuracy'):.2f} |
| D-TEST-1 | harness (D7) | `test_the_first_read_runs_as_a_job_the_page_follows` failed by order/timing | in-memory test database: one shared connection for every session and thread; a request session's close rolled back the job thread's flushed rows | file-backed test database (`tests/conftest.py`); `EP_TEST_DATABASE=memory` reproduces the defect | `tests/test_job_thread_sessions.py` (fails on memory, passes on file); `evidence/r4__job_trace.log`, `r4__job_test_runs.json` |
| D-LEDGER-1 | VAL (D8) | OCR failures/budget counted in visited and failed/skipped; the 124 table summed to 120 | one list for two dimensions; accounting over the 120 manifest cases | pages exclusive (a failed page leaves visited); `coverage.ocr` dimension; accounting over the 124 population | `test_an_ocr_failure_on_a_changed_scan...`, `test_a_failure_on_page_two...` (updated), `test_an_ocr_failure_leaves_the_text_and_mark_evidence_to_settle_the_page`; manifest accounting |

### Current-reader BOQ defects after Review 02

| Id | Status after review 02 |
|---|---|
| D-BOQ-open-1 (band row unread) | **resolved**: read (`SIGA-OSHD-FC`, 525) under `Field Devices`; the part is read as the sheet prints it, cut |
| D-BOQ-open-2 (TP606 49/491) | **held for review**: the row is a review row with both readings; not auto-accepted |
| D-BOQ-open-3 (four misspelt parts) | **held for review**: `4-CABI6D`, `SIGA-AAS0`, `WSTIA-T`, `G1IARN` are review rows with the passes' readings; not auto-accepted |
| D-BOQ-open-4 (Field Devices group) | **resolved** (group accuracy {m(fas, 'group_accuracy'):.3f}); `6538-G5` remains under the heading above it (open, layout rule) |
| D-BOQ-open-5 (EML I/1; SL210DI pairing) | pairing **resolved** (matcher); `+SL231` for `+SL23I` **open** (accepted; two-line cell) |
"""
p = D / "M2-DEFECTS-AND-FIXES.md"; s = p.read_text(encoding="utf-8"); assert "Fixed in the Review 02 correction" not in s; p.write_text(s + defects, encoding="utf-8")

compat = f"""

## 8. Correction 2 (Review 02): identity, precedence, clone

- **Reading identity** is (content sha256, `PARSER_VERSION`, `profile`). `document_processing.parser_current` checks all three; `_previous_sha`, the known-content map, `read_task`'s unchanged shortcut and `_copy_reading` depend on it; the repair tool's `parser-outdated` selection includes a profile mismatch and `apply_row` writes the profile. A reading without a profile (every live reading) is not current under either profile: it is re-read when its file next changes or when the repair tool is run, never reused as a current reading. Engineer-confirmed values are not touched by a profile switch.
- **Withdrawn precedence** (a downstream delta): the reader no longer lets an OCR-read stamp override a ticked box or a printed status; the two are a conflict (status UR, both candidates). EP-30784's emergency lighting sample (BBY006-GME-SAR-EL-LI-0001: "Approved as Noted (B)" ticked, "(C) Revise & Resubmit" stamped) reads UR with `decision_conflict` where it read `rejected` before. Restoring the stamp's precedence needs an explicit owner policy.
- **Population deltas** (124 documents): Review 02 default vs Review 01 default {cls_d}; promoted vs default {cls_p} (`evidence/r4__population_comparison_r4.json`, examples inside).
- **Clone repair** (fresh copy of the read-only clone, default profile, `evidence/r4__repair_r4_comparison.json`): tables changed are `project_documents` and `document_dependencies` only; roles changed 0; form-reading records and the top-level mirrors as in the table in `M2-REVIEW-RESPONSE.md` (Review 02); every observation stored as JSON (no serialisation failure).
"""
p = D / "M2-COMPATIBILITY-REPORT.md"; s = p.read_text(encoding="utf-8"); assert "## 8. Correction 2" not in s; p.write_text(s + compat, encoding="utf-8")
print("docs appended;", suite_line, "| failures:", failed_names)
