"""floor schedule: kept in step with the workbook in the project folder

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-19 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("project_floor_schedule", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column("project_floor_schedule", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.add_column("project_floor_schedule",
                  sa.Column("edits", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("project_floor_schedule", "edits")
    op.drop_column("project_floor_schedule", "source_sha256")
    op.drop_column("project_floor_schedule", "source_path")
