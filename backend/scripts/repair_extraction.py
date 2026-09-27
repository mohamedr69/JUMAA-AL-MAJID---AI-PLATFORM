"""Targeted, resumable, idempotent repair of stored document readings.

Three things a classification backfill cannot do, kept apart on purpose:

  1. extraction repair    re-read the selected documents with the parser
                          as it is now (document_control.PARSER_VERSION)
                          and replace their stored records -- the form
                          reading the model made, the notes, the manual
                          fields and the history are kept;
  2. reassessment         classify the repaired rows again from what they
                          now hold (only with DOCUMENT_CLASSIFICATION_V2 on);
  3. reconciliation       bring the shop drawing records and the submittal
                          map up to the repaired readings -- a separate
                          step (`--reconcile`), because it is the one that
                          touches business registers.

Dry run by default: nothing is written, a manifest says what would be.

    venv\\Scripts\\python scripts\\repair_extraction.py --project 4 --select date-references,truncated-references
    venv\\Scripts\\python scripts\\repair_extraction.py --ids 434,436 --manifest out.json
    venv\\Scripts\\python scripts\\repair_extraction.py --project 4 --select parser-outdated --apply --manifest out.json
    venv\\Scripts\\python scripts\\repair_extraction.py --rollback out.json

Selection (`--select`, any of): `date-references` (a stored record whose
reference is a date), `truncated-references` (a stored reference that
another row's reference extends past a slash, or that ends in the
reference code's segment), `parser-outdated` (a reading made under an
earlier parser), `all` (every fresh row of the project); or `--ids`.

Apply mode re-checks, row by row, that the file's hash, size and time
are what the preview saw and that the row is still fresh; a row that
moved is skipped and named. Each row is its own transaction, so a stop
leaves the rows before it repaired and the manifest says which. Running
the same repair again changes nothing: a row whose stored records equal
the re-read is skipped as "unchanged". `--rollback` puts back the
readings the manifest recorded, for rows whose current reading is still
the one the repair wrote.

Reads the files (no OCR beyond what the reader does; the page cache is
reused), calls no model, enqueues no job. Set DATABASE_URL and CACHE_ROOT
to point it at a clone and a scratch cache."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.timeutils import utc_now  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import Project, ProjectDocument  # noqa: E402
from app.services import document_control, document_sync  # noqa: E402
from app.services.document_sync import FRESH, INTAKE_ROLES  # noqa: E402

SELECTIONS = ("date-references", "truncated-references", "parser-outdated", "all")


def _records(row: ProjectDocument) -> list[dict]:
    return [r for r in ((row.extracted or {}).get("records") or []) if isinstance(r, dict)]


def _truncated_bases(rows: list[ProjectDocument]) -> set[str]:
    """References that other rows extend past a slash: "X-SD-MEP" when
    some row holds "X-SD-MEP/FA-104"."""
    bases = set()
    for row in rows:
        for record in _records(row):
            reference = str(record.get("reference") or "")
            if "/" in reference:
                bases.add(reference.split("/", 1)[0].upper())
    return bases


def select_rows(db: Session, project: Project | None, selections: set[str], ids: list[int]) -> list[tuple[ProjectDocument, list[str]]]:
    """The rows to repair, each with the reasons it was selected."""
    query = db.query(ProjectDocument).filter(ProjectDocument.state != "removed", ProjectDocument.role.notin_(INTAKE_ROLES))
    if project is not None:
        query = query.filter(ProjectDocument.project_id == project.id)
    if ids:
        query = query.filter(ProjectDocument.id.in_(ids))
    rows = query.order_by(ProjectDocument.id).all()
    bases = _truncated_bases(rows) if "truncated-references" in selections else set()
    out = []
    for row in rows:
        reasons = []
        if ids:
            reasons.append("selected by id")
        records = _records(row)
        if "date-references" in selections and any(document_control.is_date_shaped(r.get("reference")) for r in records):
            reasons.append("a stored record's reference is a date")
        if "truncated-references" in selections:
            for record in records:
                reference = str(record.get("reference") or "").upper()
                if "/" not in reference and (reference in bases or reference.rsplit("-", 1)[-1] in ("SD", "SDW", "DWG", "MAS", "MAR", "SAR", "MEP")):
                    reasons.append(f"a stored reference looks truncated ({record.get('reference')})")
                    break
        if "parser-outdated" in selections and (row.extracted or {}).get("parser_version") != document_control.PARSER_VERSION:
            reasons.append("read by an earlier parser")
        if "all" in selections:
            reasons.append("every row of the project")
        if reasons:
            out.append((row, reasons))
    return out


def reread(row: ProjectDocument, ocr: bool) -> tuple[list[dict], list[str], dict | None, dict]:
    """The row's file read again with the parser as it is now: (records as
    stored, notes, page evidence, the file's stat)."""
    path = Path(row.path)
    stat = os.stat(document_control._os_path(path))
    sha = document_sync.sha256_of(path)
    role, records, notes, timing = document_sync.extract(row.path, row.relative_path or row.filename, sha, ocr)
    root = Path(root_of(row) or "")
    relative = row.relative_path or row.filename
    stored = [document_sync._record_dict(document_control.replace(r, path=relative), root) for r in (records or ())]
    evidence = timing.pop("evidence", None) if isinstance(timing, dict) else None
    return stored, list(notes or ()), evidence, {"sha256": sha, "size": stat.st_size, "mtime": stat.st_mtime, "role_read": role}


