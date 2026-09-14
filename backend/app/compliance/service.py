"""Prepare a compliance statement, fill it from the knowledge base, keep it
under review, or check a submitted one -- end to end.

Two workflows, kept apart on purpose:

A. Everything deterministic. Preparing reads the specification's clauses
   and applies the rules (a heading is not answered; definitions,
   references and related sections are "Noted"). Auto-fill looks every
   empty clause up in the compliance knowledge base (app.knowledge.autofill)
   and writes in the answers that stand on their own -- as drafts, their
   historical status proposed, never verified. Checking lays a submitted
   statement against the specification. None of this calls a model: not
   opening the tab, not preparing, not filling, not saving, not exporting,
   not checking, not when the BOQ or the knowledge base changes.

B. One clause, on request. The engineer picks a clause and asks for a
   review (app.knowledge.review); the suggestion sits beside the draft until
   they accept, edit or reject it. Accepting is not reviewing: only the
   explicit "mark reviewed" action makes a row the engineer's.

Every row carries two statuses. Its WORKFLOW status -- unfilled, autofilled
(a draft from the knowledge base), candidate (something to look at, nothing
written), ai_pending (a suggestion awaits a decision), reviewed (the
engineer's), recheck (an input changed under it) -- and its TECHNICAL
status -- complies, does not comply, partially, insufficient evidence, not
applicable -- which is proposed until an engineer marks the row reviewed.
Every change is audited with the inputs it was made against, and when the
specification, the BOQ, the scope or the knowledge base changes, the rows
that came from them are flagged for recheck, their text untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.knowledge import autofill as knowledge_autofill
from app.knowledge import policy
from app.knowledge.importer import last_import_id
from app.models import ComplianceAudit, ComplianceStatement, Project, User
from app.services.spec_finder import SYSTEMS, open_spec

from . import assist, matcher, references
from .references import Reference, fingerprint
from .spec_text import Clause, SpecText, read_bytes
from .statements import RESPONSES, read_statement
from .verify import Verdict, verify, words

# The DRF / project system rows that mean each system code.
SYSTEM_NAMES = {
    "FAS": ("Fire Alarm", "Fire Telephone"),
    "VES": ("Voice Evacuation",),
    "EML": ("Emergency Light Monitoring", "Emergency Lighting"),
    "CBS": ("Central Battery System",),
    "PAVA": ("PA/VA & BGM", "Public Address"),
}
_NOTED_HEADINGS = re.compile(
    r"^(related\s+documents|definitions?|references?|abbreviations?|acronyms|related\s+sections?|reference\s+standards|"
    r"codes\s+and\s+standards|standards|applicable\s+standards)\b",
    re.IGNORECASE,
)
_NOTED_TEXT = re.compile(
    r"^(related\s+sections?\b|division\s+\d+\s+section|section\s+\d{2}\s?\d{2}\s?\d{2}\b|drawings\s+and\s+general\s+provisions|"
    r"(nfpa|bs|en|ul|iec|iso|astm|fm|ieee|nema|ansi|tia|eia|dcd|ce)\s?[\d-]+\b.{0,120}$|(?-i:[A-Z]{2,8})\s*:\s*.{0,80}$)",
    re.IGNORECASE,
)
WORKFLOW_LABELS = {
    "unfilled": "Unfilled", "autofilled": "Auto-filled draft", "candidate": "Candidate requires review",
    "ai_pending": "AI suggestion pending review", "reviewed": "Engineer reviewed", "recheck": "Needs recheck",
}


class ComplianceError(Exception):
    pass


# --- the specification ---------------------------------------------------------


@dataclass
class SpecSource:
    path: str
    member: str | None = None
    first_page: int | None = None
    last_page: int | None = None


def uploads_root(project: Project) -> Path:
    return Path(get_settings().uploads_root) / f"EP-{project.ep_number}"


def spec_roots(project: Project) -> list[Path]:
    roots = [Path(project.source_folder_path)] if project.source_folder_path else []
    roots.append(uploads_root(project))
    return roots


def load_spec(project: Project, source: SpecSource) -> tuple[SpecText, bytes, bool]:
    for root in spec_roots(project):
        if not root.is_dir():
            continue
        content = open_spec(root, source.path, source.member)
        if content is not None:
            uploaded = root == uploads_root(project)
            try:
                spec = read_bytes(content, source.first_page or 1, source.last_page)
            except Exception as exc:  # noqa: BLE001 -- a PDF that will not open
                raise ComplianceError(f"The specification could not be read: {exc}") from exc
            return spec, content, uploaded
    raise ComplianceError("No such specification in this project")


def spec_record(source: SpecSource, spec: SpecText, uploaded: bool) -> dict:
    return {
        "path": source.path, "member": source.member, "first_page": source.first_page or 1,
        "last_page": source.last_page, "filename": Path(source.member or source.path).name, "uploaded": uploaded,
        "sha256": spec.sha256, "section_numbers": spec.section_numbers, "title": spec.title,
        "header_lines": spec.header_lines[:4], "clauses": len(spec.clauses), "warnings": spec.warnings,
    }


# --- the project's facts ---------------------------------------------------------------------


def project_brands(project: Project, system_code: str) -> set[str]:
    names = SYSTEM_NAMES.get(system_code, ())
    brands = {(s.brand or "").strip() for s in project.systems if s.name in names}
    brands |= {(i.manufacturer or "").strip() for i in project.boq_items if (i.system_code or "").upper() == system_code}
    return {b for b in brands if b}


def offered_brands(project: Project, system_code: str) -> set[str]:
    """The project's manufacturers by their canonical names ("Edwards EST4"
    and "EDWARDS" are one), so they compare with what a statement names."""
    return {policy.canonical_manufacturer(b) or b.upper() for b in project_brands(project, system_code)}


def project_facts(project: Project, system_code: str) -> str:
    system = SYSTEMS.get(system_code)
    lines = [
        f"EP number: EP-{project.ep_number}",
        f"Project: {project.project_name or '-'}",
        f"Plot: {project.plot_number or '-'}",
        f"Location: {project.location or '-'}",
        f"Client: {project.client or '-'}",
        f"Consultant: {project.consultant or '-'}",
        f"Main contractor: {project.contractor or '-'}",
        f"Scope of work: {project.scope_of_work or '-'}",
        f"System: {system.name if system else system_code} ({system_code})",
        f"Manufacturer offered: {', '.join(sorted(project_brands(project, system_code))) or 'not recorded'}",
        f"Other systems on the project: {', '.join(sorted({s.name for s in project.systems})) or '-'}",
    ]
    if project.other_information:
        lines.append(f"Other information: {project.other_information[:400]}")
    return "\n".join(lines)


def inputs_of(project: Project, system_code: str, spec_sha256: str) -> dict:
    """The inputs a statement's drafts were made against, to tell later
    whether they moved."""
    return {"spec_sha256": spec_sha256, "boq_hash": knowledge_autofill.boq_hash(project, system_code),
            "scope_hash": knowledge_autofill.scope_hash(project), "knowledge_import_id": last_import_id()}


# --- verification (rules; the model only on the explicit Verify action) ---------------------


def verify_spec(db: Session, project: Project, system_code: str, spec: SpecText, filename: str, *,
                use_ai: bool = False) -> Verdict:
    verdict = verify(project, system_code, spec, filename)
    if verdict.settled or not use_ai or not assist.available():
        return verdict
    session = assist.open_session(db, project.id, spec.sha256)
    answer = assist.verify_spec(session, project_facts(project, system_code), spec.identity_text,
                                SYSTEMS[system_code].name if system_code in SYSTEMS else system_code)
    if answer is None:
        verdict.evidence.append("The model could not be asked: " + (session.errors[-1] if session.errors else session.exhausted or "no reply"))
        return verdict
    if verdict.project == "unknown" and answer["project"] != "unknown":
        verdict.project = answer["project"]
        verdict.decided_by = "ai"
    if verdict.system == "unknown" and answer["system"] != "unknown":
        verdict.system = answer["system"]
        verdict.decided_by = "ai"
    if answer.get("reason"):
        verdict.evidence.append(f"Model: {answer['reason'][:240]}")
    if answer.get("project_named_in_spec") and answer["project_named_in_spec"] not in verdict.names_in_spec:
        verdict.names_in_spec.append(answer["project_named_in_spec"][:160])
    return verdict


# --- rows ----------------------------------------------------------------------------------------


def is_lead_in(clauses: list[Clause], index: int) -> bool:
    """A clause that only introduces the list under it."""
    clause = clauses[index]
    following = clauses[index + 1] if index + 1 < len(clauses) else None
    return (following is not None and following.level > clause.level
            and (clause.text.rstrip().endswith(":") or len(clause.text) <= 40))


def _row(clause: Clause) -> dict:
    return {"id": clause.id, "ref": clause.ref, "label": clause.label, "level": clause.level, "text": clause.text,
            "page": clause.page, "heading": clause.heading, "response": "", "remark": "", "source": "none",
            "state": "ok", "note": None, "origin": "none", "workflow": "unfilled",
            "technical": {"status": None, "origin": None, "verified": False}, "match": None, "ai_review": None}


def _answerable(row: dict) -> bool:
    return not row.get("heading") and row.get("source") != "lead_in"


LEGACY_NOTE = "Filled by the earlier past-statement matcher, before the knowledge base rules: check it."


def upgrade_row(row: dict) -> dict:
    """A row saved before rows carried a workflow and technical status, in
    the current shape. Its response is kept as it was; where it came from
    decides the status it gets. An answer the earlier similarity matcher
    wrote was never checked against the eligibility rules, so it becomes a
    candidate for review, not a draft."""
    if "workflow" in row:
        return row
    row = dict(row)
    source = row.get("source") or "none"
    for stale in ("reference", "similar", "suggestion"):
        row.pop(stale, None)
    row.update(match=None, ai_review=None, technical={"status": None, "origin": None, "verified": False})
    if row.get("heading") or source == "lead_in" or not row.get("response"):
        row.update(origin="none", workflow="unfilled", state="ok")
        if source in ("reference", "ai", "none"):
            row["source"] = "none" if not row.get("heading") and source != "lead_in" else source
    elif source == "rule":
        row.update(origin="rule", workflow="autofilled", state="ok",
                   technical={"status": "not_applicable", "origin": "rule", "verified": False})
    elif source == "engineer":
        row.update(origin="manual", workflow="autofilled", state="ok")
    else:  # "reference" (the earlier matcher) or "ai" (the earlier bulk fill)
        row.update(origin="legacy", workflow="candidate", state="review", note=LEGACY_NOTE)
    return row


def rows_of(statement: ComplianceStatement) -> list[dict]:
    """The statement's rows as copies, in the current shape."""
    return [upgrade_row(dict(row)) for row in statement.rows]


