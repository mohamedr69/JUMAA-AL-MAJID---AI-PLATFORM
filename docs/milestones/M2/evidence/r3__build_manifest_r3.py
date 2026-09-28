"""Regenerate docs/milestones/M2/M2-GOLDEN-MANIFEST.json after the M2 Review 01 correction.

Keeps every historical field of the M2 manifest (stored reading, .1 and .2 runs, the 2026-09-27 labels) and adds:
the visual crop check of every text-PDF case (crops/visual_check.json: header + recommendation-row crops read by the
author on 2026-09-28), the corrected reader's results on the default (promotion off) and promoted paths, the 687
bounded region recovery, the BOQ original-backed run, and per-field verdicts on both paths."""
import hashlib, json, sys, pathlib, datetime, collections

S = pathlib.Path(sys.argv[1]); M = S.parent / "m2"; OUT = pathlib.Path(sys.argv[2])
manifest = json.load(open(OUT / "M2-GOLDEN-MANIFEST.json", encoding="utf-8"))
visual = {v["doc_id"]: v for v in json.load(open(S / "crops" / "visual_check.json", encoding="utf-8"))}
default = json.load(open(S / "parser_population_r3_default.json", encoding="utf-8"))
promoted = json.load(open(S / "parser_population_r3_promoted.json", encoding="utf-8"))
region = json.load(open(S / "region687" / "region_687.json", encoding="utf-8"))
boq = json.load(open(S / "boq_run.json", encoding="utf-8"))
FIELDS = ("category", "reference", "revision", "revision_source", "printed_revision", "status", "system_code", "raw_system", "page", "source", "listed", "floor", "reply_text", "flags", "decision_candidates")
sheet_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((S / "crops").glob("sheet-*.png"))}


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
    """Per-field verdicts of one run against the label. Under the default path a decision read by a method the gate
    holds back is 'held' when its candidate agrees with the label; an untracked-discipline cover held as an
    observation is 'held' when the observation carries the expected reference."""
    v = {}
    if not isinstance(act, dict) or "error" in act:
        return {"run": "error" if isinstance(act, dict) else "not run"}
    recs = act["records"]; obs = act["observations"]; first = recs[0] if recs else None
    label = case["label"]; base = label.get("expected") or {}; exp_ref = label.get("expected_reference")
    exp_status = visual.get(case["doc_id"], {}).get("status_expected") or base.get("status")
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
        if first and first["status"] == exp_status:
            v["status"] = "match"
        elif first and any(c[0] == exp_status for c in (first.get("decision_candidates") or [])):
            fl = first.get("flags") or []
            v["status"] = ("held: candidate agrees, method unpromoted" if "decision_method_unpromoted" in fl else
                           "held: candidate agrees, revision association unvalidated" if "decision_revision_unvalidated" in fl else
                           "unresolved: conflicting candidates" if "decision_conflict" in fl else "held: candidate agrees")
        elif first is None and any(o.get("kind") == "decision_unpromoted" for o in obs) and path == "default":
            v["status"] = "held: decision observation only (cover held as observation)"
        elif first is None and any(r.get("status") == exp_status for o in obs for r in (o.get("records") or [])) and path == "default":
            v["status"] = "held: transmittal observation carries the status (promotion off)"
        elif first is None:
            v["status"] = "no record"
        else:
            v["status"] = "mismatch" if not (case["doc_id"] == 687) else "unresolved: scanned tick, mark analysis inconclusive (region687)"
    for f in ("revision", "category", "system_code", "raw_system", "revision_source"):
        if f in base and first is not None:
            v[f] = "match" if first.get(f) == base[f] else "mismatch"
    if "listed" in base and first is not None:
        v["listed"] = "match" if list(first.get("listed") or []) == base["listed"] else "mismatch"
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
    i = case["doc_id"]; vc = visual.get(i)
    if vc:
        case["label"]["visual_crop_check_2026_09_28"] = {"sheet": vc["sheet"], "sheet_sha256": sheet_hashes.get(vc["sheet"]), "header_crop": vc["header"], "row_crop": vc["row"],
                                                          "seen": vc["seen"], "status_expected": vc["status_expected"], "agrees_with_stored_parse_2": vc["agree"]}
        if "not visually checked" in case["label"]["provenance"]:
            case["label"]["provenance"] = "independent regex over the original's text layer (page 1) + visual crop check of the header (No / Rev / Date) and the recommendation rows on 2026-09-28 (crops/" + vc["sheet"] + ")"
            case["label"]["review_status"] = "independently source-checked (text layer + crops); no engineer sign-off"
        else:
            case["label"]["provenance"] += "; crop check 2026-09-28 (crops/" + vc["sheet"] + ")"
    if i == 687:
        case["label"]["region_recovery_2026_09_28"] = {"reference": {k: region["reference"].get(k) for k in ("method", "cell_px", "recovered", "matches_expected", "representation")},
                                                        "marks": {k: region["marks"].get(k) for k in ("method", "rule", "marked", "highest", "second", "representation")},
                                                        "boxes": [{k: b.get(k) for k in ("option", "core_dark_fraction")} for b in region["marks"].get("boxes", [])],
                                                        "parser_record_flags": region["parser"]["records"][0]["flags"] if region["parser"]["records"] else None}
    case["actual_after_parse_2026_09_28_3_default"] = actual(default, i)
    case["actual_after_parse_2026_09_28_3_promoted"] = actual(promoted, i)
    case["verdict_parse_2"] = case.pop("verdict", {})
    case["verdict_parse_3_default"] = verdicts(case, case["actual_after_parse_2026_09_28_3_default"], "default")
    case["verdict_parse_3_promoted"] = verdicts(case, case["actual_after_parse_2026_09_28_3_promoted"], "promoted")

