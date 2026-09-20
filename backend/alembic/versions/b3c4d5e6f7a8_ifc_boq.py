"""BOQ as per IFC drawings: the symbol library and a project's drawings

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ifc_device_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "ifc_symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("inner_label", sa.String(length=200), nullable=False),
        sa.Column("raster_hex", sa.Text(), nullable=False),
        sa.Column("svg", sa.Text(), nullable=False),
        sa.Column("entity_counts", sa.JSON(), nullable=False),
        sa.Column("block_names", sa.JSON(), nullable=False),
        sa.Column("device_type_id", sa.Integer(), nullable=True),
        sa.Column("is_ignored", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source_drawing", sa.String(length=300), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_type_id"], ["ifc_device_types.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ifc_symbols_signature", "ifc_symbols", ["signature"], unique=True)
    op.create_table(
        "ifc_block_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("block_name", sa.String(length=300), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["ifc_symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ifc_block_aliases_block_name", "ifc_block_aliases", ["block_name"], unique=True)
    op.create_table(
        "project_ifc_drawings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("stored_path", sa.String(length=500), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("units", sa.String(length=20), nullable=False),
        sa.Column("dxf_version", sa.String(length=20), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_ifc_drawings_project_id", "project_ifc_drawings", ["project_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_project_ifc_drawings_project_id", table_name="project_ifc_drawings")
    op.drop_table("project_ifc_drawings")
    op.drop_index("ix_ifc_block_aliases_block_name", table_name="ifc_block_aliases")
    op.drop_table("ifc_block_aliases")
    op.drop_index("ix_ifc_symbols_signature", table_name="ifc_symbols")
    op.drop_table("ifc_symbols")
    op.drop_table("ifc_device_types")
