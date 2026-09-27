"""Golden Project validation: what the readers extracted, before against after.

    venv\\Scripts\\python -m scripts.golden_records --from-db 1 --out bench/golden-before.json
    venv\\Scripts\\python -m scripts.benchmark_processing --folder ... --records bench/golden-after.json
    venv\\Scripts\\python -m scripts.golden_records --compare bench/golden-before.json bench/golden-after.json

A performance change to the reading pipeline is accepted only if what it
extracts is what it extracted before: every document-control record's
category, reference, revision, status, floor, system and title, per
document. `--from-db` takes the "before" from the platform's own index
(project_documents.extracted, written by the processing job), `--compare`
lists every document whose records differ, and exits 1 when any do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RECORD_FIELDS = ("category", "reference", "revision", "status", "floor", "system_code", "name", "page", "source")


def record_key_fields(record) -> dict:
    """The fields of a record that carry engineering meaning, as a dict --
    from a ControlledDocument or from the stored dict form."""
    if isinstance(record, dict):
        return {field: record.get(field) for field in RECORD_FIELDS}
    return {field: getattr(record, field, None) for field in RECORD_FIELDS}


def _normalise(document: dict) -> dict:
    # A record the model's form reading stood in for the page (source
    # "submittal form", document_sync.record_for_the_log) is the AI's, not
    # the reader's: a run without the model has none, and that is not a
    # difference in the reading.
    records = sorted((record_key_fields(r) for r in document.get("records") or []
                      if record_key_fields(r).get("source") != "submittal form"),
                     key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return {"role": document.get("role"), "records": records}


def from_db(project_id: int) -> dict[str, dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.database import SessionLocal
    from app.models import ProjectDocument

    db = SessionLocal()
    try:
        rows = (db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id,
                                                 ProjectDocument.state != "removed",
                                                 ProjectDocument.role.notin_(("drf", "design_sheet"))).all())
        return {row.relative_path or row.filename: {"role": row.role, "notes": (row.extracted or {}).get("notes") or [],
                                                     "records": (row.extracted or {}).get("records") or []}
                for row in rows}
    finally:
        db.close()


def compare(before: dict[str, dict], after: dict[str, dict], *, ignore_role: bool = False) -> list[str]:
    """Human-readable differences; empty when the two agree."""
    differences: list[str] = []
    for relative in sorted(set(before) | set(after)):
        if relative not in after:
            differences.append(f"MISSING after: {relative}")
            continue
        if relative not in before:
            differences.append(f"NEW after (no before): {relative}")
            continue
        old, new = _normalise(before[relative]), _normalise(after[relative])
        if not ignore_role and old["role"] != new["role"]:
            differences.append(f"ROLE {relative}: {old['role']} -> {new['role']}")
        if old["records"] != new["records"]:
            differences.append(f"RECORDS {relative}:")
            for record in old["records"]:
                if record not in new["records"]:
                    differences.append(f"    - {json.dumps(record, default=str)}")
            for record in new["records"]:
                if record not in old["records"]:
                    differences.append(f"    + {json.dumps(record, default=str)}")
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-db", type=int, metavar="PROJECT_ID", help="dump the index's records of a project")
    parser.add_argument("--out", help="where --from-db writes")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="two dumps to compare")
    parser.add_argument("--ignore-role", action="store_true", help="compare records only, not the classification")
    args = parser.parse_args()
    if args.from_db is not None:
        golden = from_db(args.from_db)
        out = Path(args.out or f"golden-project-{args.from_db}.json")
        out.write_text(json.dumps(golden, indent=1, default=str, sort_keys=True), encoding="utf-8")
        print(f"written {out} ({len(golden)} documents, "
              f"{sum(len(d['records']) for d in golden.values())} records)")
        return 0
    if args.compare:
        before = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        after = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        differences = compare(before, after, ignore_role=args.ignore_role)
        common = len(set(before) & set(after))
        print(f"{common} documents in both; {len(differences)} difference line(s)")
        for line in differences:
            print(line)
        return 1 if differences else 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
