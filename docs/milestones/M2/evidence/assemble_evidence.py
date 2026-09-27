"""Copy the M2 evidence artifacts into docs/milestones/M2/evidence/ and write the manifest of hashes.
Page images and page-text dumps of client originals stay in the scratchpad (referenced by hash only)."""
import hashlib, json, shutil, sys, pathlib, subprocess, datetime

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); E = REPO / "docs/milestones/M2/evidence"; E.mkdir(parents=True, exist_ok=True)
copy = ["before_manifest.json", "before_status.txt", "before_tracked.diff", "m2_changes.diff", "baseline_focused.log", "baseline_focused.xml", "final_focused.log", "final_focused.xml",
        "parser_population.json", "parser_population_after.json", "parser_population_after_cold_r1.json", "parser_population_after_run2.json", "repair2_snapshot_tables.json", "repair2_record_stats.json", "timing_comparison.json", "m2_changes_provenance.json", "make_diff.py", "repair_vs_population_crosscheck.json", "fill_reports.py", "run_parser_final.py", "parser_actual.json", "parser_r1.json", "clone_doc_index.json", "render2_report.json",
        "boq_metrics_platform_p1.json", "boq_metrics_read_p1.json", "boq_metrics_read_p1.log", "repair2_comparison.json", "run_parser.py", "run_parser_after2.py", "render_golden.py", "render2.py", "build_manifest.py", "compare_repair.py", "assemble_evidence.py",
        "repair2/repair_p4.json", "repair2/repair_p1.json", "repair2/repair_p4.log", "repair2/repair_p1.log", "repair2/snapshot_before_p4.json", "repair2/snapshot_after_p4.json", "repair2/snapshot_before_p1.json", "repair2/snapshot_after_p1.json",
        "repair2/records_before_p4.json", "repair2/records_after_p4.json", "repair2/records_before_p1.json", "repair2/records_after_p1.json"]
hashes = {}
for rel in copy:
    src = S / rel
    if not src.is_file():
        hashes[rel] = None; continue
    dst = E / rel.replace("/", "__")
    if rel == "clone_doc_index.json":
        # strip absolute paths of the originals; keep ids, relative paths, hashes, records
        d = json.load(open(src, encoding="utf-8"))
        for v in d["docs"].values():
            v.pop("path", None)
        json.dump(d, open(dst, "w", encoding="utf-8"), indent=1, default=str)
    else:
        shutil.copyfile(src, dst)
    hashes[rel.replace("/", "__")] = hashlib.sha256(dst.read_bytes()).hexdigest()
# scratch-only artifacts, referenced by hash
scratch = {}
for p in sorted((S / "golden_render").glob("*.png")):
    scratch[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
for name in ("golden_render/render_summary.json",):
    p = S / name
    if p.is_file():
        scratch[name] = hashlib.sha256(p.read_bytes()).hexdigest()
clones = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(S.glob("m2_clone*.db"))}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
src_hashes = {}
for f in ["backend/app/services/document_control.py", "backend/app/services/document_sync.py", "backend/app/services/transmittals.py", "backend/app/ai/sheet_reader.py", "backend/tests/test_extraction_m2.py", "backend/tests/test_ai_sheet_reader.py", "backend/tests/test_extraction_repair.py", "backend/tests/test_repair_tool.py", "backend/tests/fixtures/boq_ep30784_golden_v1.json", "backend/scripts/repair_extraction.py", "backend/scripts/golden_records.py", "backend/scripts/business_snapshot.py", "backend/scripts/boq_metrics.py"]:
    src_hashes[f] = hashlib.sha256((REPO / f).read_bytes()).hexdigest()
manifest = {"assembled_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "head": head, "git_status_lines": len(status), "git_status": status,
            "source_hashes_after_m2": src_hashes, "evidence_files": hashes, "scratch_only_artifacts_sha256": scratch, "scratch_dir": str(S), "clones": clones}
json.dump(manifest, open(E / "M2-EVIDENCE-MANIFEST.json", "w", encoding="utf-8"), indent=1)
print("evidence files", sum(1 for v in hashes.values() if v), "missing", [k for k, v in hashes.items() if v is None], "png", len(scratch))
