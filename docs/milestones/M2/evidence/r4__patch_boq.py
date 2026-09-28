"""Review 02 D: the inked band between two tables is read as rows (or held for review with an issue); a quantity read
below 90 % confidence is checked against independent passes and a longer reading (a digit cut at the cell's edge)
sends it to review; the group heading in force carries across a split table and across pages; the Golden matcher
pairs repeated parts by page and order."""
import pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
p = B / "app/services/design_sheet_extractor.py"; s = p.read_text(encoding="utf-8")

# --- D2: the recheck threshold and the trailing-digit rule
old = '''CONFIDENT_QUANTITY = 85
'''
new = '''CONFIDENT_QUANTITY = 85
# A strip reading below this confidence is checked against the independent
# passes on its own cell even when it parsed: EP-30784 FAS read TP606's 491
# as 49 at 86 % and the confident-read rule above never looked again (M2
# review 02, D). Rows at or above it are taken on the strip's word.
RECHECK_QUANTITY_BELOW = 90
'''
assert old in s; s = s.replace(old, new, 1)
old = '''            elif line.verify_quantity or (line.quantity_confidence is not None
                                          and line.quantity_confidence < LOW_QUANTITY_CONFIDENCE):
                # Confident strip reads are not second-guessed: a single-cell
                # pass drops or adds digits on multi-digit counts more often
                # than the strip misreads them (EP-30784 FAS: "121" read "12").
                _confirm_quantity(image, line)'''
new = '''            elif line.verify_quantity or (line.quantity_confidence is not None
                                          and line.quantity_confidence < RECHECK_QUANTITY_BELOW):
                # Strip reads below RECHECK_QUANTITY_BELOW are checked against
                # the passes on the cell; a single-cell pass drops or adds digits
                # on multi-digit counts more often than the strip misreads them
                # (EP-30784 FAS: "121" read "12"), so `_confirm_quantity` keeps a
                # confident strip value the passes half-agree with -- unless a
                # pass read the strip's digits plus one more (491 over 49).
                _confirm_quantity(image, line)'''
assert old in s; s = s.replace(old, new, 1)
old = '''    line.alternates = readings
    agreeing = valid.count(strip_value)
    others = {v: valid.count(v) for v in valid if v != strip_value}
    confidence = f" ({line.quantity_confidence:.0f}%)" if line.quantity_confidence is not None else ""
    if not others or (agreeing >= 1 and (line.quantity_confidence or 0) >= CONFIDENT_QUANTITY):'''
new = '''    line.alternates = readings
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
assert old in s; s = s.replace(old, new, 1)

# --- D4: the heading in force travels through the table readers
old = '''    row_rules = _row_rules(
        dark, top, bottom, rules[layout.quantity[0]], rules[layout.quantity[1]]
    )
    if len(row_rules) >= MIN_ROW_RULES:
        return _read_ruled_rows(descriptions, quantities, catalogs, row_rules, page_number, section)
'''
new = '''    row_rules = _row_rules(
        dark, top, bottom, rules[layout.quantity[0]], rules[layout.quantity[1]]
    )
    if len(row_rules) >= MIN_ROW_RULES:
        return _read_ruled_rows(descriptions, quantities, catalogs, row_rules, page_number, section, heading)
'''
assert old in s; s = s.replace(old, new, 1)
old = '''    top: int,
    bottom: int,
    section: str | None,
) -> list[ExtractedBoqLine]:
    def strip(bounds: tuple[int, int]) -> list[tuple[float, str, float]]:'''
new = '''    top: int,
    bottom: int,
    section: str | None,
    heading: str | None = None,
) -> tuple[list[ExtractedBoqLine], str | None]:
    """The table's lines and the group heading in force at its foot. `heading`
    is the one in force above it: on a sheet whose table the scan splits (a
    streak across the column rules) or that continues on the next page, the
    rows after the split are under the same heading until the sheet names
    another (M2 review 02, D: EP-30784 FAS's 26 Field Devices rows on page 2
    came back with no group)."""
    def strip(bounds: tuple[int, int]) -> list[tuple[float, str, float]]:'''
assert old in s; s = s.replace(old, new, 1)
old = '''    lines: list[ExtractedBoqLine] = []
    heading: str | None = None
    verify_following = False
    trailing_heading: tuple[float, str] | None = None
    heading_before_trailing: str | None = None
    entry_section = section
