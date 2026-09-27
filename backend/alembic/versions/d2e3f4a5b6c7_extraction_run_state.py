"""Design Sheet reads have a state apart from their outcome

A read of a Design Sheet (app.ai.sheet_reader) can stop before it is done:
the time budget, a provider that did not answer, a stop. `extraction_runs.
outcome` says what was read; `state` says whether the reading is finished --
"completed", "partial", "timed_out", "failed" or "cancelled" -- so an
unfinished read is never shown as complete, and a re-read resumes what is
pending. Existing rows are "completed": their reads had finished.

`document_readings.status` is a string column and gains the value "partial"
without a schema change.

Revision ID: d2e3f4a5b6c7
Revises: c9e1f2a3b4d6
Create Date: 2026-09-27 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c9e1f2a3b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch:
        batch.add_column(sa.Column("state", sa.String(length=16), nullable=False, server_default="completed"))


def downgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch:
        batch.drop_column("state")
