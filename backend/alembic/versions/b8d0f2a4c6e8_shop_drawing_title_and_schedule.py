"""Shop drawing: its normalized title, and the drawing schedule as evidence

The Logs register is the shop drawing records, one row per logical drawing.
A drawing carries its own title as the title block reads it ("Basement-4
Floor Plan Fire Alarm Layout"), and the schedule entry that planned it
("FA 102" in the project's shop drawings log) as evidence of the record --
never a row of its own.

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1f3b5d2
Create Date: 2026-09-26 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d0f2a4c6e8"
down_revision: Union[str, None] = "a7c9e1f3b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_shop_drawings") as batch:
        batch.add_column(sa.Column("title", sa.String(200), nullable=True))
        batch.add_column(sa.Column("schedule_reference", sa.String(120), nullable=True))
        batch.add_column(sa.Column("schedule_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_shop_drawings") as batch:
        batch.drop_column("schedule_path")
        batch.drop_column("schedule_reference")
        batch.drop_column("title")
