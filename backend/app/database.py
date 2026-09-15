from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")
is_sqlite_memory = is_sqlite and ":memory:" in settings.database_url

engine_kwargs: dict = {}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
if is_sqlite_memory:
    engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

if is_sqlite:
    from sqlalchemy import event

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


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
