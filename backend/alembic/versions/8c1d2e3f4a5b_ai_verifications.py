"""ai verifications of BOQ and project details

Revision ID: 8c1d2e3f4a5b
Revises: 5b2e8c41d7a3
Create Date: 2026-09-15 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c1d2e3f4a5b'
down_revision: Union[str, Sequence[str], None] = '5b2e8c41d7a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_verifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('notes', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('version_after', sa.Integer(), nullable=True),
        sa.Column('undo', sa.JSON(), nullable=True),
        sa.Column('models', sa.JSON(), nullable=False),
        sa.Column('calls', sa.Integer(), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_ai_verifications_created_by_id_users'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_ai_verifications_project_id_projects'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ai_verifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_verifications_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_verifications_scope'), ['scope'], unique=False)
    with op.batch_alter_table('project_boq_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_check', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('project_boq_items', schema=None) as batch_op:
        batch_op.drop_column('ai_check')
    with op.batch_alter_table('ai_verifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_verifications_scope'))
        batch_op.drop_index(batch_op.f('ix_ai_verifications_project_id'))
    op.drop_table('ai_verifications')
