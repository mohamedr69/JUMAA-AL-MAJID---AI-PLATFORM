"""R5, run 2: after the JSON-safe observation fix, repair the rows run 1 failed on (35: every untracked-discipline
cover and scanned transmittal) on the same clone, then take the after-snapshots and record dumps again. Run 1's
after-artifacts are kept under *_run1 -- the failed run stays in the record."""
import json, os, sys, pathlib, shutil, subprocess, time, datetime, importlib.util

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); BACKEND = REPO / "backend"; PY = BACKEND / "venv" / "Scripts" / "python.exe"
R = S / "repair_r3"; clone = S / "clone_r3.db"
spec = importlib.util.spec_from_file_location("r5", S / "r5_repair.py")
env = dict(os.environ)
env.update({"DATABASE_URL": f"sqlite:///{clone.as_posix()}", "CACHE_ROOT": str(S / "cache_r3"), "AI_ENABLED": "false",
            "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "ARCHIVE_DATASHEET_LIBRARIES": "{}",
            "ARCHIVE_SUBMITTAL_LIBRARY": "", "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
            "COMPLIANCE_KNOWLEDGE_SOURCE": "", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false",
            "DOCUMENT_CLASSIFICATION_V2": "false", "SYNC_FILE_WORKERS": "0", "PYTHONIOENCODING": "utf-8"})
timeline = json.load(open(R / "r5_repair_timeline.json", encoding="utf-8"))
for name in ("snapshot_after_p4.json", "snapshot_after_p1.json", "golden_after_p4.json", "golden_after_p1.json", "records_after.json"):
    src = R / name
    if src.is_file() and not (R / name.replace(".json", "_run1.json")).is_file():
        shutil.copyfile(src, R / name.replace(".json", "_run1.json"))


def run(args, log):
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8") as h:
        rc = subprocess.run([str(PY)] + args, cwd=BACKEND, env=env, stdout=h, stderr=subprocess.STDOUT).returncode
    timeline["timeline"].append({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "run": 2, "cmd": " ".join(args), "rc": rc, "seconds": round(time.perf_counter() - t0, 1)})
    print(rc, round(time.perf_counter() - t0, 1), "s", " ".join(args)[:100], flush=True)


for pid in ("4", "1"):
    m = json.load(open(R / f"repair_p{pid}.json", encoding="utf-8"))
    failed = [str(e["document_id"]) for e in m["entries"] if e.get("outcome") == "failed"]
    if failed:
        run(["scripts/repair_extraction.py", "--project", pid, "--ids", ",".join(failed), "--apply", "--manifest", str(R / f"repair_p{pid}_rerun.json")], R / f"repair_p{pid}_rerun.log")
for pid in ("4", "1"):
    run(["scripts/business_snapshot.py", pid, str(R / f"snapshot_after_p{pid}.json")], R / f"snapshot_after_p{pid}_run2.log")
    run(["scripts/golden_records.py", "--from-db", pid, "--out", str(R / f"golden_after_p{pid}.json")], R / f"golden_after_p{pid}_run2.log")
# the record dump, as run 1 made it
src = (S / "r5_repair.py").read_text(encoding="utf-8")
ns = {}
exec(src[src.index("def dump_records"):src.index("for pid in (\"4\", \"1\"):")], {"sqlite3": __import__("sqlite3"), "json": json, "clone": clone, "R": R}, ns)
ns["dump_records"]("after")
timeline["run2_note"] = "run 2 = the rows run 1 failed on (datetime in an observation record; fixed by document_control.observed_record), same clone, same settings"
json.dump(timeline, open(R / "r5_repair_timeline.json", "w", encoding="utf-8"), indent=1)
print("done")