'''
new = '''    lines: list[ExtractedBoqLine] = []
    verify_following = False
    trailing_heading: tuple[float, str] | None = None
    heading_before_trailing: str | None = None
    entry_section = section
'''
assert old in s; s = s.replace(old, new, 1)
# the unruled reader's tail: find its return and hand the heading back too
i = s.index("def _read_table(")
j = s.find("\ndef ", i + 10)
if j < 0:
    j = len(s)      # _read_table is the last function of the module
body = s[i:j]
assert body.count("\n    return ") >= 1, "unruled return not found"
# every `return <expr>` at function level in _read_table (after the ruled-rows return) returns lines: wrap them
lines_ret = []
out = []
for ln in body.split("\n"):
    if ln.startswith("    return ") and "_read_ruled_rows" not in ln:
        expr = ln[len("    return "):]
        ln = f"    return {expr}, heading"
        lines_ret.append(expr)
    out.append(ln)
body = "\n".join(out)
s = s[:i] + body + s[j:]
print("unruled returns rewritten:", lines_ret)

old = '''    lines: list[ExtractedBoqLine] = []
    heading: str | None = None

    def take_heading(text: str) -> None:
        nonlocal heading, section
        if heading is not None and not lines and _is_banner(heading):
            section = heading
        heading = text

    for row_top, row_bottom in zip(row_rules, row_rules[1:]):'''
new = '''    lines: list[ExtractedBoqLine] = []

    def take_heading(text: str) -> None:
        nonlocal heading, section
        if heading is not None and not lines and _is_banner(heading):
            section = heading
        heading = text

    for row_top, row_bottom in zip(row_rules, row_rules[1:]):'''
assert old in s; s = s.replace(old, new, 1)
i = s.index("def _read_ruled_rows(")
head_end = s.index(") -> list[ExtractedBoqLine]:", i)
sig = s[i:head_end]
assert "section: str | None = None," in sig
s = s[:i] + sig.replace("section: str | None = None,", "section: str | None = None,\n    heading: str | None = None,") + ") -> tuple[list[ExtractedBoqLine], str | None]:" + s[head_end + len(") -> list[ExtractedBoqLine]:"):]
old = """    banded = _read_quantity_banded_rows(descriptions[start:], quantities, catalogs, lines, top, bottom, page_number,
                                        entry_section)"""
new = """    banded = _read_quantity_banded_rows(descriptions[start:], quantities, catalogs, lines, top, bottom, page_number,
                                        entry_section, entry_heading)"""
assert old in s; s = s.replace(old, new, 1)
old = """    entry_section = section
"""
new = """    entry_section = section
    entry_heading = heading
"""
assert s.count(old) == 1; s = s.replace(old, new, 1)
old = '''            )
        )

    return lines


def _is_count(text: str | None) -> bool:'''
new = '''            )
        )

    return lines, heading


