"""IFC reads in a worker; drawing identity and a revision chain the database keeps whole; AI symbol review

  * background_jobs: `params` (what a worker job needs to run: the staged
    upload, the drawing and revision it is to be) and `dedup_key`, with a
    partial unique index allowing one queued-or-running job per key -- the
    same file sent twice for the same drawing and revision is one read.
  * background_workers: `lane` ("sync" | "ifc"), so the API can tell
    whether an IFC worker is running.
  * project_ifc_drawings: `drawing_reference` (FA-101, whatever the file is
    called), `source_sha256` (the same file is not imported twice), and
    `deleted_at` / `deleted_by_id` (a revision is archived, never removed).
    A partial unique index lets a live drawing be revised once: two workers
    cannot both make R1 of the same R0.
  * ifc_symbols: `source` (engineer | deterministic | ai), `confidence`,
    `reviewed_by_id`, `reviewed_at`. Every symbol so far was an engineer's.
  * ifc_block_aliases: `is_ambiguous`.
  * ifc_symbol_reviews: every AI classification, the AI's cache, and what
    the engineer then did with it.

Existing drawings get their reference from their file name, a revision
taking the reference of the drawing it revises. A drawing revised twice
(two live revisions of one drawing, made before this index could refuse
the second) keeps its first revision; the later ones are archived, not
deleted, so the index can be built and nothing is lost.

Every change is a column or an index added in place: no table is rebuilt.
A rebuild of ifc_symbols with foreign keys enforced cascades into every
block alias (checked: 7,880 aliases gone), and project_ifc_drawings holds
each drawing's symbol groups -- megabytes a drawing -- so the new "who"
columns are plain user ids rather than foreign keys.

Revision ID: e1f2a3b4c5d7
Revises: d8b2c4e6f7a1
Create Date: 2026-09-25 21:00:00.000000

"""
import re
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d7"
down_revision: Union[str, None] = "d8b2c4e6f7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE_DEDUP = "dedup_key IS NOT NULL AND status IN ('queued', 'running')"
LIVE_REVISION = "supersedes_id IS NOT NULL AND deleted_at IS NULL"

# As app.ifc.services.revisions.reference was written on this date: kept
# here so this migration does not change when that function does.
_REVISION_SUFFIX = re.compile(r"[\s_.-]*\(?\s*\bR(?:EV)?\.?\s*0*(\d{1,2})\s*\)?\s*$", re.I)
_UPLOADED = re.compile(r"\s*\(uploaded [^)]*\)\s*$", re.I)
_DRAWING_NUMBER = re.compile(r"(?<![A-Z0-9])((?:[A-Z]{1,5}-){1,2}\d{2,4}[A-Z]?)(?![A-Z0-9])")


def _reference(filename: str) -> str:
    stem = re.sub(r"\.(dwg|dxf)$", "", filename, flags=re.I)
    stem = _REVISION_SUFFIX.sub("", _UPLOADED.sub("", stem).strip()).strip(" -_.")
    upper = " ".join(stem.replace("_", " ").upper().split())
    numbers = {n for n in _DRAWING_NUMBER.findall(upper) if not n.startswith("EP-")}
    if len(numbers) == 1:
        return numbers.pop()[:120]
    return (upper or filename.upper())[:120]


