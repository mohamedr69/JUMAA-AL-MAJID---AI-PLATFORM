"""What would change if an existing project were read again, before anything does.

Projects created before provenance, the intake gate and the current reader
hold BOQs no one can trace: EP-30208's 50 stored lines against the 97 its
sheet carries, EP-31112's DRF under another contractor's folder, EP-30058's
documents named for EP-30088. Repairing them starts with a report, never with
a rewrite:

  documents   -- what the intake gate finds about each stored document
  folder      -- sheets in the project folder that are not on the project,
                 stored sheets no longer there, superseded revisions attached
  reader      -- the parser version each stored line and run was read with,
                 against the current one
  boq         -- when the sheets are read: line and quantity counts before
                 and after, lines changed / added / removed, system and
                 building assignment

`build` is read-only unless `write` is set: then the intake findings are
stored and the re-read is recorded as a BOQ candidate for review on the
Re-read page -- still not applied. `backend/scripts/repair_report.py` runs it
for named projects from the command line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ExtractionRun, Project
from app.services import boq_candidates, design_sheet_extractor, document_intake, system_rules
from app.services.ep_resolver import find_design_sheet_candidates


@dataclass
class RepairReport:
    ep_number: str
    project_name: str | None
    documents: list[dict] = field(default_factory=list)
    folder: dict = field(default_factory=dict)
    reader: dict = field(default_factory=dict)
    boq: dict = field(default_factory=dict)
    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    candidate_id: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _documents(project: Project, report: RepairReport) -> None:
    settings = get_settings()
    root = Path(settings.projects_root) if settings.projects_root else None
    for role, code, path in document_intake.project_documents(project):
        record = document_intake.check(project, role, path, archive_root=root)
        blocked = [f for f in record["findings"] if f["severity"] == document_intake.BLOCKED]
        report.documents.append({"role": role, "system_code": code, "filename": path.name, "page_count": record["page_count"],
                                 "findings": record["findings"]})
        report.blocking.extend(f"{path.name}: {f['message']}" for f in blocked)


def _folder(project: Project, report: RepairReport) -> None:
    if not project.source_folder_path or not Path(project.source_folder_path).is_dir():
        report.folder = {"reachable": False}
        report.notes.append("The project folder is not reachable, so new or superseded sheets cannot be checked.")
        return
    errors: list[str] = []
    found = find_design_sheet_candidates(Path(project.source_folder_path), errors)
    stored = {Path(s.document_path).resolve() for s in project.design_sheets}
    in_folder = {c.path.resolve(): c for c in found}
    report.folder = {
        "reachable": True,
        "new_sheets": [{"filename": c.path.name, "system_code": c.system_guess, "selected": c.selected,
                        "note": c.matched_via} for p, c in in_folder.items() if p not in stored],
        "missing_sheets": [p.name for p in stored if p not in in_folder and not p.is_file()],
        "superseded_attached": [c.path.name for p, c in in_folder.items() if p in stored and not c.selected],
        "unlabelled": [Path(s.document_path).name for s in project.design_sheets if not s.system_code],
        "errors": errors,
    }
    if report.folder["superseded_attached"]:
        report.blocking.append("A superseded revision is attached: " + ", ".join(report.folder["superseded_attached"]))
    if report.folder["new_sheets"]:
        report.notes.append(f"{len(report.folder['new_sheets'])} sheet(s) in the folder are not on the project.")


def _reader(db: Session, project: Project, report: RepairReport) -> None:
    items = project.boq_items
    versions: dict[str, int] = {}
    for item in items:
        versions[item.parser_version or "none recorded"] = versions.get(item.parser_version or "none recorded", 0) + 1
    runs = db.query(ExtractionRun).filter(ExtractionRun.project_id == project.id, ExtractionRun.kind == "design_sheet").all()
    report.reader = {
        "current": design_sheet_extractor.PARSER_VERSION,
        "lines_by_parser_version": versions,
        "recorded_runs": len(runs),
        "legacy_lines": sum(1 for i in items if i.origin == "legacy"),
        "unassigned_lines": sum(1 for i in items if not i.system_code),
    }
    if report.reader["legacy_lines"]:
        report.notes.append(f"{report.reader['legacy_lines']} line(s) have no source record.")
    if report.reader["unassigned_lines"]:
        report.blocking.append(f"{report.reader['unassigned_lines']} BOQ line(s) have no system.")


def _boq(db: Session, project: Project, report: RepairReport, *, write: bool, user) -> None:
    if not project.design_sheets:
        report.boq = {"read": False, "reason": "no Design Sheets on the project"}
        return
    if write:
        candidate = boq_candidates.build(db, project, user)
        report.candidate_id = candidate.id
        changes, summary = candidate.changes, candidate.summary
    else:
        # Read without recording anything: the same comparison the candidate
        # makes, from lines built in memory.
        new_lines: list[dict] = []
        sheets = []
        for sheet in project.design_sheets:
            result = boq_candidates._read(db, project, sheet)
            sheets.append({"document_name": Path(sheet.document_path).name, "system_code": sheet.system_code,
                           "outcome": result.outcome.value, "lines": len(result.lines), "failure": result.failure,
                           "issues": len(result.issues), "buildings": [b["display"] for b in result.buildings]})
            if result.failure:
                continue
            for line in result.lines:
                from app.services import boq_provenance

                item = boq_provenance.extracted_item(system_code=system_rules.effective_code(sheet.system_code, project),
                                                     line=line, run=None, manufacturer=None, position=len(new_lines))
                record = boq_provenance.item_record(item)
                record["cid"] = f"n{len(new_lines)}"
                new_lines.append(record)
        old_lines = [boq_candidates._old_line(item) for item in project.boq_items]
        changes, unchanged = boq_candidates.compare(old_lines, new_lines)
        summary = {"old_lines": len(old_lines), "new_lines": len(new_lines),
                   "old_quantity": boq_candidates._quantity_units(old_lines),
                   "new_quantity": boq_candidates._quantity_units(new_lines), "unchanged": unchanged,
                   "changed": sum(1 for c in changes if c["kind"] == "changed"),
                   "probable": sum(1 for c in changes if c["match"] == "probable"),
                   "added": sum(1 for c in changes if c["kind"] == "added"),
                   "removed": sum(1 for c in changes if c["kind"] == "removed"), "sheets": sheets}
    system_changes = sum(1 for c in changes if c["kind"] == "changed" and "system_code" in c["fields"])
    building_changes = sum(1 for c in changes if c["kind"] == "changed" and "group_heading" in c["fields"])
    report.boq = {"read": True, **summary, "system_changes": system_changes, "group_or_building_changes": building_changes,
                  "sample": [{"kind": c["kind"], "match": c["match"], "reason": c["reason"],
                              "before": _brief(c.get("before")), "after": _brief(c.get("after"))}
                             for c in changes if c["kind"] != "unchanged"][:40]}


def _brief(line: dict | None) -> dict | None:
    if not line:
        return None
    return {k: line.get(k) for k in ("system_code", "group_heading", "catalog_no", "description", "quantity")}


def build(db: Session, project: Project, *, read_sheets: bool = True, write: bool = False, user=None) -> RepairReport:
    report = RepairReport(ep_number=project.ep_number, project_name=project.project_name)
    _documents(project, report)
    _folder(project, report)
    _reader(db, project, report)
    if write:
        document_intake.run(db, project)
    if read_sheets:
        _boq(db, project, report, write=write, user=user)
    return report
