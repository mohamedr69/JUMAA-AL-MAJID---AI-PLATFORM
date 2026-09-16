"""the project document index: every file in the folder, its state, what was read off it, and what depends on it

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('project_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mtime', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('state', sa.String(length=16), nullable=False, server_default='fresh'))
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('reference', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('revision', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('extracted', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('reading_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('index_version', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('last_processed_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_project_documents_reading_id_document_readings', 'document_readings',
                                    ['reading_id'], ['id'])
    op.create_table(
        'document_dependencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('source_document_id', sa.Integer(), nullable=False),
        sa.Column('dependent_type', sa.String(length=24), nullable=False),
        sa.Column('dependent_id', sa.String(length=120), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('last_validated_sha256', sa.String(length=64), nullable=True),
        sa.Column('stale', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_document_dependencies_project_id_projects'),
        sa.ForeignKeyConstraint(['source_document_id'], ['project_documents.id'],
                                name='fk_document_dependencies_source_document_id_project_documents'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_document_id', 'dependent_type', 'dependent_id', name='uq_document_dependency'),
    )
    with op.batch_alter_table('document_dependencies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_dependencies_project_id'), ['project_id'], unique=False)
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('documents_synced_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('documents_listing_sha256', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('documents_listing_sha256')
        batch_op.drop_column('documents_synced_at')
    with op.batch_alter_table('document_dependencies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_dependencies_project_id'))
    op.drop_table('document_dependencies')
    with op.batch_alter_table('project_documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_project_documents_reading_id_document_readings', type_='foreignkey')
        for name in ('last_processed_at', 'index_version', 'reading_id', 'extracted', 'status', 'revision', 'reference',
                     'error', 'state', 'mtime'):
            batch_op.drop_column(name)
