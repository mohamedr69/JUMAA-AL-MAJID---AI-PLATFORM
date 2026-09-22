"""Add the estimation team's initial project area and account role."""
from alembic import op
import sqlalchemy as sa

revision = "b9d3f8a5c210"
down_revision = "a8c2e7f4b109"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'estimation_engineer'")
    elif op.get_bind().dialect.name != "sqlite":
        op.alter_column("users", "role", type_=sa.Enum("admin", "design_manager", "design_engineer", "draftsman", "viewer", "estimation_engineer", name="roleenum"))
    op.create_table(
        "estimation_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("client", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("estimation_projects")
    # Keep the enum value: existing estimation accounts must remain readable.
