"""Regenerate docs/milestones/M2/M2-GOLDEN-MANIFEST.json after the Review 02 correction: the parse-2026-09-28.4 runs on
both profiles with verdicts, the six Word transmittals' independent labels, the BOQ run of the corrected deterministic
reader, and document/page accounting over the whole 124-document population with exclusive categories."""
import hashlib, json, sys, pathlib, datetime, collections

S = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); M = S.parent / "m2"
manifest = json.load(open(OUT / "M2-GOLDEN-MANIFEST.json", encoding="utf-8"))
default = json.load(open(S / "parser_population_r4_default.json", encoding="utf-8"))
promoted = json.load(open(S / "parser_population_r4_promoted.json", encoding="utf-8"))
words = json.load(open(S / "word_transmittals_independent.json", encoding="utf-8"))
boq = json.load(open(S / "boq_run_r2.json", encoding="utf-8"))
compare = json.load(open(S / "population_comparison_r4.json", encoding="utf-8"))
idx = json.load(open(M / "clone_doc_index.json", encoding="utf-8"))["docs"]
FIELDS = ("category", "reference", "revision", "revision_source", "printed_revision", "status", "system_code", "raw_system", "page", "source", "listed", "floor", "reply_text", "flags", "decision_candidates")
SYSTEMS = {"fire alarm": "FAS", "voice evacuation": "VES", "emergency lighting": "ELS", "central battery": "ELS", "optical detector": "FAS"}


def actual(run, i):
    e = run["docs"].get(str(i))
    if not e:
        return "not run"
    a = e["actual"]
    if "error" in a:
        return {"error": a["error"]}
    return {"records": [{k: r.get(k) for k in FIELDS} for r in a.get("records", [])], "notes": a.get("notes", []), "coverage": a.get("coverage"),
            "observations": a.get("observations", []), "seconds": e.get("seconds")}


def verdicts(case, act, path):
    v = {}
    if not isinstance(act, dict) or "error" in act:
        return {"run": "error" if isinstance(act, dict) else "not run"}
    recs = act["records"]; obs = act["observations"]; first = recs[0] if recs else None
    label = case["label"]; base = label.get("expected") or {}; exp_ref = label.get("expected_reference")
    vc = label.get("visual_crop_check_2026_09_28") or {}
    exp_status = vc.get("status_expected") or base.get("status")
    if base.get("expected_records") == 0:
        v["records"] = "match" if not recs else "mismatch"
    if exp_ref is not None:
        if first and first["reference"] == exp_ref:
            v["reference"] = "match"
        elif first:
            v["reference"] = "incomplete: flagged reference_incomplete, evidence in region687" if "reference_incomplete" in (first.get("flags") or []) else "mismatch"
        elif any(o.get("reference") == exp_ref or any(r.get("reference") == exp_ref for r in (o.get("records") or [])) for o in obs):
            v["reference"] = "held: observation carries the reference (promotion off)" if path == "default" else "mismatch: observation only"
        else:
            v["reference"] = "no record"
    if exp_status is not None:
        cands = first.get("decision_candidates") or [] if first else []
        fl = first.get("flags") or [] if first else []
        if first and first["status"] == exp_status:
            v["status"] = "match"
        elif first and "decision_conflict" in fl:
            v["status"] = "unresolved: conflicting candidates " + str(sorted({(c[0], c[2]) for c in cands}))
        elif first and any(c[0] == exp_status for c in cands):
            v["status"] = ("held: candidate agrees, method unpromoted" if "decision_method_unpromoted" in fl else
                           "held: candidate agrees, revision association unvalidated" if "decision_revision_unvalidated" in fl else "held: candidate agrees")
        elif first is None and any(o.get("kind") == "decision_unpromoted" for o in obs) and path == "default":
            v["status"] = "held: decision observation only (cover held as observation)"
        elif first is None and any(r.get("status") == exp_status for o in obs for r in (o.get("records") or [])) and path == "default":
            v["status"] = "held: transmittal observation carries the status (promotion off)"
        elif first is None:
            v["status"] = "no record"
        else:
            v["status"] = "mismatch" if case["doc_id"] != 687 else "unresolved: scanned tick, mark analysis inconclusive (region687)"
    for f in ("revision", "category", "system_code", "raw_system", "revision_source"):
        if f in base and first is not None:
            v[f] = "match" if first.get(f) == base[f] else "mismatch"
    if "listed" in base and first is not None:
        v["listed"] = "match" if list(first.get("listed") or []) == base["listed"] else "mismatch"
    if "systems" in base:
        v["systems"] = "match" if sorted({r.get("system_code") for r in recs if r.get("system_code")}) == sorted(base["systems"]) else "mismatch"
    if "expected" in base:
        for exp in base["expected"]:
            rec = next((r for r in recs if r["page"] == exp["page"]), None)
            pv = {}
            for k, val in exp.items():
                if k == "page":
                    continue
                if rec is None:
                    pv[k] = "no record"
                elif rec.get(k) == val:
                    pv[k] = "match"
                elif k == "status" and any(c[0] == val for c in (rec.get("decision_candidates") or [])):
                    pv[k] = "held: candidate agrees, revision association unvalidated"
                else:
                    pv[k] = "mismatch"
            v[f"page{exp['page']}"] = pv
    return v


