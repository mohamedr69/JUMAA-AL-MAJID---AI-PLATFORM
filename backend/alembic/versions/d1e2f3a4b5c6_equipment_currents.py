"""equipment currents: what each part of a fire alarm system draws, settled once for every project

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-09-16 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'equipment_currents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('manufacturer', sa.String(length=64), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('part_no', sa.String(length=64), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('no_load', sa.Boolean(), nullable=False),
        sa.Column('standby_ma', sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column('alarm_ma', sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column('included_in', sa.String(length=64), nullable=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('confirmed_by', sa.String(length=120), nullable=True),
        sa.Column('aliases', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_equipment_currents_created_by_id_users'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('manufacturer', 'key', name='uq_equipment_current_key'),
    )
    with op.batch_alter_table('equipment_currents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_equipment_currents_key'), ['key'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('equipment_currents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_equipment_currents_key'))
    op.drop_table('equipment_currents')
