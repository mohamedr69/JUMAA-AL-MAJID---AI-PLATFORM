"""An engineer's role is their discipline and what they do in it

A design engineer and an estimation engineer are not the same job, and
neither is a fire alarm designer and an ELV one. The three general
engineer roles become six specific ones.

Mapping, as the design manager gave it on 23 Sep 2026:

    design_engineer         -> fire_alarm_design_engineer
    elv_engineer            -> elv_design_engineer
    fire_fighting_engineer  -> fire_fighting_design_engineer
    estimation_engineer     -> fire_alarm_estimation_engineer

The estimation account was the company's one estimation user and its work
is fire alarm; the other two general roles had no users at all, so the
only rows that move are the four design engineers and that one account.

The column is widened first: "fire_fighting_estimation_engineer" is 33
characters and the column held 22.

Revision ID: f2a8c4e01d76
Revises: e4c7a1d93b58
Create Date: 2026-09-23 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a8c4e01d76'
down_revision: Union[str, Sequence[str], None] = 'e4c7a1d93b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RENAMED = {
    "design_engineer": "fire_alarm_design_engineer",
    "elv_engineer": "elv_design_engineer",
    "fire_fighting_engineer": "fire_fighting_design_engineer",
    "estimation_engineer": "fire_alarm_estimation_engineer",
}


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.alter_column("role", existing_type=sa.String(22), type_=sa.String(40), existing_nullable=False)
    for old, new in RENAMED.items():
        op.execute(sa.text("UPDATE users SET role = :new WHERE role = :old").bindparams(new=new, old=old))


def downgrade() -> None:
    """Downgrade schema.

    The six specific roles fold back onto the three general ones. An ELV
    or fire fighting *estimation* engineer has no general role to return
    to -- there was never one -- so they come back as that discipline's
    engineer, which is what they were nearest to.
    """
    back = {
        "fire_alarm_design_engineer": "design_engineer",
        "elv_design_engineer": "elv_engineer",
        "fire_fighting_design_engineer": "fire_fighting_engineer",
        "fire_alarm_estimation_engineer": "estimation_engineer",
        "elv_estimation_engineer": "elv_engineer",
        "fire_fighting_estimation_engineer": "fire_fighting_engineer",
    }
    for new, old in back.items():
        op.execute(sa.text("UPDATE users SET role = :old WHERE role = :new").bindparams(old=old, new=new))
    with op.batch_alter_table("users", schema=None) as batch:
        batch.alter_column("role", existing_type=sa.String(40), type_=sa.String(22), existing_nullable=False)
