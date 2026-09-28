"""Copy the M2 Review 01 correction evidence into docs/milestones/M2/evidence/ (prefixed r3__; the M2 submission's files
are kept untouched) and rewrite M2-EVIDENCE-MANIFEST.json with fresh hashes of every evidence file, the source hashes
after the correction, the current commit and the working-tree status."""
import hashlib, json, shutil, sys, pathlib, subprocess, datetime

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); E = REPO / "docs/milestones/M2/evidence"; E.mkdir(parents=True, exist_ok=True)
copy = ["boq_run.json", "boq_run.log", "boq_run.py", "region687/region_687.json", "region687/run.log", "region_687.py", "crops.py", "crops/visual_check.json", "crops/index.json",
        "parser_population_r3_default.json", "parser_population_r3_default.log", "parser_population_r3_promoted.json", "parser_population_r3_promoted.log", "run_parser_r3.py",
        "population_comparison.json", "compare_population.py", "r5_repair.py", "r5_repair.log", "repair_r3/repair_p4.json", "repair_r3/repair_p1.json", "repair_r3/repair_p4.log", "repair_r3/repair_p1.log",
        "repair_r3/snapshot_before_p4.json", "repair_r3/snapshot_after_p4.json", "repair_r3/snapshot_before_p1.json", "repair_r3/snapshot_after_p1.json",
        "repair_r3/golden_before_p4.json", "repair_r3/golden_after_p4.json", "repair_r3/golden_before_p1.json", "repair_r3/golden_after_p1.json",
        "repair_r3/records_before.json", "repair_r3/records_after.json", "repair_r3/r5_repair_timeline.json", "repair_r3/repair_p4_rerun.json", "repair_r3/repair_p1_rerun.json", "repair_r3/repair_p4_rerun.log", "repair_r3/repair_p1_rerun.log", "repair_r3/records_after_run1.json", "repair_r3/snapshot_after_p4_run1.json", "repair_r3/snapshot_after_p1_run1.json", "repair_r3/golden_after_p4_run1.json", "repair_r3/golden_after_p1_run1.json", "r5_repair_rerun.py", "r5_repair_rerun.log", "final_r3_run1.xml", "final_r3_run1.log", "live_db_readonly_check.json", "fill_r5.py", "fill_final.py", "patch_observed.py", "repair_r3_comparison.json", "compare_repair_r3.py",
        "final_r3.xml", "final_r3.log", "flaky_1.log", "flaky_2.log", "compare_repair_r3.log", "build_manifest_r3.log", "m2_review01_changes.diff", "before_manifest.json", "build_manifest_r3.py", "assemble_evidence_r3.py", "probe/probe_results.json"]
hashes = {}
for rel in copy:
    src = S / rel
    if not src.is_file():
        hashes["r3__" + rel.replace("/", "__")] = None; continue
    dst = E / ("r3__" + rel.replace("/", "__"))
    shutil.copyfile(src, dst)
    hashes[dst.name] = hashlib.sha256(dst.read_bytes()).hexdigest()
# every evidence file, old and new
all_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(E.iterdir()) if p.is_file() and p.name != "M2-EVIDENCE-MANIFEST.json"}
scratch = {}
for sub in ("crops", "region687"):
    for p in sorted((S / sub).glob("*.png")):
        scratch[f"{sub}/{p.name}"] = hashlib.sha256(p.read_bytes()).hexdigest()
for p in sorted((S.parent / "m2" / "golden_render").glob("*.png")):
    scratch[f"golden_render/{p.name}"] = hashlib.sha256(p.read_bytes()).hexdigest()
clones = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [S / "clone_r3.db"] + sorted((S.parent / "m2").glob("m2_clone*.db")) if p.is_file()}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
src_files = ["backend/app/services/document_control.py", "backend/app/services/document_sync.py", "backend/app/services/document_processing.py", "backend/app/services/design_sheet_extractor.py",
             "backend/app/core/config.py", "backend/app/services/transmittals.py", "backend/app/ai/sheet_reader.py", "backend/scripts/repair_extraction.py", "backend/scripts/golden_records.py",
             "backend/scripts/business_snapshot.py", "backend/scripts/boq_metrics.py", "backend/tests/test_extraction_m2.py", "backend/tests/test_extraction_m2_review.py", "backend/tests/test_document_control.py",
             "backend/tests/test_design_sheet_extractor.py", "backend/tests/test_ai_sheet_reader.py", "backend/tests/test_extraction_repair.py", "backend/tests/test_repair_tool.py", "backend/tests/fixtures/boq_ep30784_golden_v1.json"]
src_hashes = {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in src_files}
docs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO / "docs/milestones/M2").iterdir()) if p.is_file()}
old = json.load(open(E / "M2-EVIDENCE-MANIFEST.json", encoding="utf-8")) if (E / "M2-EVIDENCE-MANIFEST.json").is_file() else {}
manifest = {"assembled_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "head": head, "head_note": "the reviewed commit ed7d221 with the uncommitted M2 Review 01 correction (git status below); the M2 submission's own manifest is kept under m2_submission_2026_09_28",
            "git_status_lines": len(status), "git_status": status, "source_hashes_after_correction": src_hashes, "m2_docs_sha256": docs,
            "evidence_files": all_hashes, "correction_evidence_added": hashes, "scratch_only_artifacts_sha256": scratch, "scratch_dir": str(S), "clones": clones,
            "m2_submission_2026_09_28": {k: old.get(k) for k in ("assembled_at_utc", "head", "git_status_lines", "source_hashes_after_m2", "evidence_files", "clones") if k in old}}
json.dump(manifest, open(E / "M2-EVIDENCE-MANIFEST.json", "w", encoding="utf-8"), indent=1)
print("evidence files", len(all_hashes), "added", sum(1 for v in hashes.values() if v), "missing", [k for k, v in hashes.items() if v is None], "png", len(scratch), "head", head, "status lines", len(status))
