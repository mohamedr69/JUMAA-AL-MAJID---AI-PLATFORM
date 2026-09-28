"""Review 02 B: text, marks and OCR candidates settled together, once; D8: exclusive page outcomes, OCR as its own dimension."""
import pathlib
p = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend\app\services\document_control.py"); s = p.read_text(encoding="utf-8")

old = '''                if found and all(row.status == "UR" for row in found):
                    marks = _marks(page, text, sha256, index)
                    outcome, decision, evidence = resolve_marks(marks)
                    if outcome == "conflict":
                        found = [replace(row, status="UR", reply_text=evidence, decision_candidates=_candidates_of(marks),
                                         flags=(*row.flags, "decision_conflict")) for row in found]
                        observations.append({"page": number, "kind": "decision_conflict", "marks": marks})
                    elif outcome == "resolved":
                        methods = {m["method"] for m in marks}
                        # A rectangle drawn or highlighted into the page is a
                        # method the accepted reader did not read: promoted
                        # only on the evaluation path; the mark is kept.
                        if methods <= {"drawn_frame"} and not promote:
                            found = [_hold_decision(row, decision, evidence, "drawn_frame", "decision_method_unpromoted") for row in found]
                            observations.append({"page": number, "kind": "decision_unpromoted", "marks": marks})
                        else:
                            found = [replace(row, status=decision, reply_text=evidence) for row in found]
'''
new = '''                # The page's marks are collected whatever its text says (M2
                # review 02, B): a status printed in the text and a frame drawn
                # round another option are two candidates, settled together
                # with what OCR adds, below -- never the first one found.
                marks = _marks(page, text, sha256, index) if found else []
                ocr_candidates: list[dict] = []
'''
assert old in s; s = s.replace(old, new, 1)

old = '''                # OCR title blocks of scanned pages and image stamps on forms.
                # Also a page whose own text or ticked box already gave a
                # decision: the consultant's stamp, pasted on as an image, is
                # the verdict that stands, and it can say otherwise -- EP-30784's
                # emergency lighting sample (BBY006-GME-SAR-EL-LI-0001) has
                # "Approved as Noted (B)" ticked and a "(C) Revise & Resubmit"
                # stamp beside it. What OCR finds is cached by content'''
new = '''                # OCR title blocks of scanned pages and image stamps on forms.
                # Also a page whose own text or ticked box already gave a
                # decision: the consultant's stamp, pasted on as an image, can
                # say otherwise -- EP-30784's emergency lighting sample
                # (BBY006-GME-SAR-EL-LI-0001) has "Approved as Noted (B)"
                # ticked and a "(C) Revise & Resubmit" stamp beside it. The
                # stamp used to override the tick implicitly; since M2 review
                # 02 (B) the two are a conflict the record shows, until an
                # explicit precedence policy is authorised (none is). What OCR
                # finds is cached by content'''
assert old in s; s = s.replace(old, new, 1)

old = '''                            if not found: found = ocr_found
                            if ocr_found or decision != "UR":
                                counted("ocr_pages_used")   # the OCR text changed what was read
                            if decision != "UR":
                                found = [replace(row, status=decision, reply_text=evidence) for row in found]
                            text += "\\n" + ocr_text
                        except Exception as exc:  # noqa: BLE001 -- OCR failed on this page: noted, the page's text stands
                            warnings.append(f"Could not OCR {path.name}, page {number}.")
                            ocr_failed.append(number)
                            failed.append({"page": number, "reason": f"OCR failed: {type(exc).__name__}: {exc}"[:200]})
                    else:
                        warnings.append(f"OCR limit reached in {path.name}; some replies may need verification.")
                        skipped.append({"page": number, "reason": "OCR page limit"})
                # A decision read off a sheet whose revision came from the
                # folder while the sheet prints another is a mark, not yet
                # that revision's status: kept beside the record.
                found = [_hold_decision(replace(row, status="UR"), row.status, row.reply_text, "sheet_mark", "decision_revision_unvalidated")
                         if row.status != "UR" and _revision_unvalidated(row) else row for row in found]'''