def _audit(db: Session, project: Project, statement: ComplianceStatement, row: dict, *, action: str, origin: str,
           previous: dict, user: User | None, **extra) -> None:
    inputs = (statement.summary or {}).get("inputs") or {}
    db.add(ComplianceAudit(
        project_id=project.id, statement_id=statement.id, clause_id=row["id"], action=action, origin=origin,
        spec_sha256=statement.spec.get("sha256"), boq_hash=inputs.get("boq_hash"), scope_hash=inputs.get("scope_hash"),
        knowledge_import_id=inputs.get("knowledge_import_id"),
        knowledge_record_ids=extra.pop("knowledge_record_ids", None),
        previous_response=previous.get("response"), current_response=row.get("response"),
        previous_status=(previous.get("technical") or {}).get("status"), current_status=(row.get("technical") or {}).get("status"),
        review_status=row.get("workflow"), user_id=user.id if user else None, **extra,
    ))


def _snapshot(row: dict) -> dict:
    return {"response": row.get("response"), "remark": row.get("remark"), "technical": dict(row.get("technical") or {})}


# --- prepare ---------------------------------------------------------------------------------------


def prepare(db: Session, project: Project, system_code: str, source: SpecSource, user: User | None) -> ComplianceStatement:
    """Read the specification and apply the rules. Every other row is left
    unfilled for auto-fill and the engineer. No model is called."""
    spec, _content, uploaded = load_spec(project, source)
    if not spec.clauses:
        raise ComplianceError(spec.warnings[0] if spec.warnings else "No clauses could be read from the specification.")
    verdict = verify_spec(db, project, system_code, spec, Path(source.member or source.path).name, use_ai=False)

    rows: list[dict] = []
    heading_text = ""
    for index, clause in enumerate(spec.clauses):
        row = _row(clause)
        rows.append(row)
        if clause.level <= 1:
            heading_text = clause.text
        if clause.heading:
            row["source"] = "heading"
            continue
        if is_lead_in(spec.clauses, index):
            # "Include the following features:" is answered by its items.
            row["source"] = "lead_in"
            continue
        if _NOTED_HEADINGS.match(heading_text) or _NOTED_TEXT.match(clause.text):
            row.update(response="Noted", source="rule", origin="rule", workflow="autofilled",
                       note="Informative clause: definitions, references or related sections.",
                       technical={"status": "not_applicable", "origin": "rule", "verified": False})

    statement = ComplianceStatement(
        project_id=project.id, kind="prepare", system_code=system_code,
        spec=spec_record(source, spec, uploaded), verification=verdict.as_dict(), rows=rows, reference_files=[],
        summary=summarize(rows, [], inputs=inputs_of(project, system_code, spec.sha256)), ai_calls=0,
        created_by_id=user.id if user else None,
    )
    db.add(statement)
    db.commit()
    db.refresh(statement)
    return statement


