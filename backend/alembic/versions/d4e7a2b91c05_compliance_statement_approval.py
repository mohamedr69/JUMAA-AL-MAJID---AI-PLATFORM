"""compliance statement approval

Revision ID: d4e7a2b91c05
Revises: c831568dbcbb
Create Date: 2026-09-14 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e7a2b91c05'
down_revision: Union[str, Sequence[str], None] = 'c831568dbcbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('compliance_statements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('approved_by_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approved_by_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('approved_fingerprint', sa.String(length=64), nullable=True))
        batch_op.create_foreign_key('fk_compliance_statements_approved_by_id_users', 'users', ['approved_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('compliance_statements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_compliance_statements_approved_by_id_users', type_='foreignkey')
        batch_op.drop_column('approved_fingerprint')
        batch_op.drop_column('approved_by_name')
        batch_op.drop_column('approved_by_id')
        batch_op.drop_column('approved_at')
