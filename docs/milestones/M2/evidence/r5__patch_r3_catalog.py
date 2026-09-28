"""Review 03, R3-03: a multiline catalog cell is read whole (block mode over the row's full bounds); a part number the
passes do not confirm -- no pass read the whole strip value -- is uncertain, not accepted; the cell's geometry and every
reading are kept on the review row. The Golden fixture records what the sheet prints where the transcriber completed
a cut part, and the matcher accepts the printed form."""
import json, pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
p = B / "app/services/design_sheet_extractor.py"; s = p.read_text(encoding="utf-8")

old = '''_CATALOG_PASSES = (
    ("greyscale, single line, part characters", "greyscale", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
    ("binarised, single line, part characters", "binarised", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
)
'''
new = '''_PART_CHARACTERS = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."
_CATALOG_PASSES = (
    ("greyscale, single line, part characters", "greyscale", "--psm 7 " + _PART_CHARACTERS),
    ("binarised, single line, part characters", "binarised", "--psm 7 " + _PART_CHARACTERS),
)
# A cell taller than a line and a half holds a part number that wraps
# ("SL2MNM65D3C-M" over "+SL23I" on EP-30784's EML sheet): read as a block,
# its lines joined, not as one line (which returns a fragment of one line).
_CATALOG_BLOCK_PASSES = (
    ("greyscale, block, part characters", "greyscale", "--psm 6 " + _PART_CHARACTERS),
    ("binarised, block, part characters", "binarised", "--psm 6 " + _PART_CHARACTERS),
)
MULTILINE_CELL_PX = int(3.2 * _CELL_HALF_HEIGHT_PX)
'''
assert old in s; s = s.replace(old, new, 1)

old = '''    cell = _catalog_cell_image(image, line, catalog_span)
    if cell is None:
        return
    padded = ImageOps.expand(cell, border=_PASS_PADDING_PX, fill=255)
    greyscale = padded.resize((padded.width * _PASS_SCALE, padded.height * _PASS_SCALE), Image.LANCZOS)
    array = np.array(greyscale)
    binarised = Image.fromarray(np.where(array < _otsu_threshold(array), 0, 255).astype(np.uint8))
    sources = {"greyscale": greyscale, "binarised": binarised}
    readings: list[dict] = []
    for name, source, config in _CATALOG_PASSES:
        try:
            text = pytesseract.image_to_string(sources[source], config=config).strip()
        except Exception:  # noqa: BLE001 -- a pass that cannot run is no evidence either way
            continue
        value = identity.clean_catalog(text)[0] if text else None
        readings.append({"pass": name, "text": text, "value": value})
    line.catalog_alternates = readings
    strip = identity.clean_catalog(line.catalog_no or "")[0] if line.catalog_no else None
    others = [r["value"] for r in readings if r["value"] and r["value"] != strip]
    if not strip or not others:
        return'''
new = '''    cell = _catalog_cell_image(image, line, catalog_span)
    if cell is None:
        return
    x0, x1 = catalog_span
    y0, y1 = line.row_bounds or (int((line.y_px or 0) - _CELL_HALF_HEIGHT_PX), int((line.y_px or 0) + _CELL_HALF_HEIGHT_PX))
    line.catalog_cell = [int(x0), int(y0), int(x1), int(y1)]
    multiline = (y1 - y0) >= MULTILINE_CELL_PX
    padded = ImageOps.expand(cell, border=_PASS_PADDING_PX, fill=255)
    greyscale = padded.resize((padded.width * _PASS_SCALE, padded.height * _PASS_SCALE), Image.LANCZOS)
    array = np.array(greyscale)
    binarised = Image.fromarray(np.where(array < _otsu_threshold(array), 0, 255).astype(np.uint8))
    sources = {"greyscale": greyscale, "binarised": binarised}
    readings: list[dict] = []
    for name, source, config in (_CATALOG_BLOCK_PASSES if multiline else _CATALOG_PASSES):
        try:
            text = " ".join(pytesseract.image_to_string(sources[source], config=config).split())
        except Exception:  # noqa: BLE001 -- a pass that cannot run is no evidence either way
            continue
        value = identity.clean_catalog(text)[0] if text else None
        readings.append({"pass": name, "text": text, "value": value})
    line.catalog_alternates = readings
    strip = identity.clean_catalog(line.catalog_no or "")[0] if line.catalog_no else None
    if not strip:
        return
    confirmed = any(r["value"] == strip for r in readings)
    others = [r["value"] for r in readings if r["value"] and r["value"] != strip]
    if not confirmed:
        # No pass read the strip's value whole: fragments or nothing are not
        # a confirmation (M2 review 03, R3-03). The strip's reading is not
        # accepted on its own word; the row is for the engineer with what
        # every pass read.
        line.catalog_uncertain = True
        line.catalog_check = {"confirmed": False, "multiline": multiline, "reason": "no independent pass read the whole part number"}
        return
    line.catalog_check = {"confirmed": True, "multiline": multiline, "reason": "an independent pass read the same part number"}
    if not others:
        return'''
