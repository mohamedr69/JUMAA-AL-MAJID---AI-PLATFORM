from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.database import SessionLocal, engine
from app.migrations import upgrade_to_head
from app.routers import auth, modules, projects, users
from app.seed import seed_default_admin

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_to_head(engine)
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The frontend runs on another origin, where the browser hides response
    # headers it isn't told it may read; this one carries an export's filename.
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(modules.router)
app.include_router(projects.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
