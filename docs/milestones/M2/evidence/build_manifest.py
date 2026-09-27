"""Build docs/milestones/M2/M2-GOLDEN-MANIFEST.json and the per-field expected/actual report.

Expected values come from the originals, never from the parser: visually checked pages (listed by hand below) and,
for the population documents, an independent regex over the page's own text layer (pymupdf, no app code) that reads
the cover's "No:" serial. Labels carry their provenance. Actual values: stored (live readings, old parser) and the
patched parser's isolated run (parser_population_after.json)."""
import hashlib, json, os, re, sys, pathlib, datetime
import pymupdf

S = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
PREFIX = "\\\\?\\"
idx = json.load(open(S / "clone_doc_index.json")); docs = idx["docs"]; pops = idx["populations"]
after = json.load(open(S / "parser_population_after.json"))["docs"]
before = json.load(open(S / "parser_population.json"))["docs"]
SERIAL = re.compile(r"\b(ICC-DLRC-SPM-SD-MEP(?:-[A-Z]{2,3})?-\d{3,4})\b")
SHEET = re.compile(r"\b(ICC-DLRC-SPM-SD-MEP/[A-Z]{2}-\d{3}(?:[ -]?[A-Z](?:~\d{3}\s?[A-Z])?)?)\b")
DATE = re.compile(r"\d{1,2}[-/.](?:\d{1,2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/.]\d{2,4}", re.I)
G = "ICC-DLRC-SPM-SD-MEP"

