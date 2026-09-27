"""Independent rendering/text dump of Golden candidate originals (pymupdf directly, not the parser).
Reads the clone read-only for paths; reads source files read-only; writes PNG/JSON into the scratchpad only."""
import json, os, re, sqlite3, sys, pathlib, hashlib
import pymupdf

S = pathlib.Path(sys.argv[1]); OUT = S / "golden_render"; OUT.mkdir(exist_ok=True)
db = sorted(S.glob("m2_clone_*.db"))[-1]
con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True); con.execute("PRAGMA query_only=1")
G = "ICC-DLRC-SPM-SD-MEP"
DATE = re.compile(r"\d{1,2}[-/.](?:\d{1,2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/.]\d{2,4}", re.I)

rows = con.execute("select id, project_id, role, state, relative_path, path, sha256, page_count, reference, revision, status, extracted from project_documents where state!='removed' order by id").fetchall()
pop = {}; info = {}
def add(k, i): pop.setdefault(k, set()).add(i)
for id_, pid, role, state, rel, path, sha, pc, ref, rev, st, ext in rows:
    e = json.loads(ext) if ext else {}; recs = e.get("records") or []
    info[id_] = {"project_id": pid, "role": role, "relative_path": rel, "path": path, "sha256": sha, "page_count": pc, "reference": ref, "revision": rev, "status": st,
                 "records": [{k: r.get(k) for k in ("category", "reference", "revision", "status", "system_code", "page", "source", "listed", "floor", "name", "reply_text")} for r in recs],
                 "notes": e.get("notes"), "form": e.get("form") or None, "exists": os.path.isfile(path or "")}
    if any(DATE.fullmatch(str(r.get("reference") or "").strip()) for r in recs): add("date_embedded", id_)
    if any(str(r.get("reference") or "") == G for r in recs): add("generic_embedded_exact", id_)
    if ref and ref.startswith(G) and ref != G: add("generic_prefix_only", id_)
    if role == "transmittal": add("word_transmittals", id_)
fk = next(c[1] for c in con.execute("PRAGMA table_info(shop_drawing_revisions)") if c[1].endswith("_id") and "drawing" in c[1])
r1 = con.execute(f"select r.id, d.id, d.drawing_reference, d.floor_label, r.revision, r.status, r.source, r.drawing_path, r.drawing_page, r.reply_text from shop_drawing_revisions r join project_shop_drawings d on d.id=r.{fk} where d.project_id=1 and r.revision in ('R1','R01') order by r.id").fetchall()
json.dump({"populations": {k: sorted(v) for k, v in pop.items()}, "ep30784_r1": r1, "docs": {str(k): v for k, v in info.items()}}, open(S / "clone_doc_index.json", "w"), indent=1, default=str)
print({k: len(v) for k, v in pop.items()}, "R1 rows", len(r1))

ids = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else []
summary = {}
for i in ids:
    d = info.get(i)
    if not d or not d["exists"]:
        summary[i] = {"error": "missing row or file"}; continue
    p = pathlib.Path(d["path"])
    if p.suffix.lower() != ".pdf":
        summary[i] = {"skipped": "not a pdf", "path": d["relative_path"]}; continue
    try:
        pdf = pymupdf.open(str(p))
    except Exception as exc:
        summary[i] = {"error": f"{type(exc).__name__}: {exc}"[:200]}; continue
    n = len(pdf); pages = list(range(min(n, 4)))
    texts = {}
    for k in pages:
        page = pdf[k]
        texts[k + 1] = page.get_text()[:6000]
        pix = page.get_pixmap(dpi=90)
        pix.save(str(OUT / f"doc-{i}-p{k + 1}.png"))
        annots = [(a.type[1], a.rect.round().tolist() if hasattr(a.rect, 'tolist') else str(a.rect)) for a in page.annots()] if page.annots() else []
        texts[f"{k + 1}_annots"] = annots[:20]
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    summary[i] = {"relative_path": d["relative_path"], "pages": n, "sha256_now": sha, "sha256_row": d["sha256"], "same_hash": sha == d["sha256"], "text": texts}
    pdf.close()
json.dump(summary, open(OUT / "render_summary.json", "w"), indent=1, default=str)
for i, s in summary.items():
    print(i, s.get("relative_path", s)[:80] if isinstance(s.get("relative_path"), str) else s, "pages", s.get("pages"), "hash ok", s.get("same_hash"))