assert old in s; s = s.replace(old, new, 1)
old = '''    agreed = len(others) >= 2 and len(set(others)) == 1 and whole(others[0])
    near = any(whole(o) and abs(len(o) - len(strip)) <= 1 and _edit_distance(o.upper(), strip.upper()) <= 2 for o in others)
    if agreed or near:
        line.catalog_uncertain = True
'''
new = '''    agreed = len(others) >= 2 and len(set(others)) == 1 and whole(others[0])
    near = any(whole(o) and abs(len(o) - len(strip)) <= 1 and _edit_distance(o.upper(), strip.upper()) <= 2 for o in others)
    if agreed or near:
        line.catalog_uncertain = True
        line.catalog_check = {"confirmed": False, "multiline": multiline,
                              "reason": "an independent pass read another part number" + (" (a near miss of the strip's)" if near else "")}
'''
assert old in s; s = s.replace(old, new, 1)
old = '''    # Set when an independent pass on the catalog cell read another part
    # number: the row is for review, not a line (`_uncertain_part_issue`).
    catalog_uncertain: bool = False'''
new = '''    # Set when an independent pass on the catalog cell read another part
    # number, or none read the strip's whole: the row is for review, not a
    # line (`_uncertain_part_issue`). `catalog_check` says why, and
    # `catalog_cell` is the cell's box on the rendered page.
    catalog_uncertain: bool = False
    catalog_check: dict | None = None
    catalog_cell: list[int] | None = None'''
assert old in s; s = s.replace(old, new, 1)
old = '''            "reason": f"the column read the part number {line.catalog_no!r}"
                      + (f" at {line.catalog_confidence:.0f}%" if line.catalog_confidence is not None else "")
                      + f"; independent passes on the cell read {', '.join(alternates)}",
            "catalog_alternates": line.catalog_alternates,'''
new = '''            "reason": f"the column read the part number {line.catalog_no!r}"
                      + (f" at {line.catalog_confidence:.0f}%" if line.catalog_confidence is not None else "")
                      + (f"; independent passes on the cell read {', '.join(alternates)}" if alternates else "; no independent pass read a part number")
                      + (f" ({line.catalog_check['reason']})" if line.catalog_check else ""),
            "catalog_alternates": line.catalog_alternates, "catalog_check": line.catalog_check, "catalog_cell": line.catalog_cell,'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# --- the Golden fixture records the printed form of the cut part; the matcher accepts it
f = B / "tests/fixtures/boq_ep30784_golden_v1.json"; g = json.loads(f.read_text(encoding="utf-8"))
hit = [r for r in g["rows"] if r.get("part_number") == "SIGA-OSHD-FCN"]
assert len(hit) == 1
hit[0]["printed_part_number"] = "SIGA-OSHD-FC"
hit[0]["identity_note"] = "the sheet prints SIGA-OSHD-FC with the last glyph cut by the column rule (source crop, M2 review 03); FCN is the transcriber's completion, not established by the source -- an extractor reading the printed text is right, and the identity is incomplete"
g["source"]["notes"].append("2026-09-28 (M2 review 03): a row may carry printed_part_number where the sheet cuts the part and the transcriber completed it; the metrics accept the printed form as a match (part_number is the completion, not source evidence).")
g["source"]["notes"].append("2026-09-28 (M2 review 03): 6538-G5 'Call for Assistance Kit' is printed as a stand-alone item (its own quantity 4) below the Remote power supply kit's components with no heading of its own; its null group here is the transcriber's judgement, the reader's 'Booster Power Supply' is the heading in force above it -- a layout rule the sheet does not print; left as a group disagreement, not an extractor error.")
f.write_text(json.dumps(g, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

m = B / "scripts/boq_metrics.py"; s = m.read_text(encoding="utf-8")
old = '''def _part(text) -> str:'''
new = '''def _gold_parts(gold: dict) -> set:
    """The part keys a golden row accepts: its part number and, where the
    sheet prints a cut part the transcriber completed, the printed form."""
    return {k for k in (_part(gold.get("part_number")), _part(gold.get("printed_part_number"))) if k}


