"""Review 02 D, tightened: a strip quantity read at 60-90 % is checked for a cut digit only (a pass reading the
strip's digits plus one more sends the row to review; a pass reading something else is the cell pass being worse
than the strip, as it is known to be, and changes nothing); a part number is uncertain only when both passes agree
on another value or a pass reads a near miss of the strip's (edit distance <= 2), not on garbled fragments."""
import pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
p = B / "app/services/design_sheet_extractor.py"; s = p.read_text(encoding="utf-8")

old = '''            elif line.verify_quantity or (line.quantity_confidence is not None
                                          and line.quantity_confidence < RECHECK_QUANTITY_BELOW):
                # Strip reads below RECHECK_QUANTITY_BELOW are checked against
                # the passes on the cell; a single-cell pass drops or adds digits
                # on multi-digit counts more often than the strip misreads them
                # (EP-30784 FAS: "121" read "12"), so `_confirm_quantity` keeps a
                # confident strip value the passes half-agree with -- unless a
                # pass read the strip's digits plus one more (491 over 49).
                _confirm_quantity(image, line)'''
new = '''            elif line.verify_quantity or (line.quantity_confidence is not None
                                          and line.quantity_confidence < LOW_QUANTITY_CONFIDENCE):
                # Confident strip reads are not second-guessed: a single-cell
                # pass drops or adds digits on multi-digit counts more often
                # than the strip misreads them (EP-30784 FAS: "121" read "12").
                _confirm_quantity(image, line)
            elif line.quantity_confidence is not None and line.quantity_confidence < RECHECK_QUANTITY_BELOW:
                # ... except for the one failure the cell pass is better at
                # seeing: a digit cut at the cell's edge (491 read 49 at 86 %).
                _check_cut_digit(image, line)'''
assert old in s; s = s.replace(old, new, 1)

old = '''    line.alternates = readings
    agreeing = valid.count(strip_value)
    others = {v: valid.count(v) for v in valid if v != strip_value}
    confidence = f" ({line.quantity_confidence:.0f}%)" if line.quantity_confidence is not None else ""
    longer = sorted(v for v in others if strip_value and v.startswith(strip_value) and len(v) > len(strip_value))
    if longer:
        # A pass read the strip's digits and one more: the strip may have cut
        # the last digit at the cell's edge (EP-30784 FAS: TP606 printed 491,
        # the column read 49 at 86 %, the digits-only pass read 491). Neither
        # value is taken: the row goes for review with both readings.
        line.quantity = None
        line.quantity_parse = {**(line.quantity_parse or {}), "status": values.AMBIGUOUS,
                               "rule": f"the column read {strip_value!r}{confidence} but an independent pass read {longer[0]}: "
                                       "a digit may be cut at the cell's edge"}
        return
    if not others or (agreeing >= 1 and (line.quantity_confidence or 0) >= CONFIDENT_QUANTITY):'''
new = '''    line.alternates = readings
    agreeing = valid.count(strip_value)
    others = {v: valid.count(v) for v in valid if v != strip_value}
    confidence = f" ({line.quantity_confidence:.0f}%)" if line.quantity_confidence is not None else ""
    if _cut_digit(line, strip_value, others, confidence):
        return
    if not others or (agreeing >= 1 and (line.quantity_confidence or 0) >= CONFIDENT_QUANTITY):'''
assert old in s; s = s.replace(old, new, 1)

old = '''def _independent_readings(image: Image.Image, line: ExtractedBoqLine) -> list[dict] | None:'''
new = '''def _cut_digit(line: ExtractedBoqLine, strip_value: str | None, others: dict, confidence: str) -> bool:
    """A pass read the strip's digits and one more: the strip may have cut
    the last digit at the cell's edge (EP-30784 FAS: TP606 printed 491, the
    column read 49 at 86 %, the digits-only pass read 491). Neither value is
    taken: the row goes for review with both readings. Returns whether so."""
    longer = sorted(v for v in others if strip_value and v.startswith(strip_value) and len(v) > len(strip_value))
    if not longer:
        return False
    line.quantity = None
    line.quantity_parse = {**(line.quantity_parse or {}), "status": values.AMBIGUOUS,
                           "rule": f"the column read {strip_value!r}{confidence} but an independent pass read {longer[0]}: "
                                   "a digit may be cut at the cell's edge"}
    return True


def _check_cut_digit(image: Image.Image, line: ExtractedBoqLine) -> None:
    """The cut-digit check alone, for a strip reading confident enough to
    stand otherwise (M2 review 02, D): the passes on the cell are recorded,
    and only a reading of the strip's digits plus one sends the row for
    review; any other disagreement is the cell pass being the worse reader."""
    readings = _independent_readings(image, line)
    if readings is None:
        return
    line.alternates = readings
    valid = [r["value"] for r in readings if r["value"] is not None]
    strip_value = line.quantity
    others = {v: valid.count(v) for v in valid if v != strip_value}
    confidence = f" ({line.quantity_confidence:.0f}%)" if line.quantity_confidence is not None else ""
    _cut_digit(line, strip_value, others, confidence)


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _independent_readings(image: Image.Image, line: ExtractedBoqLine) -> list[dict] | None:'''
assert old in s; s = s.replace(old, new, 1)

