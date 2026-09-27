"""Document processing runs as a job of its own: one active per project

File Sync is split in two (app.services.document_sync, app.services.
document_processing): the sync indexes the folder -- a stat per file,
seconds -- and a `process_documents` job then reads the documents the
index found, in a worker of its own (app.workers.document_worker). A
partial unique index allows one queued-or-running processing job per
project, as `uq_background_jobs_one_active_sync` does for the sync: the
database, not a check in Python, refuses the second.

No data changes. `project_documents.state` is a string column and gains
the value "pending" (discovered, not read yet) without a schema change.

Revision ID: c9e1f2a3b4d6
Revises: b8d0f2a4c6e8
Create Date: 2026-09-27 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1f2a3b4d6"
down_revision: Union[str, None] = "b8d0f2a4c6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE_PROCESSING = "kind = 'process_documents' AND status IN ('queued', 'running')"


def upgrade() -> None:
    op.create_index(
        "uq_background_jobs_one_active_processing", "background_jobs", ["project_id", "kind"], unique=True,
        sqlite_where=sa.text(ACTIVE_PROCESSING), postgresql_where=sa.text(ACTIVE_PROCESSING),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_one_active_processing", table_name="background_jobs")
