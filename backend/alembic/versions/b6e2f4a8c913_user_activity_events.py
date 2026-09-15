"""user activity events

Revision ID: b6e2f4a8c913
Revises: a7d3e9b1c254
Create Date: 2026-09-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e2f4a8c913'
down_revision: Union[str, Sequence[str], None] = 'a7d3e9b1c254'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'activity_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('project_label', sa.String(length=300), nullable=True),
        sa.Column('entity_type', sa.String(length=32), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('activity_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_events_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_events_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_events_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_events_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('activity_events')
