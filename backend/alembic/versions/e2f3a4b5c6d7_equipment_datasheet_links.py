"""equipment currents: the datasheet each row was read from, as a link

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-16 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('equipment_currents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('datasheet_library', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('datasheet_path', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('datasheet_pages', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('datasheet_match', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('datasheet_sha256', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('equipment_currents', schema=None) as batch_op:
        batch_op.drop_column('datasheet_sha256')
        batch_op.drop_column('datasheet_match')
        batch_op.drop_column('datasheet_pages')
        batch_op.drop_column('datasheet_path')
        batch_op.drop_column('datasheet_library')