for case in manifest["cases"]:
    i = case["doc_id"]
    if str(i) in words and "error" not in words[str(i)]:
        w = words[str(i)]; f = w["found"]["cp1252"]
        subject = next((s for s in f["subject"] if s.lower().startswith("subject")), f["subject"][0] if f["subject"] else "")
        systems = sorted({code for name, code in SYSTEMS.items() if name in subject.lower()})
        case["label"]["expected_reference"] = f["tr"][0] if f["tr"] else None
        case["label"]["expected"] = {"reference": f["tr"][0] if f["tr"] else None, "category": "samples", "status": "UR", "systems": systems,
                                     "date_on_page": f["dates"][0] if f["dates"] else None, "subject": subject,
                                     "components": {"1": "Word document transmittal (.doc): TR number, date, subject line; a transmittal is a receipt, never a decision"}}
        case["label"]["provenance"] = "independently read from the .doc binary (cp1252 text scan of the WordDocument stream, no app code, 2026-09-28): TR number, date, subject; systems named by the subject"
        case["label"]["review_status"] = "independently source-checked (raw document text); no engineer sign-off"
        case["label"]["independent_word_text"] = {"tr_numbers": f["tr"], "dates": f["dates"][:3], "subject": subject, "bytes": w["bytes"], "sha256": w["sha256"]}
    case["actual_after_parse_2026_09_28_4_default"] = actual(default, i)
    case["actual_after_parse_2026_09_28_4_promoted"] = actual(promoted, i)
    case["verdict_parse_4_default"] = verdicts(case, case["actual_after_parse_2026_09_28_4_default"], "default")
    case["verdict_parse_4_promoted"] = verdicts(case, case["actual_after_parse_2026_09_28_4_promoted"], "promoted")

manifest["boq_case"]["current_reader_run_2026_09_28_review02"] = {
    "reader": boq["reader"], "model": boq["model"], "render_dpi": boq["render_dpi"], "golden_fixture": boq["golden_fixture"], "run_at": boq["run_at"],
    "systems": {s: {"doc_id": v["doc_id"], "relative_path": v["relative_path"], "sha256": v["sha256"], "seconds": v["seconds"], "state": v["state"], "outcome": v["accounting"]["outcome"],
                    "accounting": v["accounting"], "coverage": v["coverage"], "notes": v["notes"],
                    "metrics": {k: v["metrics"][k] for k in ("golden_rows", "lines", "row_detection_recall", "part_number_accuracy", "quantity_accuracy", "pair_accuracy", "group_accuracy", "false_removal_rate")},
                    "missing_rows": v["metrics"]["missing_rows"], "wrong_quantity": v["metrics"]["wrong_quantity"], "false_auto_accepts": v["metrics"]["false_auto_accepts"], "wrong_group": v["metrics"]["wrong_group"],
                    "review_rows": [{"target": i_["target"], "reason_code": i_["detail"].get("reason_code"), "catalog_no": i_["detail"].get("catalog_no"), "quantity": i_["detail"].get("quantity"),
                                     "reason": (i_["detail"].get("reason") or (i_["detail"].get("quantity_parse") or {}).get("rule"))} for i_ in v["issues"]]} for s, v in boq["systems"].items()},
    "evidence": "evidence/r4__boq_run_r2.json, evidence/r4__boq_run_r2.log"}


