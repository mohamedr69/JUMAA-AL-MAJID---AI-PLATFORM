"""battery panel results: each panel's calculation kept under the hash of its inputs

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-17 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'battery_panel_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('panel_key', sa.String(length=200), nullable=False),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(length=16), server_default='fresh', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'panel_key', name='uq_battery_panel_result'),
    )
    with op.batch_alter_table('battery_panel_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_battery_panel_results_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('battery_panel_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_battery_panel_results_project_id'))
    op.drop_table('battery_panel_results')