def upgrade() -> None:
    # Plain ALTER TABLE ADD COLUMN throughout (not batch mode): nothing is rebuilt.
    op.add_column("background_jobs", sa.Column("params", sa.JSON(), nullable=True))
    op.add_column("background_jobs", sa.Column("dedup_key", sa.String(length=200), nullable=True))
    op.create_index("uq_background_jobs_active_dedup", "background_jobs", ["dedup_key"], unique=True,
                    sqlite_where=sa.text(ACTIVE_DEDUP), postgresql_where=sa.text(ACTIVE_DEDUP))

    op.add_column("background_workers", sa.Column("lane", sa.String(length=16), nullable=True))

    op.add_column("project_ifc_drawings", sa.Column("drawing_reference", sa.String(length=120), nullable=True))
    op.add_column("project_ifc_drawings", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.add_column("project_ifc_drawings", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("project_ifc_drawings", sa.Column("deleted_by_id", sa.Integer(), nullable=True))
    op.create_index("ix_project_ifc_drawings_drawing_reference", "project_ifc_drawings", ["drawing_reference"])
    op.create_index("ix_project_ifc_drawings_source_sha256", "project_ifc_drawings", ["source_sha256"])

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, filename, supersedes_id FROM project_ifc_drawings ORDER BY id")).fetchall()
    by_id = {r[0]: r for r in rows}
    # A drawing revised twice: the first revision stays, the others are archived.
    seen: dict[int, int] = {}
    now = datetime.utcnow()
    for drawing_id, _, supersedes_id in rows:
        if supersedes_id is None:
            continue
        if supersedes_id in seen:
            connection.execute(sa.text("UPDATE project_ifc_drawings SET deleted_at = :now WHERE id = :id"),
                               {"now": now, "id": drawing_id})
        else:
            seen[supersedes_id] = drawing_id
    # References: a chain's root names it, and its revisions take the root's.
    for drawing_id, filename, _ in rows:
        root, hops = by_id[drawing_id], 0
        while root[2] is not None and root[2] in by_id and hops < 1000:
            root, hops = by_id[root[2]], hops + 1
        connection.execute(sa.text("UPDATE project_ifc_drawings SET drawing_reference = :ref WHERE id = :id"),
                           {"ref": _reference(root[1] or filename or ""), "id": drawing_id})
    op.create_index("uq_project_ifc_drawings_one_revision", "project_ifc_drawings", ["supersedes_id"], unique=True,
                    sqlite_where=sa.text(LIVE_REVISION), postgresql_where=sa.text(LIVE_REVISION))

    op.add_column("ifc_symbols", sa.Column("source", sa.String(length=20), nullable=False, server_default="engineer"))
    op.add_column("ifc_symbols", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("ifc_symbols", sa.Column("reviewed_by_id", sa.Integer(), nullable=True))
    op.add_column("ifc_symbols", sa.Column("reviewed_at", sa.DateTime(), nullable=True))

    op.add_column("ifc_block_aliases", sa.Column("is_ambiguous", sa.Boolean(), nullable=False, server_default="0"))

    op.create_table(
        "ifc_symbol_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signature", sa.String(length=40), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("candidate_ids", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("device_type_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("requires_engineer", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("validation", sa.String(length=16), nullable=False),
        sa.Column("validation_reason", sa.String(length=200), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("drawing_name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("outcome_device_type_id", sa.Integer(), nullable=True),
        sa.Column("outcome_by_id", sa.Integer(), nullable=True),
        sa.Column("outcome_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ifc_symbol_reviews_signature", "ifc_symbol_reviews", ["signature"])
    op.create_index("ix_ifc_symbol_reviews_cache_key", "ifc_symbol_reviews", ["cache_key"])
    op.create_index("ix_ifc_symbol_reviews_project_id", "ifc_symbol_reviews", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_ifc_symbol_reviews_project_id", table_name="ifc_symbol_reviews")
    op.drop_index("ix_ifc_symbol_reviews_cache_key", table_name="ifc_symbol_reviews")
    op.drop_index("ix_ifc_symbol_reviews_signature", table_name="ifc_symbol_reviews")
    op.drop_table("ifc_symbol_reviews")
    # ALTER TABLE DROP COLUMN (SQLite 3.35+): nothing is rebuilt going down either.
    op.drop_column("ifc_block_aliases", "is_ambiguous")
    for column in ("reviewed_at", "reviewed_by_id", "confidence", "source"):
        op.drop_column("ifc_symbols", column)
    op.drop_index("uq_project_ifc_drawings_one_revision", table_name="project_ifc_drawings")
    op.drop_index("ix_project_ifc_drawings_source_sha256", table_name="project_ifc_drawings")
    op.drop_index("ix_project_ifc_drawings_drawing_reference", table_name="project_ifc_drawings")
    for column in ("deleted_by_id", "deleted_at", "source_sha256", "drawing_reference"):
        op.drop_column("project_ifc_drawings", column)
    op.drop_column("background_workers", "lane")
    op.drop_index("uq_background_jobs_active_dedup", table_name="background_jobs")
    op.drop_column("background_jobs", "dedup_key")
    op.drop_column("background_jobs", "params")