# Visually checked labels (page images rendered independently and read on 2026-09-27; see golden_render/).
VISUAL = {
    434: {"reference": "ICC-DLRC-SPM-SD-MEP-FA-0054", "revision": "R1", "revision_source": "cover", "status": "rejected", "category": "drawings", "system_code": "FAS",
          "listed": ["ICC-DLRC-SPM-SD-MEP/FA-104 A~104 M"], "title": "TYPICAL 2ND TO 14TH FLOOR PLAN FIRE ALARM LAYOUT", "date_on_page": "6-Mar-2026",
          "components": {"1": "shop drawing submittal cover (consultant recommendation C framed by a stamp annotation; consultant comment 'Revise and Resubmit with MAR Approval')", "2": "contractor reply sheet (CRS) to consultant comments on ICC-DLRC-SPM-SD-MEP/FA-100,101,102,104&105 rev 0 - contractor replies 'Noted'/'Noted & Complied', no consultant decision", "3": "consultant comment list", "4+": "drawing sheets"},
          "ambiguities": ["the CRS on page 2 is dated 09/02/2026 and lists sheets 100-105 while the cover lists 104 A~104 M: association of the reply to this submission is by sheet overlap only"]},
    436: {"reference": "ICC-DLRC-SPM-SD-MEP-FA-0055", "revision": "R1", "revision_source": "cover", "status": "rejected", "category": "drawings", "system_code": "FAS",
          "listed": ["ICC-DLRC-SPM-SD-MEP/FA-105"], "title": "ROOF FLOOR PLAN FIRE ALARM LAYOUT", "date_on_page": "6-Mar-2026", "components": {"1": "cover, C framed (stamp annotation)", "2": "contractor CRS", "3": "consultant comments", "4": "drawing"}},
    437: {"reference": "ICC-DLRC-SPM-SD-MEP-FA-0128", "revision": "R0", "revision_source": "cover", "status": "rejected", "category": "drawings", "system_code": "FAS",
          "listed": ["ICC-DLRC-SPM-SD-MEP/FA-110"], "title": "FIRST FLOOR FIRE ALARM LAYOUT", "date_on_page": "7-Mar-2026",
          "components": {"1": "cover, C framed (stamp annotation), date stamp 09-03-2026", "2": "drawing sheet ICC-DLRC-SPM-SD-MEP/FA-110 REV 00 with an unticked A/B/C/D status checklist (not a decision)"}},
    783: {"same_as": 437}, 848: {"same_as": 437}, 578: {"same_as": 437},
    440: {"reference": "ICC-DLRC-SPM-SD-MEP-FF-0047", "revision": "R2", "revision_source": "cover", "status": "rejected", "category": "drawings", "system_code": None, "raw_system": "FIREFIGHTING",
          "listed": ["ICC-DLRC-SPM-SD-MEP/FF-100"], "title": "BASEMENT-01 FLOOR PLAN FIREFIGHTING LAYOUT", "date_on_page": "3-Mar-2026",
          "components": {"1": "cover; consultant recommendation C highlighted orange (a filled rectangle drawn into the page); comment 'Revise and resubmit refer to marked comments on drawing'; signed Osama Mukhtar 28.03.2026", "2": "drawing"},
          "ambiguities": ["fire fighting is not a platform system: the record carries system_code None and raw_system FIREFIGHTING; no register consumes it"]},
    409: {"reference": "ICC-DLRC-SPM-SD-MEP-0011", "revision": "R0", "revision_source": "cover", "status": "rejected", "category": "drawings", "system_code": "ELS",
          "listed": ["ICC-DLRC-SPM-SD-MEP/EM-101"], "title": "BASEMENT-02 FLOOR PLAN EMERGENCY LIGHT LAYOUT", "date_on_page": "28-Nov-2025",
          "components": {"1": "cover; consultant recommendation C framed by a green rectangle drawn into the page (vector stroke, no annotation); comments '- PQ and MAS to be submitted...'; Osama Mukhtar 12-01-2026", "2": "drawing"}},
    771: {"reference": "ICC-DLRC-SPM-SD-MEP-FA-005", "revision": "R0", "revision_source": "default", "status": "UR", "category": "drawings", "system_code": "FAS",
          "title": "LEVEL-01 TO 14TH FLOOR FIRE & VOICE EVACUATION SYSTEM LAYOUT", "components": {"1": "drawing sheet; REV 00 14.08.2026 ISSUED FOR APPROVAL; consultant comments status checklist unticked (not a decision)"}, "control": True},
    784: {"reference": "ICC-DLRC-SPM-SD-MEP-FA-006", "revision": "R0", "status": "UR", "category": "drawings", "system_code": "FAS", "control": True, "components": {"1": "drawing sheet (prefix control)"}},
    868: {"reference": "ICC-DLRC-SPM-SD-MEP/FA-100,101,102,104&105", "revision": "R0", "status": "UR", "category": "reply", "system_code": None,
          "components": {"1": "contractor reply sheet (CRS): 'Reply to Consultant Comments', Drawing No ICC-DLRC-SPM-SD-MEP/FA-100,101,102,104&105 Rev 0, per-sheet blocks 100..105 with AJE Reply 'Noted' / 'Noted & Complied' - a contractor reply, never a consultant decision"},
          "ambiguities": ["which submission(s) the reply answers is decided only by listed-sheet overlap (document_control._answers); left unresolved beyond that"]},
    729: {"reference": "TR/204/26", "revision": "R0", "status": "UR", "category": "samples", "system_code": "ELS", "date_on_page": "13/08/2026", "printed_project_code": "EP-30058 (indexed under EP-30088)",
          "components": {"1": "scanned Al Arabia DOCUMENT TRANSMITAL: subject 'Sample Board / Central Battery System -EATON', scope CBS ticked; 'RECEIVED BY' signature with mobile and date = receipt, not approval"},
          "ambiguities": ["the printed Project ID EP-30058 conflicts with the indexing project EP-30088: preserved as a conflict (content evidence), never rewritten"]},
    730: {"reference": "TR/187/26", "revision": "R0", "status": "UR", "category": "samples", "system_codes": ["FAS", "VES"], "date_on_page": "24/07/2026", "printed_project_code": "EP-30058 (indexed under EP-30088)",
          "components": {"1": "scanned DOCUMENT TRANSMITAL: 'Sample Board / Fire Alarm, Voice Evacuation System'; received-by signature = receipt"}},
    687: {"reference": "R1029-CSM-CO-ELE-FA-MAR-PJW-ZZZ-ZZZ-1004", "revision": "R2", "status": "ANN", "category": "submittals", "system_code": "FAS", "date_on_page": "15-Apr-2025", "printed_project_code": "R1029 THE ISLAND DEVELOPMENT (another project; file is 'Previous Approval.pdf', a reference copy)",
          "components": {"1": "scanned Material Submittal form (KLING/Wasl), status 'Approved as Noted - (B)' ticked, engineer signature 10.5.25, stamp 'B-Approved with Comments'", "3": "Material Approval Request 17200-DCB3-ECC-MAR-ELE-00020 rev 03 (Dubai Creek Harbour; another project)", "9": "a page whose record reference was the date 24-Mar-22 (stored reading)"},
          "ambiguities": ["the B tick is an image mark on a scan: reading it needs checkbox-mark analysis that the parser does not do (status stays UR; the AI form reading gave ANN on the row) - engineer confirmation pending", "every page is another project's approval: the printed project codes conflict with EP-30088 and must stay conflicts"]},
    620: {"reference": None, "category": None, "components": {"1-4": "specification sections 265200 EMERGENCY LIGHTING (structured cabling text) under AREC ENGINEERING CONSULTANTS - not a compliance statement despite the file name"}, "control": True, "expected_records": 0},
    120: {"project": 1, "reference": "BBY006-GME-SDW-FP-FA-POD-BGF-010002", "components": {"1": "Shop Drawings Submittal Form, SDW Rev.: 00, dated 19 August 2026; Re-Submit (C) box filled (red) and a '(C) Revise & Resubmit' consultant stamp; 'Refer to BBY006-GME-SDW-FP-FA-ZZZ-ZZZ-010010 for comments'", "2": "the returned drawing sheet: title block REV 00 (06.08.2026 ISSUED FOR APPROVAL), consultant '(C) Revise & Resubmit' stamp image top right, QA/QC comment list; file sits in the R1 folder"},
          "expected": [{"page": 1, "revision": "R0", "revision_source": "printed", "status": "rejected"}, {"page": 2, "revision": "R1", "revision_source": "folder", "printed_revision": "00", "status": "rejected"}],
          "ambiguities": ["page 2 prints revision 00 but is filed under R1; the settled owner rule (folder where the sheet prints 00 across resubmissions) assigns R1 - both facts are now kept (revision + printed_revision + revision_source); which revision the C stamp answers is for M4/engineer confirmation (F8)"]},
    22: {"project": 1, "reference": "BBY006-GME-SDW-FP-FA-BSM-B04-010025", "components": {"1": "drawing sheet; title block REV 01 (16.04.2026; history 00 ISSUED FOR APPROVAL, 01 REVISED AS PER CONSULTANT COMMENTS 24.09.2026)"},
         "expected": [{"page": 1, "revision": "R1", "revision_source": "folder", "printed_revision": "01", "status": "UR"}], "control": True},
    "BOQ": {"project": 1, "source": "01- EP-30784 Scan/EP-30784 FAS Design.pdf page 2 (scan), rendered at 200 dpi", "expected": {"TP606": {"quantity": "491", "description": "2\"x4\" GI Concealed Back Box Single Gange"}, "TP434": {"quantity": "142", "description": "4\"x4\" GI Concealed Back Box Double Gange"}},
            "fixture": "backend/tests/fixtures/boq_ep30784_golden_v1.json rows for TP606/TP434 agree with the scan; the dev fixture boq_ep30784.json's TP606 = 49 does not", "provenance": "visually checked 2026-09-27; owner countersignature still pending"},
}
VISUAL_IDS = {k for k in VISUAL if isinstance(k, int)}


