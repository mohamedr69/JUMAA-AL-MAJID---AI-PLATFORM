"""Fill the R5 clone-repair placeholders in the three M2 documents from repair_r3_comparison.json."""
import json, pathlib, sys

S = pathlib.Path(sys.argv[1]); D = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\docs\milestones\M2")
r = json.load(open(S / "repair_r3_comparison.json", encoding="utf-8"))


def row(pid):
    m = r[f"manifest_p{pid}"]; rec = r[f"records_p{pid}"]; snap = r[f"snapshot_p{pid}"]; vs = r[f"r3_vs_m2_repair_p{pid}"]
    tables = ", ".join(f"`{t}` ({v['rows_changed']} of {v['after']} rows)" for t, v in snap["tables_with_changed_rows"].items()) or "none"
    r2 = r.get(f"manifest_p{pid}_run2")
    run2 = (f"; run 2 on the {r2['selected']} failed rows: {r2['counts'].get('repaired', 0)} repaired, {len(r2['errors'])} failed, {r2['seconds']} s" if r2 else "")
    return (f"| EP-{'30088' if pid == 4 else '30784'} (project {pid}) | run 1: {m['selected']} selected, {m['counts'].get('repaired', 0)} repaired, {m['counts'].get('skipped', 0)} skipped, "
            f"{len(m['errors'])} failed (D-EXT-10), {m['seconds']} s{run2} | {tables}; project fields changed: {snap['project_fields_changed']} | {rec['documents_with_changed_records_all_sources']} of {rec['documents']} "
            f"(records {rec['records_before']} -> {rec['records_after']}; form-reading records {rec['form_reading_records_before']} -> {rec['form_reading_records_after']}, "
            f"{rec['documents_with_changed_form_reading_records']} documents with changed form-reading records) | {rec['roles_changed']} | "
            f"{rec['states_after']}; attempts {rec['attempt_markers_after']}, stale {rec['stale_after']}; coverage {rec['coverage_outcomes_after']} | "
            f"{vs['classes']} |")


table = ("| Project | Repair manifest | Tables changed (snapshot) | Documents with changed records, all sources | Roles changed | States / attempts / coverage after | Stored records vs the M2 submission's repair (`repair2`, `.2`) |\n"
         "|---|---|---|---|---|---|---|\n" + row(4) + "\n" + row(1) + "\n\n"
         "Snapshots: `scripts/business_snapshot.py` before/after (every business table; hash), `scripts/golden_records.py --from-db` before/after (form-reading records excluded, as the tool does) **and** a direct dump of every `project_documents.extracted` of both projects before/after (`repair_r3__records_before/after.json`: records of every source, form-reading records apart, attempt, stale, coverage, observations, flags). "
         f"Flags after: project 4 {r['records_p4']['flags_after']}, project 1 {r['records_p1']['flags_after']}; observation kinds after: project 4 {r['records_p4']['observation_kinds_after']}, project 1 {r['records_p1']['observation_kinds_after']}. "
         f"Status counts (all sources): project 4 {r['records_p4']['status_counts_before']} -> {r['records_p4']['status_counts_after']}; project 1 {r['records_p1']['status_counts_before']} -> {r['records_p1']['status_counts_after']}.\n\n"
         "**Form-reading records.** Four EP-30088 material submittal forms (634, 639, 670, 674) held the AI form reading's own record in `records` (status RR on three, UR on one) because the page reader had read nothing off their pages. With bounded page discovery (D-EXT-9) the reader now reads the form's own submittal record on page 2 and the reply pages behind it, so by the existing stand-in rule (`document_sync.record_for_the_log`: the model's record stands for the form only when nothing was read off the page) the form-reading record leaves `records`; the row's mirrored reference / revision / status keep the form reading's values (`kept_form_reading`) and `extracted.form` is unchanged. For a consumer building the log from `records`, three forms move from RR to the page's UR. This is the existing rule applied to pages that are now visited, not a new rule; it is a business-visible delta and is flagged for the owner (option not taken here: keep the model's record beside the page's when the page record carries no decision). The five other forms keep their form-reading record. **Conflicts on real data**: one document of the clone (441, `FF-0047-03-COMMENTED-B.pdf`) carries a filled box on C and a drawn frame on B; it is stored as a `decision_conflict` observation with both marks and no decision, as R2 requires.\n\n"
         "Reading the last column: `identical` = the same stored records as the M2 submission's repair; `changed:status` = a decision the gate holds back (candidate kept) or a folder-revision hold; `records_removed` = an untracked-discipline cover, an OCR-mangled second cover or a scanned transmittal held as an observation; `records_added` would be new records, none expected. Form-reading records (`source = submittal form`, written by the AI form reader, never by the parser) must not change under an extract-only repair with the model off.")
def _runs(pid):
    m = r[f"manifest_p{pid}"]; r2 = r.get(f"manifest_p{pid}_run2")
    return f"run 1 {m['selected']} selected / {m['counts'].get('repaired', 0)} repaired / {len(m['errors'])} failed on the observation JSON defect D-EXT-10" + (f", run 2 on those {r2['selected']}: {r2['counts'].get('repaired', 0)} repaired / {len(r2['errors'])} failed" if r2 else "")


summary = (f"project 4: {_runs(4)}, tables changed {list(r['snapshot_p4']['tables_with_changed_rows'])}, roles changed {r['records_p4']['roles_changed']}, "
           f"form-reading records {r['records_p4']['form_reading_records_before']} -> {r['records_p4']['form_reading_records_after']} ({r['records_p4']['documents_with_changed_form_reading_records']} documents changed); "
           f"project 1: {_runs(1)}, tables changed {list(r['snapshot_p1']['tables_with_changed_rows'])}, roles changed {r['records_p1']['roles_changed']}, "
           f"form-reading records {r['records_p1']['form_reading_records_before']} -> {r['records_p1']['form_reading_records_after']} ({r['records_p1']['documents_with_changed_form_reading_records']} changed); "
           f"vs the M2 submission's repair: project 4 {r['r3_vs_m2_repair_p4']['classes']}, project 1 {r['r3_vs_m2_repair_p1']['classes']}")
for name, marker, text in (("M2-REVIEW-RESPONSE.md", "<!-- R5-CLONE-SUMMARY -->", summary), ("M2-ACCEPTANCE-REPORT.md", "<!-- R5-CLONE-SUMMARY -->", summary), ("M2-COMPATIBILITY-REPORT.md", "<!-- R5-CLONE-TABLE -->", table)):
    p = D / name; s = p.read_text(encoding="utf-8"); assert marker in s, name; p.write_text(s.replace(marker, text), encoding="utf-8")
print(summary)
