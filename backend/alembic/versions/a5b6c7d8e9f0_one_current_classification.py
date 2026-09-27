"""One current classification assessment per document

At most one row of document_classifications per document may be current
(superseded_at IS NULL): the database refuses a second one, as it refuses
a second active sync per project. app.services.document_classification.
record closes the earlier row before it opens the new one. Any current
duplicates already stored (none expected: the feature was off) are
closed first, the latest kept -- superseded, not deleted: the history
stays.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-27 19:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ONE_CURRENT = "superseded_at IS NULL"


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE document_classifications SET superseded_at = created_at "
        "WHERE superseded_at IS NULL AND id NOT IN ("
        "  SELECT MAX(id) FROM document_classifications WHERE superseded_at IS NULL GROUP BY document_id)"
    ))
    op.create_index("uq_document_classifications_one_current", "document_classifications", ["document_id"], unique=True,
                    sqlite_where=sa.text(ONE_CURRENT), postgresql_where=sa.text(ONE_CURRENT))


def downgrade() -> None:
    op.drop_index("uq_document_classifications_one_current", table_name="document_classifications")
