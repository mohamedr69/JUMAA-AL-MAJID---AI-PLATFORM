"""One material submittal per system and brand, with its revisions

A system's material submittal is one per brand, revised R0, R1, ... until
approved (app.services.submittal_identity). The register kept a row per
form reference, so our own copy of a revision ("MS EML R0.pdf", under
review) and the copy the consultant answered (BBY006-GME-MAS-EL-LI-0001,
approved) were two submittals of one brand.

  * project_submittals.brand_key: the brand a submittal is for, in one
    spelling (Menvier is Eaton's; "FIREGUARD / RAMCRO" is Fireguard).
  * project_submittal_revisions: a submittal's revisions, each with one
    current status, updated in place when the consultant answers.
  * project_submittal_status_history: what a revision's status was before.
    History only; nothing is counted from it.
  * The register rows of one system and brand are merged into one: its
    revisions come from all of them (the answered copy of a revision over
    one still under review, the other references kept as "also filed as"),
    their events move onto the row that stays. A system submitted from
    several brands keeps a row for each. A partial unique index then
    refuses a second submittal for a system and brand.

Revision ID: c5f9a2b3d4e6
Revises: b4e8f1a2c3d5
Create Date: 2026-09-25 14:00:00.000000

"""
import json
import re
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5f9a2b3d4e6'
down_revision: Union[str, Sequence[str], None] = 'b4e8f1a2c3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WITH_SYSTEM = "system_code IS NOT NULL"
ANSWERED = {"approved": 2, "rejected": 2, "under_review": 1, "not_submitted": 0}


def _number(revision) -> int:
    digits = re.sub(r"\D", "", revision or "")
    return int(digits) if digits else 0


