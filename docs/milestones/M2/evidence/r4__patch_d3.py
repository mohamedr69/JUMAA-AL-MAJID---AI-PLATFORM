"""Review 02 D3: a part number the column strip read below RECHECK_QUANTITY_BELOW confidence is checked against two
independent passes on its own cell; any disagreement sends the row for review (part number conflict) with every
reading recorded -- no character is substituted, no Golden value is known to the reader."""
import pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
p = B / "app/services/design_sheet_extractor.py"; s = p.read_text(encoding="utf-8")

old = '''    # Tesseract's confidence in the quantity strip's own reading, 0-100.
    quantity_confidence: float | None = None'''
new = '''    # Tesseract's confidence in the quantity strip's own reading, 0-100.
    quantity_confidence: float | None = None
    # Tesseract's confidence in the catalog strip's reading of this row, and
    # what independent passes on the catalog cell read when the strip's
    # reading was checked (M2 review 02, D): [{"pass", "text", "value"}].
    catalog_confidence: float | None = None
    catalog_alternates: list[dict] | None = None
    # Set when an independent pass on the catalog cell read another part
    # number: the row is for review, not a line (`_uncertain_part_issue`).
    catalog_uncertain: bool = False'''
assert old in s; s = s.replace(old, new, 1)

# the ruled reader records the catalog confidence
old = '''                catalog_raw=catalog_text or None,
                quantity_confidence=quantity_conf if quantity_text else None,
            )
        )

    return lines, heading'''
new = '''                catalog_raw=catalog_text or None,
                quantity_confidence=quantity_conf if quantity_text else None,
                catalog_confidence=catalog_conf if catalog_text else None,
            )
        )

    return lines, heading'''
assert old in s; s = s.replace(old, new, 1)

# the unruled reader too: its ExtractedBoqLine(...) call carries catalog_raw=... ; add the confidence beside it
i = s.index("def _read_table(")
body = s[i:]
old_u = "catalog_raw=catalog_text or None,"
assert body.count(old_u) >= 1, body.count(old_u)
body = body.replace(old_u, "catalog_raw=catalog_text or None,\n                catalog_confidence=catalog_conf if catalog_text else None,", 1)
s = s[:i] + body

# the check itself
old = '''def _independent_readings(image: Image.Image, line: ExtractedBoqLine) -> list[dict] | None:'''
new = '''_CATALOG_PASSES = (
    ("greyscale, single line, part characters", "greyscale", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
    ("binarised, single line, part characters", "binarised", "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/+."),
)


def _catalog_cell_image(image: Image.Image, line: ExtractedBoqLine, catalog_span: tuple[int, int]) -> Image.Image | None:
    x0, x1 = catalog_span
    if line.row_bounds is not None:
        y0, y1 = line.row_bounds
    elif line.y_px is not None:
        y0, y1 = line.y_px - _CELL_HALF_HEIGHT_PX, line.y_px + _CELL_HALF_HEIGHT_PX
    else:
        return None
    box = (int(x0) + 4, int(y0) + 2, int(x1) - 4, int(y1) - 2)
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return image.crop(box)


def _confirm_catalog(image: Image.Image, line: ExtractedBoqLine, catalog_span: tuple[int, int]) -> None:
    """Check a part number the column strip read against two independent
    passes on its own cell (M2 review 02, D). Both agreeing with the strip
    confirms it. A pass that reads another part number -- "4-CAB16D" for the
    strip's "4-CABI6D", "757-7A-T" for "WSTIA-T" on EP-30784's FAS sheet --
    makes the row uncertain: it is offered for review with every reading,
    nothing is substituted (an I is not turned into a 1 by rule). Passes
    that read nothing are no evidence either way."""
    from PIL import ImageOps

    cell = _catalog_cell_image(image, line, catalog_span)
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
    others = {r["value"] for r in readings if r["value"] and r["value"] != strip}
    if others:
        line.catalog_uncertain = True


def _uncertain_part_issue(line: ExtractedBoqLine, ordinal: int) -> Issue:
    """A row whose part number the passes did not confirm: for the engineer,
    with the row's quantity and every reading of the cell, as the
    verification path words a part-number conflict."""
    x0, x1 = line.table_span or (0, 0)
    y0, y1 = line.row_bounds or (int((line.y_px or 0) - _CELL_HALF_HEIGHT_PX), int((line.y_px or 0) + _CELL_HALF_HEIGHT_PX))
    alternates = sorted({r["value"] for r in (line.catalog_alternates or []) if r["value"]})
    return Issue(
        code=IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE, page=line.page, region=(int(x0), int(y0), int(x1), int(y1)),
        target=f"boq_line:{line.page}:{ordinal}",
        detail={
            "description": line.description, "catalog_no": line.catalog_no, "group_heading": line.group_heading,
            "raw_quantity": line.raw_quantity, "quantity_parse": line.quantity_parse, "quantity": line.quantity,
            "alternates": line.alternates, "building": line.building, "reader": "ocr",
            "reason_code": ReviewReason.PART_NUMBER_CONFLICT.value,
            "reason": f"the column read the part number {line.catalog_no!r}"
                      + (f" at {line.catalog_confidence:.0f}%" if line.catalog_confidence is not None else "")
                      + f"; independent passes on the cell read {', '.join(alternates)}",
            "catalog_alternates": line.catalog_alternates,
            "bbox": [int(x0), int(y0), int(x1), int(y1)],
        },
    )


def _independent_readings(image: Image.Image, line: ExtractedBoqLine) -> list[dict] | None:'''
assert old in s; s = s.replace(old, new, 1)

# wire it into the page loop and the line/issue split
old = '''        rules, layout = found
        readable_pages += 1
        page_lines, section, heading, regions = _read_page(image, dark, rules, layout, page_number, section, heading)
        for line in page_lines:
            if not line.quantity:
                _read_quantity_again(image, line)'''