new = '''                            if not found: found = ocr_found
                            if ocr_found or decision != "UR":
                                counted("ocr_pages_used")   # the OCR text changed what was read
                            if decision != "UR":
                                ocr_candidates.append({"status": decision, "label": evidence or "", "method": "ocr", "rect": None})
                            text += "\\n" + ocr_text
                        except Exception as exc:  # noqa: BLE001 -- OCR failed on this page: noted, the page's text stands
                            warnings.append(f"Could not OCR {path.name}, page {number}.")
                            ocr_failed.append(number)
                            ocr_failures.append({"page": number, "reason": f"OCR failed: {type(exc).__name__}: {exc}"[:200]})
                    else:
                        warnings.append(f"OCR limit reached in {path.name}; some replies may need verification.")
                        ocr_skipped.append(number)
                # Every decision candidate the page gave -- its text, its marks,
                # its OCR -- settled together, once (M2 review 02, B).
                if found:
                    found, page_observations = settle_decision(found, marks, ocr_candidates, promote)
                    observations.extend({"page": number, **o} for o in page_observations)
                # A decision read off a sheet whose revision came from the
                # folder while the sheet prints another is a mark, not yet
                # that revision's status: kept beside the record.
                found = [_hold_decision(replace(row, status="UR"), row.status, row.reply_text, "sheet_mark", "decision_revision_unvalidated")
                         if row.status != "UR" and _revision_unvalidated(row) else row for row in found]'''
assert old in s; s = s.replace(old, new, 1)

old = '''            except Exception as exc:  # noqa: BLE001 -- this page failed the reader: recorded; the pages before it stand
                failed.append({"page": number, "reason": f"{type(exc).__name__}: {exc}"[:200]})
                warnings.append(f"Page {number} of {path.name} could not be read.")
                pending = None'''
new = '''            except Exception as exc:  # noqa: BLE001 -- this page failed the reader: recorded; the pages before it stand
                if number in visited:
                    visited.remove(number)   # visited, skipped and failed are exclusive: this page failed
                failed.append({"page": number, "reason": f"{type(exc).__name__}: {exc}"[:200]})
                warnings.append(f"Page {number} of {path.name} could not be read.")
                pending = None'''
assert old in s; s = s.replace(old, new, 1)

old = '''    if stop_reason == "unavailable":
        outcome = "unavailable"
    elif failed:
        outcome = "partial"
    elif skipped or stop_reason:
        outcome = "bounded"
    else:
        outcome = "complete"
    coverage = {"outcome": outcome, "pages_total": total, "pages_visited": visited, "pages_skipped": skipped,
                "pages_failed": failed, "ocr_failed_pages": ocr_failed, "stop_reason": stop_reason,
                "parser_version": PARSER_VERSION, "promoted": bool(promote)}'''
new = '''    if stop_reason == "unavailable":
        outcome = "unavailable"
    elif failed or ocr_failed:
        outcome = "partial"
    elif skipped or stop_reason or ocr_skipped:
        outcome = "bounded"
    else:
        outcome = "complete"
    # Two dimensions, each exclusive within itself (M2 review 02, D): a page
    # was visited, skipped (by reason) or failed (by reason), and never two
    # of these; OCR, attempted on some of the visited pages, either ran,
    # failed or was left out by the OCR budget -- listed under "ocr", not
    # among the pages' own outcomes (a page whose OCR failed was visited).
    coverage = {"outcome": outcome, "pages_total": total, "pages_visited": visited, "pages_skipped": skipped,
                "pages_failed": failed, "ocr_failed_pages": ocr_failed,
                "ocr": {"attempted": ocr_count, "failed": ocr_failures, "skipped_budget": ocr_skipped},
                "stop_reason": stop_reason, "parser_version": PARSER_VERSION, "promoted": bool(promote),
                "profile": extraction_profile(promote)}'''
