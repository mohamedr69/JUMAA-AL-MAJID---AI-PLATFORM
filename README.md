# Engineering Project Platform

Phase 1: authentication, roles/permissions, and the basic app shell.
Phase 2: Create Project / Open Project, EP-number resolution against the
project archive, and DRF extraction (project fields, Scope of Work, and the
Systems table with per-system brand and Method Statement / Drawing ticks).
Phase 3: the project workspace -- Home overview, editable Project Info, and
the BOQ (read once from the Design Sheets, then edited, filtered, totalled,
exported to Excel and issued as frozen revisions).

## Backend (FastAPI)

```
cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit SECRET_KEY (see comment in the file)
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Tests: `.\venv\Scripts\python -m pytest tests -v` (auth/RBAC, the EP-folder
resolver, the Project API, DRF and Design Sheet extraction, BOQ export and
revisions, and the migrations). Two kinds of tests are
gated and skip by default:
- DRF-extraction tests need Tesseract OCR (see below) -- skip automatically if it's not installed/configured.
- The live-archive tests (one per real DRF: EP-24601, EP-29495, EP-30784) need `EP_PLATFORM_LIVE_ARCHIVE_ROOT` set to the real project archive path -- they skip otherwise. Never run by default; the rest of the suite uses synthetic fixtures, not the live OneDrive tree. They pin exact extracted values, so they are what catches a regression in the grid-line geometry: those failures are silent otherwise, showing up as a value quietly losing its first word rather than as an error.

Default seeded admin: whatever `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set to in `.env` (change the password after first login).

### Database migrations (Alembic)

The schema is managed by Alembic migrations in `backend/alembic/versions/`, and the app runs `alembic upgrade head` itself at startup (`app/migrations.py`), so pulling a change and restarting is enough. `alembic` reads the same `DATABASE_URL` as the app.

To change the schema: edit `app/models.py`, run `.\venv\Scripts\alembic revision --autogenerate -m "what changed"`, read the generated file (autogenerate misses some things and SQLite needs `batch_alter_table`, which `env.py` turns on), then restart. The test suite builds its database through the migrations, and `tests/test_migrations.py` fails if the models and the migrations disagree -- a model change without a migration is caught there, not in the running app.

This replaced `Base.metadata.create_all`, which created missing tables but never altered existing ones: a new column silently did not appear and the app failed at query time (it happened twice). A database created that way before the switch should be marked as already at the baseline with `.\venv\Scripts\alembic stamp 61c335c3ba25` and then upgraded; the dev database has been.

### Project archive + OCR (Phase 2)

