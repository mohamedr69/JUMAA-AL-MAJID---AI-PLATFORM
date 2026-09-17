"""brand suppliers: who supplies each brand, for every project

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-09-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, Sequence[str], None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'brand_suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brand', sa.String(length=64), nullable=False),
        sa.Column('supplier', sa.String(length=200), nullable=False),
        sa.Column('contact', sa.String(length=200), nullable=True),
        sa.Column('phone', sa.String(length=64), nullable=True),
        sa.Column('emails', sa.String(length=300), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('map_url', sa.String(length=500), nullable=True),
        sa.Column('website', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('brand'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('brand_suppliers')