def _is_count(text: str | None) -> bool:'''
assert old in s; s = s.replace(old, new, 1)
old = '''    banded = _read_ruled_rows(descriptions, quantities, catalogs, edges, page_number, section)
    if _orphan_quantities(quantities, banded):'''
new = '''    banded, _heading = _read_ruled_rows(descriptions, quantities, catalogs, edges, page_number, section, heading)
    if _orphan_quantities(quantities, banded):'''
assert old in s; s = s.replace(old, new, 1)
old = '''    page_number: int,
    section: str | None,
) -> list[ExtractedBoqLine] | None:
    """A second reading of an unruled table whose rows the pairing missed,'''
new = '''    page_number: int,
    section: str | None,
    heading: str | None = None,
) -> list[ExtractedBoqLine] | None:
    """A second reading of an unruled table whose rows the pairing missed,'''
assert old in s; s = s.replace(old, new, 1)

# --- D1 + D4 in the page reader
old = '''    page_number: int,
    section: str | None = None,
) -> tuple[list[ExtractedBoqLine], str | None]:
    """Every table on the page, in order, each under the section named
    above it. Returns the lines and the section in force at the foot of the
    page, which the next page's first table continues."""
    lines: list[ExtractedBoqLine] = []
    regions: list[RegionCoverage] = []
    previous_bottom = 0
    span = (rules[layout.quantity[0]], rules[layout.quantity[1]])
    for top, bottom in _table_extents(dark, rules[layout.description[0]]):
        named = _section_title(image, rules[0], rules[-1], max(previous_bottom, top - SECTION_BAND_PX), top)
        if named:
            section = named
        elif previous_bottom and _unread_band(dark, previous_bottom, top, rules[layout.description[0]], rules[layout.description[1]]):
            # M2 review 01 (R4): on EP-30784's FAS sheet, page 2, a scanner
            # streak breaks the column rules across one item row, the table
            # extent splits around it and the row (SIGA-OSHD-FCN, 525) was
            # neither read nor reported. The band is not read as rows here --
            # that is the row reader's contract to settle -- but it is no
            # longer silent: it is a skipped region of the page's coverage.
            regions.append(RegionCoverage("band", previous_bottom, top, "skipped",
                                          reason="inked band between two tables where the column rules break: not read as rows"))
        table_lines = _read_table(image, dark, rules, layout, page_number, top, bottom, section)
        for line in table_lines:
            line.quantity_span = span
            line.table_span = (rules[0], rules[-1])
        lines.extend(table_lines)
        accepted = sum(1 for line in table_lines if line.quantity)
        regions.append(RegionCoverage("table", top, bottom, "processed", rows_accepted=accepted,
                                      rows_dropped=len(table_lines) - accepted))
        previous_bottom = bottom
    return lines, section, regions
'''
new = '''    page_number: int,
    section: str | None = None,
    heading: str | None = None,
) -> tuple[list[ExtractedBoqLine], str | None, str | None, list[RegionCoverage]]:
    """Every table on the page, in order, each under the section named
    above it. Returns the lines, the section and the group heading in force
    at the foot of the page -- which the next page's first table continues
    -- and the page's regions."""
    lines: list[ExtractedBoqLine] = []
    regions: list[RegionCoverage] = []
    previous_bottom = 0
    span = (rules[layout.quantity[0]], rules[layout.quantity[1]])

    def place(table_lines: list[ExtractedBoqLine]) -> int:
        for line in table_lines:
            line.quantity_span = span
            line.table_span = (rules[0], rules[-1])
        lines.extend(table_lines)
        return sum(1 for line in table_lines if line.quantity)

    for top, bottom in _table_extents(dark, rules[layout.description[0]]):
        named = _section_title(image, rules[0], rules[-1], max(previous_bottom, top - SECTION_BAND_PX), top)
        if named:
            section = named
            heading = None      # a new section: the heading in force was the last section's
        elif previous_bottom and _unread_band(dark, previous_bottom, top, rules[layout.description[0]], rules[layout.description[1]]):
            # M2 review 01/02 (R4, D): on EP-30784's FAS sheet, page 2, a
            # scanner streak breaks the column rules across one item row and
            # the table extent splits around it; the row (SIGA-OSHD-FCN, 525)
            # was neither read nor reported. The band is read as rows of its
            # own, under the heading in force; a band that gives no row is a
            # skipped region the caller raises an issue for, so the sheet is
            # not accepted as complete over it.
            band_lines, heading = _read_table(image, dark, rules, layout, page_number, previous_bottom, top, section, heading)
            if band_lines:
                accepted = place(band_lines)
                regions.append(RegionCoverage("band", previous_bottom, top, "processed", rows_accepted=accepted,
                                              rows_dropped=len(band_lines) - accepted,
                                              reason="inked band between two tables where the column rules break: read as rows"))
            else:
                regions.append(RegionCoverage("band", previous_bottom, top, "skipped",
                                              reason="inked band between two tables where the column rules break: no row could be read"))
        table_lines, heading = _read_table(image, dark, rules, layout, page_number, top, bottom, section, heading)
        accepted = place(table_lines)
        regions.append(RegionCoverage("table", top, bottom, "processed", rows_accepted=accepted,
                                      rows_dropped=len(table_lines) - accepted))
        previous_bottom = bottom
    return lines, section, heading, regions
