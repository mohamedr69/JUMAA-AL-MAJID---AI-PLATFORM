"""Deltas between the M2 run (parse-2026-09-28.2, cold cache) and the corrected run (parse-2026-09-28.3, cold cache)
on the same population, plus the promoted-path run: record-level changes, flags, candidates, coverage outcomes,
observations and timing on identical work."""
import json, sys, pathlib, collections

S = pathlib.Path(sys.argv[1]); M = S.parent / "m2"
before = json.load(open(M / "parser_population_after.json", encoding="utf-8"))
after = json.load(open(S / "parser_population_r3_default.json", encoding="utf-8"))
promoted = json.load(open(S / "parser_population_r3_promoted.json", encoding="utf-8")) if (S / "parser_population_r3_promoted.json").is_file() else None
KEY = ("category", "reference", "revision", "status", "page", "source", "system_code", "raw_system")


def keys(entry):
    a = entry.get("actual", {})
    return sorted(json.dumps({k: r.get(k) for k in KEY}, sort_keys=True, default=str) for r in a.get("records", []))


def classify(b, a):
    kb, ka = keys(b), keys(a)
    if kb == ka:
        return "identical"
    rb = {json.loads(x)["reference"]: json.loads(x) for x in kb}; ra = {json.loads(x)["reference"]: json.loads(x) for x in ka}
    if len(kb) == len(ka) and set(rb) == set(ra):
        diff = {f for ref in rb for f in KEY if rb[ref].get(f) != ra[ref].get(f)}
        return "changed:" + ",".join(sorted(diff))
    return "records_removed" if len(ka) < len(kb) else "records_added"


def summarise(run, label):
    docs = run["docs"]; out = {"label": label, "parser_version": run.get("parser_version"), "mode": run.get("mode"), "docs": len(docs),
                               "records": sum(len(e.get("actual", {}).get("records", [])) for e in docs.values()),
                               "errors": [k for k, e in docs.items() if "error" in e.get("actual", {})],
                               "seconds_total": round(sum(e["seconds"] for e in docs.values()), 1)}
    out["coverage_outcomes"] = dict(collections.Counter((e.get("actual", {}).get("coverage") or {}).get("outcome", "n/a") for e in docs.values()))
    out["stop_reasons"] = dict(collections.Counter((e.get("actual", {}).get("coverage") or {}).get("stop_reason", "n/a") for e in docs.values()))
    out["pages_skipped_docs"] = sum(1 for e in docs.values() if (e.get("actual", {}).get("coverage") or {}).get("pages_skipped"))
    out["pages_skipped_total"] = sum(len((e.get("actual", {}).get("coverage") or {}).get("pages_skipped") or []) for e in docs.values())
    out["pages_failed_total"] = sum(len((e.get("actual", {}).get("coverage") or {}).get("pages_failed") or []) for e in docs.values())
    out["pages_visited_total"] = sum(len((e.get("actual", {}).get("coverage") or {}).get("pages_visited") or []) for e in docs.values())
    out["observations_by_kind"] = dict(collections.Counter(o.get("kind") for e in docs.values() for o in e.get("actual", {}).get("observations", [])))
    out["docs_with_observations"] = sum(1 for e in docs.values() if e.get("actual", {}).get("observations"))
    out["flags"] = dict(collections.Counter(f for e in docs.values() for r in e.get("actual", {}).get("records", []) for f in (r.get("flags") or [])))
    out["records_with_candidates"] = sum(1 for e in docs.values() for r in e.get("actual", {}).get("records", []) if r.get("decision_candidates"))
    out["status_counts"] = dict(collections.Counter(r.get("status") for e in docs.values() for r in e.get("actual", {}).get("records", [])))
    out["category_counts"] = dict(collections.Counter(r.get("category") for e in docs.values() for r in e.get("actual", {}).get("records", [])))
    return out


report = {"before": summarise(before, "parse-2026-09-28.2 cold (M2 submission, evidence/parser_population_after.json)"),
          "after_default": summarise(after, "parse-2026-09-28.3 cold, default settings (promotion off)")}
if promoted:
    report["after_promoted"] = summarise(promoted, "parse-2026-09-28.3 warm cache, promote=True")
common = sorted(set(before["docs"]) & set(after["docs"]), key=int)
classes = collections.Counter(); examples = collections.defaultdict(list)
for k in common:
    c = classify(before["docs"][k], after["docs"][k]); classes[c] += 1
    if c != "identical" and len(examples[c]) < 12:
        examples[c].append({"doc": int(k), "path": after["docs"][k]["relative_path"][-60:],
                            "before": [json.loads(x) for x in keys(before["docs"][k])], "after": [json.loads(x) for x in keys(after["docs"][k])],
                            "flags_after": [r.get("flags") for r in after["docs"][k]["actual"].get("records", [])],
                            "candidates_after": [r.get("decision_candidates") for r in after["docs"][k]["actual"].get("records", [])],
                            "observations_after": after["docs"][k]["actual"].get("observations", [])})
report["default_vs_m2"] = {"common_docs": len(common), "classes": dict(classes), "examples": examples}
tb = sum(before["docs"][k]["seconds"] for k in common); ta = sum(after["docs"][k]["seconds"] for k in common)
slow = sorted(((after["docs"][k]["seconds"] - before["docs"][k]["seconds"], int(k), after["docs"][k]["relative_path"][-50:]) for k in common), reverse=True)[:8]
report["timing_identical_work"] = {"documents": len(common), "before_parse_2_cold_s": round(tb, 1), "after_parse_3_cold_s": round(ta, 1),
                                   "largest_increases": [{"delta_s": round(d, 1), "doc": i, "path": p} for d, i, p in slow],
                                   "note": "both cold scratch caches, same files, same machine; the .3 run overlapped with nothing but the 687 region script (~10 s of OCR); not a latency claim beyond this workload"}
if promoted:
    pc = collections.Counter(); pex = collections.defaultdict(list)
    for k in common:
        if k in promoted["docs"]:
            c = classify(after["docs"][k], promoted["docs"][k]); pc[c] += 1
            if c != "identical" and len(pex[c]) < 12:
                pex[c].append({"doc": int(k), "path": promoted["docs"][k]["relative_path"][-60:], "default": [json.loads(x) for x in keys(after["docs"][k])],
                               "promoted": [json.loads(x) for x in keys(promoted["docs"][k])], "observations_default": after["docs"][k]["actual"].get("observations", [])})
    report["promoted_vs_default"] = {"common_docs": sum(1 for k in common if k in promoted["docs"]), "classes": dict(pc), "examples": pex}
json.dump(report, open(S / "population_comparison.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: v for k, v in report.items() if k != "default_vs_m2" and k != "promoted_vs_default"}, indent=1)[:5000])
print("default_vs_m2", report["default_vs_m2"]["classes"])
if promoted:
    print("promoted_vs_default", report["promoted_vs_default"]["classes"])
