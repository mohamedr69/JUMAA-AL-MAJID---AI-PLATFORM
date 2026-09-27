"""Engineer corrections of BOQ rows, kept as evaluation data

Every decision an engineer makes on a row the Design Sheet read produced --
a review row accepted or rejected, a machine-read line's sheet values
edited -- is kept with what the machine had read of it (the first reading,
the verification's readings, the geometry's evidence, the reason the row
was theirs) and what they made it. Nothing is trained on it now; it is the
set a later evaluation or fine-tuning measures against
(app.services.boq_corrections).

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-27 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "boq_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("document_sha256", sa.String(length=64), nullable=True, index=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("row_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("primary_part_number", sa.String(length=128), nullable=True),
        sa.Column("primary_quantity", sa.String(length=32), nullable=True),
        sa.Column("primary_description", sa.Text(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("final_part_number", sa.String(length=128), nullable=True),
        sa.Column("final_quantity", sa.String(length=32), nullable=True),
        sa.Column("final_description", sa.Text(), nullable=True),
        sa.Column("final_group", sa.String(length=255), nullable=True),
        sa.Column("processor_version", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("boq_corrections")
