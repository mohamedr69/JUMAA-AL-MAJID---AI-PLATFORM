"""R3-03 clip detection: ink in the columns just inside the catalog cell's right rule, for the cut SIGA-OSHD-FC row
against uncut rows; and the SAR sample documents (351/352/353) read by the current reader for the compatibility note."""
import sys, pathlib, json, os, datetime
import numpy as np, pymupdf
S = pathlib.Path(sys.argv[1]); idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "AI_ENABLED": "false", "CACHE_ROOT": str(S / "cache_sar"), "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
                   "DATASHEET_LIBRARIES": "{}", "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "SYNC_FILE_WORKERS": "0", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false"})
for d in ("cache_sar", "library", "uploads"):
    (S / d).mkdir(exist_ok=True)
sys.path.insert(0, r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend"); os.chdir(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
from app.services import design_sheet_extractor as dse, document_control as dc

pdf = pymupdf.open(idx["3"]["path"]); page = pdf[1]; img = dse._render_page(page); dark = np.array(img) < 128
rules, layout = dse._find_layout(dark); cx0, cx1 = rules[layout.catalog[0]], rules[layout.catalog[1]]
# where exactly is the rule? columns with high ink near cx1
prof = dark[:, cx1 - 20: cx1 + 6].mean(axis=0); print("rule profile", [round(float(v), 2) for v in prof])
rule_x = cx1 - 20 + int(np.argmax(prof)); print("rule at", rule_x)
extents = dse._table_extents(dark, rules[layout.description[0]]); (t0, b0), (t1, b1) = extents[:2]
rows = []
for top, bottom in [(b0, t1), (t0, b0), (t1, b1)]:
    lines, _ = dse._read_table(img, dark, rules, layout, 2, top, bottom, None)
    for l in lines:
        if not l.catalog_no: continue
        y0, y1 = l.row_bounds or (int(l.y_px - 22), int(l.y_px + 22))
        cell = dark[int(y0) + 2: int(y1) - 2, cx0 + 4: rule_x - 3]        # up to 3 px before the rule
        inner = cell[:, -6:]                                            # the last 6 px before the rule
        gap = cell[:, -14:-6]
        rows.append((l.catalog_no, round(float(inner.mean()), 3), round(float(gap.mean()), 3), round(float(cell[:, :-14].mean()), 3)))
for r in sorted(rows, key=lambda r: -r[1])[:8]: print("edge ink", r)
print("...", [r for r in rows if r[0] in ("SIGA-OSHD-FC", "SIGA-OSD-FCN", "SIGA-HRD-FCN", "SIGA-SB")])

out = {}
for k in ("351", "352", "353"):
    d = idx[k]; p = d["path"]; st = os.stat(dc._os_path(pathlib.Path(p)))
    for promote in (False, True):
        r = dc.read_pdf_full(p, st.st_mtime_ns, st.st_size, True, d["sha256"], promote=promote)
        out[f"{k}:{'promoted' if promote else 'default'}"] = {"relative_path": d["relative_path"], "stored_status": d.get("status"),
            "records": [{"page": x.page, "reference": x.reference, "revision": x.revision, "status": x.status, "flags": list(x.flags), "candidates": [list(c) for c in x.decision_candidates], "reply_text": (x.reply_text or "")[:120]} for x in r.records],
            "outcome": r.coverage["outcome"], "observations": [o.get("kind") for o in r.observations]}
        print(k, promote, [(x["page"], x["status"], x["flags"], x["candidates"]) for x in out[f"{k}:{'promoted' if promote else 'default'}"]["records"]][:2])
json.dump({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "parser_version": dc.PARSER_VERSION, "docs": out}, open(S / "sar_sample_reads.json", "w", encoding="utf-8"), indent=1, default=str)
