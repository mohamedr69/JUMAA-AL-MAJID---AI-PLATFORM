"""Fill the {{...}} placeholders of the compatibility and acceptance reports from the final evidence JSON."""
import json, sys, pathlib, re, collections

S = pathlib.Path(sys.argv[1]); D = pathlib.Path(sys.argv[2]) / "docs/milestones/M2"
cmp_ = json.load(open(S / "repair2_comparison.json")); tables = json.load(open(S / "repair2_snapshot_tables.json")); stats = json.load(open(S / "repair2_record_stats.json"))
timing = json.load(open(S / "timing_comparison.json")); manifest = json.load(open(D / "M2-GOLDEN-MANIFEST.json"))
aft = json.load(open(S / "parser_population_after.json"))["docs"]; bef = json.load(open(S / "parser_population.json"))["docs"]

def summ(recs): return [(r["category"], r["reference"], r["revision"], r["status"], r["page"]) for r in recs]
kinds = collections.Counter()
for k in aft:
    if k not in bef: continue
    b, a = summ(bef[k]["actual"].get("records", [])), summ(aft[k]["actual"].get("records", []))
    if b == a: kinds["identical"] += 1
    elif not b and a: kinds["record where none (untracked-discipline cover)"] += 1
    elif len(a) > len(b) and a[:len(b)] == b: kinds["records added (kept reply page / second cover)"] += 1
    elif [x[:2] + x[4:] for x in a] == [x[:2] + x[4:] for x in b]: kinds["status changed only (framed / highlighted / B option)"] += 1
    else: kinds["status changed and a second-cover record added"] += 1
population_delta = "; ".join(f"{v} {k}" for k, v in kinds.most_common())

rows = []
for p, name in (("4", "EP-30088 (project 4)"), ("1", "EP-30784 (project 1)")):
    m = cmp_[f"manifest_p{p}"]; r = cmp_[f"records_p{p}"]
    rows.append(f"| {name} | {m['selected']} selected, {m['counts'].get('repaired', 0)} repaired, {m['counts'].get('skipped', 0)} skipped ({', '.join(m['skip_reasons']) or '-'}), {len(m['errors'])} failed, {m['seconds']} s | {r['documents']} | {r['documents_with_changed_records']} | {r['records_before']} -> {r['records_after']} | {r['roles_changed']} |")
repair_table = "| Project | Repair manifest | Documents compared | Documents with changed records | Records before -> after | Roles changed |\n|---|---|---|---|---|---|\n" + "\n".join(rows)

t_rows = []
for p in ("4", "1"):
    for table, v in tables[f"p{p}"].items():
        t_rows.append(f"| project {p} | `{table}` | {v['rows_before']} -> {v['rows_after']} | {v['rows_changed']} | {', '.join(f'{k} ({n})' for k, n in sorted(v['fields'].items(), key=lambda kv: -kv[1]))} |")
tables_changed = "| Project | Table | Rows before -> after | Rows changed | Fields changed (rows) |\n|---|---|---|---|---|\n" + "\n".join(t_rows)

s_rows = []
for p in ("4", "1"):
    b, a = stats[f"p{p}"]["before"], stats[f"p{p}"]["after"]
    for key, label in (("records", "records (form-reading records excluded)"), ("docs_with_records", "documents with records"), ("date_shaped_refs", "date-shaped references (F1)"), ("generic_exact_refs", "exact generic references (F2)"), ("system_none", "drawings records with system None (untracked discipline / OCR-mangled cover)"), ("pdf_transmittal_records", "sample records read from scanned PDF transmittals"), ("frame_or_box_evidence", "decisions evidenced by a framed/boxed option label"), ("printed_revision_set", "records carrying a printed revision")):
        s_rows.append(f"| project {p} | {label} | {b.get(key, 0)} | {a.get(key, 0)} |")
    s_rows.append(f"| project {p} | records by status | {b['by_status']} | {a['by_status']} |")
    s_rows.append(f"| project {p} | records by category | {b['by_category']} | {a['by_category']} |")
    s_rows.append(f"| project {p} | revision_source | {b['revision_source']} | {a['revision_source']} |")
record_stats = "| Project | Marker | Before | After |\n|---|---|---|---|\n" + "\n".join(s_rows)

observed = "\n".join([
    f"- Untracked-discipline covers now read: {stats['p4']['after']['system_none']} `drawings` records with `system_code None` on EP-30088 (fire-fighting, architecture/pump-room covers, one OCR-mangled second cover of FA-0042); 0 before. No register consumes them.",
    f"- Reply pages behind a submission are kept: reply records {stats['p4']['before']['by_category'].get('reply', 0)} -> {stats['p4']['after']['by_category'].get('reply', 0)} on EP-30088 and {stats['p1']['before']['by_category'].get('reply', 0)} -> {stats['p1']['after']['by_category'].get('reply', 0)} on EP-30784 (the old readings held them as records; the .1 reader had dropped the folded ones).",
    f"- Status counts move with the stamp/frame evidence now read: EP-30088 {stats['p4']['before']['by_status']} -> {stats['p4']['after']['by_status']}; EP-30784 {stats['p1']['before']['by_status']} -> {stats['p1']['after']['by_status']}. On EP-30784 the changes are page-2 sheets whose consultant stamp the region OCR reads (F8): revision by folder, `printed_revision` 00 recorded, engineer confirmation still required.",
    f"- Scanned PDF transmittals now give sample records: {stats['p4']['after']['pdf_transmittal_records']} on EP-30088, {stats['p1']['after']['pdf_transmittal_records']} on EP-30784; `transmittals.number` counts a Word original and its signed PDF copy once.",
    "- The first version of two M2 rules produced wrong readings that the clone comparison caught before acceptance and that are now tested against: a coloured comment box mentioning \"approved\" read as an approval (8 EP-30784 records), and a hyphen-joined `...-SD-MEP-` / `0042` read as a serial. Neither is in the final run.",
])
compat = (D / "M2-COMPATIBILITY-REPORT.md").read_text(encoding="utf-8")
for key, val in (("{{REPAIR_TABLE}}", repair_table), ("{{TABLES_CHANGED}}", tables_changed), ("{{RECORD_STATS}}", record_stats), ("{{POPULATION_DELTA}}", population_delta), ("{{OBSERVED}}", observed)):
    compat = compat.replace(key, val)
(D / "M2-COMPATIBILITY-REPORT.md").write_text(compat, encoding="utf-8")
print("compat filled; leftover placeholders:", re.findall(r"\{\{[A-Z_]+\}\}", compat))
print(json.dumps({"population_delta": population_delta, "timing": timing}, indent=1)[:1500])
