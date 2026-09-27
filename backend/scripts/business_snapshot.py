"""A read-only snapshot of a project's business state, for proving that a
metadata-only operation (a classification backfill) changed none of it.

    venv\\Scripts\\python scripts\\business_snapshot.py <project_id> [out.json]

Prints a stable hash of the snapshot and writes the snapshot as JSON.
Everything a classification must not touch is in it -- the document
index (id, role, state, hash, reading identity), the submittal register
and its revisions and replies, the shop drawings and their revisions, the
BOQ items and revisions, the dependencies with their stale flags, and the
project's domain fields -- and nothing that moves on its own (heartbeats,
job rows, timings). Classification rows are excluded on purpose: they are
what the operation writes. Reads only; opens no file."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.database import engine  # noqa: E402

# (table, key columns to order by, columns to leave out: timing-only or self-moving)
TABLES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("project_documents", ("id",), ("last_seen_at", "checked_at")),
    ("project_submittals", ("id",), ()),
    ("project_submittal_revisions", ("id",), ()),
    ("project_submittal_events", ("id",), ()),
    ("project_submittal_status_history", ("id",), ()),
    ("submittal_replies", ("id",), ()),
    ("project_shop_drawings", ("id",), ()),
    ("shop_drawing_revisions", ("id",), ()),
    ("shop_drawing_events", ("id",), ()),
    ("shop_drawing_candidates", ("id",), ()),
    ("drawing_requirement_states", ("id",), ()),
    ("drawing_issues", ("id",), ()),
    ("project_ifc_drawings", ("id",), ()),
    ("project_boq_items", ("id",), ()),
    ("project_boq_revisions", ("id",), ()),
    ("boq_snapshots", ("id",), ()),
    ("boq_candidates", ("id",), ()),
    ("boq_corrections", ("id",), ()),
    ("extraction_runs", ("id",), ()),
    ("extraction_issues", ("id",), ()),
    ("document_dependencies", ("id",), ()),
    ("document_readings", ("id",), ()),
    ("project_design_sheets", ("id",), ()),
    ("project_systems", ("id",), ()),
    ("project_building_floors", ("id",), ()),
    ("project_floor_schedule", ("id",), ()),
    ("project_proposed_materials", ("id",), ()),
    ("project_actions", ("id",), ()),
    ("project_changes", ("id",), ()),
    ("compliance_statements", ("id",), ()),
]
PROJECT_EXCLUDE = ("updated_at",)


def _rows(connection, table: str, project_id: int, exclude: tuple[str, ...]) -> list[dict] | None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return None
    columns = [c["name"] for c in inspector.get_columns(table)]
    keep = [c for c in columns if c not in exclude]
    if "project_id" in columns:
        where, params = "project_id = :pid", {"pid": project_id}
    elif table == "document_readings":
        # Readings are per content, shared across projects: the ones the project's rows point at.
        where, params = "id IN (SELECT reading_id FROM project_documents WHERE project_id = :pid AND reading_id IS NOT NULL)", {"pid": project_id}
    else:
        return None
    sql = f"SELECT {', '.join(keep)} FROM {table} WHERE {where} ORDER BY id"
    out = []
    for row in connection.execute(text(sql), params).mappings():
        out.append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()})
    return out


def snapshot(project_id: int) -> dict:
    with engine.connect() as connection:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("projects") if c["name"] not in PROJECT_EXCLUDE]
        project = connection.execute(text(f"SELECT {', '.join(columns)} FROM projects WHERE id = :pid"), {"pid": project_id}).mappings().first()
        if project is None:
            raise SystemExit(f"no project {project_id}")
        out = {"project": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in project.items()}, "tables": {}}
        for table, _keys, exclude in TABLES:
            rows = _rows(connection, table, project_id, exclude)
            if rows is not None:
                out["tables"][table] = rows
        return out


def digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def main(argv: list[str]) -> int:
    project_id = int(argv[0])
    data = snapshot(project_id)
    data["hash"] = digest({"project": data["project"], "tables": data["tables"]})
    if len(argv) > 1:
        Path(argv[1]).write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    counts = {t: len(r) for t, r in data["tables"].items() if r}
    print(f"project {project_id} ({data['project'].get('ep_number')}) snapshot hash {data['hash']}")
    print("rows:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