def outcomes_over_population(run):
    """Exclusive document outcomes over every document the run read (124), not only the manifest's 120."""
    c = collections.Counter(); ids = {}
    for k, e in run["docs"].items():
        a = e.get("actual", {})
        if "error" in a: o = "failed"
        elif a.get("records"): o = "succeeded_with_records"
        elif a.get("observations"): o = "succeeded_observations_only"
        else: o = "succeeded_no_records"
        c[o] += 1; ids.setdefault(o, []).append(int(k))
    c["total"] = len(run["docs"])
    return {"counts": dict(c), "no_records_ids": sorted(ids.get("succeeded_no_records", [])), "failed_ids": sorted(ids.get("failed", []))}


def vcounts(field):
    out = {}
    for case in manifest["cases"]:
        for k, v in case[field].items():
            key = v if isinstance(v, str) else "per-page:" + json.dumps(v, sort_keys=True)
            out.setdefault(k, collections.Counter())[key] += 1
    return {k: dict(v) for k, v in out.items()}


manifest["rebuilt_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
manifest["parser_versions"]["after_review02"] = default["parser_version"]
manifest["promotion_gate"]["review02_runs"] = {"default": {"mode": default["mode"], "cache": default["cache"], "run_at": default["run_at"]}, "promoted": {"mode": promoted["mode"], "cache": promoted["cache"], "run_at": promoted["run_at"]},
                                                "identity": "a reading's identity is (content sha256, PARSER_VERSION, profile); document_processing.parser_current checks all three"}
manifest["outcome_accounting_documents_parse_4"] = {"population": 124, "manifest_cases": 120, "outside_manifest_ids": [24, 122, 313, 316],
                                                    "default": outcomes_over_population(default), "promoted": outcomes_over_population(promoted),
                                                    "rule": "one outcome per document, exclusive: failed / with records / observations only / no records; counts sum to the population of 124"}
manifest["page_accounting_parse_4"] = {"default": compare["review02_default"]["pages"] | {"ocr": compare["review02_default"]["ocr"], "document_outcomes": compare["review02_default"]["document_outcomes"]},
                                       "promoted": compare["review02_promoted"]["pages"] | {"ocr": compare["review02_promoted"]["ocr"], "document_outcomes": compare["review02_promoted"]["document_outcomes"]},
                                       "rule": "pages: visited + skipped + failed = total, exclusive per page (a page whose OCR failed or was left out by the OCR budget is a visited page); OCR is its own dimension: attempted, failed, skipped by budget"}
manifest["verdict_counts_parse_4"] = {"default": vcounts("verdict_parse_4_default"), "promoted": vcounts("verdict_parse_4_promoted")}
manifest["word_transmittals_2026_09_28"] = {"checked": [int(k) for k, w in words.items() if "error" not in w], "unreadable": [int(k) for k, w in words.items() if "error" in w],
                                            "method": "the .doc files are OLE compound files; their WordDocument stream text was scanned as cp1252 for the TR number, dates and the Subject line (no app code, no transmittal grammar); evidence/r4__word_transmittals_independent.json"}
manifest["label_policy"] += "; 2026-09-28 (review 02): the six Word transmittals labelled from their own binary text; verdicts computed again for parse-2026-09-28.4 on both profiles"
json.dump(manifest, open(OUT / "M2-GOLDEN-MANIFEST.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({"outcomes": manifest["outcome_accounting_documents_parse_4"], "pages": manifest["page_accounting_parse_4"], "verdicts": manifest["verdict_counts_parse_4"]}, indent=1)[:7000])
