"""datasheet document reference numbers

Revision ID: e4c7a1d93b58
Revises: d1f5b0c7e432
Create Date: 2026-09-22 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4c7a1d93b58'
down_revision: Union[str, Sequence[str], None] = 'd1f5b0c7e432'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'datasheet_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('reference_no', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=16), server_default='engineer', nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('library', 'path', name='uq_datasheet_document'),
    )
    with op.batch_alter_table('datasheet_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_datasheet_documents_library'), ['library'], unique=False)
        batch_op.create_index(batch_op.f('ix_datasheet_documents_reference_no'), ['reference_no'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('datasheet_documents')
