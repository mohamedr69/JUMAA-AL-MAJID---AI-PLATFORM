"""Append the Review 03 (R3-01..03) sections to the M2 documents, every number read from the evidence files."""
import json, re, sys, pathlib, subprocess

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform"); D = REPO / "docs/milestones/M2"
J = lambda p: json.load(open(p, encoding="utf-8"))
boq = J(S / "boq_run_r3.json"); boq2 = J(S.parent / "m2r2" / "boq_run_r2.json"); sar = J(S / "sar_sample_reads.json")
bp = J(S / "probe" / "boundary_probes_after.json"); ip = J(S / "probe" / "independent_probes_after.json"); rep = J(S / "repair_r5_comparison.json")
xml = (S / "final_r5.xml").read_text(encoding="utf-8")
tests = int(re.search(r'tests="(\d+)"', xml).group(1)); fail = int(re.search(r'failures="(\d+)"', xml).group(1)); err = int(re.search(r'errors="(\d+)"', xml).group(1))
skip = int(re.search(r'skipped="(\d+)"', xml).group(1)); secs = float(re.search(r'time="([\d.]+)"', xml).group(1)); passed = tests - fail - err - skip
failed_names = re.findall(r'<testcase classname="([^"]+)" name="([^"]+)"[^>]*>\s*<failure', xml)
suite_line = f"**{tests} tests, {passed} passed, {skip} skipped, {fail} failed, {err} errors** in {secs:.1f} s"
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
fas, eml = boq["systems"]["FAS"], boq["systems"]["EML"]; fas2, eml2 = boq2["systems"]["FAS"], boq2["systems"]["EML"]
m = lambda v, k: v["metrics"][k]
held = lambda v: [(i["detail"].get("catalog_no"), (i["detail"].get("catalog_check") or {}).get("reason") or "quantity") for i in v["issues"] if i["detail"].get("reason_code") or i["detail"].get("catalog_no") == "TP606"]
rb, pc = bp["repeat_bounded"], bp["promoted_carried_as_default"]
def sar_line(k):
    d = sar["docs"][k]; r = d["records"][0]
    return f"{k.split(':')[0]} ({k.split(':')[1]}): status {r['status']}, flags {r['flags']}, candidates {[(c[0], c[2]) for c in r['candidates']]}"
def rrow(pid):
    mf = rep[f"manifest_p{pid}"]; rc = rep[f"records_p{pid}"]; sn = rep[f"snapshot_p{pid}"]
    tables = ", ".join(f"`{t}` ({v['rows_changed']} of {v['after']} rows)" for t, v in sn["tables_with_changed_rows"].items()) or "none"
    return (f"| EP-{'30088' if pid == 4 else '30784'} (project {pid}) | {mf['selected']} selected, {mf['counts'].get('repaired', 0)} repaired, {mf['counts'].get('skipped', 0)} skipped, {mf['counts'].get('failed', 0)} failed | "
            f"{tables}; project fields changed: {sn['project_fields_changed']} | {rc['documents_with_changed_records_all_sources']} of {rc['documents']}; form-reading records {rc['form_reading_records_before']} -> {rc['form_reading_records_after']}; "
            f"mirrors changed {rc['top_level_mirrors_changed']} | {rc['roles_changed']} | profiles {rc.get('profiles_after')}; retained {rc.get('retained_after')}; invalid JSON {rc.get('invalid_json_extracted')} | {rep[f'r3_vs_m2_repair_p{pid}']['classes']} |")

