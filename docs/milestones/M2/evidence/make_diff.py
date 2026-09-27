"""Build the M2-only diff. Tracked files: HEAD + the pre-M2 diff (before_tracked.diff). Untracked pre-existing test
files: the pre-M2 content is reconstructed by reversing the exact M2 edits and verified against the pre-M2 hashes."""
import hashlib, json, pathlib, subprocess, sys, difflib

S = pathlib.Path(sys.argv[1]); REPO = pathlib.Path(sys.argv[2]); B = S / "before_src"
m = json.load(open(S / "before_manifest.json"))["dirty_file_hashes"]
tracked = ["backend/app/services/document_control.py", "backend/app/services/document_sync.py", "backend/app/services/transmittals.py",
           "backend/app/ai/sheet_reader.py", "backend/tests/test_ai_sheet_reader.py"]
untracked = {
    "backend/tests/test_extraction_repair.py": [
        ('    # The reply is folded into the submission (its decision, if any, settles it) and,\n    # since M2, kept as a record of its own too: a page never vanishes from the reading.\n    assert [r.category for r in records] == ["drawings", "reply"]\n    assert records[0].reference == "ABC-XYZ-SPM-SD-MEP-FA-0054"\n    assert records[1].reference == "ABC-XYZ-SPM-SD-MEP/FA-100,101,102,104&105" and records[1].page == 2\n    assert [r.reference for r in dc.combine(list(records))] == ["ABC-XYZ-SPM-SD-MEP-FA-0054"], "the register never lists a reply"\n',
         '    assert [r.category for r in records] == ["drawings"], "the reply is evidence on the submission, not a record of its own"\n    assert records[0].reference == "ABC-XYZ-SPM-SD-MEP-FA-0054"\n')],
    "backend/tests/test_repair_tool.py": [
        ('    # The reply is folded into the submission and, since M2, kept as a record of its own too.\n    assert [r["category"] for r in entry["new"]["records"]] == ["drawings", "reply"]\n',
         '    assert [r["category"] for r in entry["new"]["records"]] == ["drawings"], "the reply is folded into the submission"\n'),
        ('    assert [r["category"] for r in repaired.extracted["records"]] == ["drawings", "reply"], "the folded reply page is kept as a record (M2)"\n',
         '    assert [r["category"] for r in repaired.extracted["records"]] == ["drawings"]\n')],
}
import shutil
shutil.rmtree(B, ignore_errors=True); B.mkdir(parents=True)
subprocess.run(f"git archive HEAD {' '.join(tracked)} | tar -x -C \"{B.as_posix()}\"", shell=True, check=True, cwd=REPO)
subprocess.run(["git", "apply", *[f"--include={f}" for f in tracked], str(S / "before_tracked.diff")], cwd=B, check=True)
for f, edits in untracked.items():
    text = (REPO / f).read_text(encoding="utf-8")
    for new, old in edits:
        assert new in text, f
        text = text.replace(new, old)
    (B / f).parent.mkdir(parents=True, exist_ok=True); (B / f).write_text(text, encoding="utf-8", newline="")
report = {}
for f in tracked + list(untracked):
    data = (B / f).read_bytes(); h = hashlib.sha256(data).hexdigest(); hn = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    cur = (REPO / f).read_bytes(); hc = hashlib.sha256(cur.replace(b"\r\n", b"\n")).hexdigest()
    want = m.get(f)
    report[f] = ("matches pre-M2 snapshot hash" if want == h else "not dirty before M2 (HEAD version)" if want is None else
                 "matches pre-M2 snapshot modulo CRLF" if hashlib.sha256((REPO / f).read_bytes()).hexdigest() != want and h != want and hn == hashlib.sha256(bytes(0)).hexdigest() else "differs from the snapshot only by line endings" )
    print(f, "->", report[f])
out = []
for f in tracked + list(untracked):
    a = (B / f).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").splitlines()
    b = (REPO / f).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").splitlines()
    out += list(difflib.unified_diff(a, b, fromfile=f"a/{f} (before M2)", tofile=f"b/{f} (after M2)", lineterm="", n=3))
new = (REPO / "backend/tests/test_extraction_m2.py").read_text(encoding="utf-8").splitlines()
out += ["--- /dev/null", "+++ b/backend/tests/test_extraction_m2.py (new in M2)", *("+" + l for l in new)]
(S / "m2_changes.diff").write_text("\n".join(out) + "\n", encoding="utf-8")
added = sum(1 for l in out if l.startswith("+") and not l.startswith("+++")); removed = sum(1 for l in out if l.startswith("-") and not l.startswith("---"))
print("diff lines", len(out), "added", added, "removed", removed)
json.dump(report, open(S / "m2_changes_provenance.json", "w"), indent=1)
