"""Document classification assessments, beside the index

One row per assessment of a project document (app.services.
document_classification): what the file appears to contain, at what
stage of evidence, why, under which rules, for which content and in
which context. Earlier assessments are kept, superseded; an engineer's
confirmation is never superseded by an automatic one. Nothing else reads
this table: it is metadata, additive and optional (feature flag
DOCUMENT_CLASSIFICATION_V2, default off), and existing rows need no
backfill.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-27 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_classifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("project_documents.id"), nullable=False, index=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("rules_version", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("primary_type", sa.String(length=32), nullable=False),
        sa.Column("component_types", sa.JSON(), nullable=False),
        sa.Column("evidence_strength", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_sources", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("system_code", sa.String(length=16), nullable=True),
        sa.Column("discipline", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("engineer_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_document_classifications_current", "document_classifications", ["document_id", "superseded_at"])


def downgrade() -> None:
    op.drop_index("ix_document_classifications_current", table_name="document_classifications")
    op.drop_table("document_classifications")