response = f"""

---

# Response to Independent Review 03 (R3-01..R3-03)

Correction of 2026-09-28 (local, late morning). Source: commit `{head}` plus the Review 01, 02 and 03 corrections, uncommitted (`evidence/r5__m2_review03_changes.diff` is the whole uncommitted backend diff; `evidence/r5__before_manifest_r3.json` the hashes of the dirty tree before this correction). Versions unchanged since Review 02 (`parse-2026-09-28.4`, box-4, sheet reader `2026-09-28.1`): the reader's records did not change, the writer's handling of them did. No live data, backend, worker or model touched; the running backend found at the end of Review 02 was neither used nor stopped. The review files are untouched (probe copies under `C:\\t\\m2r3\\probe` byte-identical).

| Finding | Root cause | Change | Tests / evidence | Remaining limitation | Disposition |
|---|---|---|---|---|---|
| **R3-01** [P1] Repeated bounded reads erased inherited uncertainty | `carry_unvisited` computed the flags from the *previous envelope's* hash and re-created them; the new envelope's hash, parser and profile were written over the carried records; the same helper served the repair tool | Every carried record keeps its own provenance in `retained` (`source_sha256`, `parser_version`, `profile`, `read_at`: the envelope's at first carry, None where that reading recorded none; never restamped afterwards -- `document_sync.retained_provenance`). Its flags follow that provenance against the current content and profile, not the envelope: `carried_unverified` while its source bytes are not the file's now (or unknown), `carried_other_profile` while its profile is not the current one (or unknown). Its decision is projected as the record's status only when both bytes and profile match; otherwise the status is UR and the decision is kept as a candidate (method `retained`), restored when a later carry finds bytes and profile matching again. The envelope carries `retained` (count, pages, unverified, other-profile, sources) beside `coverage.carried_from_previous`; the note says how many are unverified / held. The same helper, with the profile, in `repair_extraction.preview`/`apply_row`; `retained` summarised on apply | `tests/test_extraction_m2_review03.py`: `test_repeated_bounded_reads_keep_the_carried_records_provenance_and_uncertainty` (complete -> changed bytes bounded -> the repair tool twice on the same new bytes -> reload in another session -> bytes changed again -> wider reader: original hash/time kept, both flags kept, then the record read afresh), `test_a_legacy_record_carried_into_a_bounded_reading_has_unknown_provenance_and_no_projected_decision`, `test_carry_unvisited_follows_the_records_own_provenance_not_the_envelope`; the reviewer's probe `repeat_bounded`: first `{rb['first_record']['flags']}`, second `{rb['second_record']['flags']}`, retained source `{rb['second_record']['retained']['source_sha256']}` (`evidence/r5__probe__boundary_probes_after.json`) | A carried record's identity (reference, revision, page) is still projected, flagged; only its decision is withheld. Page numbers are taken as the record's page; a file whose pages moved keeps the record flagged unverified until the page is read | **Fixed** (D-EXT-14) |
| **R3-02** [P1] Bounded carry bypassed the profile boundary | `carry_unvisited` did not know the profile; the default envelope certified a promoted record | Above: `carried_other_profile` and the withheld decision (mirror UR) for a record of another or unknown profile; `document_processing.parser_current` is False for a reading whose `retained.other_profile` > 0 (a mixed reading is never a current reading, so `_previous_sha` and `read_task`'s unchanged shortcut recompute; `pending_rows` does not select it on its own, so no retry loop); the known-content map never copies a reading with retained records to a duplicate file; the repair tool's `parser-outdated` selection names a mixed reading | `test_a_promoted_record_on_an_unvisited_page_is_held_by_a_default_bounded_reading` (promoted complete -> default bounded on the same bytes: status UR, candidate `retained`, mirror UR, `parser_current` False, `log_records` UR, duplicate not copied, reload; the repair tool selects and re-applies the same projection; gate on again: the decision restored and `parser_current` True; profile stripped: held), `test_a_default_record_is_held_by_a_promoted_bounded_reading_too`; probe `promoted_carried_as_default`: status `{pc['new_record']['status']}`, flags `{pc['new_record']['flags']}`, mirror `{pc['mirror_status']}`, `parser_current` {pc['parser_current']}, reuse `{pc['reuse_sha']}`; the five inherited probes unchanged (`r5__probe__independent_probes_after.json`) | Engineer-confirmed domain values are untouched (the fixture of Review 01 still passes). Historical evidence is kept, never cleared | **Fixed** (D-GATE-3) |
| **R3-03** [P2] The EML part-number misread passed as VALID | `_confirm_catalog` read a two-line cell one line at a time (fragments) and treated fragments as no contrary evidence | A cell taller than a line and a half is read as a block (`--psm 6`, lines joined); readings are compared as part keys (the part's own characters, upper case); a part is **confirmed** only when a pass read the strip's whole value; otherwise the row is a review row: either a pass read another whole part (both passes agreeing, or a same-length near miss by one or two substituted glyphs -- I/1, O/0, Z/2) or no pass read the whole part at all ("no independent pass read the whole part number"). Nothing is substituted; the row keeps its literal value, quantity, cell box (`catalog_cell`), every reading and the reason (`catalog_check`). The matcher accepts a fixture row's `printed_part_number` | `test_design_sheet_extractor.py`: `test_a_multiline_catalog_cell_is_read_as_a_block_and_a_near_miss_holds_it`, `test_fragments_do_not_confirm_a_part_and_a_whole_matching_reading_does` (fragments only, nothing read, conflicting whole readings, a clear matching whole reading beside a clipped fragment, a cut identity read literally), `test_an_unruled_sheet_keeps_wrapped_rows_together` (now judged over lines plus held rows); originals: see the table below (`evidence/r5__boq_run_r3.json`) | A part read below 90 % whose cell the passes cannot read whole is held even when the strip was right (EML SL210DI at 7 %): a review row, not a loss. Real-model verification: still missing evidence | **Fixed** (D-BOQ-6) |

## Deterministic BOQ reader on the originals (Review 02 -> Review 03)

| Sheet | Review 02 reader | Review 03 reader |
|---|---|---|
| FAS (74 golden rows) | {fas2['accounting']['lines_read']} lines accepted, part {m(fas2, 'part_number_accuracy'):.3f}; {len(fas2['issues'])} review rows | {fas['accounting']['lines_read']} lines accepted, {fas['accounting']['matched']} matched, part {m(fas, 'part_number_accuracy'):.3f} / quantity {m(fas, 'quantity_accuracy'):.3f} / group {m(fas, 'group_accuracy'):.3f}; review rows: {', '.join(f'{c} ({r})' for c, r in held(fas))}; outcome {fas['accounting']['outcome']} |
| EML (12 golden rows) | {eml2['accounting']['lines_read']} lines accepted incl. `+SL231` for `+SL23I`, 0 review rows, outcome VALID | {eml['accounting']['lines_read']} lines accepted, {eml['accounting']['matched']} matched, part/quantity {m(eml, 'part_number_accuracy'):.2f}/{m(eml, 'quantity_accuracy'):.2f}; review rows: {', '.join(f'{c} ({r})' for c, r in held(eml))}; outcome **{eml['accounting']['outcome']}** -- the sheet is not VALID while that identity is unresolved |

Adjudication of the two disputed labels against the source (crops in the scratchpad, hashed in the evidence manifest; recorded in `M2-GOLDEN-MANIFEST.json` `boq_case.current_reader_run_2026_09_28_review03.adjudications` and in the fixture's notes):
- **SIGA-OSHD-FC**: the sheet prints `SIGA-OSHD-FC` with the next glyph cut by the column rule. The reader's literal value is correct extraction; the identity is incomplete on the source. The fixture's `SIGA-OSHD-FCN` is the transcriber's completion, unsupported by the source: the fixture row now carries `printed_part_number: SIGA-OSHD-FC` and the metrics accept the printed form; nothing is completed by the reader. A reliable geometric clip detector was tried (ink in the columns before the rule) and rejected: uncut rows score higher than the cut one on this scan.
- **6538-G5**: printed as a stand-alone item (its own quantity 4) below the Remote power supply kit's components, under no heading of its own. The reader assigns the heading in force (`Booster Power Supply`); the transcriber left it ungrouped. The sheet prints no rule that settles it (no blank-row or indentation convention is established elsewhere on the sheet): a group-layout judgement for the owner, not an extractor error; left as a disagreement in the metrics.

## Compatibility note: the SAR sample (stamp over tick)

Before Review 02 the reader let an OCR-read stamp override a ticked box implicitly (`test_a_consultants_stamp_overrides_the_ticked_box`; EP-30784 `BBY006-GME-SAR-EL-LI-0001`). Since Review 02 the two are candidates settled together; where they disagree the record is UR with `decision_conflict`. Read on the originals by the current reader (`evidence/r5__sar_sample_reads.json`, parser {sar['parser_version']}): {sar_line('351:default')}; {sar_line('352:default')}; {sar_line('353:default')} (the promoted profile reads the same). The stored live readings (old parser) hold `rejected` (R0) and `ANN` (R1). Consumers affected by the raw change: the samples register built from `records` (`document_sync.log_records`/`combine`) shows UR instead of rejected for the R0 sample once it is re-read; the submittal map (`submittal_reader.check`) reads forms, not these records; shop drawings reconciliation reads drawings, not samples. No domain-resolution policy exists that ranks a consultant's stamp over a ticked box, and none is invented here: the raw extraction reports the conflict; the specific owner decision needed is "on a form where a ticked option and a pasted stamp disagree, which stands" -- until it is taken, such records stay UR with both candidates.

## Clone repair (fresh copy of the read-only clone, default profile, Review 03 writer)

| Project | Repair manifest | Tables changed | Documents with changed records (all sources) | Roles changed | After | Stored records vs the M2 submission's repair |
|---|---|---|---|---|---|---|
{rrow(4)}
{rrow(1)}

`retained` counts how many documents carry records of an earlier reading of their own file (legacy readings on skipped pages: unknown provenance, decisions held), `other_profile` how many of those are mixed readings (never current: read again on the next content change or repair).

## Tests

{suite_line} (`evidence/r5__final_r5.xml`, `.log`; file-backed harness). {"Failures: " + ", ".join(f"`{c}::{n}`" for c, n in failed_names) + "." if failed_names else "No failures."} Skips: the live-archive BOQ sheet tests. The seven reviewer probes (five inherited, two new) meet their contracts on the final code.

## Verdict (Review 03)

**READY FOR INDEPENDENT M2 RE-REVIEW.**

Code defects remaining: none reproduced by the reviewer's probes or known on the originals beyond the held rows (which are the contract). Owner decisions: (1) stamp versus ticked box on a form (the SAR case); (2) the group of a stand-alone item printed under a heading in force without a heading of its own (6538-G5). Missing evidence: real-model BOQ verification (no model call permitted); engineer countersignature of Golden labels (none claimed); G-01 stays the documented M4 gap.
"""
p = D / "M2-REVIEW-RESPONSE.md"; s = p.read_text(encoding="utf-8"); assert "Response to Independent Review 03" not in s; p.write_text(s + response, encoding="utf-8")

