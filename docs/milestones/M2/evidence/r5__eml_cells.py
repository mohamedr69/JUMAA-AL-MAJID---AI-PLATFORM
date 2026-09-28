"""R3-03: the EML sheet's catalog cells read whole (psm 6 over the row's full bounds) vs line by line (psm 7), and
crops of the disputed FAS rows (6538-G5 and the cut SIGA-OSHD-FC) for source adjudication."""
import sys, pathlib, json, os
import numpy as np, pymupdf, pytesseract
from PIL import Image, ImageOps
S = pathlib.Path(sys.argv[1]); idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "AI_ENABLED": "false"})
sys.path.insert(0, r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
from app.services import design_sheet_extractor as dse
from app.extraction import identity
from app.core.config import get_settings
if get_settings().tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = get_settings().tesseract_cmd
OUT = S / "crops"; OUT.mkdir(exist_ok=True)
WL = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+. "


def passes(img, x0, x1, y0, y1, tag):
    cell = img.crop((x0 + 4, y0 + 2, x1 - 4, y1 - 2)); cell.save(OUT / f"{tag}.png")
    padded = ImageOps.expand(cell, border=30, fill=255); grey = padded.resize((padded.width * 3, padded.height * 3), Image.LANCZOS)
    arr = np.array(grey); binar = Image.fromarray(np.where(arr < dse._otsu_threshold(arr), 0, 255).astype(np.uint8))
    out = {}
    for name, src, cfg in (("grey7", grey, "--psm 7 " + WL), ("bin7", binar, "--psm 7 " + WL), ("grey6", grey, "--psm 6 " + WL), ("bin6", binar, "--psm 6 " + WL)):
        t = pytesseract.image_to_string(src, config=cfg)
        out[name] = " ".join(t.split())
    # ink touching the right edge of the cell: a clipped part
    dark = np.array(cell) < 128
    out["right_edge_ink"] = round(float(dark[:, -6:].mean()), 3) if dark.size else None
    out["left_edge_ink"] = round(float(dark[:, :6].mean()), 3) if dark.size else None
    return out


# EML
pdf = pymupdf.open(idx["2"]["path"]); page = pdf[0]; img = dse._render_page(page); dark = np.array(img) < 128
rules, layout = dse._find_layout(dark); cx0, cx1 = rules[layout.catalog[0]], rules[layout.catalog[1]]
print("EML rules", rules)
for top, bottom in dse._table_extents(dark, rules[layout.description[0]]):
    lines, _h = dse._read_table(img, dark, rules, layout, 1, top, bottom, None)
    for i, l in enumerate(lines):
        if l.row_bounds:
            y0, y1 = l.row_bounds
        elif l.y_px:
            y0, y1 = l.y_px - dse._CELL_HALF_HEIGHT_PX, l.y_px + dse._CELL_HALF_HEIGHT_PX
        else:
            continue
        r = passes(img, cx0, cx1, int(y0), int(y1), f"eml-row{i}")
        print(f"EML row{i} strip={l.catalog_no!r} conf={l.catalog_confidence} bounds={l.row_bounds} h={int(y1 - y0)} | {r}")
# FAS page 1: 6538-G5 region; page 2: the band row
pdf = pymupdf.open(idx["3"]["path"])
for pn, want in ((0, "6538-G5"), (1, "SIGA-OSHD-FC")):
    page = pdf[pn]; img = dse._render_page(page); dark = np.array(img) < 128
    rules, layout = dse._find_layout(dark); cx0, cx1 = rules[layout.catalog[0]], rules[layout.catalog[1]]
    extents = dse._table_extents(dark, rules[layout.description[0]])
    spans = list(extents)
    if pn == 1:
        (t0, b0), (t1, b1) = extents[:2]; spans = [(b0, t1)] + spans
    for top, bottom in spans:
        lines, _h = dse._read_table(img, dark, rules, layout, pn + 1, top, bottom, None)
        for l in lines:
            if l.catalog_no and l.catalog_no.startswith(want.split("-")[0]) and want[:6] in (l.catalog_no or ""):
                y0, y1 = l.row_bounds or (l.y_px - 22, l.y_px + 22)
                r = passes(img, cx0, cx1, int(y0), int(y1), f"fas-{want}")
                print(f"FAS p{pn+1} {want}: strip={l.catalog_no!r} group={l.group_heading!r} y={l.y_px} | {r}")
                # context crop: 5 rows above to 2 below, full table width
                img.crop((rules[0], max(0, int(y0) - 260), rules[-1], min(img.height, int(y1) + 100))).save(OUT / f"fas-{want}-context.png")
print("done")
