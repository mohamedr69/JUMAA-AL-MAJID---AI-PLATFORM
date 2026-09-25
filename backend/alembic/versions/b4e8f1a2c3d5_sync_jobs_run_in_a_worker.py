"""Document syncs run in a worker process, one active sync per project

The sync ran as a thread inside the API, so a long read of a project folder
competed with every page request, and three quick clicks on Sync started
three syncs of the same project -- the guard read "no sync running" three
times before any of them wrote one.

  * background_jobs gains the worker's bookkeeping: which worker holds a job
    (`worker_id`), when it last said it was alive (`heartbeat_at`), and how
    many times it has been started (`attempts`) so a job that keeps killing
    its worker is not restarted for ever.
  * background_workers: one row per worker process, with its heartbeat, so
    the API can tell a sync that is waiting for a worker from one that has
    none to wait for.
  * A partial unique index allows one queued-or-running sync per project.
    The database, not a check in Python, refuses the second.

Existing duplicates are resolved first, or the index could not be built: the
newest active sync of a project is put back in the queue for the worker (the
thread that ran it died with the old API process) and the others are failed
as duplicates of it.

Revision ID: b4e8f1a2c3d5
Revises: a3d7e5c81f92
Create Date: 2026-09-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8f1a2c3d5'
down_revision: Union[str, Sequence[str], None] = 'a3d7e5c81f92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE_SYNC = "kind = 'sync_documents' AND status IN ('queued', 'running')"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('background_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('worker_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('heartbeat_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))

    op.create_table(
        'background_workers',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=False),
        sa.Column('stopped_at', sa.DateTime(), nullable=True),
        sa.Column('current_job_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    connection = op.get_bind()
    active = connection.execute(sa.text(
        f"SELECT id, project_id FROM background_jobs WHERE {ACTIVE_SYNC} ORDER BY project_id, id DESC"
    )).fetchall()
    kept: dict = {}
    for job_id, project_id in active:
        if project_id not in kept:
            kept[project_id] = job_id
            connection.execute(sa.text(
                "UPDATE background_jobs SET status = 'queued', started_at = NULL, worker_id = NULL, "
                "heartbeat_at = NULL, cancel_requested = 0, "
                "progress = :progress WHERE id = :id"
            ), {"id": job_id, "progress": '{"done": 0, "total": 0, "message": "Queued again for the background worker"}'})
        else:
            connection.execute(sa.text(
                "UPDATE background_jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
                "error = :error WHERE id = :id"
            ), {"id": job_id, "error": f"A duplicate of sync job {kept[project_id]}, which carries on in its place."})

    op.create_index(
        'uq_background_jobs_one_active_sync', 'background_jobs', ['project_id', 'kind'], unique=True,
        sqlite_where=sa.text(ACTIVE_SYNC), postgresql_where=sa.text(ACTIVE_SYNC),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_background_jobs_one_active_sync', table_name='background_jobs')
    op.drop_table('background_workers')
    with op.batch_alter_table('background_jobs', schema=None) as batch_op:
        batch_op.drop_column('attempts')
        batch_op.drop_column('heartbeat_at')
        batch_op.drop_column('worker_id')