def text_of(path, page=0):
    try:
        with pymupdf.open(PREFIX + path) as pdf:
            return pdf[page].get_text() if len(pdf) > page else ""
    except Exception as exc:  # noqa: BLE001
        return f"<unreadable: {exc}>"


def independent_expectation(doc_id, d):
    """From the original's own text layer (page 1), independently of the parser: the serial in the No: field,
    the sheets listed, whether the page is a cover, a text page or a scan."""
    if not d["exists"]:
        return {"provenance": "file missing", "expected_reference": None}
    p = pathlib.Path(d["path"])
    if p.suffix.lower() != ".pdf":
        return {"provenance": "Word transmittal; expected from the transmittal reader's own grammar is not independent - label pending", "expected_reference": None}
    t = text_of(d["path"])
    serials = SERIAL.findall(t); sheets = SHEET.findall(t)
    cover = bool(re.search(r"SHOP\s*DRAWINGS?\s+SUBMITTAL", t, re.I)) and "submitting herewith" in t.lower()
    return {"provenance": "independent regex over the original's text layer (page 1); not visually checked" if doc_id not in VISUAL_IDS else "visually checked page image + text layer",
            "text_layer_chars": len(t), "is_cover": cover, "is_scan": len(t.strip()) < 80, "serials_on_page": serials[:4], "sheets_on_page": sheets[:4],
            "expected_reference": serials[0] if cover and serials else None,
            "generic_string_alone_on_page": bool(re.search(r"\bICC-DLRC-SPM-SD-MEP\b(?![-/])", t)) and not serials}