def summarize(rows: list[dict], notes: list[str] | None = None, **extra) -> dict:
    answerable = [r for r in rows if _answerable(r)]
    by_source: dict[str, int] = {}
    by_response: dict[str, int] = {}
    by_workflow: dict[str, int] = {}
    for row in answerable:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        key = row.get("response") or "unanswered"
        by_response[key] = by_response.get(key, 0) + 1
        by_workflow[row.get("workflow", "unfilled")] = by_workflow.get(row.get("workflow", "unfilled"), 0) + 1
    return {"clauses": len(answerable), "by_source": by_source, "by_response": by_response, "by_workflow": by_workflow,
            "review": sum(1 for r in answerable if r.get("workflow") in ("candidate", "ai_pending", "recheck")),
            "notes": notes or [], **extra}


def _resummarize(statement: ComplianceStatement, notes: list[str] | None = None) -> None:
    kept = {k: v for k, v in statement.summary.items()
            if k not in ("clauses", "by_source", "by_response", "by_workflow", "review", "notes")}
    statement.summary = summarize(statement.rows, notes if notes is not None else statement.summary.get("notes"), **kept)


# --- auto-fill from the knowledge base ------------------------------------------------------------


def autofill(db: Session, project: Project, statement: ComplianceStatement, user: User | None) -> ComplianceStatement:
    """Fill the empty rows the knowledge base can answer. Rows the engineer
    answered, reviewed, or has a suggestion waiting on are left alone. No
    model is called."""
    if statement.kind != "prepare":
        raise ComplianceError("Only a prepared statement can be filled")
    rows = rows_of(statement)
    chosen = [r for r in rows if _answerable(r) and not r.get("response") and r.get("workflow") in ("unfilled", "candidate")
              and not (r.get("ai_review") or {}).get("suggestion")]
    counts = {"filled": 0, "flagged": 0, "candidates": 0, "unmatched": 0, "blocked": 0}
    if chosen:
        assessments = knowledge_autofill.assess(db, project, statement.system_code, [(r["id"], r["text"]) for r in chosen])
        for row in chosen:
            assessment = assessments.get(row["id"])
            if assessment is None:
                continue
            row["match"] = assessment.match
            previous = _snapshot(row)
            if assessment.result in ("eligible", "flagged"):
                clean = assessment.result == "eligible"
                row.update(response=assessment.response, remark=assessment.remark, source="database", origin="database",
                           workflow="autofilled" if clean else "candidate", state="ok" if clean else "review",
                           note=None if clean else "Past record flagged in the source: check its PDF page before relying on it.",
                           technical={"status": assessment.technical_status, "origin": "historical", "verified": False})
                counts["filled" if clean else "flagged"] += 1
                _audit(db, project, statement, row, action="autofill", origin="database", previous=previous, user=user,
                       knowledge_record_ids=", ".join(i for i in (assessment.match.get("requirement_id"), assessment.match.get("response_id")) if i),
                       detail={"result": assessment.result})
            elif assessment.result == "none":
                row.update(workflow="unfilled", state="ok")
                counts["unmatched"] += 1
            else:
                row.update(workflow="candidate", state="review")
                counts["candidates" if assessment.result == "candidate" else "blocked"] += 1
    if not chosen:
        notes = ["Auto-fill had nothing to look up: every clause already has a response, a review in progress, or a suggestion waiting."]
    else:
        notes = [f"Auto-fill ({len(chosen)} empty row{'s' if len(chosen) != 1 else ''} looked up): {counts['filled']} filled, "
                 f"{counts['flagged']} filled from flagged records (verify the source), {counts['candidates']} candidates to review, "
                 f"{counts['blocked']} blocked (BOQ, scope or conflict), {counts['unmatched']} with no match."]
    statement.rows = rows
    statement.summary = {**statement.summary, "autofill": counts, "inputs": inputs_of(project, statement.system_code, statement.spec["sha256"])}
    _resummarize(statement, notes)
    db.commit()
    db.refresh(statement)
    return statement


