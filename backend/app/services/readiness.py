"""Whether a project is ready to issue, and what stands in the way.

One read-only pass over everything that has to be settled before a document
leaves the platform, as checks with a status each:

  ok       -- settled
  warning  -- worth attention, does not stop an issue
  blocked  -- stops an issue until someone settles it
  unknown  -- not checked yet (documents never through the intake gate)

Checks carry a `scope`: "boq" checks gate issuing a BOQ revision; the
others (calculations, compliance, submittals) are shown on the dashboard
and gate their own outputs where they already do. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.extraction import values
from app.models import BoqCandidate, ComplianceStatement, ExtractionRun, Project, ProjectDocument
from app.services import document_intake

OK, WARNING, BLOCKED, UNKNOWN = "ok", "warning", "blocked", "unknown"
_RANK = {OK: 0, UNKNOWN: 1, WARNING: 2, BLOCKED: 3}


@dataclass
class Check:
    key: str
    label: str
    scope: str
    status: str
    summary: str
    items: list[str] = field(default_factory=list)
    count: int = 0
    link: str | None = None


def _latest_design_runs(db: Session, project: Project) -> dict[str, ExtractionRun]:
    runs = (db.query(ExtractionRun).filter(ExtractionRun.project_id == project.id, ExtractionRun.kind == "design_sheet")
            .order_by(ExtractionRun.id.desc()).all())
    latest: dict[str, ExtractionRun] = {}
    for run in runs:
        latest.setdefault(run.document_path, run)
    return latest


def _documents(db: Session, project: Project) -> Check:
    expected = document_intake.project_documents(project)
    if not expected:
        return Check("documents", "Source documents", "boq", WARNING,
                     "No DRF or Design Sheet is attached; the BOQ has no document to be checked against.", link="documents")
    rows = {(d.role, d.path): d for d in db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id)}
    unchecked = [path.name for role, _code, path in expected if (role, str(path)) not in rows]
    blocked, warned = [], []
    for row in rows.values():
        acked = {a.get("code") for a in row.acknowledged or []}
        for finding in row.findings or []:
            line = f"{row.filename}: {finding['message']}"
            if finding["severity"] == BLOCKED and finding["code"] not in acked:
                blocked.append(line)
            elif finding["severity"] == WARNING or finding["code"] in acked:
                warned.append(line + (" (accepted)" if finding["code"] in acked else ""))
    if blocked:
        return Check("documents", "Source documents", "boq", BLOCKED,
                     f"{len(blocked)} finding{'s' if len(blocked) != 1 else ''} to settle before the documents can be trusted.",
                     blocked + warned, len(blocked), "documents")
    if unchecked:
        return Check("documents", "Source documents", "boq", UNKNOWN,
                     f"{len(unchecked)} document{'s' if len(unchecked) != 1 else ''} not checked yet: run the document check.",
                     unchecked, len(unchecked), "documents")
    if warned:
        return Check("documents", "Source documents", "boq", WARNING, "Checked, with points worth a look.", warned, len(warned), "documents")
    return Check("documents", "Source documents", "boq", OK, f"{len(expected)} document{'s' if len(expected) != 1 else ''} checked.",
                 link="documents")


def _coverage(db: Session, project: Project, latest: dict[str, ExtractionRun]) -> Check:
    if not project.design_sheets:
        return Check("coverage", "Design Sheet reading", "boq", OK, "No Design Sheets to read.", link="boq")
    problems: list[str] = []
    never: list[str] = []
    for sheet in project.design_sheets:
        run = latest.get(sheet.document_path)
        name = Path(sheet.document_path).name
        if run is None:
            never.append(name)
            continue
        if run.failure:
            problems.append(f"{name}: could not be read ({run.failure})")
        pages = [p["page"] for p in (run.coverage or {}).get("pages", []) if p.get("detected") and not p.get("processed")]
        if pages:
            problems.append(f"{name}: page{'s' if len(pages) != 1 else ''} {', '.join(map(str, pages))} not read")
    if problems:
        return Check("coverage", "Design Sheet reading", "boq", BLOCKED,
                     "Part of a Design Sheet was not read, so lines may be missing from the BOQ.", problems + never, len(problems), "boq")
    if never:
        return Check("coverage", "Design Sheet reading", "boq", WARNING,
                     "Some sheets have no read on record (the BOQ predates recorded reads): re-read to check them.",
                     never, len(never), "boq/reread")
    return Check("coverage", "Design Sheet reading", "boq", OK, "Every page of every sheet was read.", link="boq")


def _issues(latest: dict[str, ExtractionRun]) -> tuple[Check, Check]:
    open_rows, proposed = [], []
    for run in latest.values():
        for issue in run.issues:
            label = f"{Path(run.document_path).name} p.{issue.page or '?'}: " + (
                (issue.detail or {}).get("description") or issue.code.replace("_", " ").lower())[:90]
            if issue.state == "proposed":
                proposed.append(label)
            elif issue.state in ("open", "starved"):
                open_rows.append(label)
    unresolved = Check("unresolved_rows", "Rows the read could not settle", "boq",
                       BLOCKED if open_rows else OK,
                       f"{len(open_rows)} row{'s' if len(open_rows) != 1 else ''} dropped or unreadable: add or reject each."
                       if open_rows else "No unresolved rows.", open_rows, len(open_rows), "boq")
    ai = Check("ai_review", "AI suggestions awaiting an engineer", "boq",
               BLOCKED if proposed else OK,
               f"{len(proposed)} AI reading{'s' if len(proposed) != 1 else ''} not yet accepted or rejected."
               if proposed else "No AI suggestion is waiting.", proposed, len(proposed), "boq")
    return unresolved, ai


def _boq_lines(project: Project) -> Check:
    items = project.boq_items
    if not items:
        return Check("boq_lines", "BOQ lines", "boq", BLOCKED, "The BOQ has no lines.", link="boq")
    unassigned = [f"{i.catalog_no or ''} {i.description[:60]}".strip() for i in items if not i.system_code]
    invalid, blank = [], []
    for item in items:
        parsed = values.parse_quantity(item.quantity)
        if parsed.status == values.EMPTY:
            blank.append(f"{item.catalog_no or ''} {item.description[:60]}".strip())
        elif not parsed.ok:
            invalid.append(f"{item.catalog_no or ''} {item.description[:50]}: '{item.quantity}' ({parsed.rule})".strip())
    legacy = sum(1 for i in items if i.origin == "legacy")
    blocked = [f"No system: {x}" for x in unassigned] + [f"Invalid quantity: {x}" for x in invalid]
    if blocked:
        return Check("boq_lines", "BOQ lines", "boq", BLOCKED,
                     ", ".join(filter(None, [f"{len(unassigned)} without a system" if unassigned else "",
                                             f"{len(invalid)} with an invalid quantity" if invalid else ""])) + ".",
                     blocked[:200], len(blocked), "boq")
    warned = [f"No quantity: {x}" for x in blank]
    if legacy:
        warned.append(f"{legacy} line{'s' if legacy != 1 else ''} stored before sources were kept: re-read the sheets to attach them.")
    if warned:
        return Check("boq_lines", "BOQ lines", "boq", WARNING, f"{len(items)} lines; some worth a look.", warned[:200], len(warned), "boq")
    return Check("boq_lines", "BOQ lines", "boq", OK, f"{len(items)} lines, each with a system and a valid quantity.", link="boq")


def _revisions(project: Project) -> Check:
    by_code: dict[str, list[str]] = {}
    for sheet in project.design_sheets:
        by_code.setdefault(sheet.system_code or "no system", []).append(Path(sheet.document_path).name)
    ambiguous = [f"{code}: {', '.join(names)}" for code, names in by_code.items() if len(names) > 1]
    unlabelled = by_code.get("no system", [])
    if ambiguous:
        return Check("sheet_revisions", "One Design Sheet per system", "boq", BLOCKED,
                     "More than one sheet is attached for a system: which revision governs is the engineer's call. "
                     "Remove the superseded one under Documents.", ambiguous, len(ambiguous), "documents")
    if unlabelled:
        return Check("sheet_revisions", "One Design Sheet per system", "boq", BLOCKED,
                     "A Design Sheet has no system, so its lines cannot be placed.", unlabelled, len(unlabelled), "documents")
    return Check("sheet_revisions", "One Design Sheet per system", "boq", OK, "Each system has one sheet.", link="documents")


def _candidates(db: Session, project: Project) -> Check:
    pending = (db.query(BoqCandidate).filter(BoqCandidate.project_id == project.id, BoqCandidate.status == "pending")
               .order_by(BoqCandidate.id.desc()).first())
    if pending is None:
        return Check("reread", "Pending re-read", "boq", OK, "No re-read waiting on a decision.", link="boq/reread")
    stale = pending.base_boq_version != project.boq_version
    return Check("reread", "Pending re-read", "boq", WARNING,
                 f"A re-read of the sheets with {len([c for c in pending.changes if c['kind'] != 'unchanged'])} differences "
                 + ("is out of date." if stale else "is waiting on decisions."), link="boq/reread")


def _calculations(db: Session, project: Project) -> Check:
    try:
        from app.routers.design import _battery_calculation

        result = _battery_calculation(db, project)
    except Exception as exc:  # noqa: BLE001 -- the dashboard must not fail because a calculation did
        return Check("battery", "Battery calculation", "calculations", UNKNOWN, f"Could not be calculated: {exc}",
                     link="calculations/battery")
    if not result.panels:
        return Check("battery", "Battery calculation", "calculations", OK, "No panels with a battery in the BOQ.",
                     link="calculations/battery")
    incomplete = [f"{p.name or p.heading}: {len(p.missing_parts)} part{'s' if len(p.missing_parts) != 1 else ''} without a current"
                  for p in result.panels if p.lower_bound]
    short = [f"{p.name or p.heading}: the quoted battery is smaller than required" for p in result.panels if p.quoted_short]
    unconfirmed = [f"{c['part_no']}: set to draw no current automatically, not yet confirmed" for c in result.needs_confirmation]
    if short:
        return Check("battery", "Battery calculation", "calculations", BLOCKED, "A quoted battery is too small.",
                     short + incomplete + unconfirmed, len(short), "calculations/battery")
    if unconfirmed:
        return Check("battery", "Battery calculation", "calculations", WARNING,
                     f"{len(unconfirmed)} automatic no-current setting{'s' if len(unconfirmed) != 1 else ''} to confirm.",
                     unconfirmed + incomplete, len(unconfirmed), "calculations/battery")
    if incomplete:
        return Check("battery", "Battery calculation", "calculations", WARNING,
                     "Some loads are lower bounds: parts have no current on file.", incomplete, len(incomplete), "calculations/battery")
    return Check("battery", "Battery calculation", "calculations", OK, f"{len(result.panels)} panel(s) calculated in full.",
                 link="calculations/battery")


def _compliance(db: Session, project: Project) -> Check:
    from app.compliance import service

    statements = db.query(ComplianceStatement).filter(ComplianceStatement.project_id == project.id,
                                                      ComplianceStatement.kind == "prepare").all()
    if not statements:
        return Check("compliance", "Compliance statements", "compliance", UNKNOWN, "No statement prepared yet.", link="compliance")
    items, blocked, warned = [], 0, 0
    for statement in statements:
        recorded = (statement.summary or {}).get("inputs") or {}
        try:
            current = service.inputs_of(project, statement.system_code, statement.spec.get("sha256"))
            moved = [k for k in ("boq_hash", "scope_hash", "knowledge_import_id") if recorded.get(k) not in (None, current[k])]
        except Exception:  # noqa: BLE001
            moved = []
        if moved and statement.approved_at is not None:
            blocked += 1
            items.append(f"{statement.system_code}: approved, but its inputs changed since ({', '.join(moved)}): needs recheck")
        elif service.is_approved(statement) and not moved:
            items.append(f"{statement.system_code}: approved")
        else:
            warned += 1
            reasons = service.approval_blockers(statement)
            items.append(f"{statement.system_code}: not approved" + (f" — {' '.join(reasons[:2])}" if reasons else ""))
    status = BLOCKED if blocked else WARNING if warned else OK
    return Check("compliance", "Compliance statements", "compliance", status,
                 f"{len(statements) - blocked - warned} of {len(statements)} approved and current.", items, blocked + warned,
                 "compliance")


def evaluate(db: Session, project: Project, *, include_calculations: bool = True) -> dict:
    latest = _latest_design_runs(db, project)
    unresolved, ai = _issues(latest)
    checks = [_documents(db, project), _revisions(project), _coverage(db, project, latest), unresolved, ai,
              _boq_lines(project), _candidates(db, project)]
    if include_calculations:
        checks.append(_calculations(db, project))
    checks.append(_compliance(db, project))
    boq = [c for c in checks if c.scope == "boq"]
    boq_blockers = [c for c in boq if c.status in (BLOCKED, UNKNOWN)]
    overall = max((c.status for c in checks), key=lambda s: _RANK[s])
    return {
        "status": overall,
        "boq_ready_for_issue": not boq_blockers,
        "boq_blockers": [f"{c.label}: {c.summary}" for c in boq_blockers],
        "checks": [asdict(c) for c in checks],
    }


def boq_blockers(db: Session, project: Project) -> list[str]:
    """What stops a BOQ revision being issued; empty when nothing does."""
    return evaluate(db, project, include_calculations=False)["boq_blockers"]
