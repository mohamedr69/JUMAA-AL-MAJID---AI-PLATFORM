"""Source review of the Golden population: for every manifest case, crop the original's page-1 header (No / Rev / Date)
and the consultant recommendation row, and lay them out on contact sheets (12 per sheet) for a visual check.
Reads originals read-only through pymupdf (long paths); writes PNGs and a JSON index into the scratchpad only."""
import json, os, sys, pathlib
import pymupdf
from PIL import Image, ImageDraw

S = pathlib.Path(sys.argv[1]); M = pathlib.Path(sys.argv[2]); OUT = S / "crops"; OUT.mkdir(exist_ok=True)
PREFIX = "\\\\?\\"
manifest = json.load(open(M / "M2-GOLDEN-MANIFEST.json", encoding="utf-8"))
idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
cases = [c for c in manifest["cases"] if c["format"] == "text-pdf"]
index = []
tiles = []
for c in cases:
    d = idx[str(c["doc_id"])]; p = d["path"]
    try:
        pdf = pymupdf.open(PREFIX + p if not p.startswith(PREFIX) else p); page = pdf[0]
    except Exception as exc:  # noqa: BLE001
        index.append({"doc_id": c["doc_id"], "error": str(exc)[:120]}); continue
    r = page.rect
    hits = page.search_for("No:")
    header = pymupdf.Rect(r.width * 0.55, hits[0].y0 - 6, r.width, hits[0].y1 + 26) if hits else pymupdf.Rect(r.width * 0.5, r.height * 0.12, r.width, r.height * 0.2)
    rec = page.search_for("Consultant") or page.search_for("Recommendation")
    rec_rect = None
    hits_r = page.search_for("Recommendation") + page.search_for("Approved")
    if hits_r:
        rec_rect = pymupdf.Rect(0, min(h.y0 for h in hits_r) - 10, r.width, max(h.y1 for h in hits_r) + 12)
    if rec_rect is None:
        rec_rect = pymupdf.Rect(0, r.height * 0.74, r.width, r.height * 0.8)
    header = header & r; rec_rect = rec_rect & r
    if header.is_empty or header.width < 20 or header.height < 10:
        header = pymupdf.Rect(r.width * 0.5, r.height * 0.12, r.width, r.height * 0.2) & r
    if rec_rect.is_empty or rec_rect.width < 20 or rec_rect.height < 10:
        rec_rect = pymupdf.Rect(0, r.height * 0.74, r.width, r.height * 0.8) & r
    try:
        a = page.get_pixmap(dpi=110, clip=header); b = page.get_pixmap(dpi=80, clip=rec_rect)
    except Exception as exc:  # noqa: BLE001
        index.append({"doc_id": c["doc_id"], "error": "render: " + str(exc)[:120]}); pdf.close(); continue
    ha = OUT / f"h-{c['doc_id']}.png"; hb = OUT / f"r-{c['doc_id']}.png"; a.save(str(ha)); b.save(str(hb))
    index.append({"doc_id": c["doc_id"], "expected_reference": c["label"]["expected_reference"], "relative_path": c["relative_path"][-60:], "header": ha.name, "row": hb.name,
                  "stored_after": [(x.get("reference"), x.get("revision"), x.get("status")) for x in (c["actual_after_parse_2026_09_28_2"] if isinstance(c["actual_after_parse_2026_09_28_2"], list) else [])][:2]})
    tiles.append((c["doc_id"], ha, hb))
    pdf.close()
# contact sheets: 6 rows x 2 columns of (header, row) pairs
sheet_no = 0
for start in range(0, len(tiles), 6):
    group = tiles[start:start + 6]
    ims = [(i, Image.open(h), Image.open(rw)) for i, h, rw in group]
    W = 1700; row_h = 300
    sheet = Image.new("RGB", (W, row_h * len(ims) + 10), "white"); draw = ImageDraw.Draw(sheet)
    for n, (i, h, rw) in enumerate(ims):
        y = n * row_h + 5
        draw.text((5, y), f"doc {i}", fill="red")
        h.thumbnail((700, row_h - 30)); rw.thumbnail((960, row_h - 30))
        sheet.paste(h, (40, y + 12)); sheet.paste(rw, (740, y + 12))
    sheet_no += 1
    sheet.save(OUT / f"sheet-{sheet_no:02d}.png")
json.dump(index, open(OUT / "index.json", "w"), indent=1)
print("cases", len(cases), "tiles", len(tiles), "sheets", sheet_no, "errors", sum(1 for x in index if "error" in x))