assert old in s; s = s.replace(old, new, 1)

old = '''    ocr_failed: list[int] = []
    stop_reason = None
    try:
        if clock is not None:
            clock.set("page_count", total)
        pending = None
        ocr_count = 0'''
new = '''    ocr_failed: list[int] = []
    ocr_failures: list[dict] = []
    ocr_skipped: list[int] = []
    stop_reason = None
    try:
        if clock is not None:
            clock.set("page_count", total)
        pending = None
        ocr_count = 0'''
assert old in s; s = s.replace(old, new, 1)

old = '''def _hold_decision(row: ControlledDocument, status: str, evidence: str | None, method: str, flag: str) -> ControlledDocument:'''
new = '''# Methods the accepted reader did not read: promoted only on the evaluation
# path. A rectangle drawn or highlighted into the page (`drawn_frame`).
UNPROMOTED_METHODS = frozenset({"drawn_frame"})


def settle_decision(rows: list, marks: list[dict], ocr_candidates: list[dict], promote: bool) -> tuple[list, list[dict]]:
    """The page's decision settled from every candidate at once (M2 review
    02, B): the status its text carries (method "text"), the marks on it
    (filled boxes, annotations, drawn frames and highlights, `decision_marks`)
    and what OCR read (method "ocr"). Candidates that name different answers
    are a conflict: status UR, every candidate kept, flag `decision_conflict`
    -- no method wins by running later. Candidates that agree settle the
    status when at least one of them is a promoted method (text, filled box,
    annotation, OCR) or the evaluation path is on; agreement among held
    methods alone is a candidate, not a status (`decision_method_unpromoted`).
    No precedence between sources is applied: the stamp-over-tick rule the
    reader once applied implicitly is withdrawn until an explicit policy is
    authorised. Returns (rows, page observations without their page number)."""
    settled: list = []
    conflict: list[dict] | None = None
    held: list[dict] | None = None
    for row in rows:
        own = ([{"status": row.status, "label": row.reply_text or "status read from the page text", "method": "text", "rect": None}]
               if row.status != "UR" else [])
        candidates = own + list(marks) + list(ocr_candidates)
        if not candidates:
            settled.append(row)
            continue
        unpromoted = [c for c in candidates if c["method"] in UNPROMOTED_METHODS and not promote]
        usable = [c for c in candidates if c["method"] not in UNPROMOTED_METHODS or promote]
        if len({c["status"] for c in candidates}) > 1:
            evidence = "Conflicting evidence: " + "; ".join(f"{c['label']} [{c['method']}]" for c in candidates)
            flags = (*row.flags, "decision_conflict", *(("decision_method_unpromoted",) if unpromoted else ()))
            settled.append(replace(row, status="UR", reply_text=evidence, decision_candidates=_candidates_of(candidates),
                                   flags=tuple(dict.fromkeys(flags))))
            conflict = candidates
        elif usable:
            label = next((c["label"] for c in usable if c["method"] != "text"), usable[0]["label"])
            changes = {"status": usable[0]["status"]}
            if row.status == "UR":
                changes["reply_text"] = label
            if len({c["method"] for c in candidates}) > 1 or unpromoted:
                changes["decision_candidates"] = _candidates_of(candidates)
            settled.append(replace(row, **changes))
        else:
            kept = replace(row, status="UR")
            for c in unpromoted:
                kept = _hold_decision(kept, c["status"], c["label"], c["method"], "decision_method_unpromoted")
            settled.append(kept)
            held = unpromoted
    observations = []
    if conflict:
        observations.append({"kind": "decision_conflict", "marks": conflict})
    if held:
        observations.append({"kind": "decision_unpromoted", "marks": held})
    return settled, observations


def _hold_decision(row: ControlledDocument, status: str, evidence: str | None, method: str, flag: str) -> ControlledDocument:'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8"); print("B + D8 patched")
