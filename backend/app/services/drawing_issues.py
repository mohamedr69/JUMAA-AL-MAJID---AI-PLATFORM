"""Review & Issues: what on a system's shop drawings needs an engineer's eye.

An issue is held once, by key, and opened and resolved by the rules
(`reconcile`, from what the records say) or settled by an engineer
(`resolve`). Two sources, kept apart on the page: "system" -- a
deterministic finding, a fact -- and "ai" -- what the model suggested
about something the rules could not settle. A fact is never labelled as
a guess.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import DrawingIssue, Project

INFO, WARNING, ERROR = "info", "warning", "error"
SYSTEM, AI = "system", "ai"

KINDS = {
    "revision_candidate": ("New revision available", INFO),
    "revision_gap": ("Revision gap", WARNING),
    "history_inconsistent": ("Historical status inconsistency", WARNING),
    "reply_missing": ("Consultant reply missing", WARNING),
    "submission_missing": ("Submission evidence missing", WARNING),
    "reference_conflict": ("Drawing reference conflict", ERROR),
    "revision_conflict": ("Duplicate revision", ERROR),
    "system_mismatch": ("System mismatch", ERROR),
    "floor_unknown": ("Unknown floor", WARNING),
    "floor_not_in_ifc": ("Floor missing from latest IFC", WARNING),
    "source_missing": ("Source file missing", WARNING),
    "reply_unmatched": ("Consultant reply not matched", WARNING),
    "status_conflict": ("Status conflict", WARNING),
    "ai_review_required": ("AI review required", INFO),
    "ai_suggestion": ("AI review", INFO),
}


def label_of(kind: str) -> str:
    return KINDS.get(kind, (kind.replace("_", " ").capitalize(), WARNING))[0]


def severity_of(kind: str) -> str:
    return KINDS.get(kind, ("", WARNING))[1]


def reconcile(db: Session, project: Project, wanted: dict[str, dict], *, source: str = SYSTEM,
              system: str | None = None) -> dict[str, list[str]]:
    """Open the issues of `source` the records call for and resolve the
    ones they no longer do -- within `system` when one is named, so one
    system's pass never closes another's. `wanted`: key -> {kind, text,
    system_code, shop_drawing_id, floor_key, severity, detail, ai}. An
    issue an engineer resolved stays resolved while its text is the same:
    it is reopened only when it says something new. Not committed."""
    query = db.query(DrawingIssue).filter(DrawingIssue.project_id == project.id, DrawingIssue.source == source)
    if system is not None:
        query = query.filter(DrawingIssue.system_code == system)
    existing = {i.key: i for i in query}
    now = utc_now()
    opened, reopened, resolved = [], [], []
    for key, want in wanted.items():
        fields = {"kind": want["kind"], "severity": want.get("severity") or severity_of(want["kind"]),
                  "text": want["text"], "system_code": want.get("system_code"),
                  "shop_drawing_id": want.get("shop_drawing_id"), "floor_key": want.get("floor_key"),
                  "detail": want.get("detail"), "ai": want.get("ai")}
        issue = existing.get(key)
        if issue is None:
            db.add(DrawingIssue(project_id=project.id, key=key, source=source, created_at=now, updated_at=now, **fields))
            opened.append(key)
            continue
        changed = issue.text != want["text"]
        for name, value in fields.items():
            setattr(issue, name, value)
        if issue.resolved_at is not None and (changed or issue.resolved_by_id is None):
            issue.resolved_at, issue.resolved_by_id, issue.resolution = None, None, None
            reopened.append(key)
        issue.updated_at = now
    for key, issue in existing.items():
        if key not in wanted and issue.resolved_at is None:
            issue.resolved_at, issue.resolution = now, "No longer found by the rules" if source == SYSTEM else "No longer applies"
            resolved.append(key)
    db.flush()
    return {"opened": opened, "reopened": reopened, "resolved": resolved}


def open_issues(db: Session, project_id: int, system: str | None = None) -> list[DrawingIssue]:
    """The open issues, of one system (and the building's own, which
    every system shows) or of all."""
    query = db.query(DrawingIssue).filter(DrawingIssue.project_id == project_id, DrawingIssue.resolved_at.is_(None))
    if system is not None:
        query = query.filter((DrawingIssue.system_code == system) | DrawingIssue.system_code.is_(None))
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    return sorted(query.all(), key=lambda i: (order.get(i.severity, 3), i.floor_key or "", i.id))


def resolve(db: Session, issue: DrawingIssue, user, resolution: str) -> DrawingIssue:
    issue.resolved_at, issue.resolved_by_id, issue.resolution = utc_now(), user.id if user else None, resolution
    db.flush()
    return issue


def out(issue: DrawingIssue) -> dict:
    return {
        "id": issue.id, "key": issue.key, "kind": issue.kind, "label": label_of(issue.kind), "severity": issue.severity,
        "source": issue.source, "system": issue.system_code, "shop_drawing_id": issue.shop_drawing_id,
        "floor_key": issue.floor_key, "text": issue.text, "detail": issue.detail or {}, "ai": issue.ai,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
        "resolved_at": issue.resolved_at.isoformat() if issue.resolved_at else None,
        "resolution": issue.resolution,
    }
