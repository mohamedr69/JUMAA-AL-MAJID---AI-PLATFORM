"""ai proposal outcomes and injection flags

Revision ID: 5b2e8c41d7a3
Revises: 1378dd58ff7a
Create Date: 2026-09-15 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b2e8c41d7a3'
down_revision: Union[str, Sequence[str], None] = '1378dd58ff7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('ai_proposals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('injection_flags', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('outcome', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('outcome_value', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('outcome_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_ai_proposals_outcome'), ['outcome'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('ai_proposals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_proposals_outcome'))
        batch_op.drop_column('outcome_at')
        batch_op.drop_column('outcome_value')
        batch_op.drop_column('outcome')
        batch_op.drop_column('injection_flags')
