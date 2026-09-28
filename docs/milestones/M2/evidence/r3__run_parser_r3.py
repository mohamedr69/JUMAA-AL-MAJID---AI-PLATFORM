"""Isolated run of the corrected parser (parse-2026-09-28.3) over the M2 population with the full ledger.
No database writes (in-memory DB, scratch cache); files opened read-only. Modes: default (settings gate, promote=None)
or promoted (promote=True). Output: per document records (with flags and decision candidates), notes, coverage,
observations and wall seconds."""
import json, os, sys, pathlib, time, datetime

S = pathlib.Path(sys.argv[1]); M = S.parent / "m2"; out_name = sys.argv[2]; cache = sys.argv[3]; mode = sys.argv[4]
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "CACHE_ROOT": str(S / cache), "AI_ENABLED": "false",
                   "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "ARCHIVE_DATASHEET_LIBRARIES": "{}",
                   "ARCHIVE_SUBMITTAL_LIBRARY": "", "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
                   "COMPLIANCE_KNOWLEDGE_SOURCE": "", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false",
                   "DOCUMENT_CLASSIFICATION_V2": "false", "SYNC_FILE_WORKERS": "0"})
for d in (cache, "library", "uploads"):
    (S / d).mkdir(exist_ok=True)
BACKEND = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend"); os.chdir(BACKEND); sys.path.insert(0, str(BACKEND))
from app.core.config import get_settings  # noqa: E402
from app.services import document_control, transmittals  # noqa: E402
from app.services.word_text import read_word_text  # noqa: E402

promote = True if mode == "promoted" else None
idx = json.load(open(M / "clone_doc_index.json", encoding="utf-8"))["docs"]
ids = sorted(int(k) for k in json.load(open(M / "parser_population_after.json", encoding="utf-8"))["docs"])
if len(sys.argv) > 5:
    ids = [int(x) for x in sys.argv[5].split(",")]
FIELDS = ("category", "reference", "revision", "revision_source", "printed_revision", "status", "system_code", "raw_system", "floor", "name", "page", "source", "listed", "reply_text", "group_reference", "flags", "decision_candidates")
out = {"parser_version": document_control.PARSER_VERSION, "box_version": document_control.BOX_VERSION, "mode": mode, "promote_argument": promote,
       "settings_extraction_promote_observations": get_settings().extraction_promote_observations, "cache": cache,
       "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "docs": {}}
for i in ids:
    d = idx[str(i)]; p = pathlib.Path(d["path"]); t0 = time.perf_counter()
    entry = {"relative_path": d["relative_path"], "role": d["role"]}
    try:
        st = os.stat(document_control._os_path(p)); modified = datetime.datetime.fromtimestamp(st.st_mtime_ns / 1e9, datetime.timezone.utc)
        if d["role"] == "transmittal" or p.suffix.lower() in (".doc", ".docx"):
            records = transmittals.read_transmittal(read_word_text(p), d["relative_path"], modified)
            entry["actual"] = {"records": [{k: getattr(r, k, None) for k in FIELDS} for r in records], "notes": [], "coverage": {"outcome": "complete", "reader": "transmittal"}, "observations": []}
        else:
            document_control.begin_stage_clock()
            reading = document_control.read_pdf_full(str(p), st.st_mtime_ns, st.st_size, True, d["sha256"], promote=promote)
            entry["actual"] = {"records": [{k: getattr(r, k, None) for k in FIELDS} for r in reading.records], "notes": list(reading.notes),
                               "coverage": reading.coverage, "observations": list(reading.observations)}
    except Exception as exc:  # noqa: BLE001
        entry["actual"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    entry["seconds"] = round(time.perf_counter() - t0, 1)
    out["docs"][i] = entry
    a = entry["actual"]; cov = a.get("coverage") or {}
    print(i, d["relative_path"][-55:], "|", [(r["category"], r["reference"], r["revision"], r["status"], r["page"], list(r["flags"] or ())) for r in a.get("records", [])][:4],
          cov.get("outcome"), len(a.get("observations", [])), "obs", a.get("error", ""), entry["seconds"], "s", flush=True)
json.dump(out, open(S / out_name, "w", encoding="utf-8"), indent=1, default=str)
print("written", out_name, "docs", len(out["docs"]), "total_s", round(sum(e["seconds"] for e in out["docs"].values()), 1))