def _part(text) -> str:'''
assert old in s; s = s.replace(old, new, 1)
old = '''            part = _part(row.get("part_number"))
            if gold_part:
                if part != gold_part:
                    continue
                score = 10'''
new = '''            part = _part(row.get("part_number"))
            if gold_part:
                if part not in _gold_parts(gold):
                    continue
                score = 10'''
assert old in s; s = s.replace(old, new, 1)
old = '''    part_right = sum(1 for g, e in matched if _part(g.get("part_number")) == _part(e.get("part_number")))'''
new = '''    part_right = sum(1 for g, e in matched if _part(e.get("part_number")) in _gold_parts(g))'''
assert old in s; s = s.replace(old, new, 1)
old = '''    pair_right = sum(1 for g, e in matched if _part(g.get("part_number")) == _part(e.get("part_number"))
                     and _quantity_key(g.get("quantity")) == _quantity_key(e.get("quantity")))'''
new = '''    pair_right = sum(1 for g, e in matched if _part(e.get("part_number")) in _gold_parts(g)
                     and _quantity_key(g.get("quantity")) == _quantity_key(e.get("quantity")))'''
assert old in s; s = s.replace(old, new, 1)
old = '''    false_accepts = [(g, e) for g, e in lines_matched
                     if _part(g.get("part_number")) != _part(e.get("part_number"))'''
new = '''    false_accepts = [(g, e) for g, e in lines_matched
                     if _part(e.get("part_number")) not in _gold_parts(g)'''
assert old in s; s = s.replace(old, new, 1)
m.write_text(s, encoding="utf-8")

t = B / "tests/test_design_sheet_extractor.py"; ts = t.read_text(encoding="utf-8")
ts += '''

def test_a_multiline_catalog_cell_is_read_as_a_block_and_a_near_miss_holds_it(monkeypatch):
    """EP-30784 EML: "SL2MNM65D3C-M" over "+SL23I" in one cell; the strip
    read "+SL231". Line mode returns a fragment of one line; block mode
    reads both lines, and a reading a glyph apart holds the row."""
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    configs = []
    answers = iter(["SL2MNM65D3C-M\\n+SLZ31", "SL2MNM65D3C-M\\n+SL231"])

    def ocr(img, config=""):
        configs.append(config)
        return next(answers)
    monkeypatch.setattr(dse.pytesseract, "image_to_string", ocr)
    line = dse.ExtractedBoqLine(catalog_no="SL2MNM65D3C-M +SL231", description="Wall Mounted Exit", quantity="1", group_heading=None, confidence=67.0,
                                page=1, y_px=1552.0, row_bounds=(1500, 1604), raw_quantity="1", catalog_confidence=67.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 1700), 255), line, (100, 300))
    assert all("--psm 6" in c for c in configs), "a two-line cell is read as a block"
    assert line.catalog_uncertain and line.catalog_check["multiline"] and "near miss" in line.catalog_check["reason"]
    assert [r["value"] for r in line.catalog_alternates] == ["SL2MNM65D3C-M +SLZ31", "SL2MNM65D3C-M +SL231"]
    assert line.catalog_cell == [100, 1500, 300, 1604] and line.catalog_no == "SL2MNM65D3C-M +SL231", "the literal strip value and the cell stay on the row"
    issue = dse._uncertain_part_issue(line, 1)
    assert issue.detail["catalog_check"]["multiline"] and issue.detail["catalog_cell"] == [100, 1500, 300, 1604] and issue.detail["quantity"] == "1"


def test_fragments_do_not_confirm_a_part_and_a_whole_matching_reading_does(monkeypatch):
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    def line_for(part):
        return dse.ExtractedBoqLine(catalog_no=part, description="x", quantity="2", group_heading=None, confidence=70.0, page=1, y_px=100.0,
                                    row_bounds=(80, 130), raw_quantity="2", catalog_confidence=70.0, table_span=(0, 600))
    # fragments only: unconfirmed, held
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(iter(["C-M"])))
    answers = iter(["C-M", "SS C-M"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SL2MNM65D3C-M +SL231"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and line.catalog_check == {"confirmed": False, "multiline": False, "reason": "no independent pass read the whole part number"}
    # nothing read at all: unconfirmed, held
    answers = iter(["", ""]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("4-CPU"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and not line.catalog_check["confirmed"]
    # conflicting whole readings: held
    answers = iter(["757-7A-T", "757-7A-T"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("WSTIA-T"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and "another part number" in line.catalog_check["reason"]
    # a clear matching whole reading: confirmed, even beside a clipped fragment
    answers = iter(["-L210DI", "SL210DI"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SL210DI"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain and line.catalog_check["confirmed"]
    # a cut identity read literally by every pass is confirmed as printed: nothing is completed
    answers = iter(["SIGA-OSHD-FC", "SIGA-OSHD-FC"]); monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = line_for("SIGA-OSHD-FC"); dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain and line.catalog_no == "SIGA-OSHD-FC"
'''
t.write_text(ts, encoding="utf-8")
print("R3-03 patched")