def actual_summary(entry):
    a = entry.get("actual", {})
    if "error" in a:
        return {"error": a["error"]}
    return [{k: r.get(k) for k in ("category", "reference", "revision", "revision_source", "printed_revision", "status", "system_code", "raw_system", "page", "source", "listed", "floor", "reply_text")} for r in a.get("records", [])]


cases = []; accounting = {"date_embedded": [], "generic_embedded_exact": [], "generic_prefix_only": [], "word_transmittals": [], "named": []}
population = sorted(set(pops["date_embedded"]) | set(pops["generic_embedded_exact"]) | set(pops["generic_prefix_only"]) | set(pops["word_transmittals"]) | VISUAL_IDS)
for i in population:
    d = docs[str(i)]; ind = independent_expectation(i, d)
    vis = VISUAL.get(i); base = VISUAL.get(vis["same_as"]) if vis and "same_as" in vis else vis
    stored = [{k: r.get(k) for k in ("category", "reference", "revision", "status", "page", "source")} for r in d["records"]]
    aft = after.get(str(i)); bef = before.get(str(i))
    actual_after = actual_summary(aft) if aft else "not run"
    expected_ref = (base or {}).get("reference", ind.get("expected_reference")) if base else ind.get("expected_reference")
    if base and "expected" in base:
        expected_ref = base["reference"]
    # per-field verdicts on the first record where an expectation exists
    verdict = {}
    if isinstance(actual_after, list):
        first = actual_after[0] if actual_after else None
        if base and base.get("expected_records") == 0:
            verdict["records"] = "match" if not actual_after else "mismatch"
        if expected_ref is not None:
            verdict["reference"] = "match" if first and first["reference"] == expected_ref else "mismatch" if first else "no record"
        for f in ("revision", "status", "category", "system_code", "raw_system", "revision_source"):
            if base and f in base and first is not None:
                verdict[f] = "match" if first.get(f) == base[f] else "mismatch"
        if base and "listed" in base and first is not None:
            verdict["listed"] = "match" if list(first.get("listed") or []) == base["listed"] else "mismatch"
        if base and "expected" in base:
            for exp in base["expected"]:
                rec = next((r for r in actual_after if r["page"] == exp["page"]), None)
                verdict[f"page{exp['page']}"] = {k: ("match" if rec and rec.get(k) == v else "mismatch" if rec else "no record") for k, v in exp.items() if k != "page"}
        if expected_ref is None and not base and ind.get("generic_string_alone_on_page"):
            verdict["reference"] = "unresolved: the page carries the generic string only (no serial in the text layer); source identity needs the sheet"
        if expected_ref is None and not base and not ind.get("generic_string_alone_on_page") and ind.get("is_cover") is False and not ind.get("is_scan"):
            verdict["reference"] = "unlabelled: not a cover page; expected reference not independently established"
    case = {"doc_id": i, "project_id": d["project_id"], "relative_path": d["relative_path"], "sha256": d["sha256"], "role": d["role"], "pages": d["page_count"],
            "format": "word" if d["relative_path"].lower().endswith((".doc", ".docx")) else "scan" if ind.get("is_scan") else "text-pdf",
            "populations": [k for k in ("date_embedded", "generic_embedded_exact", "generic_prefix_only", "word_transmittals") if i in pops[k]],
            "label": {"provenance": ind["provenance"] if not base else "visually checked page image (golden_render/doc-%d-p*.png) + text layer" % (vis.get("same_as", i)), "expected_reference": expected_ref,
                      "expected": {k: v for k, v in (base or {}).items() if k not in ("same_as",)}, "independent_text_layer": {k: v for k, v in ind.items() if k != "provenance"},
                      "review_status": "independently source-checked" if base else "text-layer derived, visual check pending"},
            "actual_stored_old_parser": stored, "actual_before_run_parse_2026_09_28_1": actual_summary(bef) if bef else "not run", "actual_after_parse_2026_09_28_2": actual_after,
            "seconds_after": aft.get("seconds") if aft else None, "verdict": verdict}
    cases.append(case)
    for k in case["populations"]:
        accounting[k].append(i)
    if i in VISUAL_IDS and not case["populations"]:
        accounting["named"].append(i)

