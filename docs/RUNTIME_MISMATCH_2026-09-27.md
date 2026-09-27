# Stale worker / runtime mismatch of 2026-09-27 -- root cause and hardening

## The failure

```
ImportError: cannot import name 'DocumentClassification' from 'app.models'
(C:\Users\moham\Desktop\dev\dev\ep-platform\backend\app\models.py)
```

File Sync succeeded, three new files were indexed, and the document
processing job failed at once: 0 processed, 3 still pending.

## Root cause: a stale long-lived worker (confirmed)

Kind: **stale worker process**, combined with lazy per-job imports. Not a
wrong interpreter, not another checkout, not a circular import, not an
import defect.

Evidence, all from the database and the file system:

| Fact | Where |
|---|---|
| The document worker `documents:LAPTOP-IL4L4UAJ:16400:1790497019` (PID 16400) started at 08:16:59 UTC (12:16 local), last heartbeat 13:34:32 UTC | `background_workers` |
| `app/models.py` gained `DocumentClassification` at 14:23 local; `document_processing.py` gained `_classify` at 13:50; `document_classification.py` was created at 13:53 | file mtimes |
| Jobs 14 and 15 (`process_documents`, project 2) failed at 13:25 UTC (17:25 local) on worker 16400 with the ImportError; the stored traceback goes `document_processing.run` -> `_classify` -> `from app.services import document_classification` -> `from app.models import DocumentClassification` | `background_jobs.detail.trace` |
| The traceback's paths are all `C:\Users\moham\Desktop\dev\dev\ep-platform\backend\...` -- the expected root; the worker's interpreter is `backend\venv\Scripts\python.exe` (start-backend.bat), and the same interpreter imports the class from the working copy | manual check below |
| The sync job 13 succeeded on the equally old sync worker (PID 24920) because `document_sync` had been imported at its start, before the hook existed: old module, no import | `_start_background_reading` imports `document_sync` at start |

Mechanism: a worker imports `app.models` when it starts (through
`app.services.jobs`) and its service modules lazily, per job
(`_run_processing` does `from app.services import document_processing`
on each job). After a code change the next job imports the *new*
`document_processing`, which imports the *new* `document_classification`,
which asks the *old* `app.models` object in memory for a class added
after the process started. Python reports exactly "cannot import name",
without "partially initialized module" (which a circular import would
say).

Manual check with the workers' interpreter, from the backend folder:

```
venv\Scripts\python -c "import sys, app.models; print(sys.executable); print(app.models.__file__); from app.models import DocumentClassification; print(DocumentClassification)"
C:\Users\moham\Desktop\dev\dev\ep-platform\backend\venv\Scripts\python.exe
C:\Users\moham\Desktop\dev\dev\ep-platform\backend\app\models.py
<class 'app.models.DocumentClassification'>
```

## A second finding: orphaned API server children

`uvicorn --reload` serves the API from a `multiprocessing` child whose
command line reads `python -c "from multiprocessing.spawn import
spawn_main; ..."` -- it does not name uvicorn. Restarting by stopping the
processes whose command line contains `uvicorn app.main` (what the
earlier manual restarts did) killed the supervisors and left their server
children alive: PID 24656 (started 11:45) and PID 6720 (started 12:16)
were still running at 18:00, and `Get-NetTCPConnection -LocalPort 8000`
still attributed the listening socket to the dead supervisor 20512. The
11:45 child answered `/health` with the payload of 11:45 and ran the BOQ
read of project 2 (job 17) with the reader of 11:45 (a full second
reading of every page), while the 17:34 child ran the current code and
got no connections. Same class of defect, on the API side.

## Hardening

- `app/workers/runtime.py` (new): `announce(process)` logs a fingerprint
  (process, PID, Python, version, root, the `models.py` loaded, cwd,
  revision read off `.git` without needing git, Classification V2 flag
  and rules version); `validate(process)` proves `app.models` came from
  this backend, that the models every job needs are on it
  (`DocumentClassification` too when the feature is on), and that the
  worker's service modules import -- and warns when the interpreter is
  not the backend's venv. `start(process)` = announce + validate, exit
  status 2 on a mismatch with the paths in the message. Also a CLI:
  `venv\Scripts\python -m app.workers.runtime document-worker`.
- `sync_worker.main`, `document_worker.main`, `ifc_worker.main` call
  `runtime.start(...)` before waiting for the schema. The API's lifespan
  calls `runtime.announce("api")` and `/health` returns the fingerprint
  and the two feature flags as that process sees them.
- `document_processing._classify` and `document_sync.sync` read the
  feature flag from the settings *before* importing the classification
  module, so a process with the feature off never imports it.
- `stop-backend.bat` + `stop-backend.ps1` (new): stop, in order, the
  children of this folder's backend processes, the backend processes
  themselves (`app.workers.*` / `uvicorn app.main` from this backend
  folder), and orphaned `multiprocessing` children of this venv's own
  interpreter whose parent is gone. Nothing else. `start-backend.bat`
  and `start.bat` call it first.
- README: the restart rule and the fingerprint.
- Tests: `tests/test_worker_runtime.py` (10): the imports (TEST 1-3), the
  mismatch failing at start with the paths in the message (TEST 5), the
  fingerprint (TEST 6), a classification exception leaving the job
  successful (TEST 4), a pending job surviving a worker's death and
  being run once by the next worker with no duplicate job or row
  (TEST 7-8), and `/health`. File Sync V2, Document Processing V2 and
  the classification suites pass unchanged (TEST 9-10).

## Verification on this PC (2026-09-27, 17:56-18:00 local)

1. `stop-backend.bat`, first run: stopped the four backend roots and their
   cmd parents, but its orphan step failed -- `$home` is a read-only
   PowerShell variable -- and three orphaned API children stayed alive
   (PIDs 24656 of 11:45, 6720 of 12:16, 24684 of 17:34), the listener on
   8000 still attributed to a dead PID. Fixed (`$venvHome`); second run
   stopped the three orphans by name. After it: no listener on 8000, no
   backend Python process of this folder.
2. `start-backend.bat` (which now calls the stop script first): the API
   and the three workers up at 17:57:20, `/health` answering after
   15 s with the fingerprint of the new API child (PID 25360, venv
   interpreter 3.12.10, root and models under this backend, revision
   13eb73ce85e5, both flags off).
3. `background_workers`: fresh rows `documents:...:25128`,
   `sync:...:8052`, `ifc:...:16760:0/1`, heartbeats current.
4. `python -m app.workers.runtime` for sync-worker, document-worker and
   ifc-worker under the venv: all three "runtime check passed", exit 0,
   same interpreter, root and models as `/health`.
5. Jobs 17 (`boq_read`) and 18 (`ai_verify`) of project 2 had finished
   (succeeded, 13:49 UTC) before the restart, so nothing was
   interrupted. Project 2 had no pending document (6 fresh): job 19
   (`process_documents`, enqueued through `document_processing.enqueue`)
   ran on the new worker 25128 and succeeded -- 0 planned, 0 failed, no
   ImportError, `document_processing` imported by the new process.
6. Tests: `tests/test_worker_runtime.py` 10 passed; File Sync V2,
   Document Processing V2, classification and document sync suites
   passed in one sequential session (see the session report for counts).

Remaining risk: the workers still have no hot reload by design. A code
change is live in a worker only after `start-backend.bat`; a worker
started before a change now fails at its next start rather than on a
job, and `/health` shows what the API child actually runs. The reload of
`uvicorn --reload` itself is unchanged; the stop script is what removes
its orphaned children.
