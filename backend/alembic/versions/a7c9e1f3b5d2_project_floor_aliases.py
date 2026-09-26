"""Project floor aliases: one physical floor, however the documents name it

The Building Floor Registry keeps three things apart: a floor's canonical
identity ("L2"), the names the IFC and the shop drawings give it ("L02",
"1ST MECHANICAL FLOOR PLAN"), and the project's own aliases between them.
A special floor name -- 1st Mechanical, Structural, Transfer -- is never
mapped to a level for every project: which level it is on this building
is this project's, read off a title that names both, or confirmed by an
engineer, and kept here so every IFC revision, sync and restart reuses it.

  * project_floor_aliases: alias -> canonical floor, with the decision
    ("merge" | "separate"), where it came from ("evidence" | "engineer" |
    "ai") and the evidence. An engineer's decision is never overwritten.
  * project_building_floors.secondary_name: the alias shown under the
    canonical floor ("L02" / "1st Mechanical Floor").
  * project_building_floors.merged_into: a floor merged into another
    keeps its row, inactive, pointing at the floor it is now.

Revision ID: a7c9e1f3b5d2
Revises: f0a1b2c3d4e5
Create Date: 2026-09-26 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b5d2"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_floor_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_key", sa.String(80), nullable=False),
        sa.Column("canonical_key", sa.String(80), nullable=False),
        sa.Column("alias_label", sa.String(160), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False, server_default="merge"),
        sa.Column("source", sa.String(16), nullable=False, server_default="evidence"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "alias_key", name="uq_project_floor_alias"),
    )
    op.create_index("ix_project_floor_aliases_project_id", "project_floor_aliases", ["project_id"])
    with op.batch_alter_table("project_building_floors") as batch:
        batch.add_column(sa.Column("secondary_name", sa.String(200), nullable=True))
        batch.add_column(sa.Column("merged_into", sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_building_floors") as batch:
        batch.drop_column("merged_into")
        batch.drop_column("secondary_name")
    op.drop_index("ix_project_floor_aliases_project_id", table_name="project_floor_aliases")
    op.drop_table("project_floor_aliases")
