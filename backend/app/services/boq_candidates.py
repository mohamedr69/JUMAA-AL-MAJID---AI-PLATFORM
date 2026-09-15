"""Re-reading the Design Sheets without replacing the BOQ.

`build` reads every sheet again with the current parser, records the runs,
and lays the lines it read against the BOQ as it stands, row by row:

  unchanged -- the same line with the same values (its source record is
               refreshed when applied; nothing on it changes)
  changed   -- the same line, read with a different quantity, group or text
  added     -- a line the sheet has that the BOQ does not
  removed   -- a line the BOQ has that the sheet no longer yields

Lines are paired conservatively. An exact pair shares its system, group,
catalog number and description (ignoring case and punctuation). A probable
pair shares the system and either the catalog number or a near-identical
description, and is marked uncertain: it is the kind of pair a person has
to confirm. A line that pairs with nothing is added or removed -- never
silently merged with the nearest thing.

`apply` writes only the changes an engineer took, after keeping the BOQ as
it was in a snapshot. Every change must be decided; a BOQ saved since the
candidate was built refuses the apply (the comparison is out of date).
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.extraction import pipeline, values
from app.models import BoqCandidate, ExtractionRun, Project, ProjectBoqItem, User
from app.services import boq_provenance, design_sheet_extractor, system_rules

PROBABLE_DESCRIPTION_RATIO = 0.82
SHEET_FIELDS = boq_provenance.SHEET_FIELDS
DECISIONS = {"accept", "keep"}


class CandidateError(Exception):
    pass


class CandidateStale(CandidateError):
    pass


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _key(line: dict) -> tuple:
    return (line.get("system_code") or "", _norm(line.get("group_heading")), _norm(line.get("catalog_no")),
            _norm(line.get("description")))


def _quantity_units(lines: list[dict]) -> int:
    total = 0
    for line in lines:
        parsed = values.parse_quantity(line.get("quantity"))
        if parsed.ok and str(parsed.value).isdigit():
            total += int(parsed.value)
    return total


def _read(path: Path, on_page=None):
    # The projects router's seam, which the test suite stubs.
    from app.routers import projects as projects_router

    return projects_router._read_design_sheet(path, on_page=on_page)


def _candidate_line(project: Project, sheet, line, run: ExtractionRun, index: int) -> dict:
    system_code = system_rules.effective_code(sheet.system_code, project)
    item = boq_provenance.extracted_item(system_code=system_code, line=line, run=run, manufacturer=None, position=index)
    record = boq_provenance.item_record(item)
    record["cid"] = f"n{index}"
    record["document_name"] = Path(run.document_path).name
    return record


def _old_line(item: ProjectBoqItem) -> dict:
    record = boq_provenance.item_record(item)
    record["status"] = ("corrected" if boq_provenance.is_corrected(item) else item.origin)
    return record


def _differences(old: dict, new: dict) -> list[str]:
    return [name for name in SHEET_FIELDS if (old.get(name) or None) != (new.get(name) or None)]


def compare(old_lines: list[dict], new_lines: list[dict]) -> tuple[list[dict], int]:
    """(the changes, how many lines are unchanged)."""
    changes: list[dict] = []
    unchanged = 0
    by_key: dict[tuple, list[dict]] = {}
    for old in old_lines:
        by_key.setdefault(_key(old), []).append(old)

    pending_new: list[dict] = []
    matched_old: set[int] = set()
    for new in new_lines:
        candidates = by_key.get(_key(new))
        if candidates:
            old = candidates.pop(0)
            matched_old.add(old["id"])
            fields = _differences(old, new)
            if fields:
                changes.append({"kind": "changed", "match": "exact", "old_id": old["id"], "before": old, "after": new,
                                "fields": fields, "reason": "same system, group, part number and description"})
            else:
                changes.append({"kind": "unchanged", "match": "exact", "old_id": old["id"], "before": old, "after": new,
                                "fields": [], "reason": ""})
                unchanged += 1
        else:
            pending_new.append(new)

    remaining_old = [old for old in old_lines if old["id"] not in matched_old]
    for new in pending_new:
        best, best_ratio, reason = None, 0.0, ""
        for old in remaining_old:
            if (old.get("system_code") or "") != (new.get("system_code") or ""):
                continue
            ratio = difflib.SequenceMatcher(None, _norm(old.get("description")), _norm(new.get("description"))).ratio()
            same_catalog = bool(_norm(old.get("catalog_no"))) and _norm(old.get("catalog_no")) == _norm(new.get("catalog_no"))
            if same_catalog and ratio >= 0.5 and ratio > best_ratio:
                best, best_ratio, reason = old, ratio, "same part number, description differs"
            elif ratio >= PROBABLE_DESCRIPTION_RATIO and ratio > best_ratio and not same_catalog:
                best, best_ratio, reason = old, ratio, f"descriptions {round(ratio * 100)}% alike, part number differs"
        if best is not None:
            remaining_old.remove(best)
            changes.append({"kind": "changed", "match": "probable", "old_id": best["id"], "before": best, "after": new,
                            "fields": _differences(best, new), "reason": reason})
        else:
            changes.append({"kind": "added", "match": None, "old_id": None, "before": None, "after": new, "fields": [],
                            "reason": "not in the BOQ"})
    for old in remaining_old:
        changes.append({"kind": "removed", "match": None, "old_id": old["id"], "before": old, "after": None, "fields": [],
                        "reason": "the sheet no longer yields this line" if old.get("origin") != "manual"
                        else "typed in by an engineer; not on the sheet"})
    for index, change in enumerate(changes, start=1):
        change["id"] = f"c{index}"
    return changes, unchanged


def build(db: Session, project: Project, user: User | None, ctx=None) -> BoqCandidate:
    """Read the project's Design Sheets again and compare. Nothing in the
    BOQ changes. `ctx` (app.services.jobs.JobContext) receives progress per
    page and stops the read between pages when a cancel was asked for."""
    if not project.design_sheets:
        raise CandidateError("The project has no Design Sheets to read.")
    for older in db.query(BoqCandidate).filter(BoqCandidate.project_id == project.id, BoqCandidate.status == "pending"):
        older.status = "superseded"
    db.commit()

    new_lines: list[dict] = []
    runs: list[ExtractionRun] = []
    sheets: list[dict] = []
    total_sheets = len(project.design_sheets)
    for index, sheet in enumerate(project.design_sheets):
        name = Path(sheet.document_path).name

        def on_page(page: int, pages: int, index=index, name=name) -> None:
            if ctx is not None:
                ctx.progress(index * 100 + round(100 * (page - 1) / max(pages, 1)), total_sheets * 100,
                             f"Reading {name}: page {page} of {pages} (sheet {index + 1} of {total_sheets})")

        if ctx is not None:
            ctx.progress(index * 100, total_sheets * 100, f"Reading {name} (sheet {index + 1} of {total_sheets})")
        result = _read(Path(sheet.document_path), on_page=on_page)
        run = pipeline.record_design_sheet_run(db, project, sheet, result, trigger="reread")
        runs.append(run)
        coverage = run.coverage or {}
        sheets.append({
            "run_id": run.id, "document_name": Path(sheet.document_path).name, "system_code": sheet.system_code,
            "outcome": run.outcome, "lines": len(result.lines), "failure": result.failure,
            "open_issues": sum(1 for i in run.issues if i.state == "open"),
            "unprocessed_pages": [p["page"] for p in coverage.get("pages", []) if p.get("detected") and not p.get("processed")],
        })
        if result.failure:
            continue
        for line in result.lines:
            new_lines.append(_candidate_line(project, sheet, line, run, len(new_lines)))

    library = boq_provenance.part_library(db)
    for record in new_lines:
        boq_provenance.check_catalog(record, library)
    old_lines = [_old_line(item) for item in project.boq_items]
    changes, unchanged = compare(old_lines, new_lines)
    summary = {
        "old_lines": len(old_lines), "new_lines": len(new_lines),
        "old_quantity": _quantity_units(old_lines), "new_quantity": _quantity_units(new_lines),
        "unchanged": unchanged,
        "changed": sum(1 for c in changes if c["kind"] == "changed"),
        "probable": sum(1 for c in changes if c["match"] == "probable"),
        "added": sum(1 for c in changes if c["kind"] == "added"),
        "removed": sum(1 for c in changes if c["kind"] == "removed"),
        "sheets": sheets,
        "failed_sheets": [s["document_name"] for s in sheets if s["failure"]],
    }
    candidate = BoqCandidate(
        project_id=project.id, status="pending", base_boq_version=project.boq_version,
        parser_version=design_sheet_extractor.PARSER_VERSION, run_ids=[r.id for r in runs], lines=new_lines,
        changes=changes, summary=summary, created_by_id=user.id if user else None,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def decisions_needed(candidate: BoqCandidate) -> list[str]:
    return [c["id"] for c in candidate.changes if c["kind"] != "unchanged"]


def _item_from(record: dict, position: int) -> ProjectBoqItem:
    item = ProjectBoqItem(position=position)
    for name in boq_provenance.CONTENT_FIELDS + boq_provenance.PROVENANCE_FIELDS:
        value = record.get(name)
        if name in ("unit_price", "total_price", "ocr_confidence") and value is not None:
            value = Decimal(str(value))
        if name in ("edited_at", "created_at") and isinstance(value, str):
            from datetime import datetime

            value = datetime.fromisoformat(value)
        setattr(item, name, value)
    return item


def apply(db: Session, project: Project, candidate: BoqCandidate, decisions: dict[str, str], user: User) -> dict:
    """Write the taken changes. Returns counts of what was applied."""
    if candidate.status != "pending":
        raise CandidateError(f"This re-read is {candidate.status}; build a new one.")
    if candidate.base_boq_version != project.boq_version:
        raise CandidateStale("The BOQ was saved after this re-read was built, so the comparison is out of date. "
                             "Re-read the sheets again to compare against the BOQ as it is now.")
    needed = decisions_needed(candidate)
    missing = [cid for cid in needed if decisions.get(cid) not in DECISIONS]
    if missing:
        raise CandidateError(f"{len(missing)} change{'s' if len(missing) != 1 else ''} still need a decision "
                             "(take the sheet's value or keep the BOQ's).")

    boq_provenance.snapshot(db, project, f"before re-read #{candidate.id}", user)
    now = utc_now()
    by_old: dict[int, dict] = {c["old_id"]: c for c in candidate.changes if c.get("old_id") is not None}
    counts = {"changed": 0, "added": 0, "removed": 0, "kept": 0, "sources_updated": 0}

    rebuilt: list[ProjectBoqItem] = []
    for item in project.boq_items:
        change = by_old.get(item.id)
        record = boq_provenance.item_record(item)
        if change is None:
            rebuilt.append(_item_from(record, len(rebuilt)))
            continue
        decision = decisions.get(change["id"], "accept" if change["kind"] == "unchanged" else "keep")
        if change["kind"] == "removed":
            if decision == "accept":
                counts["removed"] += 1
                continue
            counts["kept"] += 1
            rebuilt.append(_item_from(record, len(rebuilt)))
            continue
        after = change["after"]
        if change["kind"] == "unchanged" or decision == "accept":
            # The sheet's values and source record; the engineer's own columns
            # (manufacturer, unit, prices, remarks) stay.
            for name in SHEET_FIELDS:
                record[name] = after.get(name)
            for name in boq_provenance.PROVENANCE_FIELDS:
                if name in ("edited_by_id", "edited_at", "created_at"):
                    continue
                record[name] = after.get(name)
            record["origin"] = "extracted"
            record["extracted_values"] = {name: after.get(name) for name in SHEET_FIELDS}
            if change["kind"] == "changed":
                record["edited_by_id"], record["edited_at"] = user.id, now
                counts["changed"] += 1
            elif item.origin != "extracted" or item.extraction_run_id != after.get("extraction_run_id"):
                counts["sources_updated"] += 1
        else:
            counts["kept"] += 1
        rebuilt.append(_item_from(record, len(rebuilt)))

    for change in candidate.changes:
        if change["kind"] != "added":
            continue
        if decisions.get(change["id"]) != "accept":
            counts["kept"] += 1
            continue
        record = dict(change["after"])
        record["manufacturer"] = _brand(project, record.get("system_code"))
        record["edited_by_id"], record["edited_at"] = user.id, now
        # After the last line of its system, so it lands in its tab's order.
        at = max((i for i, it in enumerate(rebuilt) if it.system_code == record.get("system_code")), default=len(rebuilt) - 1) + 1
        rebuilt.insert(at, _item_from(record, at))
        counts["added"] += 1
    for position, item in enumerate(rebuilt):
        item.position = position

    project.boq_items = rebuilt
    project.boq_version += 1
    candidate.status = "applied"
    candidate.decided_by_id = user.id
    candidate.decided_at = now
    candidate.decisions = decisions
    db.commit()
    return counts


def _brand(project: Project, system_code: str | None) -> str | None:
    from app.routers.projects import _brand_for

    return _brand_for(system_code, project.systems, project.separate_ve_panel)


def discard(db: Session, candidate: BoqCandidate, user: User) -> None:
    if candidate.status != "pending":
        raise CandidateError(f"This re-read is already {candidate.status}.")
    candidate.status = "discarded"
    candidate.decided_by_id = user.id
    candidate.decided_at = utc_now()
    db.commit()