new = '''        rules, layout = found
        readable_pages += 1
        page_lines, section, heading, regions = _read_page(image, dark, rules, layout, page_number, section, heading)
        catalog_span = (rules[layout.catalog[0]], rules[layout.catalog[1]])
        for line in page_lines:
            if line.catalog_no and line.quantity and (line.catalog_confidence is None or line.catalog_confidence < RECHECK_QUANTITY_BELOW):
                _confirm_catalog(image, line, catalog_span)
            if not line.quantity:
                _read_quantity_again(image, line)'''
assert old in s; s = s.replace(old, new, 1)
old = '''    ordinal = 0
    for line in read:
        if line.quantity:
            result.lines.append(line)
            continue
        ordinal += 1
        issue = _dropped_row_issue(line, ordinal)
        if issue is not None:
            result.issues.append(issue)
'''
new = '''    ordinal = 0
    for line in read:
        if line.quantity and not line.catalog_uncertain:
            result.lines.append(line)
            continue
        ordinal += 1
        if line.quantity and line.catalog_uncertain:
            # Read whole, but its part number is not confirmed: a row for the
            # engineer, not a line the BOQ takes on the strip's word.
            result.issues.append(_uncertain_part_issue(line, ordinal))
            continue
        issue = _dropped_row_issue(line, ordinal)
        if issue is not None:
            result.issues.append(issue)
'''
assert old in s; s = s.replace(old, new, 1)
old = '''from app.extraction.issues import Coverage, Issue, IssueCode, Outcome, PageCoverage, RegionCoverage, outcome_for'''
new = '''from app.extraction.issues import Coverage, Issue, IssueCode, Outcome, PageCoverage, RegionCoverage, ReviewReason, outcome_for'''
assert old in s; s = s.replace(old, new, 1)
old = '''PARSER_VERSION = "2026-09-15.2"'''
new = '''PARSER_VERSION = "2026-09-28.1"   # M2 review 02: band rows, quantity recheck < 90 %, part-number check, heading carry-over'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

t = B / "tests/test_design_sheet_extractor.py"; ts = t.read_text(encoding="utf-8")
ts += '''

def test_a_part_number_the_independent_passes_do_not_confirm_is_a_row_for_review(monkeypatch):
    """EP-30784 FAS: the column read "4-CABI6D"; a pass on the cell read
    "4-CAB16D". Nothing is substituted; the row goes to the engineer with
    both readings and its quantity."""
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    calls = iter([["4-CABI6D", "4-CAB16D"], ["SIGA-CT2", "SIGA-CT2"]])
    def image_to_string(img, config=""):
        return next(image_to_string.batch)
    image_to_string.batch = iter([])

    def run(image, line, span):
        readings = next(calls)
        line.catalog_alternates = [{"pass": f"pass {i}", "text": r, "value": r} for i, r in enumerate(readings)]
        line.catalog_uncertain = any(r != line.catalog_no for r in readings)
    monkeypatch.setattr(dse, "_confirm_catalog", run)
    uncertain = dse.ExtractedBoqLine(catalog_no="4-CABI6D", description="Door Assembly", quantity="1", group_heading="Panel", confidence=71.0,
                                     page=1, y_px=100.0, raw_quantity="1", catalog_confidence=71.0, table_span=(0, 600))
    sure = dse.ExtractedBoqLine(catalog_no="SIGA-CT2", description="Dual Input Module", quantity="119", group_heading=None, confidence=95.0,
                                page=1, y_px=200.0, raw_quantity="119", catalog_confidence=95.0, table_span=(0, 600))
    dse._confirm_catalog(None, uncertain, (0, 100))
    assert uncertain.catalog_uncertain and [r["value"] for r in uncertain.catalog_alternates] == ["4-CABI6D", "4-CAB16D"]
    issue = dse._uncertain_part_issue(uncertain, 1)
    assert issue.code == dse.IssueCode.QUANTITY_OR_UNIT_PARSE_FAILURE and issue.target == "boq_line:1:1"
    assert issue.detail["reason_code"] == "PART_NUMBER_CONFLICT" and issue.detail["quantity"] == "1" and "4-CAB16D" in issue.detail["reason"]
    dse._confirm_catalog(None, sure, (0, 100))
    assert not sure.catalog_uncertain


def test_the_catalog_check_reads_the_cell_twice_and_keeps_what_each_pass_read(monkeypatch):
    from PIL import Image
    from app.services import design_sheet_extractor as dse

    answers = iter(["757-7A-T", "757-7A-T"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="WSTIA-T", description="Horn/Strobe", quantity="56", group_heading=None, confidence=72.0,
                                page=2, y_px=100.0, raw_quantity="56", catalog_confidence=72.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert line.catalog_uncertain and [r["value"] for r in line.catalog_alternates] == ["757-7A-T", "757-7A-T"]
    assert line.catalog_no == "WSTIA-T", "nothing is substituted: the engineer decides"
    answers = iter(["", "SIGA-SB"])
    monkeypatch.setattr(dse.pytesseract, "image_to_string", lambda img, config="": next(answers))
    line = dse.ExtractedBoqLine(catalog_no="SIGA-SB", description="Base", quantity="2206", group_heading=None, confidence=94.0,
                                page=2, y_px=100.0, raw_quantity="2206", catalog_confidence=94.0, table_span=(0, 600))
    dse._confirm_catalog(Image.new("L", (600, 400), 255), line, (100, 300))
    assert not line.catalog_uncertain, "a pass that read nothing is no evidence; the other agreed"
'''
t.write_text(ts, encoding="utf-8")
print("D3 patched")
