"""Project actions and the project change log

  * project_actions: what a project is waiting on, held once for every page
    (app.services.project_state), opened and resolved from the project's
    own records.
  * project_changes: PROJECT_DATA_CHANGED, one row per change to a
    project's records, written in the transaction that made it; pages ask
    for the ones after the last they saw and reload what they touch.

Revision ID: d8b2c4e6f7a1
Revises: c5f9a2b3d4e6
Create Date: 2026-09-25 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b2c4e6f7a1"
down_revision: Union[str, None] = "c5f9a2b3d4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("system_code", sa.String(32), nullable=True),
        sa.Column("entity_type", sa.String(40), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("link", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.UniqueConstraint("project_id", "key", name="uq_project_actions_key"),
    )
    op.create_index("ix_project_actions_project_id", "project_actions", ["project_id"])
    op.create_table(
        "project_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("system_code", sa.String(32), nullable=True),
        sa.Column("change_type", sa.String(40), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_project_changes_project_id", "project_changes", ["project_id"])
    op.create_index("ix_project_changes_at", "project_changes", ["at"])


def downgrade() -> None:
    op.drop_index("ix_project_changes_at", table_name="project_changes")
    op.drop_index("ix_project_changes_project_id", table_name="project_changes")
    op.drop_table("project_changes")
    op.drop_index("ix_project_actions_project_id", table_name="project_actions")
    op.drop_table("project_actions")
