"""D1 experiment: can the reader's own row reader read the inked band between the two tables on FAS page 2?"""
import sys, pathlib, json, os
import numpy as np, pymupdf
S = pathlib.Path(sys.argv[1]); idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "AI_ENABLED": "false"})
sys.path.insert(0, r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
from app.services import design_sheet_extractor as dse
pdf = pymupdf.open(idx["3"]["path"]); page = pdf[1]
img = dse._render_page(page); dark = np.array(img) < 128
rules, layout = dse._find_layout(dark)
extents = dse._table_extents(dark, rules[layout.description[0]]); print("extents", extents)
(t0, b0), (t1, b1) = extents[:2]
for pad in (0, 6, 12):
    lines = dse._read_table(img, dark, rules, layout, 2, b0 - pad, t1 + pad, None)
    print("pad", pad, [(l.catalog_no, l.quantity, l.description[:40], l.raw_quantity, l.quantity_confidence) for l in lines])
# the TP606 cell: what do the independent passes read?
lines = dse._read_table(img, dark, rules, layout, 2, t1, b1, None)
for l in lines:
    if l.catalog_no in ("TP606", "TP434", "G1IARN", "WSTIA-T", "SIGA-SB"):
        l.quantity_span = (rules[layout.quantity[0]], rules[layout.quantity[1]]); l.table_span = (rules[0], rules[-1])
        readings = dse._independent_readings(img, l)
        print(l.catalog_no, l.quantity, l.quantity_confidence, "passes", [(r["pass"][:12], r["text"], r["value"]) for r in (readings or [])])
