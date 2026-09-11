# Engineering Project Platform

Phase 1: authentication, roles/permissions, and the basic app shell.
Phase 2: Create Project / Open Project, EP-number resolution against the
project archive, and DRF extraction (project fields, Scope of Work, and the
Systems table with per-system brand and Method Statement / Drawing ticks).

## Backend (FastAPI)

```
cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit SECRET_KEY (see comment in the file)
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Tests: `.\venv\Scripts\python -m pytest tests -v` (auth/RBAC, the EP-folder
resolver, the Project API, and DRF field extraction). Two kinds of tests are
gated and skip by default:
- DRF-extraction tests need Tesseract OCR (see below) -- skip automatically if it's not installed/configured.
- The live-archive tests (one per real DRF: EP-24601, EP-29495, EP-30784) need `EP_PLATFORM_LIVE_ARCHIVE_ROOT` set to the real project archive path -- they skip otherwise. Never run by default; the rest of the suite uses synthetic fixtures, not the live OneDrive tree. They pin exact extracted values, so they are what catches a regression in the grid-line geometry: those failures are silent otherwise, showing up as a value quietly losing its first word rather than as an error.

Default seeded admin: whatever `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set to in `.env` (change the password after first login).

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

Extraction runs **once per project**, by itself, the first time someone with edit rights opens the BOQ tab (`POST /projects/{id}/boq/ensure`); the lines are stored and the BOQ is then an ordinary editable table, split into one tab per system. `projects.boq_extracted_at` records that the read has been attempted and is what makes the call idempotent -- re-reading would either duplicate lines or discard the engineer's corrections, and OCR over a multi-page sheet is slow enough to be felt on every page view. The stamp is set even when a sheet cannot be read, so an unreadable layout is reported once rather than retried forever; the response's warnings name the sheet.

The stamp is *claimed* rather than checked: the sheets are OCR'd first, then a single `UPDATE ... WHERE boq_extracted_at IS NULL` sets it, and only the request whose update hit a row writes the lines. Checking it up front and setting it after the OCR let two overlapping opens both extract and store every line twice -- which React StrictMode does on every first open in dev, since it fires each effect twice. A process-local lock additionally makes the second open wait for the first instead of OCR-ing the same sheets again.

This is a deliberate exception to the "extracted values are suggestions, never auto-submitted" rule that governs the DRF fields -- a BOQ is too long to hand-confirm line by line, so it is written and left editable instead. One consequence worth knowing: because it runs only once, there is no way to re-read a sheet later from the UI.

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
- **Schema changes need a migration tool before this goes anywhere real.** Tables are created by `Base.metadata.create_all` at startup, which creates *missing tables* but never alters existing ones. A new table therefore appears on restart, while a new column on an existing table silently does not -- the app then fails at query time against a column the model expects. This has already bitten twice (`project_systems`, then `project_boq_items.group_heading`, patched by hand). Adopt Alembic before the next model change.
- Brand assets (logo, hero photo, exact colors) in `frontend/src/branding.ts`, `BrandMark.tsx`, `CitySkylineBackdrop.tsx` are placeholders reconstructed from the mockups — swap for the approved files when available.
- DRF extraction (`app/services/drf_extractor.py`) is OCR + grid-line detection, not an AI model — by design, per the plan's "AI handles document understanding, Python handles deterministic logic" split, this counts as deterministic since it's pattern/geometry matching against a known fixed form template, not a language model. It targets the current DRF template (Document Reference SSD-P-06 B/IQF.17); a template redesign would need it revisited. Extracted values are always shown as editable, pre-filled suggestions with a confidence indicator — never auto-submitted.
