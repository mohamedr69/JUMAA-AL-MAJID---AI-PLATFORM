"""EP archive roots and searchable project-folder directory.

Revision ID: a8c2e7f4b109
Revises: c4d5e6f7a8b9
"""
from alembic import op
import sqlalchemy as sa

revision = "a8c2e7f4b109"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ep_archive_roots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("root_key", sa.String(64), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("scan_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scan_token", sa.String(32), nullable=True),
        sa.Column("scan_started_at", sa.DateTime(), nullable=True),
        sa.Column("scan_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_successful_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("root_key"),
    )
    op.create_table(
        "ep_archive_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("ep_number", sa.String(32), nullable=False),
        sa.Column("folder_name", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("path_key", sa.String(64), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_scan_token", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["archive_id"], ["ep_archive_roots.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("archive_id", "path_key", name="uq_ep_archive_folder_path"),
    )
    op.create_index("ix_ep_archive_folders_lookup", "ep_archive_folders",
                    ["archive_id", "ep_number", "is_available"])


def downgrade() -> None:
    op.drop_index("ix_ep_archive_folders_lookup", table_name="ep_archive_folders")
    op.drop_table("ep_archive_folders")
    op.drop_table("ep_archive_roots")
