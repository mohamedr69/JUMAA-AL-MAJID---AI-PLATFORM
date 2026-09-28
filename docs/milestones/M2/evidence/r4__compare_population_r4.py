"""Review 02 deltas on the 124-document population: the corrected reader (parse-2026-09-28.4, default and promoted)
against the Review 01 reader (parse-2026-09-28.3 default), with the ledger's two exclusive dimensions accounted."""
import json, sys, pathlib, collections

S = pathlib.Path(sys.argv[1]); R1 = S.parent / "m2r"
r3 = json.load(open(R1 / "parser_population_r3_default.json", encoding="utf-8"))
r4d = json.load(open(S / "parser_population_r4_default.json", encoding="utf-8"))
r4p = json.load(open(S / "parser_population_r4_promoted.json", encoding="utf-8"))
KEY = ("category", "reference", "revision", "status", "page", "source", "system_code", "raw_system")


def keys(entry):
    return sorted(json.dumps({k: r.get(k) for k in KEY}, sort_keys=True, default=str) for r in entry.get("actual", {}).get("records", []))


def classify(b, a):
    kb, ka = keys(b), keys(a)
    if kb == ka:
        return "identical"
    rb = {json.loads(x)["reference"] for x in kb}; ra = {json.loads(x)["reference"] for x in ka}
    if len(kb) == len(ka) and rb == ra:
        diff = {f for x, y in zip(kb, ka) for f in KEY if json.loads(x).get(f) != json.loads(y).get(f)}
        return "changed:" + ",".join(sorted(diff))
    return "records_removed" if len(ka) < len(kb) else "records_added"


def ledger(run):
    docs = run["docs"]; c = collections.Counter(); pages = collections.Counter(); ocr = collections.Counter()
    for e in docs.values():
        cov = e.get("actual", {}).get("coverage") or {}
        c[cov.get("outcome", "n/a")] += 1
        pages["visited"] += len(cov.get("pages_visited") or []); pages["skipped"] += len(cov.get("pages_skipped") or []); pages["failed"] += len(cov.get("pages_failed") or [])
        pages["total"] += cov.get("pages_total") or 0
        o = cov.get("ocr") or {}
        ocr["attempted"] += o.get("attempted") or 0; ocr["failed"] += len(o.get("failed") or []); ocr["skipped_budget"] += len(o.get("skipped_budget") or [])
        # exclusivity check per document
        v, s_, f = set(cov.get("pages_visited") or []), {p["page"] for p in cov.get("pages_skipped") or []}, {p["page"] for p in cov.get("pages_failed") or []}
        if v & s_ or v & f or s_ & f:
            c["NON_EXCLUSIVE"] += 1
        if cov.get("pages_total") is not None and len(v) + len(s_) + len(f) != cov["pages_total"]:
            c["PAGES_DO_NOT_SUM"] += 1
    flags = collections.Counter(f for e in docs.values() for r in e.get("actual", {}).get("records", []) for f in (r.get("flags") or []))
    obs = collections.Counter(o.get("kind") for e in docs.values() for o in e.get("actual", {}).get("observations", []))
    status = collections.Counter(r.get("status") for e in docs.values() for r in e.get("actual", {}).get("records", []))
    return {"parser_version": run.get("parser_version"), "mode": run.get("mode"), "docs": len(docs), "records": sum(len(e.get("actual", {}).get("records", [])) for e in docs.values()),
            "errors": [k for k, e in docs.items() if "error" in e.get("actual", {})], "seconds_total": round(sum(e["seconds"] for e in docs.values()), 1),
            "document_outcomes": dict(c), "pages": dict(pages), "ocr": dict(ocr), "flags": dict(flags), "observations": dict(obs), "status_counts": dict(status),
            "records_with_candidates": sum(1 for e in docs.values() for r in e.get("actual", {}).get("records", []) if r.get("decision_candidates"))}


report = {"review01_default": ledger(r3), "review02_default": ledger(r4d), "review02_promoted": ledger(r4p)}
for name, before, after in (("review02_default_vs_review01_default", r3, r4d), ("review02_promoted_vs_review02_default", r4d, r4p)):
    common = sorted(set(before["docs"]) & set(after["docs"]), key=int); classes = collections.Counter(); examples = collections.defaultdict(list)
    for k in common:
        c = classify(before["docs"][k], after["docs"][k]); classes[c] += 1
        if c != "identical" and len(examples[c]) < 10:
            examples[c].append({"doc": int(k), "path": after["docs"][k]["relative_path"][-60:], "before": [json.loads(x) for x in keys(before["docs"][k])], "after": [json.loads(x) for x in keys(after["docs"][k])],
                                "flags_after": [r.get("flags") for r in after["docs"][k]["actual"].get("records", [])], "candidates_after": [r.get("decision_candidates") for r in after["docs"][k]["actual"].get("records", [])]})
    report[name] = {"common_docs": len(common), "classes": dict(classes), "examples": examples}
common = sorted(set(r3["docs"]) & set(r4d["docs"]), key=int)
report["timing_identical_work"] = {"documents": len(common), "review01_parse_3_cold_s": round(sum(r3["docs"][k]["seconds"] for k in common), 1), "review02_parse_4_cold_s": round(sum(r4d["docs"][k]["seconds"] for k in common), 1),
                                   "note": "both cold scratch caches, same files, same machine; the .4 run overlapped with the BOQ suites and the deterministic BOQ run for part of its time; not a latency claim beyond this workload"}
json.dump(report, open(S / "population_comparison_r4.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: v for k, v in report.items() if "vs" not in k}, indent=1)[:5000])
for k in ("review02_default_vs_review01_default", "review02_promoted_vs_review02_default"):
    print(k, report[k]["classes"])
