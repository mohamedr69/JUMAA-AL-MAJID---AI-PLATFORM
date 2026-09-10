# Engineering Project Platform

Phase 1: authentication, roles/permissions, and the basic app shell.
Phase 2: Create Project / Open Project, EP-number resolution against the
project archive, and DRF field extraction.

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
- One live-archive test needs `EP_PLATFORM_LIVE_ARCHIVE_ROOT` set to the real project archive path -- skips otherwise. Never runs by default; the rest of the suite uses synthetic fixtures, not the live OneDrive tree.

Default seeded admin: whatever `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set to in `.env` (change the password after first login).

### Project archive + OCR (Phase 2)

- `PROJECTS_ROOT` in `.env`: local path to the synced project archive (dev/test shim -- production should use Microsoft Graph search against SharePoint instead; see `app/services/ep_resolver.py`).
- DRF field extraction needs [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed separately (it's a system binary, not a pip package). Set `TESSERACT_CMD` in `.env` to its `tesseract.exe` path if it's not already on `PATH`.

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
