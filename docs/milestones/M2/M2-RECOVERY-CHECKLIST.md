# M2 - Backend recovery checklist (separate from the M2 correction)

This is an operations procedure, written from the launcher scripts and the runtime code as they are in the checkout at the time of the M2 Review 01 correction (commit `ed7d221` plus the uncommitted correction). It was **not executed** as part of the M2 correction: the independent review observed no listener on port 8000 and a refused `GET /health`, and the correction task leaves any recovery to an explicit operational decision by the platform owner. Nothing below is validation evidence for M2.

## 1. What the launcher actually does

| Script | Effect |
|---|---|
| `stop-backend.bat` -> `stop-backend.ps1 -Backend <checkout>\backend` | Stops, in order: (1) children of this checkout's backend processes (the uvicorn `--reload` serving child that holds port 8000, the workers' reader processes); (2) the backend processes themselves (command lines running `app.workers.*` or `uvicorn app.main` from **this** backend folder); (3) orphaned multiprocessing children of this venv's interpreter whose parent is gone. Other Python programs and other checkouts are left alone. |
| `start-backend.bat` | Calls `stop-backend.bat` first, then opens **four** console windows: the API (`uvicorn app.main:app --reload --reload-dir app --port 8000`), the sync worker (`app.workers.sync_worker`), the document worker (`app.workers.document_worker`) and the IFC worker (`app.workers.ifc_worker`), the three workers at below-normal priority. There is no "API only" mode in this launcher. |
| `start.bat` | Starts everything, the web app included (not needed for recovery of the backend). |

## 2. What starts running on its own once the launcher runs

**API process (`app.main` lifespan, on every start and on every `--reload`)**

1. `runtime.announce("api")` logs the code fingerprint.
2. `adopt_local_database()`: only when `DATA_ROOT` is set and the target database does not exist yet, copies `backend/ep_platform.db` into the data folder. With `DATA_ROOT` unset (this PC), nothing happens.
3. `upgrade_to_head(engine)`: **runs every pending schema migration against the bound database**, unconditionally.
4. Seeds: default admin (`DEFAULT_ADMIN_PASSWORD` from `.env`), design rules, equipment currents, datasheet links (and removal of unconfirmed library links), suppliers, IFC device types; `import_library` / `save_library` read and **write back** the IFC symbol library file.
5. `fail_interrupted(db)`: jobs the API itself was running on its own threads when it last stopped are marked failed (worker jobs are left to the workers' own recovery).

**Workers (`sync_worker`, `document_worker`, `ifc_worker`)**

- On start and every ~30 s: `jobs.recover_stale` re-queues running jobs whose worker heartbeat is stale (up to `MAX_ATTEMPTS`, then failed), honouring stop requests.
- Then they take queued jobs: file sync, document processing (which **re-reads every document whose stored reading predates `PARSER_VERSION`**, now `parse-2026-09-28.3`; see section 5), IFC work.
- Workers have no hot reload: they run the code they started with until stopped.

## 3. Where the database is bound

- `backend/.env` -> `DATABASE_URL` (line 2 of the file on this PC; not committed). Every API and worker process reads it at import through `app.core.config`. The same file carries `SECRET_KEY`, `DEFAULT_ADMIN_PASSWORD`, `AI_API_KEY`; none of them is in git.
- The committed snapshot `backend/ep_platform.db` (commit `ed7d221`) is the SQLite file the launcher would open if `DATABASE_URL` points at it (WAL mode; the commit was taken after the backend had been stopped and the WAL checkpointed).
- `TESSERACT_CMD` is unset in `.env` and found at the Program Files install; `PROJECTS_ROOT` is unset and autodetected under the user's profile.

## 4. Checklist (to be run by the owner, deliberately, outside the M2 correction)

1. **Decide whether processing may run.** The document worker will re-read every document with an outdated reading as soon as it starts. If the intent is "API only, no re-reads", do not use `start-backend.bat` as it is; start uvicorn alone from `backend\` (`venv\Scripts\python -m uvicorn app.main:app --port 8000`) and leave the three worker commands unstarted. Note that the API start itself still runs migrations, seeds and `fail_interrupted`.
2. **Take a database backup first** (the app's backup function, or a copy of the SQLite file with its `-wal`/`-shm` files while nothing is running). `upgrade_to_head` is not reversible by the launcher.
3. **Confirm the binding**: read `DATABASE_URL` in `backend/.env`; confirm it is the intended file and that no other checkout points at the same file.
4. **Check for leftovers before starting**: `netstat -ano | findstr :8000` (expected: nothing) and the process list for `uvicorn`/`app.workers` from this checkout. `stop-backend.bat` handles this checkout's leftovers; anything else is manual.
5. **Start** (API only or the full launcher, per step 1).
6. **Verify**: `GET http://127.0.0.1:8000/health` answers; the API window shows the migration log, the seed lines and no traceback; `/health` reports the code fingerprint matching the workers if they run.
7. **Pending work**: in the jobs view (the `background_jobs` table) check what `fail_interrupted` marked failed and what `recover_stale` re-queued; decide what to re-run. Document processing state per project: rows in `project_documents` with `state` fresh/failed and `extracted.attempt` set are the incomplete readings the corrected writer now keeps beside the last complete one (`M2-REVIEW-RESPONSE.md`, R1).
8. **Control live processing**: the sync/document/IFC workers are stopped with `stop-backend.bat` (all four processes) or by closing their windows (a properly stopped worker puts its job back uncounted). There is no per-project pause switch in the launcher.

## 5. What is different after the M2 correction, for the operator

- `PARSER_VERSION` is `parse-2026-09-28.3`; all stored readings are outdated and will be re-read by the document worker when it next processes them. The promotion gate `EXTRACTION_PROMOTE_OBSERVATIONS` (settings; default **false**) keeps drawn-frame/highlight decisions, untracked-discipline covers and scanned-transmittal samples as observations/candidates on the reading rather than as records; only filled-box and stamp-annotation decisions, which the previously accepted reader already read, become records on the default path.
- A failed or partial re-read no longer replaces the last complete reading: the row keeps its records and gains `extracted.attempt` (outcome, error, coverage) and, for a changed file, `stale = true`; `state` is `failed` for failed/unavailable attempts and `fresh` for partial ones, and the worker's pending-row selection includes rows carrying an attempt.
- No migration was added by the correction; the new fields live inside the `extracted` JSON.

## 6. What this checklist is not

Not executed, not a validation of M2, not an authorisation to repair live data. Live BOQ repair stays M5; live re-reads of EP-30784 stay behind the engineer confirmation recorded in `M2-COMPATIBILITY-REPORT.md`.
