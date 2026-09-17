"""device symbols: the geometry the platform has been taught to recognise

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-18 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device", sa.String(length=120), nullable=False),
        sa.Column("system_code", sa.String(length=16), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("part_numbers", sa.JSON(), nullable=True),
        sa.Column("fingerprints", sa.JSON(), nullable=False),
        sa.Column("shapes", sa.JSON(), nullable=False),
        sa.Column("block_names", sa.JSON(), nullable=False),
        sa.Column("layers", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="legend", nullable=False),
        sa.Column("learned_from", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("device_symbols")
