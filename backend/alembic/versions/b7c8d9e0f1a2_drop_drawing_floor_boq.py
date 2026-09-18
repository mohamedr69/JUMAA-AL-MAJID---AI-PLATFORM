"""drop the floor-wise BOQ that was read off CAD drawings

The platform reads its floor-wise BOQ from the engineer's own Excel
schedule (`project_floor_schedule`). Counting symbols on a DXF -- the
symbol library the legends taught, what each symbol became in a BOQ, and
the schedule that came out of it -- is removed.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-09-19 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_symbol_boq_map_symbol_key", table_name="symbol_boq_map")
    op.drop_table("symbol_boq_map")
    op.drop_table("project_floor_boq")
    op.drop_table("device_symbols")


def downgrade() -> None:
    """Downgrade schema.

    The tables come back empty: what they held was read off drawings that
    are no longer uploaded, and nothing else in the platform refers to it.
    """
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
    op.create_table(
        "project_floor_boq",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("location_rules", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
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
