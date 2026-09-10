# Engineering Project Platform

Phase 1: authentication, roles/permissions, and the basic app shell.

## Backend (FastAPI)

```
cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit SECRET_KEY (see comment in the file)
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Tests: `.\venv\Scripts\python -m pytest tests -v` (31 tests: login/logout, wrong-password handling, account lockout, session expiry, role-based module access, admin user management).

Default seeded admin: whatever `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set to in `.env` (change the password after first login).

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
