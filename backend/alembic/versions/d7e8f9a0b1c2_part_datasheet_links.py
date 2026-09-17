"""part datasheet links: which datasheet documents a part the file names do not carry

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-09-17 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'part_datasheet_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('manufacturer', sa.String(length=64), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('part_no', sa.String(length=120), nullable=False),
        sa.Column('library', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=16), server_default='engineer', nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('manufacturer', 'key', name='uq_part_datasheet_link'),
    )
    with op.batch_alter_table('part_datasheet_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_part_datasheet_links_key'), ['key'], unique=False)
        batch_op.create_index(batch_op.f('ix_part_datasheet_links_manufacturer'), ['manufacturer'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('part_datasheet_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_part_datasheet_links_manufacturer'))
        batch_op.drop_index(batch_op.f('ix_part_datasheet_links_key'))
    op.drop_table('part_datasheet_links')
