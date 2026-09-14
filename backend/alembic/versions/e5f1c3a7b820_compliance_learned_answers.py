"""compliance learned answers

Revision ID: e5f1c3a7b820
Revises: d4e7a2b91c05
Create Date: 2026-09-14 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f1c3a7b820'
down_revision: Union[str, Sequence[str], None] = 'd4e7a2b91c05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'compliance_learned_answers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('statement_id', sa.Integer(), nullable=False),
        sa.Column('clause_id', sa.String(length=16), nullable=False),
        sa.Column('clause_ref', sa.String(length=64), nullable=True),
        sa.Column('system_code', sa.String(length=16), nullable=False),
        sa.Column('clause_text', sa.Text(), nullable=False),
        sa.Column('clause_hash', sa.String(length=32), nullable=False),
        sa.Column('response', sa.String(length=48), nullable=False),
        sa.Column('remark', sa.Text(), nullable=False),
        sa.Column('technical_status', sa.String(length=32), nullable=True),
        sa.Column('manufacturers', sa.String(length=255), nullable=True),
        sa.Column('project_name', sa.String(length=255), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_by_name', sa.String(length=255), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['statement_id'], ['compliance_statements.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('statement_id', 'clause_id', name='uq_learned_statement_clause'),
    )
    with op.batch_alter_table('compliance_learned_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_compliance_learned_answers_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_compliance_learned_answers_statement_id'), ['statement_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_compliance_learned_answers_system_code'), ['system_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_compliance_learned_answers_clause_hash'), ['clause_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_compliance_learned_answers_active'), ['active'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('compliance_learned_answers')