- `PROJECTS_ROOT` in `.env`: local path to the synced project archive (dev/test shim -- production should use Microsoft Graph search against SharePoint instead; see `app/services/ep_resolver.py`).
- DRF field extraction needs [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed separately (it's a system binary, not a pip package). Set `TESSERACT_CMD` in `.env` to its `tesseract.exe` path if it's not already on `PATH`.
- Design Sheet -> BOQ extraction uses the same Tesseract install as the DRF. No API key or network access is involved.

### The two extractors

Both are deterministic OCR, but they read different shapes and so work differently.

`drf_extractor.py` reads a **fixed form**: rows and columns are both ruled, so it finds the grid lines and OCRs each cell.

`design_sheet_extractor.py` reads a **quotation**. It detects the vertical rules and OCRs each column strip separately -- handing Tesseract the whole table lets it run its own reading order across the columns and interleave the quantity into the description, and handing it one cell at a time is worse again, because it reads a lone digit far better with the rest of the column around it. Rows come from the sheet's own row rules where it has them (which is what keeps a description that wraps onto a second line attached to its quantity), and from text spacing where it doesn't.

There is no single Design Sheet template, so the *number* of vertical rules picks the layout: 6 for the scanned EST4 quotation (`Qty | Catalog No. | Description | Unit Price | Total Price`), 4 for the spreadsheet export (`Catalog | Description | Qty`, quantity on the right). Because a column count alone is a weak signal, an extraction is rejected unless most of its lines carry a numeric quantity -- otherwise a DRF's Systems block, which happens to have six rules, would come back as a BOQ.

Only rows with a quantity are kept -- a BOQ line is something quoted in some amount, and the filter also removes the page furniture (stray header text, totals rows) that survives the earlier checks.

Known limitation: catalog numbers mix `0`/`O` and `1`/`l`/`I`, which are near-identical at the printed size, and the extractor returns what it sees. Quantities are covered exactly by tests. Where a quantity genuinely cannot be read the whole row is dropped, so the extracted count can be short of the printed one -- EP-30784's EML sheet yields 11 of its 12 items for this reason. Prices are never extracted; the Unit/Total Price columns are blank on every sheet in the archive.

Extraction runs **once per project**, by itself, the first time someone with edit rights opens the BOQ tab (`POST /projects/{id}/boq/ensure`); the lines are stored and the BOQ is then an ordinary editable table, split into one tab per system. `projects.boq_extracted_at` records that the read has been attempted and is what makes the call idempotent -- re-reading would either duplicate lines or discard the engineer's corrections, and OCR over a multi-page sheet is slow enough to be felt on every page view. The stamp is set even when a sheet cannot be read, so an unreadable layout is not retried forever. Which sheets failed is stored with it (`projects.boq_extraction_warnings`) and shown on the BOQ page on every visit, not only on the response that did the read -- that response is the one React StrictMode throws away in dev, and since the read cannot be repeated, a lost warning meant a sheet's lines silently missing.

Extraction pre-fills each line's **manufacturer** with the brand the DRF gives that line's system (`FAS` -> the Fire Alarm row, `EML` -> Emergency Light Monitoring, ...). Where a code could be more than one DRF row and they name different brands -- `ELS` can be either emergency-lighting row -- it is left blank rather than guessed. Unit and remarks start empty; the sheets carry neither.

The stamp is *claimed* rather than checked: the sheets are OCR'd first, then a single `UPDATE ... WHERE boq_extracted_at IS NULL` sets it, and only the request whose update hit a row writes the lines. Checking it up front and setting it after the OCR let two overlapping opens both extract and store every line twice -- which React StrictMode does on every first open in dev, since it fires each effect twice. A process-local lock additionally makes the second open wait for the first instead of OCR-ing the same sheets again.

This is a deliberate exception to the "extracted values are suggestions, never auto-submitted" rule that governs the DRF fields -- a BOQ is too long to hand-confirm line by line, so it is written and left editable instead. One consequence worth knowing: because it runs only once, there is no way to re-read a sheet later from the UI.

### Working with the BOQ

- **Columns** follow the plan: system (the tab), group, manufacturer, model / part no., description, quantity, unit, remarks -- plus unit and total price, which the Design Sheets have as columns. Prices are blank on every sheet in the archive, so they are kept for the engineer to fill rather than extracted.
- **Filter and totals.** The filter matches part no., description, manufacturer, group, unit and remarks within the current tab. The totals under the table sum the numeric quantities of the lines shown; lines quoted as a word ("Lot") are counted separately rather than guessed at.
- **Possible duplicates** are flagged, never removed: same system, same group heading, same part number and same description (ignoring case, spacing, punctuation). The rule is narrow on purpose. The same part legitimately recurs across a BOQ -- every panel has its own CPU and batteries -- and a part-number-only match flagged 93 of the 279 lines on the three real projects, nearly all correctly listed. This rule flags 3 pairs there, each an item listed twice under one heading. The rule lives in `frontend/src/lib/boq.ts`, since it has to follow unsaved edits.
- **Export** (`GET /projects/{id}/boq/export.xlsx`) writes the *saved* BOQ: a Summary sheet with project details and per-system totals, then one sheet per system in tab order. Numeric quantities are real numbers and the per-sheet totals are live `SUM` formulas, so they follow edits made in Excel. The button is disabled while there are unsaved edits, which would otherwise be missing from the file without any sign.
- **Revisions.** Issuing a revision (BOQ -> Revisions) freezes the saved BOQ as Rev 00, Rev 01, ... with a note, who issued it and when. It is stored as a snapshot, not as references to BOQ rows, because every save replaces those rows wholesale. There is no endpoint that edits or deletes a revision. Issuing is refused when nothing has changed since the last one, which also stops a double-click issuing the same BOQ twice. Any two revisions, or a revision and the current BOQ, can be compared (`GET /projects/{id}/boq/compare?from_rev=0&to_rev=1`): lines are paired on system + group + part no. + description (ignoring case, spacing and punctuation), so replacing a part number reads as one line removed and another added, while a quantity or price edit -- or tidying a line's text, such as removing an OCR artifact -- reads as a change to the same line. Reordering lines alone is not a change. The "nothing has changed" check on issuing uses this same comparison, so the page and the server agree on what can be issued. Each revision exports to Excel on its own.

## Frontend (React + Vite + Tailwind)

```
cd frontend
npm install
npm run dev
```

Runs at http://localhost:5173, expects the backend at http://localhost:8000 (override with `VITE_API_BASE_URL`).

## Notes

- Roles: Admin, Design Manager, Design Engineer, Draftsman, Viewer.
- Auth uses a JWT in an httpOnly cookie (not localStorage) to reduce XSS token theft. `samesite=lax` assumes frontend and API share a site in production — revisit if they end up on different domains.
- SQLite for dev (`DATABASE_URL` in `.env`); swap to Postgres for production via the same `DATABASE_URL`.
- Brand assets (logo, hero photo, exact colors) in `frontend/src/branding.ts`, `BrandMark.tsx`, `CitySkylineBackdrop.tsx` are placeholders reconstructed from the mockups — swap for the approved files when available.
- DRF extraction (`app/services/drf_extractor.py`) is OCR + grid-line detection, not an AI model — by design, per the plan's "AI handles document understanding, Python handles deterministic logic" split, this counts as deterministic since it's pattern/geometry matching against a known fixed form template, not a language model. It targets the current DRF template (Document Reference SSD-P-06 B/IQF.17); a template redesign would need it revisited. Extracted values are always shown as editable, pre-filled suggestions with a confidence indicator — never auto-submitted.
