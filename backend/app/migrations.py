import logging
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from app.core.timeutils import utc_now

from app.core.config import get_settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
BACKUPS_ROOT = Path(get_settings().backups_root or (Path(__file__).resolve().parent.parent / "backups"))

log = logging.getLogger(__name__)


class BackupFailed(RuntimeError):
    pass


def pending_migrations(engine: Engine) -> tuple[str | None, str | None]:
    """(the revision the database is at, the head) -- equal when nothing is pending."""
    config = Config(str(ALEMBIC_INI))
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return current, head


def backup_sqlite(engine: Engine, label: str, *, root: Path = BACKUPS_ROOT) -> Path | None:
    """A consistent copy of a file-backed SQLite database, verified before it
    is trusted. None for anything else (an in-memory test database, a server
    database whose backups are the server's business)."""
    database = engine.url.database
    if engine.url.get_backend_name() != "sqlite" or not database or database == ":memory:":
        return None
    source = Path(database)
    if not source.exists():
        return None
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{source.stem}-{label}-{utc_now():%Y%m%d-%H%M%S}.db"
    # The backup API copies a live database page-consistently, where a file
    # copy taken mid-write can capture a torn page.
    with sqlite3.connect(str(source)) as live, sqlite3.connect(str(target)) as copy:
        live.backup(copy)
    verify_backup(target, source)
    return target


def verify_backup(backup: Path, source: Path) -> None:
    """Refuse a backup that would not restore: it must pass SQLite's
    integrity check and hold the same tables with the same row counts."""
    with sqlite3.connect(str(backup)) as copy, sqlite3.connect(str(source)) as live:
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupFailed(f"backup {backup.name} failed the integrity check")
        tables = [row[0] for row in live.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if copy.execute(f"SELECT count(*) FROM {quoted}").fetchone() != live.execute(f"SELECT count(*) FROM {quoted}").fetchone():
                raise BackupFailed(f"backup {backup.name} does not match the database in table {table}")


def upgrade_to_head(engine: Engine) -> None:
    """Bring the database up to the latest migration.

    This replaces `Base.metadata.create_all`, which creates missing tables but
    never alters an existing one: a new column on an existing table silently
    did not appear, and the app then failed at query time.

    A database that has migrations pending is backed up first, and the
    backup verified; if it cannot be, nothing is migrated -- a schema change
    that fails half-way with no copy to go back to is the one failure that
    loses engineers' work.
    """
    current, head = pending_migrations(engine)
    if current is not None and current != head:
        backup = backup_sqlite(engine, f"before-{head}")
        if backup is not None:
            log.warning("Database backed up to %s before migrating %s -> %s", backup, current, head)
    config = Config(str(ALEMBIC_INI))
    sqlite = engine.url.get_backend_name() == "sqlite"
    with engine.connect() as connection:
        if sqlite:
            # Batch migrations rebuild a table by copying it; with foreign
            # keys enforced, dropping the old copy would fail on its children.
            # SQLite ignores this pragma inside a transaction, so it is set
            # before one begins, and set back after it ends.
            # (pysqlite opens no real transaction for a pragma; the commit only
            # closes the one SQLAlchemy began around it.)
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
