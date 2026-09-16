"""document readings: what the AI read off a Design Sheet or DRF, kept for good

Revision ID: c7d8e9f0a1b2
Revises: 8c1d2e3f4a5b
Create Date: 2026-09-16 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = '8c1d2e3f4a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'document_readings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('document_path', sa.Text(), nullable=False),
        sa.Column('document_sha256', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('pages', sa.Integer(), nullable=False),
        sa.Column('reading', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('calls', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_document_readings_created_by_id_users'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_document_readings_project_id_projects'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('document_readings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_readings_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_readings_document_sha256'), ['document_sha256'], unique=False)
    with op.batch_alter_table('extraction_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reader', sa.String(length=8), nullable=False, server_default='ocr'))
        batch_op.add_column(sa.Column('reading_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_extraction_runs_reading_id_document_readings', 'document_readings',
                                    ['reading_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('extraction_runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_extraction_runs_reading_id_document_readings', type_='foreignkey')
        batch_op.drop_column('reading_id')
        batch_op.drop_column('reader')
    with op.batch_alter_table('document_readings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_readings_document_sha256'))
        batch_op.drop_index(batch_op.f('ix_document_readings_project_id'))
    op.drop_table('document_readings')
