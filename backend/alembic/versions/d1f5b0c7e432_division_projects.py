"""Fire Fighting and ELV division projects and roles."""
from alembic import op
import sqlalchemy as sa

revision = "d1f5b0c7e432"
down_revision = "c0e4a9b6d321"
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("users") as batch:
            batch.alter_column("role", existing_type=sa.String(19), type_=sa.String(22), existing_nullable=False)
    elif dialect == "postgresql":
        op.execute("ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'fire_fighting_engineer'")
        op.execute("ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'elv_engineer'")
    else:
        op.alter_column("users", "role", type_=sa.Enum("admin", "design_manager", "design_engineer", "draftsman", "viewer", "estimation_engineer", "fire_fighting_engineer", "elv_engineer", name="roleenum"))
    op.create_table("division_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("division", sa.String(30), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("client", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("division", "reference", name="uq_division_project_reference"))


def downgrade():
    op.drop_table("division_projects")