# outcome accounting per document (exclusive)
outcomes = {}
for c in cases:
    a = c["actual_after_parse_2026_09_28_2"]
    if a == "not run": o = "not_run"
    elif isinstance(a, dict): o = "failed"
    elif not a: o = "succeeded_no_records" if (c["label"]["expected"].get("expected_records") == 0) else "succeeded_no_records_unlabelled"
    else: o = "succeeded_with_records"
    outcomes[o] = outcomes.get(o, 0) + 1
verdicts = {}
for c in cases:
    for k, v in c["verdict"].items():
        key = k if isinstance(v, str) else k
        val = v if isinstance(v, str) else json.dumps(v)
        verdicts.setdefault(key, {}).setdefault(val if isinstance(v, str) else "per-page", 0)
        verdicts[key][val if isinstance(v, str) else "per-page"] += 1
manifest = {"built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "database_clone": sorted(S.glob("m2_clone_2*.db"))[-1].name,
            "parser_versions": {"stored": "none (pre parser_version)", "before_run": before and next(iter(json.load(open(S / "parser_population.json"))["parser_version"] for _ in [0])), "after_run": json.load(open(S / "parser_population_after.json"))["parser_version"]},
            "populations": {k: {"count": len(v), "ids": v} for k, v in pops.items()}, "population_union_count": len(population), "named_cases": sorted(VISUAL_IDS), "boq_case": VISUAL["BOQ"],
            "outcome_accounting_documents": outcomes, "verdict_counts": verdicts, "cases": cases,
            "label_policy": "visually checked = page image rendered with pymupdf independently of the parser and read by the author on 2026-09-27; text-layer derived = independent regex over the original's page-1 text (no app code); the parser being tested was never the source of an expectation; no engineer sign-off is claimed anywhere"}
json.dump(manifest, open(OUT / "M2-GOLDEN-MANIFEST.json", "w"), indent=1, default=str)
print(json.dumps({"cases": len(cases), "outcomes": outcomes, "verdicts": verdicts}, indent=1)[:3000])