'''
assert old in s; s = s.replace(old, new, 1)
old = '''    section: str | None = None

    for page_index in range(document.page_count):'''
new = '''    section: str | None = None
    heading: str | None = None

    for page_index in range(document.page_count):'''
assert old in s; s = s.replace(old, new, 1)
old = '''        page_lines, section, regions = _read_page(image, dark, rules, layout, page_number, section)'''
new = '''        page_lines, section, heading, regions = _read_page(image, dark, rules, layout, page_number, section, heading)'''
assert old in s; s = s.replace(old, new, 1)
old = '''        coverage.processed = True
        coverage.regions = regions
        for region in regions:
            if region.status == "skipped":
                result.notes.append(f"page {page_number}: a band of {region.bottom - region.top} px between two tables "
                                    f"(y {region.top}-{region.bottom}) was not read as rows")
        read.extend(page_lines)'''
new = '''        coverage.processed = True
        coverage.regions = regions
        for region in regions:
            if region.kind != "band":
                continue
            if region.status == "skipped":
                result.notes.append(f"page {page_number}: a band of {region.bottom - region.top} px between two tables "
                                    f"(y {region.top}-{region.bottom}) gave no row: held for review")
                result.issues.append(Issue(IssueCode.UNPROCESSED_PAGE_OR_REGION, page=page_number,
                                           region=(int(rules[0]), int(region.top), int(rules[-1]), int(region.bottom)),
                                           target=f"page:{page_number}:band:{region.top}",
                                           detail={"reason": region.reason, "kind": "band"}))
            else:
                result.notes.append(f"page {page_number}: a band of {region.bottom - region.top} px between two tables "
                                    f"(y {region.top}-{region.bottom}) was read as {region.rows_accepted} row(s)")
        read.extend(page_lines)'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# --- D5: the Golden matcher pairs repeated parts by page and order
p = B / "scripts/boq_metrics.py"; s = p.read_text(encoding="utf-8")
old = '''    free = list(range(len(extracted)))
    pairs: list[tuple[dict, dict | None]] = []
    for gold in golden:
        gold_part = _part(gold.get("part_number"))
        gold_desc = _desc_key(gold.get("description"))
        best, best_score = None, -1
        for index in free:
            row = extracted[index]
            part = _part(row.get("part_number"))
            if gold_part:
                if part != gold_part:
                    continue
                score = 10
            else:
                if part or _desc_key(row.get("description")) != gold_desc:
                    continue
                score = 10
            if row.get("page") == gold.get("page"):
                score += 3'''
new = '''    free = list(range(len(extracted)))
    pairs: list[tuple[dict, dict | None]] = []
    # A part quoted twice (EP-30784 EML: SL210DI 33 and SL210DI 17): the
    # golden rows and the read rows are paired by their order among the rows
    # of that part on the same page (M2 review 02, D), so the quantities are
    # judged against the right occurrence, not crosswise.
    gold_ordinal = {}
    seen: dict = {}
    for i, gold in enumerate(golden):
        key = (_part(gold.get("part_number")) or _desc_key(gold.get("description")), gold.get("page"))
        gold_ordinal[i] = seen.get(key, 0)
        seen[key] = gold_ordinal[i] + 1
    read_ordinal = {}
    seen = {}
    for i, row in enumerate(extracted):
        key = (_part(row.get("part_number")) or _desc_key(row.get("description")), row.get("page"))
        read_ordinal[i] = seen.get(key, 0)
        seen[key] = read_ordinal[i] + 1
    for gi, gold in enumerate(golden):
        gold_part = _part(gold.get("part_number"))
        gold_desc = _desc_key(gold.get("description"))
        best, best_score = None, -1
        for index in free:
            row = extracted[index]
            part = _part(row.get("part_number"))
            if gold_part:
                if part != gold_part:
                    continue
                score = 10
            else:
                if part or _desc_key(row.get("description")) != gold_desc:
                    continue
                score = 10
            if row.get("page") == gold.get("page"):
                score += 3
                if read_ordinal[index] == gold_ordinal[gi]:
                    score += 4      # the same occurrence of a repeated part'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# --- the band unit test follows the new signatures
p = B / "tests/test_design_sheet_extractor.py"; s = p.read_text(encoding="utf-8")
old = '''    monkeypatch.setattr(dse, "_table_extents", lambda d, x: [(0, 100), (160, 400)])
    monkeypatch.setattr(dse, "_section_title", lambda *a, **k: None)
    monkeypatch.setattr(dse, "_read_table", lambda *a, **k: [])
    _lines, _section, regions = dse._read_page(Image.new("L", (600, 400), 255), dark, rules, layout, 2, None)
    kinds = [(r.kind, r.status, r.top, r.bottom) for r in regions]
    assert ("band", "skipped", 100, 160) in kinds
    assert [k for k in kinds if k[0] == "table"] == [("table", "processed", 0, 100), ("table", "processed", 160, 400)]

    # a blank band between two tables is not reported
    blank = np.zeros((400, 600), dtype=bool)
    _lines, _section, regions = dse._read_page(Image.new("L", (600, 400), 255), blank, rules, layout, 2, None)
    assert all(r.kind == "table" for r in regions)
