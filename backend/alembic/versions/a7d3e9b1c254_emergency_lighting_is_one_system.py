"""emergency lighting is one system: CBS and EML are ELS

Revision ID: a7d3e9b1c254
Revises: f2c8d4e6a913
Create Date: 2026-09-15 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7d3e9b1c254'
down_revision: Union[str, Sequence[str], None] = 'f2c8d4e6a913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ELS, CBS and EML are the same system (app.services.system_rules): kept as ELS.
_CODED = ("project_design_sheets", "project_boq_items", "project_submittals", "compliance_statements",
          "compliance_learned_answers")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _CODED:
        op.execute(f"UPDATE {table} SET system_code = 'ELS' WHERE upper(system_code) IN ('CBS', 'EML', 'ELM', 'EL')")


def downgrade() -> None:
    """Downgrade schema. Nothing to undo: the codes all meant emergency lighting."""
