"""Compare the clone before/after the extract-only repair: business snapshot differences, stored-record differences
(golden_records --from-db), repair manifest accounting, and the before/after parser timing on the common population."""
import json, sys, pathlib, hashlib, collections

S = pathlib.Path(sys.argv[1]); R = S / sys.argv[2]
out = {"repair_dir": str(R.name)}


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
    diffs = collections.Counter()
    fb, fa = dict(flat(b)), dict(flat(a))
    for k in set(fb) | set(fa):
        if fb.get(k) != fa.get(k):
            diffs[k.split("/")[1] if "/" in k else k] += 1
    tables_changed = {k: v for k, v in diffs.items()}
    out[f"snapshot_p{p}"] = {"before_hash": hashlib.sha256(json.dumps(b, sort_keys=True, default=str).encode()).hexdigest()[:16],
                             "after_hash": hashlib.sha256(json.dumps(a, sort_keys=True, default=str).encode()).hexdigest()[:16],
                             "top_level_keys": sorted(b.keys()) if isinstance(b, dict) else None, "changed_leaf_counts_by_section": tables_changed}
    rb, ra = load(R / f"records_before_p{p}.json"), load(R / f"records_after_p{p}.json")
    changed = []
    for path in sorted(set(rb) | set(ra)):
        x, y = rb.get(path, {}), ra.get(path, {})
        kx = sorted(json.dumps({f: r.get(f) for f in ("category", "reference", "revision", "status", "floor", "system_code", "page", "source")}, sort_keys=True) for r in x.get("records", []) if r.get("source") != "submittal form")
        ky = sorted(json.dumps({f: r.get(f) for f in ("category", "reference", "revision", "status", "floor", "system_code", "page", "source")}, sort_keys=True) for r in y.get("records", []) if r.get("source") != "submittal form")
        if kx != ky or x.get("role") != y.get("role"):
            changed.append({"path": path[-70:], "role_before": x.get("role"), "role_after": y.get("role"), "records_before": len(kx), "records_after": len(ky)})
    roles_changed = sum(1 for c in changed if c["role_before"] != c["role_after"])
    out[f"records_p{p}"] = {"documents": len(set(rb) | set(ra)), "documents_with_changed_records": len(changed), "roles_changed": roles_changed,
                            "records_before": sum(len(v.get("records", [])) for v in rb.values()), "records_after": sum(len(v.get("records", [])) for v in ra.values()),
                            "examples": changed[:15]}
    m = load(R / f"repair_p{p}.json")
    out[f"manifest_p{p}"] = {"mode": m["mode"], "parser_version": m["parser_version"], "selected": m["selected"], "counts": m["counts"], "seconds": m["seconds"],
                             "skip_reasons": dict(collections.Counter(e.get("skip_reason", "")[:60] for e in m["entries"] if e.get("outcome") == "skipped")),
                             "errors": [(e["document_id"], e.get("error")) for e in m["entries"] if e.get("outcome") == "failed"][:10]}
# timing on the common population
bef, aft = load(S / "parser_population.json")["docs"], load(S / "parser_population_after.json")["docs"]
common = sorted(set(bef) & set(aft), key=int)
tb = sum(bef[k]["seconds"] for k in common); ta = sum(aft[k]["seconds"] for k in common)
out["timing_common_population"] = {"documents": len(common), "before_parse_2026_09_28_1_cold_cache_seconds": round(tb, 1), "after_parse_2026_09_28_2_cold_cache_seconds": round(ta, 1),
                                   "note": "both runs on the same machine, same files, each with an empty scratch page cache; OCR dominates; not a latency claim beyond this workload"}
json.dump(out, open(S / f"{R.name}_comparison.json", "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str)[:6000])
