"""D3 experiment: independent re-reads of the catalog cells of FAS rows -- the four misread parts and some right ones --
to see whether a second reading of the cell alone separates the uncertain tokens from the certain ones."""
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
pdf = pymupdf.open(idx["3"]["path"])
WATCH = {"4-CABI6D", "SIGA-AAS0", "WSTIA-T", "G1IARN", "4-CPU", "SIGA-SB", "TP606", "3-SDDC2", "SIGA-CC2A", "6833-4", "757-1A-T", "STI-1230", "4-PPS/M", "SIGA-CT2"}
CONFIGS = (("grey psm7", "greyscale", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
           ("bin psm7", "binarised", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
           ("grey psm8", "greyscale", "--psm 8"))
print("start", flush=True)
for pn in (0, 1):
    page = pdf[pn]; img = dse._render_page(page); dark = np.array(img) < 128
    rules, layout = dse._find_layout(dark); print("page", pn + 1, "rules", rules, flush=True)
    cx0, cx1 = rules[layout.catalog[0]], rules[layout.catalog[1]]
    for top, bottom in dse._table_extents(dark, rules[layout.description[0]]):
        _ls = dse._read_table(img, dark, rules, layout, pn + 1, top, bottom, None); print("table", top, bottom, [(l.catalog_no, l.row_bounds) for l in _ls][:6], flush=True)
        for l in _ls:
            if l.catalog_no not in WATCH or l.y_px is None:
                continue
            rt, rb = (l.row_bounds if l.row_bounds else (int(l.y_px - dse._CELL_HALF_HEIGHT_PX), int(l.y_px + dse._CELL_HALF_HEIGHT_PX)))
            cell = img.crop((cx0 + 4, rt + 2, cx1 - 4, rb - 2))
            padded = ImageOps.expand(cell, border=30, fill=255); grey = padded.resize((padded.width * 3, padded.height * 3), Image.LANCZOS)
            arr = np.array(grey); binar = Image.fromarray(np.where(arr < dse._otsu_threshold(arr), 0, 255).astype(np.uint8))
            reads = []
            for name, src, cfg in CONFIGS:
                t = pytesseract.image_to_string(grey if src == "greyscale" else binar, config=cfg).strip()
                reads.append((name, t, identity.clean_catalog(t)[0]))
            print(f"p{pn+1} strip={l.catalog_no!r} raw={l.catalog_raw!r} conf={l.confidence:.0f}", [(n, c) for n, t, c in reads])
