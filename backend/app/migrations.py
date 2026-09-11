from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def upgrade_to_head(engine: Engine) -> None:
    """Bring the database up to the latest migration.

    This replaces `Base.metadata.create_all`, which creates missing tables but
    never alters an existing one: a new column on an existing table silently
    did not appear, and the app then failed at query time.
    """
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
