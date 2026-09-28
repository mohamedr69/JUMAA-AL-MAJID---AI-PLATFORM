"""Copy the Review 03 correction evidence into docs/milestones/M2/evidence/ (prefixed r5__; earlier files kept) and
rewrite M2-EVIDENCE-MANIFEST.json with fresh hashes of every evidence file, the sources, the documents, the commit and
the tree status."""
import hashlib, json, shutil, sys, pathlib, subprocess, datetime

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); E = REPO / "docs/milestones/M2/evidence"
copy = ["before_manifest_r3.json", "patch_r3_carry.py", "patch_r3_catalog.py", "eml_cells.py", "clip_sar_experiment.py", "sar_sample_reads.json",
        "probe/probe_correction.py", "probe/boundary_probes.py", "probe/independent_probes_after.json", "probe/boundary_probes_after.json", "probe/run_correction.log", "probe/run_boundary.log",
        "boq_run_r3.py", "boq_run_r3.json", "boq_run_r3.log",
        "r5_repair_r5.py", "r5_repair_r5.log", "repair_r5/repair_p4.json", "repair_r5/repair_p1.json", "repair_r5/repair_p4.log", "repair_r5/repair_p1.log",
        "repair_r5/snapshot_before_p4.json", "repair_r5/snapshot_after_p4.json", "repair_r5/snapshot_before_p1.json", "repair_r5/snapshot_after_p1.json",
        "repair_r5/golden_before_p4.json", "repair_r5/golden_after_p4.json", "repair_r5/golden_before_p1.json", "repair_r5/golden_after_p1.json",
        "repair_r5/records_before.json", "repair_r5/records_after.json", "repair_r5/r5_repair_timeline.json", "compare_repair_r5.py", "repair_r5_comparison.json",
        "final_r5.xml", "final_r5.log", "m2_review03_changes.diff", "docs_r5.py", "build_manifest_r5.py", "assemble_evidence_r5.py"]
hashes = {}
for rel in copy:
    src = S / rel; dst = E / ("r5__" + rel.replace("/", "__"))
    if not src.is_file():
        hashes[dst.name] = None; continue
    shutil.copyfile(src, dst); hashes[dst.name] = hashlib.sha256(dst.read_bytes()).hexdigest()
crops = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((S / "crops").glob("*.png"))}
all_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(E.iterdir()) if p.is_file() and p.name != "M2-EVIDENCE-MANIFEST.json"}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
src_files = ["backend/app/services/document_control.py", "backend/app/services/document_sync.py", "backend/app/services/document_processing.py", "backend/app/services/design_sheet_extractor.py",
             "backend/app/core/config.py", "backend/app/services/transmittals.py", "backend/app/ai/sheet_reader.py", "backend/app/database.py", "backend/app/services/jobs.py",
             "backend/scripts/repair_extraction.py", "backend/scripts/golden_records.py", "backend/scripts/business_snapshot.py", "backend/scripts/boq_metrics.py",
             "backend/tests/conftest.py", "backend/tests/test_extraction_m2.py", "backend/tests/test_extraction_m2_review.py", "backend/tests/test_extraction_m2_review02.py", "backend/tests/test_extraction_m2_review03.py",
             "backend/tests/test_job_thread_sessions.py", "backend/tests/test_document_control.py", "backend/tests/test_design_sheet_extractor.py", "backend/tests/test_ai_sheet_reader.py",
             "backend/tests/test_extraction_repair.py", "backend/tests/test_repair_tool.py", "backend/tests/fixtures/boq_ep30784_golden_v1.json"]
src_hashes = {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in src_files}
docs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO / "docs/milestones/M2").iterdir()) if p.is_file()}
old = json.load(open(E / "M2-EVIDENCE-MANIFEST.json", encoding="utf-8"))
manifest = {"assembled_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "head": head,
            "head_note": "the reviewed commit ed7d221 with the uncommitted Review 01, 02 and 03 corrections (git status below; backend/ep_platform.db and backend/library/symbols/symbol_library.json are live operational files, not correction artifacts)",
            "git_status_lines": len(status), "git_status": status, "source_hashes_after_review03": src_hashes, "m2_docs_sha256": docs,
            "evidence_files": all_hashes, "review03_evidence_added": hashes, "scratch_only_crops_sha256": crops, "scratch_dir": str(S),
            "review02_2026_09_28": {k: old.get(k) for k in ("assembled_at_utc", "head", "git_status_lines", "source_hashes_after_review02", "clones_review02") if k in old},
            "review01_2026_09_28": old.get("review01_2026_09_28"), "m2_submission_2026_09_28": old.get("m2_submission_2026_09_28")}
json.dump(manifest, open(E / "M2-EVIDENCE-MANIFEST.json", "w", encoding="utf-8"), indent=1)
print("evidence files", len(all_hashes), "added", sum(1 for v in hashes.values() if v), "missing", [k for k, v in hashes.items() if v is None], "head", head, "status lines", len(status))
