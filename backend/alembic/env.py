from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.database import Base
import app.models  # noqa: F401  -- registers every table on Base.metadata

config = context.config

# The app runs the migrations itself at startup and hands over its own
# connection (see app.migrations). Its logging is configured by then, and
# fileConfig would replace it, so the .ini's logging only applies to the CLI.
connection = config.attributes.get("connection")
if connection is None and config.config_file_name is not None:
    fileConfig(config.config_file_name)

# One source for the database location: DATABASE_URL, the same setting the
# app reads. ConfigParser treats "%" as interpolation, hence the escape.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        # SQLite can't ALTER most things in place; batch mode rebuilds the
        # table instead. Harmless on Postgres, where it alters directly.
        render_as_batch=True,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as conn:
        _configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
