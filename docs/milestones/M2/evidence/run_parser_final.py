"""Isolated run of the current document_control parser over Golden originals. No database writes: settings point
at the clone and a scratch cache; files are opened read-only. Output: actual records per document."""
import json, os, sys, pathlib, time, datetime

S = pathlib.Path(sys.argv[1]); clone = sorted(S.glob("m2_clone_*.db"))[-1]
os.environ.update({"DATABASE_URL": f"sqlite:///{clone.as_posix()}", "CACHE_ROOT": str(S / "cache_final"), "AI_ENABLED": "false",
                   "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "ARCHIVE_DATASHEET_LIBRARIES": "{}",
                   "ARCHIVE_SUBMITTAL_LIBRARY": "", "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
                   "COMPLIANCE_KNOWLEDGE_SOURCE": "", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false",
                   "DOCUMENT_CLASSIFICATION_V2": "false", "SYNC_FILE_WORKERS": "0"})
for d in ("cache", "library", "uploads"):
    (S / d).mkdir(exist_ok=True)
sys.path.insert(0, r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
from app.services import document_control, document_sync, transmittals  # noqa: E402
from app.services.word_text import read_word_text  # noqa: E402

idx = json.load(open(S / "clone_doc_index.json"))["docs"]
ids = [int(x) for x in sys.argv[2].split(",")]
out = {"parser_version": document_control.PARSER_VERSION, "box_version": document_control.BOX_VERSION, "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "docs": {}}
for i in ids:
    d = idx[str(i)]; p = pathlib.Path(d["path"]); t0 = time.perf_counter()
    entry = {"relative_path": d["relative_path"], "role": d["role"], "stored": {"reference": d["reference"], "revision": d["revision"], "status": d["status"], "records": d["records"]}}
    try:
        st = os.stat(document_control._os_path(p)); modified = datetime.datetime.fromtimestamp(st.st_mtime_ns / 1e9, datetime.timezone.utc)
        if d["role"] == "transmittal" or p.suffix.lower() in (".doc", ".docx"):
            records = transmittals.read_transmittal(read_word_text(p), d["relative_path"], modified); notes = ()
        else:
            document_control.begin_stage_clock()
            records, notes = document_control._read_pdf(str(p), st.st_mtime_ns, st.st_size, True, d["sha256"])
        entry["actual"] = {"records": [{k: getattr(r, k, None) for k in ("category", "reference", "revision", "revision_source", "printed_revision", "status", "system_code", "raw_system", "floor", "name", "page", "source", "listed", "reply_text", "group_reference")} for r in records], "notes": list(notes)}
    except Exception as exc:  # noqa: BLE001
        entry["actual"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    entry["seconds"] = round(time.perf_counter() - t0, 1)
    out["docs"][i] = entry
    a = entry["actual"]
    print(i, d["relative_path"][-60:], "|", [(r["category"], r["reference"], r["revision"], r["status"], r["page"]) for r in a.get("records", [])][:5], a.get("notes", a.get("error"))[:1] if isinstance(a.get("notes", a.get("error")), list) else a.get("error"), entry["seconds"], "s")
json.dump(out, open(S / (sys.argv[3] if len(sys.argv) > 3 else "parser_actual.json"), "w"), indent=1, default=str)
