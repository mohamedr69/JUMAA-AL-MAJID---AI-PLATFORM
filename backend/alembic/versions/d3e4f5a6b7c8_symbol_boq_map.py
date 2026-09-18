"""symbol boq map: what a symbol on a drawing becomes in a BOQ

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-18 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "symbol_boq_map",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_key", sa.String(length=64), nullable=False),
        sa.Column("device", sa.String(length=120), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("manufacturer", sa.String(length=128), nullable=True),
        sa.Column("system_code", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="engineer", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("learned_from", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_key", "manufacturer", "system_code", name="uq_symbol_boq_map_scope"),
    )
    op.create_index("ix_symbol_boq_map_symbol_key", "symbol_boq_map", ["symbol_key"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_symbol_boq_map_symbol_key", table_name="symbol_boq_map")
    op.drop_table("symbol_boq_map")
