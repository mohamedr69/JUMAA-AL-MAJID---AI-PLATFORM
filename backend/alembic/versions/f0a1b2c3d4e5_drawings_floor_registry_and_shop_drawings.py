"""The Drawings page's own records: building floors, shop drawings, revisions, candidates, issues

IFC defines the building; the shop drawings define the submission history.
Until now the Drawings Log was worked out on every request from the
document index and from every IFC drawing resolved in full. Now:

  * project_building_floors: the floors the IFC drawings in force name,
    one row per floor (a typical sheet for 3 to 14 is twelve), refreshed
    when an IFC drawing changes -- the one thing the log takes from IFC.
  * project_shop_drawings / shop_drawing_revisions: one shop drawing per
    floor per system, each official revision with the consultant's answer.
  * shop_drawing_candidates: a file found at a revision nothing proves
    was submitted ("R1 available"), with the engineer's decision on it.
  * drawing_issues: what needs an engineer's eye, by the rules or the AI.
  * drawing_requirement_states: when a required document was asked of the
    contractor -- an operational record, no longer read off the activity log.
  * shop_drawing_events: the Activity / History tab.
  * projects.drawings_reconciled_at: when the records were last brought
    up to the index.

Revision ID: f0a1b2c3d4e5
Revises: e1f2a3b4c5d7
Create Date: 2026-09-26 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e1f2a3b4c5d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("drawings_reconciled_at", sa.DateTime(), nullable=True))

    op.create_table(
        "project_building_floors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("floor_key", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("elevation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(16), nullable=False, server_default="ifc"),
        sa.Column("ifc_sheet", sa.String(300), nullable=True),
        sa.Column("first_detected_at", sa.DateTime(), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "floor_key", name="uq_project_building_floor"),
    )
    op.create_index("ix_project_building_floors_project_id", "project_building_floors", ["project_id"])

    op.create_table(
        "project_shop_drawings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_code", sa.String(16), nullable=False),
        sa.Column("drawing_reference", sa.String(160), nullable=False),
        sa.Column("floor_keys", sa.JSON(), nullable=False),
        sa.Column("floor_label", sa.String(200), nullable=True),
        sa.Column("typical", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "system_code", "drawing_reference", name="uq_project_shop_drawing"),
    )
    op.create_index("ix_project_shop_drawings_project_id", "project_shop_drawings", ["project_id"])
    op.create_index("ix_project_shop_drawings_system_code", "project_shop_drawings", ["system_code"])

    op.create_table(
        "shop_drawing_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_drawing_id", sa.Integer(), sa.ForeignKey("project_shop_drawings.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("revision", sa.String(10), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="under_review"),
        sa.Column("submitted", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("submission_reference", sa.String(160), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("reply_reference", sa.String(160), nullable=True),
        sa.Column("reply_at", sa.DateTime(), nullable=True),
        sa.Column("reply_text", sa.Text(), nullable=True),
        sa.Column("drawing_path", sa.Text(), nullable=True),
        sa.Column("drawing_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("drawing_sha256", sa.String(64), nullable=True),
        sa.Column("source_missing", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(16), nullable=False, server_default="sync"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("shop_drawing_id", "revision", name="uq_shop_drawing_revision"),
    )
    op.create_index("ix_shop_drawing_revisions_shop_drawing_id", "shop_drawing_revisions", ["shop_drawing_id"])

    op.create_table(
        "shop_drawing_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shop_drawing_id", sa.Integer(), sa.ForeignKey("project_shop_drawings.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("revision", sa.String(10), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("file_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("candidate_status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("shop_drawing_id", "revision", "file_sha256", name="uq_shop_drawing_candidate"),
    )
    op.create_index("ix_shop_drawing_candidates_project_id", "shop_drawing_candidates", ["project_id"])
    op.create_index("ix_shop_drawing_candidates_shop_drawing_id", "shop_drawing_candidates", ["shop_drawing_id"])

    op.create_table(
        "drawing_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_code", sa.String(16), nullable=True),
        sa.Column("shop_drawing_id", sa.Integer(), nullable=True),
        sa.Column("floor_key", sa.String(80), nullable=True),
        sa.Column("key", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("source", sa.String(16), nullable=False, server_default="system"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ai", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.UniqueConstraint("project_id", "key", name="uq_drawing_issue_key"),
    )
    op.create_index("ix_drawing_issues_project_id", "drawing_issues", ["project_id"])
    op.create_index("ix_drawing_issues_system_code", "drawing_issues", ["system_code"])

    op.create_table(
        "drawing_requirement_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_code", sa.String(16), nullable=False),
        sa.Column("requirement_key", sa.String(40), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remarks", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "system_code", "requirement_key", name="uq_drawing_requirement"),
    )
    op.create_index("ix_drawing_requirement_states_project_id", "drawing_requirement_states", ["project_id"])

    # The requests already made were only ever kept on the activity log:
    # carried over, so no item forgets when it was asked for.
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT project_id, at, detail, user_id FROM activity_events "
        "WHERE action = 'drawings.requested' AND project_id IS NOT NULL ORDER BY at")).fetchall()
    import json

    systems_of = {"fa_ifc": "FAS", "acs_ifc": "FAS", "ff_ifc": "FAS", "sm_ifc": "FAS", "title_block": "FAS",
                  "sd_reference": "FAS", "els_fa_ifc": "ELS", "els_lighting_ifc": "ELS", "els_load_schedule": "ELS"}
    latest: dict = {}
    for project_id, at, detail, user_id in rows:
        try:
            keys = (json.loads(detail) if isinstance(detail, str) else detail or {}).get("keys") or []
        except ValueError:
            keys = []
        for key in keys:
            system = systems_of.get(key)
            if system:
                entry = latest.setdefault((project_id, system, key), {"count": 0})
                entry.update({"at": at, "user": user_id, "count": entry["count"] + 1})
    for (project_id, system, key), entry in latest.items():
        connection.execute(sa.text(
            "INSERT INTO drawing_requirement_states (project_id, system_code, requirement_key, requested_at, "
            "requested_by_id, request_count, remarks, updated_at) VALUES (:p, :s, :k, :at, :u, :n, '', :at)"),
            {"p": project_id, "s": system, "k": key, "at": entry["at"], "u": entry["user"], "n": entry["count"]})

    op.create_table(
        "shop_drawing_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_code", sa.String(16), nullable=True),
        sa.Column("shop_drawing_id", sa.Integer(), nullable=True),
        sa.Column("floor_key", sa.String(80), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_shop_drawing_events_project_id", "shop_drawing_events", ["project_id"])
    op.create_index("ix_shop_drawing_events_system_code", "shop_drawing_events", ["system_code"])
    op.create_index("ix_shop_drawing_events_at", "shop_drawing_events", ["at"])


def downgrade() -> None:
    for table in ("shop_drawing_events", "drawing_requirement_states", "drawing_issues", "shop_drawing_candidates",
                  "shop_drawing_revisions", "project_shop_drawings", "project_building_floors"):
        op.drop_table(table)
    op.drop_column("projects", "drawings_reconciled_at")
