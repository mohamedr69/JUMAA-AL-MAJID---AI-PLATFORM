"""compliance statements

Revision ID: c3a8d1e6f2b0
Revises: b42f592e597c
Create Date: 2026-09-13 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a8d1e6f2b0'
down_revision: Union[str, Sequence[str], None] = 'b42f592e597c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('compliance_statements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('system_code', sa.String(length=16), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('verification', sa.JSON(), nullable=False),
    sa.Column('rows', sa.JSON(), nullable=False),
    sa.Column('reference_files', sa.JSON(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('statement_name', sa.Text(), nullable=True),
    sa.Column('ai_calls', sa.Integer(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('compliance_statements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_compliance_statements_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('compliance_statements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_compliance_statements_project_id'))

    op.drop_table('compliance_statements')