def use_answer(db: Session, project: Project, statement: ComplianceStatement, clause_id: str, response_id: str,
               user: User | None) -> ComplianceStatement:
    """The engineer picks one of the past answers the knowledge base showed
    for a clause, and it becomes the row's draft. Only an answer that was
    shown for this clause can be picked; no model is called."""
    from app.models import KnowledgeResponse

    rows = rows_of(statement)
    row = next((r for r in rows if r["id"] == clause_id), None)
    if row is None or not _answerable(row):
        raise ComplianceError("No such clause")
    match = row.get("match") or {}
    shown = {match.get("response_id")} | {c.get("response_id") for c in match.get("candidates") or []}
    if response_id not in shown - {None}:
        raise ComplianceError("That answer was not offered for this clause")
    record = db.get(KnowledgeResponse, response_id)
    if record is None:
        raise ComplianceError("That answer is no longer in the knowledge base")
    response, remark, technical = knowledge_autofill.draft_of(record.historical_compliance_status, record.historical_response, record.remarks)
    if not response:
        raise ComplianceError("That past answer has no response the statement can carry; write the row yourself")
    previous = _snapshot(row)
    row.update(response=response, remark=remark, source="database", origin="database", workflow="autofilled", state="ok",
               note=f"Past answer {response_id} chosen by the engineer.",
               technical={"status": technical, "origin": "historical", "verified": False})
    _audit(db, project, statement, row, action="use_answer", origin="manual", previous=previous, user=user,
           knowledge_record_ids=f"{record.requirement_id}, {response_id}")
    statement.rows = rows
    _resummarize(statement)
    db.commit()
    db.refresh(statement)
    return statement