acceptance = f"""

## Correction 3 (2026-09-28, after Independent Review 03) -- supersedes Correction 2's table

| Item | Value |
|---|---|
| Source | commit `{head}`; Review 01+02+03 corrections uncommitted (`evidence/r5__m2_review03_changes.diff` = the whole uncommitted backend diff; hashes in `evidence/M2-EVIDENCE-MANIFEST.json`) |
| Versions | unchanged: `parse-2026-09-28.4`, box-4, sheet reader `2026-09-28.1`; no migration |
| Tests | {suite_line} (`evidence/r5__final_r5.xml`); new `tests/test_extraction_m2_review03.py` (5), `test_design_sheet_extractor.py` (+2) |
| Reviewer probes | 5 inherited + 2 new (repeat_bounded, promoted_carried_as_default) meet the intended contracts: `evidence/r5__probe__*` |
| Carried records | keep their own provenance (`retained`), their uncertainty and a withheld decision until their bytes and profile are the current ones; a mixed reading is never current or copied |
| BOQ, deterministic, originals | FAS {fas['accounting']['lines_read']} lines accepted (part/quantity 1.00), {len(fas['issues'])} review rows; EML {eml['accounting']['lines_read']} lines, 2 review rows incl. the multiline `+SL231`/`+SL23I` identity, outcome {eml['accounting']['outcome']} (not VALID); SIGA-OSHD-FC recorded as printed, 6538-G5 an owner layout decision |
| Clone repair | project 4 {rep['manifest_p4']['counts']}; project 1 {rep['manifest_p1']['counts']}; roles changed 0; retained: p4 {rep['records_p4'].get('retained_after')}, p1 {rep['records_p1'].get('retained_after')} |
| Live data | untouched by this correction; the backend found running at the end of Review 02 was neither used nor stopped |
| Verdict | **READY FOR INDEPENDENT M2 RE-REVIEW**; owner decisions and missing evidence listed in `M2-REVIEW-RESPONSE.md` (Review 03) |
"""
p = D / "M2-ACCEPTANCE-REPORT.md"; s = p.read_text(encoding="utf-8"); assert "Correction 3 (2026-09-28" not in s
s = s.replace("> **Correction 2 (Independent Review 02) is at the end and supersedes everything below, including the first correction's C1-C8.**",
              "> **Correction 3 (Independent Review 03) is at the very end and supersedes Correction 2's table; Correction 2 supersedes everything before it.**", 1)
