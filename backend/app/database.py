from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")
is_sqlite_memory = is_sqlite and ":memory:" in settings.database_url

# How long a connection waits for another's write lock before giving up
# with "database is locked". The API and the worker each write to the one
# file, and a write waits at most for the other's current short transaction.
SQLITE_BUSY_TIMEOUT_S = 15

engine_kwargs: dict = {}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_S}
if is_sqlite_memory:
    engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

if is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_journal(dbapi_connection, *_args) -> None:
        """How the file is written, set once per connection.

        A local database runs in write-ahead-log mode: the API and the
        background worker both use it, and in WAL a page request reads while
        the worker writes, where the rollback journal made every reader wait
        for the writer. `synchronous=NORMAL` is WAL's usual pairing: the file
        cannot be corrupted by a crash, and at worst a power cut loses the
        last transactions. The busy timeout makes a connection wait out a
        short write of the other process rather than fail at once.

        A database in a synced folder (DATA_ROOT on OneDrive) stays in the
        rollback-journal mode below: a write-ahead log beside it is a second
        file a sync client copies half of."""
        if is_sqlite_memory:
            return
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_S * 1000}")
        if settings.data_root:
            cursor.execute("PRAGMA journal_mode=DELETE")
        else:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "connect")
    @event.listens_for(engine, "checkout")
    def _sqlite_pragmas(dbapi_connection, *_args) -> None:
        """SQLite leaves foreign keys unenforced unless asked, per connection:
        a row could point at a project that no longer exists. Asked on every
        checkout, not only on connect: migrations switch them off on the
        connection they use (batch mode rebuilds tables by copy), and that
        connection goes back to the pool."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def adopt_local_database() -> str | None:
    """The first start with DATA_ROOT set on a machine that already has a
    database under backend/: that database is copied into the data folder,
    so nothing the user made here is left behind. Returns what was done."""
    if not settings.data_root or not is_sqlite or is_sqlite_memory:
        return None
    from pathlib import Path
    import shutil

    target = Path(settings.database_url.removeprefix("sqlite:///"))
    local = Path(__file__).resolve().parents[1] / "ep_platform.db"
    if target.exists() or not local.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local, target)
    return f"Copied the local database ({local}) into the data folder ({target})"


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