old = '''    line.catalog_alternates = readings
    strip = identity.clean_catalog(line.catalog_no or "")[0] if line.catalog_no else None
    others = {r["value"] for r in readings if r["value"] and r["value"] != strip}
    if others:
        line.catalog_uncertain = True
'''
new = '''    line.catalog_alternates = readings
    strip = identity.clean_catalog(line.catalog_no or "")[0] if line.catalog_no else None
    others = [r["value"] for r in readings if r["value"] and r["value"] != strip]
    if not strip or not others:
        return
    # Evidence, not noise: both passes agreeing on another reading (the strip
    # garbled "757-7A-T" into "WSTIA-T"), or a pass reading a near miss of the
    # strip's -- a glyph or two apart ("4-CAB16D" for "4-CABI6D"). A fragment
    # or an unrelated string from one pass is the cell pass failing, and says
    # nothing about the strip.
    # A reading that is a piece of the strip's ("L210DI" for "SL210DI", "HIP"
    # for a long part) is the crop clipping the cell, not another part.
    def whole(o: str) -> bool:
        return o.upper() not in strip.upper() and len(o) >= 0.6 * len(strip)

    agreed = len(others) >= 2 and len(set(others)) == 1 and whole(others[0])
    near = any(whole(o) and abs(len(o) - len(strip)) <= 1 and _edit_distance(o.upper(), strip.upper()) <= 2 for o in others)
    if agreed or near:
        line.catalog_uncertain = True
'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

t = B / "tests/test_design_sheet_extractor.py"; ts = t.read_text(encoding="utf-8")
old = '''    dse._confirm_quantity(None, line)
    assert line.quantity is None and line.quantity_parse["status"] == "ambiguous" and "491" in line.quantity_parse["rule"]
    assert dse._dropped_row_issue(line, 1) is not None, "a row for review, not a line"
    # the same passes agreeing on the strip's value, or reading a shorter one, leave a confident value alone (G1ARN: 121 / "12")
    line = dse.ExtractedBoqLine(catalog_no="G1ARN", description="Horn", quantity="121", group_heading=None, confidence=90.0, page=2,
                                raw_quantity="121", quantity_confidence=88.0, quantity_parse={"kind": "equipment_count", "raw": "121", "value": 121, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "121", "value": "121", "status": "ok"}, {"pass": "b", "text": "12]", "value": "12", "status": "ok"}, {"pass": "c", "text": "121", "value": "121", "status": "ok"}])
    dse._confirm_quantity(None, line)
    assert line.quantity == "121"
'''
new = '''    dse._check_cut_digit(None, line)
    assert line.quantity is None and line.quantity_parse["status"] == "ambiguous" and "491" in line.quantity_parse["rule"]
    assert dse._dropped_row_issue(line, 1) is not None, "a row for review, not a line"
    # the same passes agreeing on the strip's value, or reading a shorter one, leave a confident value alone (G1ARN: 121 / "12")
    line = dse.ExtractedBoqLine(catalog_no="G1ARN", description="Horn", quantity="121", group_heading=None, confidence=90.0, page=2,
                                raw_quantity="121", quantity_confidence=88.0, quantity_parse={"kind": "equipment_count", "raw": "121", "value": 121, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "121", "value": "121", "status": "ok"}, {"pass": "b", "text": "12]", "value": "12", "status": "ok"}, {"pass": "c", "text": "121", "value": "121", "status": "ok"}])
    dse._check_cut_digit(None, line)
    assert line.quantity == "121"
    # and a pass reading something else entirely at 60-90 % leaves the strip's value alone (the cell pass is the worse reader: 48 read "86")
    line = dse.ExtractedBoqLine(catalog_no="X", description="Wrapped row", quantity="48", group_heading=None, confidence=80.0, page=1,
                                raw_quantity="48", quantity_confidence=75.0, quantity_parse={"kind": "equipment_count", "raw": "48", "value": 48, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "86", "value": "86", "status": "ok"}, {"pass": "b", "text": "86", "value": "86", "status": "ok"}, {"pass": "c", "text": "", "value": None, "status": "empty"}])
    dse._check_cut_digit(None, line)
    assert line.quantity == "48" and line.alternates is not None
    # the low-confidence path keeps its full confirmation logic, cut digit included
    line = dse.ExtractedBoqLine(catalog_no="TP606", description="Back Box", quantity="49", group_heading=None, confidence=50.0, page=2,
                                raw_quantity="49", quantity_confidence=45.0, quantity_parse={"kind": "equipment_count", "raw": "49", "value": 49, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "a", "text": "491", "value": "491", "status": "ok"}, {"pass": "b", "text": "49", "value": "49", "status": "ok"}, {"pass": "c", "text": "49", "value": "49", "status": "ok"}])
    dse._confirm_quantity(None, line)
    assert line.quantity is None and "491" in line.quantity_parse["rule"]
'''
assert old in ts; ts = ts.replace(old, new, 1)
old = '''    answers = iter(["", "SIGA-SB"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SIGA-SB", description="Base", quantity="2206", group_heading=None, confidence=94.0,
                                page=2, y_px=100.0, raw_quantity="2206", catalog_confidence=94.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "a pass that read nothing is no evidence; the other agreed"
'''
new = '''    answers = iter(["", "SIGA-SB"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SIGA-SB", description="Base", quantity="2206", group_heading=None, confidence=94.0,
                                page=2, y_px=100.0, raw_quantity="2206", catalog_confidence=94.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "a pass that read nothing is no evidence; the other agreed"
    # a near miss from one pass is evidence (4-CABI6D / 4-CAB16D); a fragment or an unrelated string from one pass is not
    answers = iter(["4-CABI6D", "4-CAB16D"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="4-CABI6D", description="Door", quantity="1", group_heading=None, confidence=71.0,
                                page=1, y_px=100.0, raw_quantity="1", catalog_confidence=71.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain
    answers = iter(["SL2MNM65D3C-M", "SL2M"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SL2MNM65D3C-M", description="Exit", quantity="1", group_heading=None, confidence=70.0,
                                page=1, y_px=100.0, raw_quantity="1", catalog_confidence=70.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "one pass agreed, the other read a fragment"
'''
assert old in ts; ts = ts.replace(old, new, 1)
t.write_text(ts, encoding="utf-8")
print("D3b patched")