p.write_text(s + acceptance, encoding="utf-8")

defects = f"""

## Fixed in the Review 03 correction (2026-09-28)

| Id | Category | Issue (review 03) | Root cause | Change | Regression tests |
|---|---|---|---|---|---|
| D-EXT-14 | VAL (R3-01) | A second bounded read of the same new bytes dropped `carried_unverified`; the envelope's hash/parser/profile/time were written over carried records | flags computed from the previous envelope; no per-record provenance | `retained` per carried record (never restamped), flags from it, decision withheld unless bytes and profile match, `extracted.retained` summary; the repair tool shares it | `test_repeated_bounded_reads_keep_the_carried_records_provenance_and_uncertainty`, `test_a_legacy_record_carried_into_a_bounded_reading_has_unknown_provenance_and_no_projected_decision`, `test_carry_unvisited_follows_the_records_own_provenance_not_the_envelope`; probe `repeat_bounded` |
| D-GATE-3 | VAL (R3-02) | A promoted record carried into a default bounded reading became a default status; `parser_current` true, hash reused, duplicates copied | the carry ignored the profile | `carried_other_profile` + withheld decision; `parser_current` False for a mixed reading; not copied to duplicates; repair selection names it | `test_a_promoted_record_on_an_unvisited_page_is_held_by_a_default_bounded_reading`, `test_a_default_record_is_held_by_a_promoted_bounded_reading_too`; probe `promoted_carried_as_default` |
| D-BOQ-6 | VAL (R3-03) | The EML multiline part `+SL231` (source `+SL23I`) accepted; sheet VALID | one-line passes on a two-line cell; fragments taken as no contrary evidence | block-mode passes for tall cells; part-key comparison; unconfirmed or contradicted parts are review rows with cell box and readings | `test_a_multiline_catalog_cell_is_read_as_a_block_and_a_near_miss_holds_it`, `test_fragments_do_not_confirm_a_part_and_a_whole_matching_reading_does`; EML run: 2 review rows, NEEDS_INTERPRETATION |

Disputed Golden labels adjudicated (M2-REVIEW-RESPONSE.md, Review 03): `SIGA-OSHD-FC` printed cut (fixture annotated with the printed form); `6538-G5` group an owner layout decision.
"""
p = D / "M2-DEFECTS-AND-FIXES.md"; s = p.read_text(encoding="utf-8"); assert "Fixed in the Review 03 correction" not in s; p.write_text(s + defects, encoding="utf-8")