# --- the engineer's own actions ----------------------------------------------------------------------


def update_rows(db: Session, project: Project, statement: ComplianceStatement, changes: list[dict], user: User | None) -> ComplianceStatement:
    """The engineer's own answers: they replace whatever proposed the row.
    Typing is not reviewing -- the row's workflow status is left for the
    explicit action, except that an unfilled row becomes the engineer's."""
    by_id = {row["id"]: row for row in rows_of(statement)}
    for change in changes:
        row = by_id.get(change.get("id"))
        if row is None or row.get("heading"):
            continue
        if row.get("source") == "lead_in" and not (change.get("response") or change.get("remark")):
            continue
        previous = _snapshot(row)
        if "response" in change and change["response"] is not None:
            row["response"] = change["response"]
        if "remark" in change and change["remark"] is not None:
            row["remark"] = change["remark"][:500]
        if "technical_status" in change and change["technical_status"] in policy.TECHNICAL_STATUSES:
            row["technical"] = {"status": change["technical_status"], "origin": "engineer", "verified": row.get("workflow") == "reviewed"}
        row["source"] = "engineer"
        row["origin"] = "manual"
        row["state"] = "ok"
        if row.get("workflow") in ("unfilled", "candidate", "autofilled", "recheck"):
            row["workflow"] = "unfilled" if not row["response"] else row["workflow"] if row["workflow"] == "reviewed" else "autofilled"
        _audit(db, project, statement, row, action="manual", origin="manual", previous=previous, user=user)
        by_id[row["id"]] = row
    statement.rows = [by_id[row["id"]] for row in statement.rows]
    _resummarize(statement)
    db.commit()
    db.refresh(statement)
    return statement


