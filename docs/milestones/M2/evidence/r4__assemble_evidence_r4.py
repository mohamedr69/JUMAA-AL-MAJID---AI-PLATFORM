"""Copy the Review 02 correction evidence into docs/milestones/M2/evidence/ (prefixed r4__; earlier files kept) and
rewrite M2-EVIDENCE-MANIFEST.json with fresh hashes of every evidence file, the sources, the documents, the commit and
the tree status."""
import hashlib, json, shutil, sys, pathlib, subprocess, datetime

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); E = REPO / "docs/milestones/M2/evidence"
copy = ["before_manifest_r2.json", "patch_a_c.py", "patch_b.py", "patch_boq.py", "patch_d3.py", "patch_d3b.py", "probe/probe_correction.py", "probe/independent_probes_after.json", "probe/run.log",
        "band_experiment.py", "catalog_experiment.py", "catalog_experiment.log", "trace_job_test.py", "job_trace.log", "job_test_runs.json", "suites_filedb.log",
        "word_transmittals_independent.json", "boq_run_r2.py", "boq_run_r2.json", "boq_run_r2.log",
        "run_parser_r4.py", "parser_population_r4_default.json", "parser_population_r4_default.log", "parser_population_r4_promoted.json", "parser_population_r4_promoted.log",
        "compare_population_r4.py", "population_comparison_r4.json", "build_manifest_r4.py", "build_manifest_r4.log",
        "r5_repair_r4.py", "r5_repair_r4.log", "repair_r4/repair_p4.json", "repair_r4/repair_p1.json", "repair_r4/repair_p4.log", "repair_r4/repair_p1.log",
        "repair_r4/snapshot_before_p4.json", "repair_r4/snapshot_after_p4.json", "repair_r4/snapshot_before_p1.json", "repair_r4/snapshot_after_p1.json",
        "repair_r4/golden_before_p4.json", "repair_r4/golden_after_p4.json", "repair_r4/golden_before_p1.json", "repair_r4/golden_after_p1.json",
        "repair_r4/records_before.json", "repair_r4/records_after.json", "repair_r4/r5_repair_timeline.json", "compare_repair_r4.py", "repair_r4_comparison.json",
        "final_r4.xml", "final_r4.log", "live_check_r4.json", "ops_note_r4.py", "job_test_runs.py", "job_test_runs.log", "compare_repair_r4.log", "compare_population_r4.log", "m2_review02_changes.diff", "docs_r4.py", "assemble_evidence_r4.py", "chain_r4.sh", "chain_r4.log"]
hashes = {}
for rel in copy:
    src = S / rel; dst = E / ("r4__" + rel.replace("/", "__"))
    if not src.is_file():
        hashes[dst.name] = None; continue
    shutil.copyfile(src, dst); hashes[dst.name] = hashlib.sha256(dst.read_bytes()).hexdigest()
all_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(E.iterdir()) if p.is_file() and p.name != "M2-EVIDENCE-MANIFEST.json"}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
src_files = ["backend/app/services/document_control.py", "backend/app/services/document_sync.py", "backend/app/services/document_processing.py", "backend/app/services/design_sheet_extractor.py",
             "backend/app/core/config.py", "backend/app/services/transmittals.py", "backend/app/ai/sheet_reader.py", "backend/app/database.py", "backend/app/services/jobs.py",
             "backend/scripts/repair_extraction.py", "backend/scripts/golden_records.py", "backend/scripts/business_snapshot.py", "backend/scripts/boq_metrics.py",
             "backend/tests/conftest.py", "backend/tests/test_extraction_m2.py", "backend/tests/test_extraction_m2_review.py", "backend/tests/test_extraction_m2_review02.py", "backend/tests/test_job_thread_sessions.py",
             "backend/tests/test_document_control.py", "backend/tests/test_design_sheet_extractor.py", "backend/tests/test_ai_sheet_reader.py", "backend/tests/test_extraction_repair.py", "backend/tests/test_repair_tool.py",
             "backend/tests/fixtures/boq_ep30784_golden_v1.json", "backend/library/symbols/symbol_library.json"]
src_hashes = {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in src_files}
docs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO / "docs/milestones/M2").iterdir()) if p.is_file()}
old = json.load(open(E / "M2-EVIDENCE-MANIFEST.json", encoding="utf-8"))
clones = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [S / "clone_r4.db"] if p.is_file()}
manifest = {"assembled_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "head": head,
            "head_note": "the reviewed commit ed7d221 with the uncommitted Review 01 and Review 02 corrections (git status below); the Review 01 manifest is kept under review01_2026_09_28",
            "git_status_lines": len(status), "git_status": status, "source_hashes_after_review02": src_hashes, "m2_docs_sha256": docs,
            "evidence_files": all_hashes, "review02_evidence_added": hashes, "scratch_dir": str(S), "clones_review02": clones,
            "review01_2026_09_28": {k: old.get(k) for k in ("assembled_at_utc", "head", "git_status_lines", "source_hashes_after_correction", "clones") if k in old},
            "m2_submission_2026_09_28": old.get("m2_submission_2026_09_28")}
json.dump(manifest, open(E / "M2-EVIDENCE-MANIFEST.json", "w", encoding="utf-8"), indent=1)
print("evidence files", len(all_hashes), "added", sum(1 for v in hashes.values() if v), "missing", [k for k, v in hashes.items() if v is None], "head", head, "status lines", len(status))
