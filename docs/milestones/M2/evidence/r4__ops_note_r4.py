"""Record, read-only, the backend processes found running at the end of the Review 02 correction and the state of the
live database file, and append the observation to the response and the acceptance summary."""
import json, sqlite3, hashlib, pathlib, subprocess, datetime, sys

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform"); D = REPO / "docs/milestones/M2"
live = REPO / "backend/ep_platform.db"
obs = {"at_local": datetime.datetime.now().isoformat(timespec="seconds"), "method": "read-only: file stats, netstat, process command lines, sqlite mode=ro"}
for name in ("backend/ep_platform.db", "backend/ep_platform.db-wal", "backend/ep_platform.db-shm", "backend/library/symbols/symbol_library.json"):
    p = REPO / name
    obs[name] = {"exists": p.is_file(), "bytes": p.stat().st_size if p.is_file() else None, "mtime_local": datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds") if p.is_file() else None}
obs["live_main_file_sha256"] = hashlib.sha256(live.read_bytes()).hexdigest()
obs["committed_snapshot_sha256"] = "8db4351189f92248d1e1f1d1"[:24] + "... (see r3__live_db_readonly_check.json; git show HEAD:backend/ep_platform.db)"
net = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
obs["port_8000_listeners"] = [l.strip() for l in net.splitlines() if ":8000" in l and "LISTEN" in l]
wm = subprocess.run(["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"], capture_output=True, text=True).stdout
obs["backend_processes"] = [" ".join(l.split()) for l in wm.splitlines() if any(k in l for k in ("uvicorn", "app.workers"))]
con = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
obs["database"] = {"project_documents": con.execute("select count(*) from project_documents").fetchone()[0],
                   "parser_versions": dict(con.execute("select coalesce(json_extract(extracted,'$.parser_version'),'none'), count(*) from project_documents group by 1").fetchall()),
                   "max_last_processed_at": con.execute("select max(last_processed_at) from project_documents").fetchone()[0],
                   "alembic_version": con.execute("select * from alembic_version").fetchall(),
                   "newest_jobs": con.execute("select id, kind, status, created_at from background_jobs order by id desc limit 3").fetchall(),
                   "last_login": con.execute("select email, last_login_at from users order by last_login_at desc limit 1").fetchall()}
con.close()
json.dump(obs, open(S / "live_check_r4.json", "w", encoding="utf-8"), indent=1, default=str)
db = obs["database"]
note = f"""

## Operations observed at the end of the Review 02 correction (read-only, {obs['at_local']} local)

Not done by this correction, and not asked for: **the backend is running from this checkout** -- an API (`uvicorn app.main:app --reload --port 8000`, listener on 127.0.0.1:8000) and the sync, document and IFC workers, with the launcher's command lines (`evidence/r4__live_check_r4.json`). Their start is outside this session: `backend/library/symbols/symbol_library.json` was exported at {obs['backend/library/symbols/symbol_library.json']['mtime_local']} and again earlier at 09:10:24 (an app start writes it), and `backend/ep_platform.db` (main file) was last written at {obs['backend/ep_platform.db']['mtime_local']} with an active write-ahead log of {obs['backend/ep_platform.db-wal']['bytes']} bytes. The live database file therefore no longer matches the committed snapshot (`git status` shows `M backend/ep_platform.db`; sha256 `{obs['live_main_file_sha256'][:16]}...` now). What the database holds, read-only: {db['project_documents']} documents, parser versions {db['parser_versions']}, last processing {db['max_last_processed_at']}, newest jobs {db['newest_jobs']}, last login {db['last_login']} -- **no document processing, job or login has happened since 2026-09-27**; the writes so far are the startup's own (migrations at the same head `{db['alembic_version'][0][0]}`, seeds, worker heartbeats). Implication: the workers run the code they started with and the API reloads on every edit, so both are running **uncommitted, in-progress correction code** (parser `parse-2026-09-28.4`, profile `default`) against the live database. Nothing has been processed, but the next sync or processing job would apply this code live. This correction did not start, stop, or use these processes, and does not do so now; whether to stop them (`stop-backend.bat`) until the re-review is the owner's call (`M2-RECOVERY-CHECKLIST.md`).
"""
p = D / "M2-REVIEW-RESPONSE.md"; s = p.read_text(encoding="utf-8"); assert "Operations observed at the end of the Review 02" not in s; p.write_text(s + note, encoding="utf-8")
p = D / "M2-ACCEPTANCE-REPORT.md"; s = p.read_text(encoding="utf-8")
old = "| Live data | untouched (no stop/start, no live processing, no repair, no model) |"
new = ("| Live data | untouched by this correction (no stop/start, no live processing, no repair, no model). **Observed at the end**: the backend (API + three workers) is running from this checkout, started outside this session; the live database file has been written by its startup (no document processing since 2026-09-27) and no longer matches the committed snapshot -- `M2-REVIEW-RESPONSE.md`, operations note; `evidence/r4__live_check_r4.json` |")
assert old in s; p.write_text(s.replace(old, new), encoding="utf-8")
print("ops note written; processes", len(obs["backend_processes"]), "listeners", obs["port_8000_listeners"])
