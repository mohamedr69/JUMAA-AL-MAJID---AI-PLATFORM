"""the amplifier keeps no speaker count of its own

How many speakers a floor has is the floor-wise BOQ's answer and nowhere
else's. A second copy here was a way for the two tabs to disagree, so it
is removed and a count set on the amplifier page now edits the BOQ.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-09-19 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("project_amplifier_design", "counts")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("project_amplifier_design",
                  sa.Column("counts", sa.JSON(), nullable=False, server_default="{}"))