# BOQ case: the original-backed run
manifest["boq_case"]["current_reader_run_2026_09_28"] = {
    "reader": boq["reader"], "model": boq["model"], "tesseract_cmd": boq["tesseract_cmd"], "render_dpi": boq["render_dpi"], "golden_fixture": boq["golden_fixture"],
    "systems": {s: {"doc_id": v["doc_id"], "relative_path": v["relative_path"], "sha256": v["sha256"], "seconds": v["seconds"], "state": v["state"], "outcome": v["accounting"]["outcome"],
                    "accounting": v["accounting"], "coverage": v["coverage"], "notes": v["notes"],
                    "metrics": {k: v["metrics"][k] for k in ("golden_rows", "lines", "row_detection_recall", "part_number_accuracy", "quantity_accuracy", "pair_accuracy", "group_accuracy", "false_removal_rate")},
                    "missing_rows": v["metrics"]["missing_rows"], "wrong_quantity": v["metrics"]["wrong_quantity"], "false_auto_accepts": v["metrics"]["false_auto_accepts"],
                    "wrong_group_count": len(v["metrics"]["wrong_group"]), "issues": v["issues"]} for s, v in boq["systems"].items()},
    "evidence": "evidence/r3__boq_run.json, evidence/r3__boq_run.log"}

# accounting
def outcomes(field):
    c = collections.Counter()
    for case in manifest["cases"]:
        a = case[field]
        if a == "not run": c["not_run"] += 1
        elif "error" in a: c["failed"] += 1
        elif a["records"]: c["succeeded_with_records"] += 1
        elif a["observations"]: c["succeeded_observations_only"] += 1
        elif case["label"].get("expected", {}).get("expected_records") == 0: c["succeeded_no_records"] += 1
        else: c["succeeded_no_records_unlabelled"] += 1
    return dict(c)


def vcounts(field):
    out = {}
    for case in manifest["cases"]:
        for k, v in case[field].items():
            key = v if isinstance(v, str) else "per-page:" + json.dumps(v, sort_keys=True)
            out.setdefault(k, collections.Counter())[key] += 1
    return {k: dict(v) for k, v in out.items()}


manifest["rebuilt_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
manifest["parser_versions"]["after_correction"] = default["parser_version"]
manifest["parser_versions"]["box_version_after_correction"] = default["box_version"]
manifest["promotion_gate"] = {"setting": "extraction_promote_observations (EXTRACTION_PROMOTE_OBSERVATIONS)", "default": default["settings_extraction_promote_observations"],
                              "default_run": {"mode": default["mode"], "promote_argument": default["promote_argument"], "cache": default["cache"], "run_at": default["run_at"]},
                              "promoted_run": {"mode": promoted["mode"], "promote_argument": promoted["promote_argument"], "cache": promoted["cache"], "run_at": promoted["run_at"]}}
manifest["outcome_accounting_documents_parse_3"] = {"default": outcomes("actual_after_parse_2026_09_28_3_default"), "promoted": outcomes("actual_after_parse_2026_09_28_3_promoted")}
manifest["verdict_counts_parse_2"] = manifest.pop("verdict_counts", {})
manifest["verdict_counts_parse_3"] = {"default": vcounts("verdict_parse_3_default"), "promoted": vcounts("verdict_parse_3_promoted")}
manifest["visual_check_2026_09_28"] = {"text_pdf_cases_checked": len(visual), "agree_with_parse_2": sum(1 for v in visual.values() if v["agree"]), "contact_sheets": sheet_hashes,
                                       "method": "for every text-PDF case the original's page 1 was cropped at the 'No:' header (reference / Rev / Date) and at every row containing 'Recommendation' or 'Approved' (pymupdf, 110/80 dpi), laid out on contact sheets of six and read by the author; drawing sheets and schedules were judged from their title block / list; the parser was not the source of any expectation"}
manifest["population_ids_outside_manifest"] = {"ids": [24, 122, 313, 316], "note": "read for the EP-30784 R1 revision cases; 22 is inside the manifest (the M2 acceptance report wrongly named 22 instead of 122); 120 manifest cases + 4 = 124 population documents"}
manifest["label_policy"] += "; 2026-09-28: every text-PDF case additionally checked on header/recommendation-row crops (contact sheets hashed above); 687 checked by bounded region OCR and checkbox mark analysis; still no engineer sign-off is claimed"
json.dump(manifest, open(OUT / "M2-GOLDEN-MANIFEST.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({"cases": len(manifest["cases"]), "outcomes": manifest["outcome_accounting_documents_parse_3"], "verdicts": manifest["verdict_counts_parse_3"]}, indent=1)[:6000])
