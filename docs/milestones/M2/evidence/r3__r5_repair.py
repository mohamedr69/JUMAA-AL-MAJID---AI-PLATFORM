"""R5: extract-only repair of the corrected reader (parse-2026-09-28.3, default settings: promotion off) on a fresh
copy of the read-only clone, with business snapshots and stored-record dumps before/after -- INCLUDING the
form-reading records that golden_records.py filters out. Every step is a separate process with the isolation
environment set before any app import. The live database is never named."""
import json, os, sys, pathlib, shutil, subprocess, sqlite3, time, datetime

S = pathlib.Path(sys.argv[1]); M = S.parent / "m2"; REPO = pathlib.Path(sys.argv[2]); BACKEND = REPO / "backend"; PY = BACKEND / "venv" / "Scripts" / "python.exe"
R = S / "repair_r3"; R.mkdir(exist_ok=True)
src = sorted(M.glob("m2_clone_2*.db"))[-1]; clone = S / "clone_r3.db"
if not clone.is_file():
    shutil.copyfile(src, clone)
    for ext in ("-wal", "-shm"):
        p = pathlib.Path(str(src) + ext)
        if p.is_file():
            shutil.copyfile(p, pathlib.Path(str(clone) + ext))
env = dict(os.environ)
env.update({"DATABASE_URL": f"sqlite:///{clone.as_posix()}", "CACHE_ROOT": str(S / "cache_r3"), "AI_ENABLED": "false",
            "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "ARCHIVE_DATASHEET_LIBRARIES": "{}",
            "ARCHIVE_SUBMITTAL_LIBRARY": "", "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
            "COMPLIANCE_KNOWLEDGE_SOURCE": "", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false",
            "DOCUMENT_CLASSIFICATION_V2": "false", "SYNC_FILE_WORKERS": "0", "PYTHONIOENCODING": "utf-8"})
timeline = []


def run(args, log):
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8") as h:
        rc = subprocess.run([str(PY)] + args, cwd=BACKEND, env=env, stdout=h, stderr=subprocess.STDOUT).returncode
    timeline.append({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "cmd": " ".join(args), "rc": rc, "seconds": round(time.perf_counter() - t0, 1)})
    print(rc, round(time.perf_counter() - t0, 1), "s", " ".join(args)[:120], flush=True)
    return rc


def dump_records(tag):
    """Every stored record of the two projects, form-reading records included, straight from the clone."""
    con = sqlite3.connect(str(clone)); con.row_factory = sqlite3.Row
    out = {}
    for row in con.execute("SELECT id, project_id, relative_path, role, state, reference, revision, status, extracted FROM project_documents WHERE project_id IN (1, 4)"):
        ex = json.loads(row["extracted"]) if row["extracted"] else {}
        recs = [r for r in (ex.get("records") or []) if isinstance(r, dict)]
        out[str(row["id"])] = {"project_id": row["project_id"], "path": row["relative_path"], "role": row["role"], "state": row["state"], "reference": row["reference"],
                               "revision": row["revision"], "status": row["status"], "parser_version": ex.get("parser_version"), "attempt": ex.get("attempt"), "stale": ex.get("stale"),
                               "coverage_outcome": (ex.get("coverage") or {}).get("outcome"), "observations": ex.get("observations"),
                               "records": recs, "form_records": [r for r in recs if r.get("source") == "submittal form"],
                               "form_reading_keys": sorted(k for k in ex.keys() if "form" in k or "ai" in k)}
    con.close()
    json.dump(out, open(R / f"records_{tag}.json", "w", encoding="utf-8"), indent=1, default=str)
    return out


for pid in ("4", "1"):
    run(["scripts/business_snapshot.py", pid, str(R / f"snapshot_before_p{pid}.json")], R / f"snapshot_before_p{pid}.log")
    run(["scripts/golden_records.py", "--from-db", pid, "--out", str(R / f"golden_before_p{pid}.json")], R / f"golden_before_p{pid}.log")
dump_records("before")
run(["scripts/repair_extraction.py", "--project", "4", "--select", "date-references,truncated-references,parser-outdated", "--apply", "--manifest", str(R / "repair_p4.json")], R / "repair_p4.log")
run(["scripts/repair_extraction.py", "--project", "1", "--select", "parser-outdated", "--apply", "--manifest", str(R / "repair_p1.json")], R / "repair_p1.log")
for pid in ("4", "1"):
    run(["scripts/business_snapshot.py", pid, str(R / f"snapshot_after_p{pid}.json")], R / f"snapshot_after_p{pid}.log")
    run(["scripts/golden_records.py", "--from-db", pid, "--out", str(R / f"golden_after_p{pid}.json")], R / f"golden_after_p{pid}.log")
dump_records("after")
json.dump({"clone": str(clone), "clone_source": src.name, "environment": {k: env[k] for k in ("DATABASE_URL", "CACHE_ROOT", "AI_ENABLED", "SYNC_FILE_WORKERS", "DOCUMENT_CLASSIFICATION_V2")}, "timeline": timeline},
          open(R / "r5_repair_timeline.json", "w", encoding="utf-8"), indent=1)
print("done")
