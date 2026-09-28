import subprocess, json, sys, pathlib, os, re
S = pathlib.Path(sys.argv[1]); env = dict(os.environ, TEMP="C:/t/m2r", TMP="C:/t/m2r")
T = "tests/test_ai_sheet_reader.py::test_the_first_read_runs_as_a_job_the_page_follows"
runs = {}
def run(name, args, extra_env=None):
    e = dict(env, **(extra_env or {}))
    r = subprocess.run([sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider", f"--basetemp=C:/t/m2r/jobrun_{name}"], capture_output=True, text=True, env=e)
    tail = [l for l in r.stdout.splitlines() if re.search(r"passed|failed|error", l)][-1:]
    runs[name] = {"args": args, "env": extra_env or {}, "rc": r.returncode, "summary": tail[0] if tail else r.stdout[-200:]}
    print(name, runs[name]["summary"], flush=True)
for i in (1, 2, 3):
    run(f"alone_{i}", [T])
run("in_module", ["tests/test_ai_sheet_reader.py"])
run("mixed_order_a", ["tests/test_boq_extraction_v2.py", T, "tests/test_document_sync.py", "tests/test_submittal.py"])
run("mixed_order_b", ["tests/test_submittal.py", "tests/test_document_sync.py", T, "tests/test_boq_extraction_v2.py"])
run("memory_harness_alone", [T], {"EP_TEST_DATABASE": "memory"})
run("memory_harness_regression", ["tests/test_job_thread_sessions.py"], {"EP_TEST_DATABASE": "memory"})
run("file_harness_regression_x3", ["tests/test_job_thread_sessions.py", "tests/test_job_thread_sessions.py", "tests/test_job_thread_sessions.py"])
json.dump(runs, open(S / "job_test_runs.json", "w"), indent=1)
