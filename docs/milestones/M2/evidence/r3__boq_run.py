"""R4: run the current deterministic Design Sheet reader (design_sheet_extractor.extract_design_sheet, OCR path,
no model) on the EP-30784 FAS and EML originals, read-only, and account every row against the Golden BOQ fixture.
No database, no job, no model: AI is disabled by settings before import. Output: boq_run.json + boq_run.log."""
import json, os, sys, pathlib, time, datetime, importlib.util, dataclasses

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); BACKEND = REPO / "backend"
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "AI_ENABLED": "false", "CACHE_ROOT": str(S / "cache_boq"),
                   "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false",
                   "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"), "SYNC_FILE_WORKERS": "0",
                   "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false"})
for d in ("cache_boq", "library", "uploads"):
    (S / d).mkdir(exist_ok=True)
os.chdir(BACKEND); sys.path.insert(0, str(BACKEND))
from app.core.config import get_settings  # noqa: E402
from app.services import design_sheet_extractor as dse  # noqa: E402

spec = importlib.util.spec_from_file_location("boq_metrics", BACKEND / "scripts" / "boq_metrics.py"); bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)
idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
golden = json.load(open(BACKEND / "tests" / "fixtures" / "boq_ep30784_golden_v1.json", encoding="utf-8"))
settings = get_settings()
out = {"run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "reader": "app.services.design_sheet_extractor.extract_design_sheet (deterministic OCR path)",
       "model": {"ai_enabled": settings.ai_enabled, "note": "no model; AI disabled by settings before import"}, "tesseract_cmd": settings.tesseract_cmd, "render_dpi": dse.RENDER_DPI,
       "golden_fixture": "backend/tests/fixtures/boq_ep30784_golden_v1.json", "systems": {}}
log = []
for system, doc_id in (("FAS", "3"), ("EML", "2")):
    d = idx[doc_id]; p = pathlib.Path(d["path"]); t0 = time.perf_counter(); pages = []

    def on_page(n, total, _pages=pages, _t0=t0):
        _pages.append({"page": n, "of": total, "started_s": round(time.perf_counter() - _t0, 2)})

    result = dse.extract_design_sheet(p, on_page=on_page)
    seconds = round(time.perf_counter() - t0, 1)
    lines = [{"part_number": ln.catalog_no, "description": ln.description, "quantity": ln.quantity, "group": ln.group_heading, "page": ln.page,
              "confidence": round(ln.confidence, 1), "quantity_confidence": ln.quantity_confidence, "row_id": ln.row_id, "region": ln.region()} for ln in result.lines]
    issues = [{"code": getattr(i.code, "value", str(i.code)), "page": i.page, "target": i.target, "detail": i.detail} for i in result.issues]
    gold = [r for r in golden["rows"] if r["system_code"] == system]
    m = bm.metrics(gold, [dict(l) for l in lines], [], run_info={"state": result.state, "reader": result.reader, "failure": result.failure, "wall_s": seconds})
    pairs = bm.match_rows(gold, [dict(l) for l in lines])
    accounting = {"golden_rows": len(gold), "lines_read": len(lines), "matched": sum(1 for g, e in pairs if g and e), "missing": sum(1 for g, e in pairs if g and not e),
                  "extra": sum(1 for g, e in pairs if g is None), "issues": len(issues), "issues_by_code": {}, "outcome": getattr(result.outcome, "value", str(result.outcome))}
    for i in issues:
        accounting["issues_by_code"][i["code"]] = accounting["issues_by_code"].get(i["code"], 0) + 1
    out["systems"][system] = {"doc_id": int(doc_id), "relative_path": d["relative_path"], "sha256": d["sha256"], "seconds": seconds, "pages": pages,
                              "coverage": [dataclasses.asdict(c) if dataclasses.is_dataclass(c) else str(c) for c in result.coverage.pages],
                              "state": result.state, "reader": result.reader, "failure": result.failure, "notes": result.notes, "buildings": result.buildings,
                              "accounting": accounting, "lines": lines, "issues": issues, "metrics": m,
                              "pairs": [{"golden": {k: g.get(k) for k in ("page", "part_number", "quantity", "group")} if g else None,
                                         "read": {k: e.get(k) for k in ("page", "part_number", "quantity", "group", "row_id")} if e else None} for g, e in pairs]}
    line = f"{system}: {seconds}s pages={len(pages)} lines={len(lines)} golden={len(gold)} matched={accounting['matched']} missing={accounting['missing']} extra={accounting['extra']} issues={accounting['issues_by_code']} state={result.state} outcome={accounting['outcome']} failure={result.failure}"
    log.append(line); print(line)
    for k in ("part_number_accuracy", "quantity_accuracy", "pair_accuracy", "group_accuracy", "row_detection_recall", "false_removal_rate"):
        print("  ", k, m[k])
json.dump(out, open(S / "boq_run.json", "w", encoding="utf-8"), indent=1, default=str)
(S / "boq_run.log").write_text("\n".join(log) + "\n", encoding="utf-8")
