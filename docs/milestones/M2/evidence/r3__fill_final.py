"""Final numbers into the M2 documents: the second full-suite run (with the flaky-test note), the D-EXT-10 defect row."""
import re, sys, pathlib

S = pathlib.Path(sys.argv[1]); D = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\docs\milestones\M2")
xml = (S / "final_r3.xml").read_text(encoding="utf-8")
tests = int(re.search(r'tests="(\d+)"', xml).group(1)); fail = int(re.search(r'failures="(\d+)"', xml).group(1)); err = int(re.search(r'errors="(\d+)"', xml).group(1))
skip = int(re.search(r'skipped="(\d+)"', xml).group(1)); secs = float(re.search(r'time="([\d.]+)"', xml).group(1)); passed = tests - fail - err - skip
xml1 = (S / "final_r3_run1.xml").read_text(encoding="utf-8")
t1 = int(re.search(r'tests="(\d+)"', xml1).group(1)); s1 = float(re.search(r'time="([\d.]+)"', xml1).group(1))
new_line = f"**{tests} tests, {passed} passed, {skip} skipped, {fail} failed, {err} errors** in {secs:.1f} s"
FLAKY = (" The one failure, `tests/test_ai_sheet_reader.py::test_the_first_read_runs_as_a_job_the_page_follows` (a BOQ read run as a background job thread; "
         "`FOREIGN KEY constraint failed` on `project_boq_items.extraction_run_id` inside the job, or `UnmappedInstanceError: NoneType` in the full run), is **not attributable to the correction**: "
         "it fails identically on a clean worktree of the reviewed commit `ed7d221` with the same interpreter (3 of 3 runs alone; `evidence/r3__flaky_1.log` is the same failure on this tree), "
         "passed in the first full run of this correction (`r3__final_r3_run1.xml`) and in the M2 submission's run, and passes when its module runs together with `tests/test_boq_extraction_v2.py` "
         "(20 passed, same code, same hour). It is order/timing dependent in untouched BOQ job code (`app/routers/projects.py::_extract_boq`, `app/services/jobs.py`), not diagnosed here, and listed as a limitation for the re-review.")
flaky = FLAKY if fail else ""
run1 = f"A first full run on the code before the observation-JSON fix (D-EXT-10) is kept as `evidence/r3__final_r3_run1.xml` ({t1} tests, all passing, {s1:.1f} s); it did not contain the persistence test that catches the defect."

resp = D / "M2-REVIEW-RESPONSE.md"; s = resp.read_text(encoding="utf-8")
old = "Result: **316 tests, 309 passed, 7 skipped, 0 failed, 0 errors** in 392.6 s (`evidence/r3__final_r3.xml`, `.log`)."
assert old in s
s = s.replace(old, f"Result: {new_line} (`evidence/r3__final_r3.xml`, `.log`).{flaky} {run1}")
s = s.replace("`tests/test_extraction_m2_review.py` adds 20;", "`tests/test_extraction_m2_review.py` adds 21;")
old_r5 = "| **R5** [P1] Ordinary-processing compatibility not established | The extract-only CLI proved its own writes only;"
assert old_r5 in s
s = s.replace(old_r5, old_r5 + " the corrected reader's first clone repair (run 1) itself failed on 35 rows because an observation carried a raw dataclass dict (a datetime) into the row's JSON column - the ordinary writer would have failed the same rows (D-EXT-10, fixed, persistence test added, run 2 on those rows);")
s = s.replace("(e) no engineer sign-off is claimed.", "(e) no engineer sign-off is claimed; (f) one pre-existing order/timing-dependent BOQ job test fails in the second full run and on the clean reviewed commit alike (section 1)." if fail else "(e) no engineer sign-off is claimed.")
resp.write_text(s, encoding="utf-8")

acc = D / "M2-ACCEPTANCE-REPORT.md"; s = acc.read_text(encoding="utf-8")
old = "**316 tests, 309 passed, 7 skipped (live-archive BOQ sheets), 0 failed, 0 errors**, 392.6 s, short scratch path"
assert old in s
s = s.replace(old, f"{new_line} (skips: live-archive BOQ sheets), short scratch path")
s = s.replace("New: `tests/test_extraction_m2_review.py` (20),", "New: `tests/test_extraction_m2_review.py` (21),")
old2 = "The reviewer's five probes pass on the final code (`evidence/r3__probe__probe_results.json`)."
assert old2 in s
s = s.replace(old2, old2 + flaky + " " + run1)
acc.write_text(s, encoding="utf-8")

defects = D / "M2-DEFECTS-AND-FIXES.md"; s = defects.read_text(encoding="utf-8")
row = ("| D-EXT-10 | VAL (R5, found by the clone repair) | An observation that holds a record (`cover_untracked`, `transmittal`) carried `asdict(record)` with a `datetime` inside; the row's JSON column refused it: clone repair run 1 failed 32 of 522 EP-30088 rows and 3 of 356 EP-30784 rows (every untracked-discipline cover and scanned transmittal), and the ordinary writer would have failed the same rows | observations were built with `asdict` while stored records go through `document_sync._record_dict` (datetime as text) | `document_control.observed_record`: the stored-record shape (datetime and path as text) for every record held in an observation | `test_an_observation_that_holds_a_record_is_stored_by_normal_processing` (fails on the pre-fix code: the job fails and the row stays `processing`); clone repair run 2 on the 35 rows (`evidence/r3__repair_r3__repair_p4_rerun.json`, `_p1_rerun.json`); run 1's manifests and after-snapshots are kept (`*_run1`) |")
anchor = "\nAlso changed, not defects:"
assert anchor in s and "D-EXT-10" not in s
s = s.replace(anchor, "\n" + row + "\n" + anchor, 1)
defects.write_text(s, encoding="utf-8")
print(new_line, "| run1", t1, s1, "| flaky note", bool(flaky))