_ROOTS: dict[int, str | None] = {}


def root_of(row: ProjectDocument) -> str | None:
    if row.project_id not in _ROOTS:
        from app.database import SessionLocal as _Session

        session = _Session()
        try:
            project = session.get(Project, row.project_id)
            _ROOTS[row.project_id] = project.source_folder_path if project else None
        finally:
            session.close()
    return _ROOTS[row.project_id]


def _summary(records: list[dict]) -> list[dict]:
    return [{"category": r.get("category"), "reference": r.get("reference"), "revision": r.get("revision"),
             "status": r.get("status"), "system": r.get("system_code"), "page": r.get("page"), "source": r.get("source"),
             "listed": list(r.get("listed") or [])} for r in records]


def _first(records: list[dict]) -> dict:
    return records[0] if records else {}


def preview(db: Session, row: ProjectDocument, reasons: list[str], ocr: bool) -> dict:
    """What repairing this row would change, without changing it."""
    entry = {"document_id": row.id, "project_id": row.project_id, "path": row.relative_path or row.filename,
             "role": row.role, "state": row.state, "reasons": reasons, "sha256": row.sha256,
             "old": {"records": _summary(_records(row)), "reference": row.reference, "revision": row.revision,
                     "status": row.status, "parser_version": (row.extracted or {}).get("parser_version")}}
    if row.state != FRESH:
        entry["outcome"] = "skipped"
        entry["skip_reason"] = f"the row is {row.state}; only a fresh reading is repaired"
        return entry
    if row.role == document_sync.ROLE_TRANSMITTAL:
        # A Word transmittal is read by the transmittal reader (transmittals.read_transmittal),
        # not by the PDF parser this repairs: nothing here applies to it.
        entry["outcome"] = "skipped"
        entry["skip_reason"] = "a transmittal is read by the transmittal reader, not the parser being repaired"
        return entry
    try:
        records, notes, evidence, stat = reread(row, ocr)
    except Exception as exc:  # noqa: BLE001 -- named in the manifest
        entry["outcome"] = "failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return entry
    entry["file"] = stat
    if stat["sha256"] != row.sha256:
        entry["outcome"] = "skipped"
        entry["skip_reason"] = "the file's content is not the content the row was read from (a sync will read it)"
        return entry
    if any(document_control.NOT_DOWNLOADED in n or n.startswith(document_control.UNREADABLE) for n in notes):
        # Not read now (online-only, damaged): an empty reading is not a repair of a full one.
        entry["outcome"] = "skipped"
        entry["skip_reason"] = "the file could not be read now: " + "; ".join(notes)[:200]
        return entry
    first = _first(records)
    # The row's reference, revision and status mirror its first record (document_sync.process);
    # with no record left there is nothing for them to mirror -- an old value would be the
    # earlier parser's mistake standing on the row after its record was repaired away.
    new = {"records": _summary(records), "reference": first.get("reference") if records else None,
           "revision": first.get("revision") if records else None,
           "status": first.get("status") if records else None,
           "parser_version": document_control.PARSER_VERSION, "notes": notes,
           "evidence_kinds": sorted({f.get("kind") for f in ((evidence or {}).get("findings") or [])})}
    # The model's reading of a form (and the reference, revision and status
    # it gave the row) outranks the page's records, as in processing; and
    # where the page gave no submittal record the reading stands in for it
    # in the log (document_sync.record_for_the_log), as processing does.
    form = (row.extracted or {}).get("form")
    if form and form.get("is_submittal"):
        new["reference"], new["revision"], new["status"] = row.reference, row.revision, row.status
        new["kept_form_reading"] = True
        from datetime import datetime, timezone

        from app.models import Project as _Project

        project = db.get(_Project, row.project_id)
        holder = {"records": records}
        if document_sync.record_for_the_log(holder, form, relative=row.relative_path or row.filename,
                                            modified=datetime.fromtimestamp(row.mtime or 0, timezone.utc),
                                            ep_number=project.ep_number if project else None):
            records = holder["records"]
            new["records"] = _summary(records)
            new["form_record_added"] = True
    entry["new"] = new
    same_records = _summary(_records(row)) == new["records"] and entry["old"]["parser_version"] == document_control.PARSER_VERSION
    entry["changed"] = {
        "records": _summary(_records(row)) != new["records"],
        "reference": (row.reference or None) != (new["reference"] or None),
        "revision": (row.revision or None) != (new["revision"] or None),
        "status": (row.status or None) != (new["status"] or None),
        "parser_version": entry["old"]["parser_version"] != document_control.PARSER_VERSION,
    }
    entry["outcome"] = "unchanged" if same_records and not any(entry["changed"][k] for k in ("reference", "revision", "status")) else "would_repair"
    entry["_records"] = records
    entry["_notes"] = notes
    entry["_evidence"] = evidence
    return entry


