"""Match SQLite's role column length to the new estimation role."""
from alembic import op
import sqlalchemy as sa

revision = "c0e4a9b6d321"
down_revision = "b9d3f8a5c210"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch:
            batch.alter_column("role", existing_type=sa.String(15), type_=sa.String(19), existing_nullable=False)


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch:
            batch.alter_column("role", existing_type=sa.String(19), type_=sa.String(15), existing_nullable=False)
