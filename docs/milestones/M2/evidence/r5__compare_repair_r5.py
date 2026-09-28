"""R5: what the corrected reader's extract-only repair (default path, promotion off) changed on the clone, business
tables included, form-reading records included, and how its stored records differ from the M2 submission's repair
(repair2, parse-2026-09-28.2) on the same clone source."""
import json, sys, pathlib, hashlib, collections

S = pathlib.Path(sys.argv[1]); M = S.parent / "m2"; R = S / "repair_r5"
out = {"repair_dir": "repair_r5", "clone_source": "m2_clone_20260927T181957Z.db (fresh copy: clone_r3.db)"}


def load(p):
    return json.load(open(p, encoding="utf-8"))


def flat(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from flat(v, f"{prefix}/{k}")
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from flat(v, f"{prefix}[{i}]")
    else:
        yield prefix, d


for p in ("4", "1"):
    b, a = load(R / f"snapshot_before_p{p}.json"), load(R / f"snapshot_after_p{p}.json")
    fb, fa = dict(flat(b)), dict(flat(a)); diffs = collections.Counter()
    import re as _re
    for k in set(fb) | set(fa):
        if fb.get(k) != fa.get(k):
            diffs[_re.sub(r"\[\d+\]$", "", k.split("/")[2] if k.startswith("/tables/") else k.split("/")[1])] += 1
    rows_changed = {}
    for table in b.get("tables", {}):
        rb = {json.dumps({kk: r.get(kk) for kk in ("id",)}, sort_keys=True): r for r in b["tables"][table]} if isinstance(b["tables"][table], list) else {}
        ra = {json.dumps({kk: r.get(kk) for kk in ("id",)}, sort_keys=True): r for r in a["tables"].get(table, [])} if isinstance(a["tables"].get(table), list) else {}
        changed = sum(1 for k in set(rb) | set(ra) if rb.get(k) != ra.get(k))
        if changed:
            rows_changed[table] = {"before": len(rb), "after": len(ra), "rows_changed": changed}
    out[f"snapshot_p{p}"] = {"before_hash": b.get("hash"), "after_hash": a.get("hash"), "changed_leaf_counts_by_table": dict(diffs), "tables_with_changed_rows": rows_changed,
                             "project_fields_changed": b.get("project") != a.get("project")}
    m = load(R / f"repair_p{p}.json")
    rerun = load(R / f"repair_p{p}_rerun.json") if (R / f"repair_p{p}_rerun.json").is_file() else None
    if rerun:
        out[f"manifest_p{p}_run2"] = {"mode": rerun["mode"], "parser_version": rerun["parser_version"], "selected": rerun["selected"], "counts": rerun["counts"], "seconds": rerun["seconds"],
                                     "errors": [(e["document_id"], e.get("error")) for e in rerun["entries"] if e.get("outcome") == "failed"][:10]}
    out[f"manifest_p{p}"] = {"mode": m["mode"], "parser_version": m["parser_version"], "selected": m["selected"], "counts": m["counts"], "seconds": m["seconds"],
                             "skip_reasons": dict(collections.Counter(e.get("skip_reason", "")[:60] for e in m["entries"] if e.get("outcome") == "skipped")),
                             "errors": [(e["document_id"], e.get("error")) for e in m["entries"] if e.get("outcome") == "failed"][:10]}

KEY = ("category", "reference", "revision", "status", "page", "source", "system_code", "raw_system")


def rec_keys(records):
    return sorted(json.dumps({k: r.get(k) for k in KEY}, sort_keys=True, default=str) for r in records)


# stored records, form-reading records included (records_before/after.json: straight from the clone)
rb, ra = load(R / "records_before.json"), load(R / "records_after.json")
for pid in (4, 1):
    ids = [k for k in ra if ra[k]["project_id"] == pid]
    changed_all = [k for k in ids if rec_keys(rb[k]["records"]) != rec_keys(ra[k]["records"])]
    changed_form = [k for k in ids if rec_keys(rb[k]["form_records"]) != rec_keys(ra[k]["form_records"])]
    roles = sum(1 for k in ids if rb[k]["role"] != ra[k]["role"])
    mirrors = sum(1 for k in ids if (rb[k]["reference"], rb[k]["revision"], rb[k]["status"]) != (ra[k]["reference"], ra[k]["revision"], ra[k]["status"]))
    out[f"records_p{pid}"] = {"documents": len(ids), "documents_with_changed_records_all_sources": len(changed_all), "documents_with_changed_form_reading_records": len(changed_form),
                              "form_reading_records_before": sum(len(rb[k]["form_records"]) for k in ids), "form_reading_records_after": sum(len(ra[k]["form_records"]) for k in ids),
                              "records_before": sum(len(rb[k]["records"]) for k in ids), "records_after": sum(len(ra[k]["records"]) for k in ids), "roles_changed": roles,
                              "top_level_mirrors_changed": mirrors, "states_after": dict(collections.Counter(ra[k]["state"] for k in ids)),
                              "attempt_markers_after": sum(1 for k in ids if ra[k]["attempt"]), "stale_after": sum(1 for k in ids if ra[k]["stale"]),
                              "coverage_outcomes_after": dict(collections.Counter(ra[k]["coverage_outcome"] for k in ids)),
                              "parser_versions_after": dict(collections.Counter(ra[k]["parser_version"] for k in ids)),
                              "docs_with_observations_after": sum(1 for k in ids if ra[k]["observations"]),
                              "observation_kinds_after": dict(collections.Counter(o.get("kind") for k in ids for o in (ra[k]["observations"] or []))),
                              "flags_after": dict(collections.Counter(f for k in ids for r in ra[k]["records"] for f in (r.get("flags") or []))),
                              "status_counts_before": dict(collections.Counter(r.get("status") for k in ids for r in rb[k]["records"])),
                              "status_counts_after": dict(collections.Counter(r.get("status") for k in ids for r in ra[k]["records"])),
                              "form_record_examples": [{"doc": k, "path": ra[k]["path"][-50:], "before": rec_keys(rb[k]["form_records"]), "after": rec_keys(ra[k]["form_records"])} for k in changed_form[:5]]}

# versus the M2 submission's repair (repair2, .2): golden_records dumps keyed by path
for pid in (4, 1):
    m2 = load(M / "repair2" / f"records_after_p{pid}.json"); r3 = load(R / f"golden_after_p{pid}.json")
    classes = collections.Counter(); examples = collections.defaultdict(list)
    for path in sorted(set(m2) | set(r3)):
        x, y = m2.get(path, {}), r3.get(path, {})
        kx, ky = rec_keys([r for r in x.get("records", []) if r.get("source") != "submittal form"]), rec_keys([r for r in y.get("records", []) if r.get("source") != "submittal form"])
        if kx == ky:
            classes["identical"] += 1; continue
        refs_x = {json.loads(k)["reference"] for k in kx}; refs_y = {json.loads(k)["reference"] for k in ky}
        if len(kx) == len(ky) and refs_x == refs_y:
            c = "changed:" + ",".join(sorted({f for a_, b_ in zip(kx, ky) for f in KEY if json.loads(a_).get(f) != json.loads(b_).get(f)}))
        else:
            c = "records_removed" if len(ky) < len(kx) else "records_added"
        classes[c] += 1
        if len(examples[c]) < 6:
            examples[c].append({"path": path[-60:], "m2_repair": [json.loads(k) for k in kx], "r3_repair": [json.loads(k) for k in ky]})
    out[f"r3_vs_m2_repair_p{pid}"] = {"documents": len(set(m2) | set(r3)), "classes": dict(classes), "examples": examples}
import sqlite3 as _sq
_con = _sq.connect(f"file:{(S / 'clone_r5.db').as_posix()}?mode=ro", uri=True)
for pid in (4, 1):
    rows = _con.execute("select coalesce(json_extract(extracted,'$.profile'),'none'), count(*) from project_documents where project_id=? group by 1", (pid,)).fetchall()
    out[f"records_p{pid}"]["profiles_after"] = dict(rows)
    out[f"records_p{pid}"]["retained_after"] = {"documents": _con.execute("select count(*) from project_documents where project_id=? and json_extract(extracted,'$.retained') is not null", (pid,)).fetchone()[0], "other_profile": _con.execute("select count(*) from project_documents where project_id=? and json_extract(extracted,'$.retained.other_profile') > 0", (pid,)).fetchone()[0], "unverified": _con.execute("select count(*) from project_documents where project_id=? and json_extract(extracted,'$.retained.unverified') > 0", (pid,)).fetchone()[0]}
    bad = _con.execute("select count(*) from project_documents where project_id=? and extracted is not null and json_valid(extracted)=0", (pid,)).fetchone()[0]
    out[f"records_p{pid}"]["invalid_json_extracted"] = bad
_con.close()
json.dump(out, open(S / "repair_r5_comparison.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: v for k, v in out.items() if not k.startswith("r3_vs")}, indent=1, default=str)[:7000])
for pid in (4, 1):
    print(f"r3_vs_m2_repair_p{pid}", out[f"r3_vs_m2_repair_p{pid}"]["classes"])
