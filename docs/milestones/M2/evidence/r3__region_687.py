"""R4: bounded region recovery on Golden case 687 (scanned KLING/Wasl material submittal, page 1).
Evidence-level only: (1) OCR of the reference cell alone, lines joined at the hyphen; (2) mark analysis of the five
Status checkboxes; (3) what the current parser records for the page (flags, candidates). Nothing is written to any
database; the original is opened read-only; crops and JSON go to the scratchpad."""
import json, os, sys, pathlib, re, datetime
import numpy as np
import pymupdf
from PIL import Image

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); BACKEND = REPO / "backend"; OUT = S / "region687"; OUT.mkdir(exist_ok=True)
os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "AI_ENABLED": "false", "CACHE_ROOT": str(S / "cache687"), "DATASHEET_LIBRARIES": "{}",
                   "PROJECTS_ROOT": "", "PROJECTS_ROOT_AUTODETECT": "false", "LIBRARY_ROOT": str(S / "library"), "UPLOADS_ROOT": str(S / "uploads"),
                   "SYNC_FILE_WORKERS": "0", "COMPLIANCE_KNOWLEDGE_AUTODETECT": "false", "COMPLIANCE_KNOWLEDGE_IMPORT_ON_START": "false"})
(S / "cache687").mkdir(exist_ok=True)
os.chdir(BACKEND); sys.path.insert(0, str(BACKEND))
from app.services import document_control as dc  # noqa: E402

pyt = dc._tesseract()
idx = json.load(open(S.parent / "m2" / "clone_doc_index.json", encoding="utf-8"))["docs"]
d = idx["687"]; p = d["path"]; EXPECTED = "R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ-ZZZ-1004"
pdf = pymupdf.open(p); page = pdf[0]
DPI = 300
pix = page.get_pixmap(dpi=DPI); img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
img.save(OUT / "p1-300dpi.png")
data = pyt.image_to_data(img, output_type=pyt.Output.DICT, config="--psm 6")
words = [{"t": data["text"][i].strip(), "x0": data["left"][i], "y0": data["top"][i], "x1": data["left"][i] + data["width"][i], "y1": data["top"][i] + data["height"][i]}
         for i in range(len(data["text"])) if data["text"][i].strip()]