'''
new = '''    monkeypatch.setattr(dse, "_table_extents", lambda d, x: [(0, 100), (160, 400)])
    monkeypatch.setattr(dse, "_section_title", lambda *a, **k: None)
    calls = []

    def read_table(image, d, r, lay, page, top, bottom, section, heading=None):
        calls.append((top, bottom, heading))
        if (top, bottom) == (100, 160):
            return [], heading                     # the band gave no row
        return [], "Field Devices" if top == 0 else heading
    monkeypatch.setattr(dse, "_read_table", read_table)
    _lines, _section, heading, regions = dse._read_page(Image.new("L", (600, 400), 255), dark, rules, layout, 2, None, None)
    kinds = [(r.kind, r.status, r.top, r.bottom) for r in regions]
    assert ("band", "skipped", 100, 160) in kinds
    assert [k for k in kinds if k[0] == "table"] == [("table", "processed", 0, 100), ("table", "processed", 160, 400)]
    assert calls == [(0, 100, None), (100, 160, "Field Devices"), (160, 400, "Field Devices")], "the heading in force carries across the band and the split"
    assert heading == "Field Devices"

    # a band that gives a row is a processed region
    monkeypatch.setattr(dse, "_read_table", lambda image, d, r, lay, page, top, bottom, section, heading=None:
                        ([dse.ExtractedBoqLine(catalog_no="SIGA-OSHD-FC", description="Multisensor", quantity="525", group_heading=heading,
                                               confidence=90.0, page=page, y_px=(top + bottom) / 2)] if (top, bottom) == (100, 160) else [], heading))
    lines, _section, _heading, regions = dse._read_page(Image.new("L", (600, 400), 255), dark, rules, layout, 2, None, "Field Devices")
    assert [(r.kind, r.status, r.rows_accepted) for r in regions if r.kind == "band"] == [("band", "processed", 1)]
    assert [(l.catalog_no, l.quantity, l.group_heading) for l in lines] == [("SIGA-OSHD-FC", "525", "Field Devices")]

    # a blank band between two tables is not reported
    blank = np.zeros((400, 600), dtype=bool)
    monkeypatch.setattr(dse, "_read_table", lambda *a, **k: ([], None))
    _lines, _section, _heading, regions = dse._read_page(Image.new("L", (600, 400), 255), blank, rules, layout, 2, None)
    assert all(r.kind == "table" for r in regions)


def test_a_longer_independent_reading_sends_a_cut_digit_to_review(monkeypatch):
    """EP-30784 FAS: TP606 printed 491, the column read 49 at 86 % and one
    independent pass read 491: neither value is taken, the row is reviewed."""
    from app.services import design_sheet_extractor as dse

    line = dse.ExtractedBoqLine(catalog_no="TP606", description="Back Box", quantity="49", group_heading=None, confidence=87.0, page=2,
                                raw_quantity="49", quantity_confidence=86.0, quantity_parse={"kind": "equipment_count", "raw": "49", "value": 49, "status": "ok"})
    monkeypatch.setattr(dse, "_independent_readings", lambda image, l: [
        {"pass": "binarised, single line, digits", "text": "491", "value": "491", "status": "ok"},
        {"pass": "binarised, single character", "text": "49]", "value": "49", "status": "ok"},
        {"pass": "greyscale, single line, digits or words", "text": "49", "value": "49", "status": "ok"}])
    dse._confirm_quantity(None, line)
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
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("BOQ patched")