def apply_row(db: Session, row: ProjectDocument, entry: dict) -> None:
    """Write the repaired reading in the row's own transaction, keeping
    the form reading, the manual fields and everything the reading is not."""
    extracted = copy.deepcopy(row.extracted or {})
    entry["old"]["extracted"] = copy.deepcopy(row.extracted)
    extracted["records"] = entry.pop("_records")
    extracted["notes"] = entry.pop("_notes")
    evidence = entry.pop("_evidence", None)
    if evidence is not None:
        extracted["evidence"] = evidence
    extracted["parser_version"] = document_control.PARSER_VERSION
    extracted["repaired_at"] = utc_now().isoformat()
    row.extracted = extracted
    row.reference, row.revision, row.status = entry["new"]["reference"], entry["new"]["revision"], entry["new"]["status"]
    row.last_processed_at = utc_now()
    # What is built from it is stale until reconciled; its own log entry follows the reading.
    document_sync.mark_stale(db, row, f"{row.filename} reading repaired ({document_control.PARSER_VERSION})")
    if row.reference and extracted.get("records"):
        document_sync.depend(db, row, "log", row.reference, f"register row from {row.relative_path or row.filename}")
    document_sync.settle_from(db, row, "log")
    db.commit()
    entry["outcome"] = "repaired"
    entry["applied_at"] = extracted["repaired_at"]


def reassess(db: Session, row: ProjectDocument) -> dict | None:
    from app.services import document_classification

    if not document_classification.enabled():
        return None
    project = db.get(Project, row.project_id)
    before = document_classification.current(db, row)
    after = document_classification.assess_row(db, project, row, source="backfill")
    db.commit()
    current = after or before
    return {"before": {"type": before.primary_type, "stage": before.stage} if before else None,
            "after": {"type": current.primary_type, "stage": current.stage, "flags": (current.assessment or {}).get("flags")}
            if current else None, "written": after is not None}