report = {"doc_id": 687, "relative_path": d["relative_path"], "sha256": d["sha256"], "page": 1, "dpi": DPI, "expected_reference": EXPECTED,
          "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def find(pred):
    return [w for w in words if pred(w["t"])]


# --- 1. reference cell -----------------------------------------------------------------------------------------------
sub = find(lambda t: t.upper().startswith("SUBMITTAL")); rev = find(lambda t: t.lower().startswith("rev"))
ref = {"method": "bounded OCR of the reference cell (right of 'MATERIAL SUBMITTAL', left of 'Rev.'), psm 6, lines joined at a trailing hyphen"}
if sub and rev:
    s = min(sub, key=lambda w: w["y0"]); r = min((w for w in rev if abs((w["y0"] + w["y1"]) / 2 - (s["y0"] + s["y1"]) / 2) < 120), key=lambda w: w["x0"], default=None)
    if r is None:
        r = {"x0": img.width - 250}
    # the cell's right edge is the vertical rule between it and the Rev cell: the first column right of the
    # label whose dark fraction over the row band is that of a ruled line
    band = np.array(img.crop((s["x1"] + 60, max(0, s["y0"] - 40), img.width, s["y1"] + 90))) < 128
    colfrac = band.mean(axis=0); rules = np.where(colfrac > 0.6)[0]
    groups = []
    for c in rules:
        if groups and c - groups[-1][-1] <= 3:
            groups[-1].append(int(c))
        else:
            groups.append([int(c)])
    edges = [s["x1"] + 60 + g[0] for g in groups]
    # the first rule closes the label's own cell; the reference cell runs from it to the next rule
    left = edges[0] + 12 if edges else s["x1"] + 20
    right = edges[1] - 12 if len(edges) > 1 else (r["x0"] - 20)
    cell = (left, max(0, s["y0"] - 40), right, s["y1"] + 90)
    crop = img.crop(cell); crop.save(OUT / "reference-cell.png")
    ref["cell_px"] = cell; ref["rules_px"] = edges[:4]; ref["right_edge"] = "second vertical rule" if len(edges) > 1 else "left of 'Rev' word"; ref["attempts"] = []
    recovered = None
    for scale in (1, 2):
        im = crop if scale == 1 else crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        text = pyt.image_to_string(im, config="--psm 6")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        joined = ""
        for ln in lines:
            # a line ending in a hyphen continues on the next: the hyphen is part of the number
            joined = (joined + ln) if joined.endswith("-") else (joined + (" " if joined else "") + ln)
        m = dc.REF.search(joined) if hasattr(dc, "REF") else None
        found = m.group(0) if m else None
        ref["attempts"].append({"scale": scale, "lines": lines, "joined": joined, "reference": found})
        if found == EXPECTED:
            recovered = found; break
        recovered = recovered or found
    ref["recovered"] = recovered; ref["matches_expected"] = recovered == EXPECTED
    ref["representation"] = ("complete: the cell's own OCR yields the full number" if recovered == EXPECTED else
                             "incomplete: the cell's own OCR does not yield the full number; the record stays flagged reference_incomplete")
else:
    ref["error"] = "header words not found"
report["reference"] = ref

# --- 2. checkbox marks ------------------------------------------------------------------------------------------------
marks = {"method": "interior dark-pixel fraction of each Status checkbox (box found left of the option label, borders shaved)"}
labels = [("A", "Approved"), ("B", "Approved as Noted"), ("C", "Revise"), ("D", "Rejected"), ("FI", "Information")]
quadrant = [w for w in words if w["x0"] > img.width * 0.6 and w["y0"] > img.height * 0.55]
marks["quadrant_words"] = [w["t"] for w in quadrant][:60]
if quadrant:
    rows = []
    for key, first in labels:
        cands = [w for w in quadrant if w["t"].replace("(", "").replace(")", "").strip(",.-").lower().startswith(first.split()[0].lower())]
        col_x0 = None
        if key == "A":
            cands = [w for w in cands if not any(x["t"].lower().startswith("as") and abs(x["y0"] - w["y0"]) < 30 and x["x0"] > w["x0"] for x in words)]
        if key == "B":
            cands = [w for w in cands if any(x["t"].lower().startswith("as") and abs(x["y0"] - w["y0"]) < 30 and x["x0"] > w["x0"] for x in words)]
        if not cands:
            rows.append({"option": key, "error": "label not found"}); continue
        # the label's first word on the option's own line: for "For Information - (FI)" that is "For", found as the
        # word on the same line left of "Information"
        w = min(cands, key=lambda w: w["y0"])
        if key == "FI":
            same = [x for x in quadrant if abs(x["y0"] - w["y0"]) < 30 and x["x0"] < w["x0"]]
            if same:
                w = min(same, key=lambda x: x["x0"])
        box = (max(0, w["x0"] - 150), w["y0"] - 30, w["x0"] - 5, w["y1"] + 30)
        crop = img.crop(box); a = np.array(crop) < 128
        # the drawn box: the widest run of columns/rows whose ink is a border (a rounded square ~90 px at 300 dpi);
        # the vertical column rule (ink down the whole crop) is left out by taking runs of 40..120 px
        def runs(profile, lo=40, hi=120):
            idx_ = np.where(profile > 0.25)[0]; best = None
            for i in idx_:
                for j in idx_[::-1]:
                    if lo <= j - i <= hi:
                        best = (int(i), int(j)); break
                if best:
                    break
            return best
        cr = runs(a.mean(axis=0)); rr = runs(a.mean(axis=1))
        if cr and rr:
            bx0, bx1 = cr; by0, by1 = rr
            iw, ih = bx1 - bx0, by1 - by0
            # interior: the central 40 % of the box, past the border and its shading
            inner = a[by0 + int(ih * 0.3): by1 - int(ih * 0.3), bx0 + int(iw * 0.3): bx1 - int(iw * 0.3)]
            frac = float(inner.mean()) if inner.size else None
            core = a[by0 + int(ih * 0.4): by1 - int(ih * 0.4), bx0 + int(iw * 0.4): bx1 - int(iw * 0.4)]
            core_frac = float(core.mean()) if core.size else None
        else:
            frac = None; core_frac = None; bx0 = bx1 = by0 = by1 = None
        crop.save(OUT / f"box-{key}.png")
        rows.append({"option": key, "label_word": w["t"], "box_px": box, "border_px": [int(x) if x is not None else None for x in (bx0, by0, bx1, by1)], "interior_dark_fraction": None if frac is None else round(frac, 4), "core_dark_fraction": None if core_frac is None else round(core_frac, 4)})
    marks["boxes"] = rows
    marks["rule"] = "marked = the box whose core (central 20 %) dark fraction is at least 0.3 and at least three times every other box's; otherwise unresolved"
    vals = [(r["core_dark_fraction"], r["option"]) for r in rows if r.get("core_dark_fraction") is not None]
    if vals:
        vals.sort(reverse=True); top, second = vals[0], (vals[1] if len(vals) > 1 else (0.0, None))
        marked = top[1] if top[0] >= 0.3 and top[0] >= 3 * max(second[0], 0.01) else None
        marks["marked"] = marked; marks["resolved_status"] = {"A": "approved", "B": "ANN", "C": "rejected", "D": "rejected", "FI": "UR"}.get(marked) if marked else None
        marks["highest"] = top; marks["second"] = second
        marks["representation"] = "resolved by mark analysis: one box carries ink, the others are empty" if marked else "unresolved: no single box stands out under the rule; the decision stays UR with the candidates recorded as evidence"
else:
    marks["error"] = "no words found in the status quadrant"
report["marks"] = marks

# --- 3. what the current parser records ---------------------------------------------------------------------------------
stt = os.stat(dc._os_path(pathlib.Path(p)))
dc.begin_stage_clock()
records, notes = dc._read_pdf(str(p), stt.st_mtime_ns, stt.st_size, True, d["sha256"])
report["parser"] = {"parser_version": dc.PARSER_VERSION, "box_version": dc.BOX_VERSION, "notes": list(notes),
                    "records": [{k: getattr(r, k, None) for k in ("category", "reference", "revision", "printed_revision", "revision_source", "status", "page", "source", "flags", "decision_candidates")} for r in records]}
json.dump(report, open(OUT / "region_687.json", "w", encoding="utf-8"), indent=1, default=str)
print(json.dumps({k: report[k] for k in ("reference", "marks")}, indent=1, default=str)[:4000])
print("parser:", report["parser"]["records"][:3], report["parser"]["notes"][:3])
