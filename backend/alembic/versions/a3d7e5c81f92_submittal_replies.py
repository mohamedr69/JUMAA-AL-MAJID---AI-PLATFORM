"""Our reply to the consultant's comments on a submittal revision

Keyed by the submittal's reference and revision rather than by a register
row: most of the submittals that need a reply were prepared outside the
platform and are read from the project folder, where they have no row of
their own.

Revision ID: a3d7e5c81f92
Revises: f2a8c4e01d76
Create Date: 2026-09-23 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3d7e5c81f92'
down_revision: Union[str, Sequence[str], None] = 'f2a8c4e01d76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'submittal_replies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=False),
        sa.Column('revision', sa.String(length=16), nullable=False),
        sa.Column('system_code', sa.String(length=16), nullable=True),
        sa.Column('consultant', sa.String(length=255), nullable=True),
        sa.Column('manufacturer', sa.String(length=120), nullable=True),
        sa.Column('rows', sa.JSON(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'reference', 'revision', name='uq_submittal_reply'),
    )
    with op.batch_alter_table('submittal_replies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submittal_replies_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('submittal_replies')
