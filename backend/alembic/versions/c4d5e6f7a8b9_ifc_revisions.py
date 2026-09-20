"""IFC drawings carry their revision, and the revision they supersede

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-19 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: a drawing read before revisions is R0 of its own."""
    with op.batch_alter_table("project_ifc_drawings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision", sa.String(length=10), nullable=False, server_default="R0"))
        batch_op.add_column(sa.Column("supersedes_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_project_ifc_drawings_supersedes", "project_ifc_drawings",
                                    ["supersedes_id"], ["id"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("project_ifc_drawings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_project_ifc_drawings_supersedes", type_="foreignkey")
        batch_op.drop_column("supersedes_id")
        batch_op.drop_column("revision")