def mark_reviewed(db: Session, project: Project, statement: ComplianceStatement, clause_id: str, user: User | None,
                  *, reviewed: bool = True) -> ComplianceStatement:
    """The explicit review action: the row and its technical status become
    the engineer's. Undone, the row goes back to a draft."""
    rows = rows_of(statement)
    row = next((r for r in rows if r["id"] == clause_id), None)
    if row is None or not _answerable(row):
        raise ComplianceError("No such clause")
    if reviewed and not row.get("response"):
        raise ComplianceError("Give the clause a response before marking it reviewed")
    previous = _snapshot(row)
    technical = dict(row.get("technical") or {})
    if reviewed:
        row["workflow"] = "reviewed"
        row["technical"] = {"status": technical.get("status"), "origin": "engineer", "verified": True}
    else:
        row["workflow"] = "autofilled" if row.get("origin") in ("database", "ai") else "unfilled" if not row.get("response") else "autofilled"
        row["technical"] = {**technical, "verified": False}
    row["state"] = "ok"
    _audit(db, project, statement, row, action="reviewed" if reviewed else "unreviewed", origin="manual", previous=previous, user=user)
    statement.rows = rows
    _resummarize(statement)
    db.commit()
    db.refresh(statement)
    return statement


def decide_suggestion(db: Session, project: Project, statement: ComplianceStatement, clause_id: str, action: str,
                      user: User | None, *, response: str | None = None, remark: str | None = None) -> ComplianceStatement:
    """Accept, edit or reject the model's suggestion for a row. Accepting
    writes it in as the draft -- it does not make the row reviewed."""
    rows = rows_of(statement)
    row = next((r for r in rows if r["id"] == clause_id), None)
    if row is None or not _answerable(row):
        raise ComplianceError("No such clause")
    review = dict(row.get("ai_review") or {})
    suggestion = review.get("suggestion")
    if not suggestion:
        raise ComplianceError("There is no suggestion to decide on")
    previous = _snapshot(row)
    if action in ("accept", "edit"):
        chosen_response = response if action == "edit" and response is not None else suggestion["suggested_response"]
        if chosen_response not in RESPONSES:
            raise ComplianceError("Unknown response")
        row.update(response=chosen_response,
                   remark=(remark if action == "edit" and remark is not None else suggestion["suggested_remark"])[:500],
                   source="ai", origin="ai", state="ok", workflow="ai_pending",
                   technical={"status": suggestion["proposed_compliance_status"], "origin": "ai", "verified": False})
        review["decision"] = "edited" if action == "edit" else "accepted"
    elif action == "reject":
        review["decision"] = "rejected"
        if row.get("workflow") == "ai_pending":
            row["workflow"] = review.get("previous_workflow") or ("autofilled" if row.get("response") else "unfilled")
    else:
        raise ComplianceError("Unknown decision")
    row["ai_review"] = review
    _audit(db, project, statement, row, action=f"ai_{review['decision']}", origin="ai", previous=previous, user=user,
           ai_model=review.get("model"), ai_prompt_version=review.get("prompt_version"))
    statement.rows = rows
    _resummarize(statement)
    db.commit()
    db.refresh(statement)
    return statement


def record_review(db: Session, project: Project, statement: ComplianceStatement, row: dict, user: User | None) -> None:
    """The audit line for a review the model just gave (or failed to)."""
    review = row.get("ai_review") or {}
    _audit(db, project, statement, row, action="ai_review", origin="ai", previous=_snapshot(row), user=user,
           ai_model=review.get("model"), ai_prompt_version=review.get("prompt_version"),
           ai_usage={"calls": review.get("calls"), "from_cache": review.get("from_cache"), "status": review.get("status")},
           detail={"error": review.get("error")} if review.get("error") else None)
    db.commit()


# --- inputs that moved ---------------------------------------------------------------------------------