def rollback(db: Session, manifest: dict) -> dict:
    """Put back the readings the manifest recorded, where the row still
    holds what the repair wrote."""
    result = {"restored": [], "skipped": []}
    for entry in manifest["entries"]:
        if entry.get("outcome") != "repaired" or "extracted" not in entry.get("old", {}):
            continue
        row = db.get(ProjectDocument, entry["document_id"])
        if row is None or row.sha256 != entry["sha256"] or (row.extracted or {}).get("repaired_at") != entry.get("applied_at"):
            result["skipped"].append({"document_id": entry["document_id"], "why": "changed since the repair"})
            continue
        row.extracted = entry["old"]["extracted"]
        row.reference, row.revision, row.status = entry["old"]["reference"], entry["old"]["revision"], entry["old"]["status"]
        document_sync.mark_stale(db, row, f"{row.filename} reading restored")
        db.commit()
        result["restored"].append(entry["document_id"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=int, help="internal project id")
    parser.add_argument("--ids", help="document ids, comma separated")
    parser.add_argument("--select", default="", help=", ".join(SELECTIONS))
    parser.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    parser.add_argument("--reassess", action="store_true", help="classify the repaired rows again (needs the flag on)")
    parser.add_argument("--reconcile", action="store_true", help="after applying: shop drawings reconciliation for the project")
    parser.add_argument("--manifest", help="where to write the manifest (JSON)")
    parser.add_argument("--rollback", help="a manifest to roll back")
    parser.add_argument("--no-ocr", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    db = SessionLocal()
    started = time.perf_counter()
    try:
        if args.rollback:
            manifest = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            result = rollback(db, manifest)
            print(json.dumps(result))
            return 0
        selections = {s.strip() for s in args.select.split(",") if s.strip()}
        unknown = selections - set(SELECTIONS)
        if unknown:
            raise SystemExit(f"unknown selection {unknown}; one of {SELECTIONS}")
        ids = [int(i) for i in args.ids.split(",")] if args.ids else []
        if not ids and not selections:
            raise SystemExit("select rows with --select or --ids")
        if not ids and args.project is None:
            raise SystemExit("--select needs --project (no project-wide reset by accident)")
        project = db.get(Project, args.project) if args.project is not None else None
        if args.project is not None and project is None:
            raise SystemExit(f"no project {args.project}")
        from app.services import submittal_scanner

        ocr = submittal_scanner.ocr_available() and not args.no_ocr
        chosen = select_rows(db, project, selections, ids)
        manifest = {"tool": "repair_extraction", "parser_version": document_control.PARSER_VERSION, "mode": "apply" if args.apply else "dry-run",
                    "database": str(engine.url).split("///")[-1], "project_id": args.project, "selection": sorted(selections) or "ids",
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": [], "counts": {}}
        counts: dict[str, int] = {}
        for row, reasons in chosen:
            entry = preview(db, row, reasons, ocr)
            if args.apply and entry["outcome"] == "would_repair":
                # Re-check the file against the preview before writing.
                stat = os.stat(document_control._os_path(Path(row.path)))
                if stat.st_size != entry["file"]["size"] or abs(stat.st_mtime - entry["file"]["mtime"]) > 1e-6:
                    entry["outcome"], entry["skip_reason"] = "skipped", "the file changed between the preview and the write"
                else:
                    db.refresh(row)
                    if row.state != FRESH or row.sha256 != entry["sha256"]:
                        entry["outcome"], entry["skip_reason"] = "skipped", "the row changed between the preview and the write"
                    else:
                        try:
                            apply_row(db, row, entry)
                            if args.reassess:
                                entry["reassessment"] = reassess(db, row)
                        except Exception as exc:  # noqa: BLE001
                            db.rollback()
                            entry["outcome"], entry["error"] = "failed", f"{type(exc).__name__}: {exc}"[:300]
            for key in ("_records", "_notes", "_evidence"):
                entry.pop(key, None)
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
            manifest["entries"].append(entry)
            print(f"{entry['outcome']:13} {row.id:5} {entry['path'][:70]}"
                  + (f"  {entry['old']['reference']} -> {entry['new']['reference']}" if entry.get("new") else "")
                  + (f"  [{entry.get('skip_reason') or entry.get('error')}]" if entry.get("skip_reason") or entry.get("error") else ""))
        if args.apply and args.reconcile and project is not None and counts.get("repaired"):
            from app.services import shop_drawings

            manifest["reconcile"] = shop_drawings.reconcile(db, project, ai=False)
            document_sync.settle(db, project, "log")
            db.commit()
        manifest["counts"] = counts
        manifest["selected"] = len(chosen)
        manifest["seconds"] = round(time.perf_counter() - started, 1)
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if args.manifest:
            Path(args.manifest).write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"mode": manifest["mode"], "selected": len(chosen), **counts, "seconds": manifest["seconds"]}))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