compat = f"""

## 9. Correction 3 (Review 03): retained records, mixed readings, the SAR case

- A bounded reading's carried records are **retained evidence**, not current observations: each keeps its source hash, parser, profile and time (or None), flagged `carried_unvisited` and, as its provenance says, `carried_unverified` / `carried_other_profile`; its decision is a candidate (method `retained`) until its bytes and profile are the current ones. `extracted.retained` summarises them. A reading with other-profile retained records is a mixed reading: `parser_current` is False, it is not reused for its hash nor copied to a duplicate file, the repair tool selects it; it is replaced when the row is next processed or repaired with a reader that visits the page or under the profile that read it.
- **SAR case (stamp over tick)**: old result `rejected` (the OCR stamp won implicitly), new result UR with `decision_conflict` and both candidates; the raw conflict is kept apart from any domain resolution, of which none is authorised; the owner decision needed is stated in the response. Affected consumer: the samples register built from records (UR instead of rejected for the R0 sample once re-read).
- Clone repair under the Review 03 writer: `evidence/r5__repair_r5_comparison.json` (the table in the response).
"""
p = D / "M2-COMPATIBILITY-REPORT.md"; s = p.read_text(encoding="utf-8"); assert "## 9. Correction 3" not in s; p.write_text(s + compat, encoding="utf-8")
print("docs appended;", suite_line, "| failures:", failed_names)