def recheck(db: Session, project: Project, statement: ComplianceStatement) -> ComplianceStatement:
    """Compare the statement's drafts with the inputs they were made against.
    When the BOQ, the scope or the knowledge base has changed, every row
    that came from the knowledge base or the model is flagged for recheck --
    its text kept, nothing rewritten, no model called."""
    if statement.kind != "prepare":
        return statement
    if any("workflow" not in row for row in statement.rows):
        # Saved before rows carried their statuses: bring it up to date once.
        statement.rows = rows_of(statement)
        statement.summary = {**(statement.summary or {}),
                             "inputs": (statement.summary or {}).get("inputs") or inputs_of(project, statement.system_code, statement.spec["sha256"])}
        legacy = sum(1 for row in statement.rows if row.get("origin") == "legacy")
        _resummarize(statement, [f"{legacy} answer{'s' if legacy != 1 else ''} from the earlier past-statement matcher kept and "
                                 "marked for review; empty clauses can now be auto-filled from the knowledge base."] if legacy else [])
        db.commit()
        db.refresh(statement)
    recorded = (statement.summary or {}).get("inputs") or {}
    current = inputs_of(project, statement.system_code, statement.spec["sha256"])
    changed = [k for k in ("boq_hash", "scope_hash", "knowledge_import_id") if recorded.get(k) not in (None, current[k])]
    if not changed:
        return statement
    rows = rows_of(statement)
    flagged = 0
    for row in rows:
        if row.get("origin") in ("database", "ai") and row.get("workflow") not in ("recheck", "reviewed"):
            previous = _snapshot(row)
            row["workflow"] = "recheck"
            row["state"] = "review"
            row["note"] = "Inputs changed since this draft: " + ", ".join(
                {"boq_hash": "the BOQ", "scope_hash": "the scope of work", "knowledge_import_id": "the knowledge base"}[k] for k in changed)
            _audit(db, project, statement, row, action="recheck", origin="system", previous=previous, user=None,
                   detail={"changed": changed})
            flagged += 1
    statement.rows = rows
    statement.summary = {**statement.summary, "inputs": current}
    notes = list(statement.summary.get("notes") or [])
    if flagged:
        notes.append(f"{flagged} row{'s' if flagged != 1 else ''} flagged for recheck: "
                     + ", ".join({"boq_hash": "the BOQ", "scope_hash": "the scope of work", "knowledge_import_id": "the knowledge base"}[k] for k in changed)
                     + " changed.")
    _resummarize(statement, notes[-5:])
    db.commit()
    db.refresh(statement)
    return statement


# --- check -----------------------------------------------------------------------------


_HARD_REQUIREMENT = re.compile(
    r"\d|\bUL\b|\bFM\b|\bEN\s?54|\blisted\b|\bapproved\b|\bminimum\b|\bmaximum\b|not\s+less|not\s+exceed|"
    r"manufactur|\bIP\s?\d\d|\bhours?\b|\bdB\b|\bvolt|\bamp|\bbrand\b|\bmodel\b",
    re.IGNORECASE,
)


def statement_files(project: Project) -> list[dict]:
    """Compliance statements in the project's own folders, to check."""
    found = []
    for root in spec_roots(project):
        if not root.is_dir():
            continue
        for path in references.candidates(root):
            found.append({"path": str(path.relative_to(root)), "filename": path.name,
                          "uploaded": root == uploads_root(project)})
    return sorted(found, key=lambda f: f["path"])


def open_statement_file(project: Project, relative: str) -> tuple[bytes, str]:
    for root in spec_roots(project):
        folder = root.resolve()
        path = (folder / relative).resolve()
        if root.is_dir() and path.is_relative_to(folder) and path.is_file():
            return path.read_bytes(), path.name
    raise ComplianceError("No such statement in this project")


def _finding(code: str, severity: str, message: str) -> dict:
    return {"code": code, "severity": severity, "message": message}


