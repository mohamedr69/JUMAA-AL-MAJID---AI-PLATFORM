from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.database import Base, engine


def test_migrations_build_the_schema_the_models_describe(client):
    """A model change without a migration fails here rather than in the
    running app, where it shows up as a query against a column that isn't
    there. The `client` fixture has just built the database by running the
    migrations; any difference from the models is a missing migration --
    `alembic revision --autogenerate` writes it."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
