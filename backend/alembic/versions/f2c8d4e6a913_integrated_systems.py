"""integrated systems: separate VE panel flag, ELS is CBS

Revision ID: f2c8d4e6a913
Revises: e5f1c3a7b820
Create Date: 2026-09-14 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8d4e6a913'
down_revision: Union[str, Sequence[str], None] = 'e5f1c3a7b820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ELS is emergency lighting, which is the central battery system (app.services.system_rules).
_CODED = ("project_design_sheets", "project_boq_items", "project_submittals", "compliance_statements",
          "compliance_learned_answers")


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('separate_ve_panel', sa.Boolean(), nullable=False, server_default=sa.false()))
    for table in _CODED:
        op.execute(f"UPDATE {table} SET system_code = 'CBS' WHERE upper(system_code) IN ('ELS', 'EL')")


def downgrade() -> None:
    """Downgrade schema. The ELS codes are not restored: they meant CBS."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('separate_ve_panel')
