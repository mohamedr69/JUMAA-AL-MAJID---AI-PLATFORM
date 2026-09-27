"""What each document consumer sees, and what the classification would
have it see: a read-only shadow comparison, for the compatibility
matrix. Nothing is migrated; the numbers say where the two agree and
which documents they disagree on.

    venv\\Scripts\\python scripts\\consumer_shadow.py <project_id> [out.json]

Consumers (the legacy selector each uses today):
  logs.submittals     records of category submittals (document_sync.log_records)
  logs.drawings       records of category drawings that are ours (is_shop_drawing)
  logs.samples        records of category samples / transmittal submissions
  submittal_map       rows with role submittal_form (project_submittals is built from them)
  shop_drawings       project_shop_drawings revisions (built from logs.drawings)
  compliance          rows with role spec (the compliance page's specification search)
  replies             rows under a received / approved folder (submittal_replies.on_file)
  intake              rows with role drf / design_sheet (BOQ, Project Info)
The shadow selector is the current classification's primary type (any
stage, and separately supported only). Set DATABASE_URL to a clone."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import DocumentClassification, Project, ProjectDocument, ProjectShopDrawing, ProjectSubmittal  # noqa: E402
from app.services import document_control, document_sync, submittal_replies  # noqa: E402

SHADOW = {
    "logs.submittals": {"MATERIAL_SUBMITTAL"}, "logs.drawings": {"SHOP_DRAWING"}, "logs.samples": {"SAMPLE_APPROVAL", "TRANSMITTAL"},
    "submittal_map": {"MATERIAL_SUBMITTAL"}, "compliance": {"SPECIFICATION"}, "replies": {"COMMENT_RESPONSE", "CONSULTANT_DECISION"},
    "intake": {"DRF", "DESIGN_SHEET"},
}


def legacy_views(db, project: Project) -> dict[str, set[int]]:
    rows = {r.id: r for r in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id, ProjectDocument.state != "removed")}
    by_path = {(r.relative_path or r.filename): r.id for r in rows.values()}
    records, _warnings = document_sync.log_records(db, project) if project.source_folder_path else ([], [])
    views: dict[str, set[int]] = {k: set() for k in ("logs.submittals", "logs.drawings", "logs.samples")}
    for record in records:
        doc_id = by_path.get(record.path)
        if doc_id is None:
            continue
        if record.category == "submittals":
            views["logs.submittals"].add(doc_id)
        elif record.category == "drawings" and record.source != "drawing schedule" and document_control.is_shop_drawing(record):
            views["logs.drawings"].add(doc_id)
        elif record.category == "samples":
            views["logs.samples"].add(doc_id)
    views["submittal_map"] = {r.id for r in rows.values() if r.role == "submittal_form"}
    views["compliance"] = {r.id for r in rows.values() if r.role == "spec"}
    filed = submittal_replies.on_file(rows.values())
    reply_names = {(where, name) for where, name, _w in filed}
    views["replies"] = {r.id for r in rows.values() if (Path((r.relative_path or "").replace("\\", "/")).parent.as_posix(), r.filename) in reply_names}
    views["intake"] = {r.id for r in rows.values() if r.role in ("drf", "design_sheet")}
    return views


def shadow_views(db, project: Project, *, supported_only: bool) -> dict[str, set[int]]:
    entries = (db.query(DocumentClassification)
               .filter(DocumentClassification.project_id == project.id, DocumentClassification.superseded_at.is_(None)).all())
    views: dict[str, set[int]] = {k: set() for k in SHADOW}
    for entry in entries:
        if supported_only and entry.stage != "supported":
            continue
        for consumer, types in SHADOW.items():
            if entry.primary_type in types:
                views[consumer].add(entry.document_id)
    return views


def registers(db, project: Project) -> dict:
    submittals = sorted((s.reference, s.system_code, str(getattr(s.status, "value", s.status)), getattr(s, "revision", None))
                        for s in db.query(ProjectSubmittal).filter(ProjectSubmittal.project_id == project.id))
    drawings = []
    for d in db.query(ProjectShopDrawing).filter(ProjectShopDrawing.project_id == project.id):
        drawings.append((d.drawing_reference, d.system_code, sorted((r.revision, r.status, bool(r.confirmed_by_id)) for r in d.revisions)))
    drawings.sort(key=lambda x: (x[0] or "", x[1] or ""))
    records, _w = document_sync.log_records(db, project) if project.source_folder_path else ([], [])
    combined = document_control.combine(records)
    logs = sorted((r.category, r.system_code or "", r.reference, r.revision, r.status, len(r.superseded)) for r in combined)
    return {"submittals": submittals, "shop_drawings": drawings, "logs": logs}


def compare(db, project: Project) -> dict:
    legacy = legacy_views(db, project)
    any_stage = shadow_views(db, project, supported_only=False)
    supported = shadow_views(db, project, supported_only=True)
    names = {r.id: (r.relative_path or r.filename) for r in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id)}
    out = {}
    for consumer in legacy:
        l = legacy[consumer]
        for label, shadow in (("any_stage", any_stage.get(consumer, set())), ("supported", supported.get(consumer, set()))):
            out[f"{consumer}/{label}"] = {
                "legacy": len(l), "shadow": len(shadow), "both": len(l & shadow),
                "legacy_only": sorted(names.get(i, str(i)) for i in l - shadow)[:40], "legacy_only_count": len(l - shadow),
                "shadow_only": sorted(names.get(i, str(i)) for i in shadow - l)[:40], "shadow_only_count": len(shadow - l),
            }
    return out


def main(argv: list[str]) -> int:
    project_id = int(argv[0])
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise SystemExit(f"no project {project_id}")
        regs = registers(db, project)
        stages = Counter(e.stage for e in db.query(DocumentClassification)
                         .filter(DocumentClassification.project_id == project.id, DocumentClassification.superseded_at.is_(None)))
        data = {"project": project.ep_number, "project_id": project.id, "registers": regs,
                "register_hash": hashlib.sha256(json.dumps(regs, sort_keys=True, default=str).encode()).hexdigest(),
                "classification_stages": dict(stages), "consumers": compare(db, project)}
        if len(argv) > 1:
            Path(argv[1]).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        print(f"project {project.ep_number}: registers hash {data['register_hash'][:16]}; logs {len(regs['logs'])} rows, "
              f"submittals {len(regs['submittals'])}, shop drawings {len(regs['shop_drawings'])}; stages {dict(stages)}")
        for key, value in data["consumers"].items():
            print(f"  {key:28} legacy {value['legacy']:4} shadow {value['shadow']:4} both {value['both']:4} "
                  f"legacy-only {value['legacy_only_count']:4} shadow-only {value['shadow_only_count']:4}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
