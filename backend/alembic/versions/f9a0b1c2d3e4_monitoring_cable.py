"""frc cables: the emergency light monitoring cable

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-09-18 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, Sequence[str], None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('project_frc_cables', schema=None) as batch_op:
        batch_op.add_column(sa.Column('monitoring_brand', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('monitoring_size', sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('project_frc_cables', schema=None) as batch_op:
        batch_op.drop_column('monitoring_size')
        batch_op.drop_column('monitoring_brand')
