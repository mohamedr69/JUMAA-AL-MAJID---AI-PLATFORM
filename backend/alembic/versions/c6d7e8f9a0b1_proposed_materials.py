"""proposed materials: parts an engineer proposes beyond the BOQ

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-17 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'project_proposed_materials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('system_code', sa.String(length=16), nullable=True),
        sa.Column('manufacturer', sa.String(length=120), nullable=True),
        sa.Column('catalog_no', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=True),
        sa.Column('quantity', sa.String(length=32), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('project_proposed_materials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_proposed_materials_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('project_proposed_materials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_proposed_materials_project_id'))
    op.drop_table('project_proposed_materials')
