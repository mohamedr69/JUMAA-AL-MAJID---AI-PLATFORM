import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token
from app.database import SessionLocal, engine
from app.migrations import upgrade_to_head
from app.routers import (
    auth,
    backups,
    boq_review,
    compliance,
    design,
    design_rules,
    extraction,
    estimation,
    divisions,
    jobs,
    knowledge,
    modules,
    projects,
    register,
    readiness,
    submittal,
    users,
    verification,
)
from app.seed import seed_default_admin, seed_design_rules

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import adopt_local_database

    adopted = adopt_local_database()
    if adopted:
        print(adopted)
    upgrade_to_head(engine)
    db = SessionLocal()
    try:
        seed_default_admin(db)
        seed_design_rules(db)
        from app.services import equipment_currents

        equipment_currents.seed(db)
        from app.services import datasheet_links

        datasheet_links.seed(db)
        datasheet_links.remove_unconfirmed_library_links(db)
        from app.services import suppliers

        suppliers.seed(db)
        # The IFC symbol library: the device types once, then anything the
        # library file on the company shelf has that this database lacks,
        # and the file written back so it always matches the database.
        from app.ifc.library_file import import_library, save_library
        from app.ifc.seed import seed_device_types

        seed_device_types(db)
        import_library(db)
        save_library(db)
        # Work a previous run of the server left unfinished never finishes.
        from app.services.jobs import fail_interrupted

        fail_interrupted(db)
    finally:
        db.close()
    # A new machine's first start: the knowledge base in `data base` is
    # imported in the background, so Auto-fill works without a setting.
    from app.knowledge import importer

    importer.import_on_start()
    # The heavy background reading -- the document syncs, the catch-up syncs
    # after a change to the reading rules, the datasheet libraries' index and
    # the archive index's scans -- is the worker's (app.workers.sync_worker),
    # a process of its own, so this one is free for pages the moment it is up.
    # The datasheet index is loaded from the worker's cache file on the first
    # lookup here.
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The frontend runs on another origin, where the browser hides response
    # headers it isn't told it may read: the export's filename, and what the
    # package builder reports about the file it built -- without these two
    # the page said "? pages assembled".
    # X-Resource-Version / ETag: the version a save names in If-Match
    # (app.services.concurrency).
    expose_headers=["Content-Disposition", "X-Package-Pages", "X-Package-Warnings", "X-Resource-Version", "ETag",
                    "X-Request-ID"],
)


_request_log = logging.getLogger("app.request")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Every request gets an id -- the caller's X-Request-ID when it is a
    sensible one, else a new one -- returned on the response and written on
    one structured log line: method, the route's template (never the filled
    path, which can carry document paths), status, duration and user id. No
    query strings, bodies, cookies or file paths are logged."""
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex[:16]
    request.state.request_id = rid
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        route = request.scope.get("route")
        user_id = None
        token = request.cookies.get(settings.cookie_name)
        if token:
            try:
                user_id = decode_access_token(token).get("sub")
            except Exception:  # noqa: BLE001 -- an invalid token is simply no user
                user_id = None
        _request_log.info(json.dumps({
            "request_id": rid, "method": request.method, "route": getattr(route, "path", "unmatched"),
            "status": status_code, "ms": round((time.perf_counter() - started) * 1000), "user": user_id,
        }))
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def slide_session(request: Request, call_next):
    """Renew the session cookie while it is in use.

    The cookie carried a fixed 30-minute expiry from login, so an engineer
    half-way through a BOQ or waiting on a long search was signed out
    mid-work -- the ten-project review hit it twice. A request made in the
    second half of the cookie's life now re-issues it for a full term, so a
    session ends only after 30 minutes of nothing. Login sets its own
    cookie and logout clears it; neither is touched.
    """
    response = await call_next(request)
    if request.url.path.startswith("/auth/"):
        return response
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return response
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001 -- an expired or bad token is the route's 401 to give, not ours
        return response
    lifetime = settings.access_token_expire_minutes * 60
    expires = payload.get("exp")
    if expires is None or expires - time.time() > lifetime / 2:
        return response
    response.set_cookie(
        key=settings.cookie_name,
        value=create_access_token(subject=str(payload["sub"]), role=str(payload.get("role", ""))),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=lifetime,
        path="/",
    )
    return response

app.include_router(auth.router)
app.include_router(estimation.router)
app.include_router(divisions.router)
app.include_router(users.router)
app.include_router(modules.router)
app.include_router(projects.router)
app.include_router(register.router)
app.include_router(boq_review.router)
app.include_router(readiness.router)
app.include_router(jobs.router)
app.include_router(backups.router)
app.include_router(design.router)
app.include_router(design_rules.router)
app.include_router(submittal.router)
app.include_router(compliance.router)
app.include_router(knowledge.router)
app.include_router(extraction.router)
app.include_router(extraction.admin_router)
app.include_router(verification.router)
from app.routers import documents as documents_router  # noqa: E402

app.include_router(documents_router.router)

from app.routers import materials as materials_router  # noqa: E402

app.include_router(materials_router.router)


from app.routers import data_location as data_location_router  # noqa: E402

app.include_router(data_location_router.router)

from app.routers import floor_schedule as floor_schedule_router  # noqa: E402

app.include_router(floor_schedule_router.router)

from app.routers import amplifier as amplifier_router  # noqa: E402

app.include_router(amplifier_router.router)

# The BOQ page's "As per IFC Drawings" tab: fire alarm devices counted off an
# IFC drawing, every unknown symbol verified before any quantity is given.
from app.routers import ifc_boq as ifc_boq_router  # noqa: E402

app.include_router(ifc_boq_router.router)
from app.routers import drawings as drawings_router  # noqa: E402
app.include_router(drawings_router.router)

# Search the archive's EP folders from the index rather than walking it.
from app.routers import archive as archive_router  # noqa: E402

app.include_router(archive_router.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