def upgrade() -> None:
    """Upgrade schema."""
    from app.services.submittal_identity import brand_key, settle_unknown_brands

    with op.batch_alter_table('project_submittals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('brand_key', sa.String(length=64), nullable=False, server_default=''))
    op.create_table(
        'project_submittal_revisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submittal_id', sa.Integer(), nullable=False),
        sa.Column('revision', sa.String(length=16), nullable=False),
        sa.Column('status', sa.Enum('not_submitted', 'under_review', 'approved', 'rejected', name='submittalstatus'),
                  nullable=False),
        sa.Column('reply_code', sa.String(length=2), nullable=True),
        sa.Column('reference', sa.String(length=64), nullable=True),
        sa.Column('also_filed_as', sa.JSON(), nullable=False),
        sa.Column('manufacturer', sa.String(length=120), nullable=True),
        sa.Column('document_path', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['submittal_id'], ['project_submittals.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submittal_id', 'revision', name='uq_submittal_revision'),
    )
    with op.batch_alter_table('project_submittal_revisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_submittal_revisions_submittal_id'), ['submittal_id'], unique=False)
    op.create_table(
        'project_submittal_status_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('revision_id', sa.Integer(), nullable=False),
        sa.Column('previous_status', sa.String(length=16), nullable=True),
        sa.Column('new_status', sa.String(length=16), nullable=False),
        sa.Column('reply_code', sa.String(length=2), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('by_id', sa.Integer(), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['revision_id'], ['project_submittal_revisions.id']),
        sa.ForeignKeyConstraint(['by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('project_submittal_status_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_submittal_status_history_revision_id'), ['revision_id'], unique=False)

    connection = op.get_bind()
    rows = [dict(r) for r in connection.execute(sa.text(
        "SELECT id, project_id, system_code, reference, revision, status, reply_code, manufacturer, document_path, "
        "note, updated_at FROM project_submittals ORDER BY id")).mappings().all()]
    # The brand of each row; a row whose brand is not known belongs to its
    # system's brand when the system has only one.
    for row in rows:
        row["brand"] = brand_key(row["manufacturer"]) if row["system_code"] else ""
    for project_id in {r["project_id"] for r in rows}:
        mine = [r for r in rows if r["project_id"] == project_id and r["system_code"]]
        settled = settle_unknown_brands([(r["system_code"], r["brand"]) for r in mine])
        for r in mine:
            r["brand"] = settled[(r["system_code"], r["brand"])]
    groups: dict = {}
    for row in rows:
        key = (row["project_id"], row["system_code"], row["brand"]) if row["system_code"] else ("row", row["id"])
        groups.setdefault(key, []).append(row)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for key, members in groups.items():
        # A revision's best copy: the one the consultant answered, then the
        # first entered. The others filed as it are kept by reference.
        by_revision: dict[int, list[dict]] = {}
        for member in members:
            by_revision.setdefault(_number(member["revision"]), []).append(member)
        best = {n: sorted(copies, key=lambda m: (-ANSWERED.get(m["status"], 0), -bool(m["reply_code"]), m["id"]))
                for n, copies in by_revision.items()}
        keeper = best[max(best)][0]
        connection.execute(sa.text("UPDATE project_submittals SET brand_key = :brand WHERE id = :id"),
                           {"brand": keeper["brand"], "id": keeper["id"]})
        for n, copies in sorted(best.items()):
            chosen = copies[0]
            others = sorted({c["reference"] for c in copies[1:] if c["reference"] and c["reference"] != chosen["reference"]})
            result = connection.execute(sa.text(
                "INSERT INTO project_submittal_revisions (submittal_id, revision, status, reply_code, reference, "
                "also_filed_as, manufacturer, document_path, note, updated_at) VALUES (:submittal_id, :revision, "
                ":status, :reply_code, :reference, :also, :manufacturer, :document_path, :note, :updated_at)"),
                {"submittal_id": keeper["id"], "revision": f"R{n:02d}", "status": chosen["status"],
                 "reply_code": chosen["reply_code"], "reference": chosen["reference"], "also": json.dumps(others),
                 "manufacturer": chosen["manufacturer"], "document_path": chosen["document_path"],
                 "note": chosen["note"], "updated_at": chosen["updated_at"] or now})
            connection.execute(sa.text(
                "INSERT INTO project_submittal_status_history (revision_id, previous_status, new_status, reply_code, "
                "source, changed_at) VALUES (:revision_id, NULL, :status, :reply_code, 'migrated', :at)"),
                {"revision_id": result.lastrowid, "status": chosen["status"], "reply_code": chosen["reply_code"],
                 "at": chosen["updated_at"] or now})
        merged = [m for m in members if m["id"] != keeper["id"]]
        if not merged:
            continue
        ids = ", ".join(str(m["id"]) for m in merged)
        connection.execute(sa.text(f"UPDATE project_submittal_events SET submittal_id = :keeper WHERE submittal_id IN ({ids})"),
                           {"keeper": keeper["id"]})
        described = "; ".join(f"{m['reference'] or 'no reference'} {m['revision']} ({(m['status'] or '').replace('_', ' ')})"
                              for m in merged)
        brand = f" {keeper['brand']}" if keeper["brand"] else ""
        connection.execute(sa.text(
            "INSERT INTO project_submittal_events (submittal_id, kind, detail, at) VALUES (:id, 'merged', :detail, :at)"),
            {"id": keeper["id"], "at": now,
             "detail": (f"One material submittal for {key[1]}{brand}: the register row{'s' if len(merged) > 1 else ''} "
                        f"{described} {'were' if len(merged) > 1 else 'was'} the same submittal, now kept in its revisions")})
        connection.execute(sa.text(f"DELETE FROM project_submittals WHERE id IN ({ids})"))

    with op.batch_alter_table('project_submittals', schema=None) as batch_op:
        batch_op.create_index('uq_project_submittals_one_per_brand', ['project_id', 'system_code', 'brand_key'],
                              unique=True, sqlite_where=sa.text(WITH_SYSTEM), postgresql_where=sa.text(WITH_SYSTEM))


def downgrade() -> None:
    """Downgrade schema. The merged register rows are not split again."""
    connection = op.get_bind()
    indexes = {i["name"] for i in sa.inspect(connection).get_indexes('project_submittals')}
    columns = {c["name"] for c in sa.inspect(connection).get_columns('project_submittals')}
    # Either index: a database first migrated when this made one submittal
    # per system has uq_project_submittals_one_per_system instead.
    for name in ('uq_project_submittals_one_per_brand', 'uq_project_submittals_one_per_system'):
        if name in indexes:
            op.execute(sa.text(f"DROP INDEX {name}"))
    op.drop_table('project_submittal_status_history')
    op.drop_table('project_submittal_revisions')
    if 'brand_key' in columns:
        with op.batch_alter_table('project_submittals', schema=None) as batch_op:
            batch_op.drop_column('brand_key')
