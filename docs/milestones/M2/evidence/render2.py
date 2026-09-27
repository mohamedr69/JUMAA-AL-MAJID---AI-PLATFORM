"""Second independent render pass with long-path support: EP-30784 R1 sheets, the BOQ scan, vector frames on covers.
Read-only on sources; writes PNG/JSON into the scratchpad only."""
import json, os, sys, pathlib, re
import pymupdf

S = pathlib.Path(sys.argv[1]); OUT = S / "golden_render"
PREFIX = "\\\\?\\"


def lp(p: str) -> str:
    return p if p.startswith(PREFIX) else PREFIX + p


idx = json.load(open(S / "clone_doc_index.json")); docs = idx["docs"]
# fix the exists flag with long-path support
missing = 0
for k, d in docs.items():
    d["exists"] = os.path.isfile(lp(d["path"])) if d.get("path") else False
    missing += 0 if d["exists"] else 1
json.dump(idx, open(S / "clone_doc_index.json", "w"), indent=1, default=str)
print("docs", len(docs), "missing files (long-path aware)", missing)

byrel = {d["relative_path"]: (k, d) for k, d in docs.items()}
report = {"r1": [], "frames": {}}
n = 0
for rid, did, ref, floor, rev, st, src, rel, page, rt in idx["ep30784_r1"]:
    hit = byrel.get(rel)
    if not hit or not hit[1]["exists"]:
        report["r1"].append({"revision_row": rid, "ref": ref, "doc": hit[0] if hit else None, "file": "missing"}); continue
    k, d = hit
    pdf = pymupdf.open(lp(d["path"]))
    entry = {"revision_row": rid, "ref": ref, "floor": floor, "doc": k, "pages": len(pdf), "stored": [(r["revision"], r["status"], r["page"], r["source"]) for r in d["records"]], "page_info": []}
    for q in range(min(3, len(pdf))):
        pg = pdf[q]
        t = pg.get_text()
        entry["page_info"].append({"page": q + 1, "text_len": len(t), "images": len(pg.get_images()), "annots": [a.type[1] for a in pg.annots()] if pg.annots() else [],
                                   "rev_tokens": re.findall(r"REV(?:ISION)?[^\n]{0,20}", t, re.I)[:4], "stamp_words": re.findall(r"(?:Revise|Resubmit|Approved|Rejected)[^\n]{0,30}", t, re.I)[:4]})
        if n < 4:
            pg.get_pixmap(dpi=70).save(str(OUT / f"r1-{k}-p{q + 1}.png"))
    n += 1
    report["r1"].append(entry)
print("R1 rows with file:", sum(1 for e in report["r1"] if "pages" in e), "missing:", sum(1 for e in report["r1"] if e.get("file") == "missing"))

fas = [d for d in docs.values() if d["relative_path"].endswith("EP-30784 FAS Design.pdf")]
if fas:
    pdf = pymupdf.open(lp(fas[0]["path"])); print("FAS design pages", len(pdf))
    for q in range(len(pdf)):
        pg = pdf[q]; r = pg.rect
        pg.get_pixmap(dpi=100).save(str(OUT / f"boq-fas-p{q + 1}.png"))
        if q == 1:
            pg.get_pixmap(dpi=200, clip=pymupdf.Rect(0, r.height * 0.45, r.width, r.height * 0.95)).save(str(OUT / "boq-fas-p2-lower.png"))
            pg.get_pixmap(dpi=200, clip=pymupdf.Rect(0, r.height * 0.05, r.width, r.height * 0.5)).save(str(OUT / "boq-fas-p2-upper.png"))
for lab in ("409", "434", "440"):
    d = docs[lab]; pdf = pymupdf.open(lp(d["path"])); pg = pdf[0]
    opts = pg.search_for("C - Revise & Re-Submit"); dr = pg.get_drawings()
    near = []
    for x in dr:
        rc = x["rect"]
        if any(rc.intersects(o) for o in opts):
            near.append({"type": x.get("type"), "rect": [round(v) for v in rc], "color": x.get("color"), "fill": x.get("fill"), "width": x.get("width")})
    report["frames"][lab] = {"drawings": len(dr), "option_rects": [[round(v) for v in o] for o in opts], "near_option": near,
                             "annots": [(a.type[1], [round(v) for v in a.rect]) for a in pg.annots()] if pg.annots() else []}
    print(lab, json.dumps(report["frames"][lab])[:600])
json.dump(report, open(S / "render2_report.json", "w"), indent=1, default=str)