def check(db: Session, project: Project, system_code: str, source: SpecSource, statement_bytes: bytes,
          statement_name: str, user: User | None) -> ComplianceStatement:
    """Lay a submitted statement against the specification: a clause with
    no row is missing, a row with no answer is unanswered, a header that
    names another project or manufacturer is wrong. Coverage and identity
    only -- no model is called."""
    spec, _content, uploaded = load_spec(project, source)
    if not spec.clauses:
        raise ComplianceError(spec.warnings[0] if spec.warnings else "No clauses could be read from the specification.")
    try:
        submitted = read_statement(statement_bytes, statement_name)
    except ValueError as exc:
        raise ComplianceError(str(exc)) from exc
    if submitted is None:
        raise ComplianceError("No compliance table could be found in the statement.")
    verdict = verify_spec(db, project, system_code, spec, Path(source.member or source.path).name, use_ai=False)

    general: list[dict] = []
    if verdict.project == "different":
        general.append(_finding("spec_other_project", "error",
                                "The specification is not this project's: " + " ".join(verdict.evidence[:1])))
    if verdict.system == "different":
        general.append(_finding("spec_other_system", "error", "The specification is not for this system."))

    title = " ".join(submitted.title_lines)
    brands = offered_brands(project, system_code)
    other_brands = {policy.canonical_manufacturer(w) for w in re.findall(r"[A-Za-z][A-Za-z\-]+", title)} - {None} - brands
    if brands and other_brands:
        general.append(_finding("statement_other_manufacturer", "error",
                                f"The statement's header names {', '.join(b.title() for b in sorted(other_brands))}; "
                                f"the project offers {', '.join(b.title() for b in sorted(brands))}."))
    name_match = re.search(r"project\s*(name)?\s*[:\-]\s*(.+?)(?:\s{2,}|$)", title, re.IGNORECASE)
    if name_match:
        written = words(name_match.group(2))
        own = words(project.project_name) | words(project.client) | words(project.location)
        if written and own and not written & own:
            general.append(_finding("statement_other_project", "error",
                                    f"The statement is headed for \"{name_match.group(2)[:120]}\", not "
                                    f"{project.project_name or 'this project'}."))

    as_reference = Reference(
        path=statement_name, systems=[system_code], title=submitted.title_lines,
        rows=[[r.label, r.text, r.response, r.remark] for r in submitted.rows],
        fingerprints=[fingerprint(r.text) if len(r.text) >= 6 else "" for r in submitted.rows], mtime=0, size=0,
    )
    aligned = matcher.match(spec.clauses, [matcher.RankedReference(as_reference, 0, 1.0)], hint_below=0.72,
                            require_answer=False)

    rows: list[dict] = []
    for index, clause in enumerate(spec.clauses):
        row = _row(clause)
        row["findings"] = []
        rows.append(row)
        if clause.heading:
            row["source"] = "heading"
            continue
        lead_in = is_lead_in(spec.clauses, index)
        found = aligned.get(clause.id)
        if found is None:
            if lead_in:
                row["source"] = "lead_in"
                continue
            # A one-word list item ("Time", "Date") is often folded into the
            # row above it; worth a look, not an error.
            severity = "warning" if len(clause.text) < 25 else "error"
            row["findings"].append(_finding("missing", severity, "The statement does not answer this clause."))
            row["source"] = "missing"
            continue
        row.update(response=found.answer or "", remark=found.remark, source="statement",
                   reference={"label": found.reference_label, "text": found.reference_text,
                              "response": found.response, "similarity": found.similarity})
        if not found.answer and not lead_in:
            row["findings"].append(_finding("unanswered", "error", "The clause is in the statement but has no answer."))
        if found.similarity < 0.95:
            row["findings"].append(_finding("altered", "info",
                                            f"The clause is worded differently in the statement ({round(found.similarity * 100)}% alike)."))
        foreign = {policy.canonical_manufacturer(w) for w in re.findall(r"[A-Za-z][A-Za-z\-]+", found.remark or "")} - {None} - brands
        if brands and foreign:
            row["findings"].append(_finding("other_manufacturer", "warning",
                                            f"The remark names {', '.join(b.title() for b in sorted(foreign))}."))
        if found.answer == "Comply" and _HARD_REQUIREMENT.search(clause.text):
            row["findings"].append(_finding("hard_requirement", "info",
                                            "States a value, listing or product: check the answer against the BOQ."))

    for row in rows:
        row["state"] = "review" if any(f["severity"] in ("error", "warning") for f in row.get("findings", [])) else "ok"
    counts: dict[str, int] = {}
    for f in general + [f for r in rows for f in r.get("findings", [])]:
        counts[f["code"]] = counts.get(f["code"], 0) + 1
    matched = {(f.reference_label, f.reference_text) for f in aligned.values()}
    extra_rows = sum(1 for r in submitted.answered if (r.label, r.text[:400]) not in matched)

    statement = ComplianceStatement(
        project_id=project.id, kind="check", system_code=system_code, spec=spec_record(source, spec, uploaded),
        verification=verdict.as_dict(), rows=rows, reference_files=[],
        summary={**summarize(rows, []), "general": general, "finding_counts": counts,
                 "statement_rows": len(submitted.rows), "statement_answered": len(submitted.answered),
                 "rows_not_in_spec": max(0, extra_rows)},
        statement_name=statement_name, ai_calls=0, created_by_id=user.id if user else None,
    )
    db.add(statement)
    db.commit()
    db.refresh(statement)
    return statement
