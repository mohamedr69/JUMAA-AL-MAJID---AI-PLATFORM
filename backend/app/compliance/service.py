"""Prepare a compliance statement, or check one, end to end.

Prepare, for each clause of the specification, in this order -- the first
that answers wins, and the model is the last resort:

1. a heading is not answered;
2. a rule: definitions, references, related sections and the standards lists
   are "Noted";
3. a past statement answered the same clause (word-level similarity at least
   COMPLIANCE_REUSE_SIMILARITY) with "Comply" or "Noted", and its remark names
   no other manufacturer -- reused as it stands;
4. everything else -- a clause no past statement answered, or answered with
   something that depends on the project ("Not applicable", "By others", a
   deviation) -- goes to the model in batches, with the past answer as a hint
   and the project's BOQ as the facts;
5. with no model, or a model that did not answer, the nearest past answer is
   offered for review, or the row is left for the engineer.

Check lays a submitted statement against the specification: a clause with no
row is missing, a row with no answer is unanswered, a header that names
another project or manufacturer is wrong. Only answers to clauses that set a
hard requirement are sent to the model, to judge against the BOQ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ComplianceStatement, Project, User
from app.services.spec_finder import SYSTEMS, open_spec

from . import assist, matcher, references
from .references import Reference, fingerprint
from .spec_text import Clause, SpecText, read_bytes
from .statements import canonical, read_statement
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
_HARD_REQUIREMENT = re.compile(
    r"\d|\bUL\b|\bFM\b|\bEN\s?54|\blisted\b|\bapproved\b|\bminimum\b|\bmaximum\b|not\s+less|not\s+exceed|"
    r"manufactur|\bIP\s?\d\d|\bhours?\b|\bdB\b|\bvolt|\bamp|\bbrand\b|\bmodel\b",
    re.IGNORECASE,
)
MAX_REVIEW_CLAUSES = 60


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


# --- the project's facts, as the model is given them ---------------------------------


def project_brands(project: Project, system_code: str) -> set[str]:
    names = SYSTEM_NAMES.get(system_code, ())
    brands = {(s.brand or "").strip() for s in project.systems if s.name in names}
    brands |= {(i.manufacturer or "").strip() for i in project.boq_items if (i.system_code or "").upper() == system_code}
    return {b for b in brands if b}


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


def boq_text(project: Project, system_code: str, limit_chars: int = 3500) -> str:
    items = [i for i in project.boq_items if (i.system_code or "").upper() == system_code]
    if not items:
        return "No BOQ lines are recorded for this system."
    lines, heading = [], None
    for item in sorted(items, key=lambda i: i.position):
        if item.group_heading and item.group_heading != heading:
            heading = item.group_heading
            lines.append(f"# {heading}")
        parts = [item.catalog_no or "", item.description or "", f"qty {item.quantity}" if item.quantity else ""]
        lines.append(" | ".join(p for p in parts if p))
    text = "\n".join(lines)
    return text if len(text) <= limit_chars else text[:limit_chars] + "\n(... more lines not shown)"


# --- verification --------------------------------------------------------------------


def verify_spec(db: Session, project: Project, system_code: str, spec: SpecText, filename: str, *,
                use_ai: bool) -> Verdict:
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


# --- prepare ---------------------------------------------------------------------------


def is_lead_in(clauses: list[Clause], index: int) -> bool:
    """A clause that only introduces the list under it."""
    clause = clauses[index]
    following = clauses[index + 1] if index + 1 < len(clauses) else None
    return (following is not None and following.level > clause.level
            and (clause.text.rstrip().endswith(":") or len(clause.text) <= 40))


def _row(clause: Clause) -> dict:
    return {"id": clause.id, "ref": clause.ref, "label": clause.label, "level": clause.level, "text": clause.text,
            "page": clause.page, "heading": clause.heading, "response": "", "remark": "", "source": "none",
            "state": "ok", "reference": None, "note": None}


def _reference_info(found: matcher.ClauseMatch) -> dict:
    return {"path": found.reference_path, "label": found.reference_label, "text": found.reference_text,
            "response": found.response, "remark": found.remark, "similarity": found.similarity,
            "agreeing": found.agreeing, "disagreeing": found.disagreeing}


def _payload(row: dict) -> dict:
    ref = row.get("reference")
    hint = None
    if ref and ref.get("response"):
        hint = ref["response"] + (f" ({ref['remark']})" if ref.get("remark") else "")
    return {"id": row["id"], "ref": row["ref"], "text": row["text"], "hint": hint}


def ask_model(db: Session, project: Project, system_code: str, spec_sha256: str, rows: list[dict], *,
              provider=None, apply: bool = True) -> tuple[int, list[str]]:
    """Ask the model about `rows`, in batches, and write its answers into
    them (source "ai"). Returns (calls made, notes for the engineer)."""
    session = assist.open_session(db, project.id, spec_sha256, provider)
    facts, boq = project_facts(project, system_code), boq_text(project, system_code)
    size = assist.batch_size()
    notes: list[str] = []
    for start in range(0, len(rows), size):
        batch = rows[start:start + size]
        answers = assist.answer_clauses(session, facts, boq, [_payload(row) for row in batch])
        for row in batch:
            answer = answers.get(row["id"])
            if answer and apply:
                row.update(response=answer["response"], remark=answer["remark"], source="ai",
                           state="review" if answer["response"] in ("Deviation", "Clarification required") else "ok")
            elif answer:
                row["suggestion"] = {"response": answer["response"], "remark": answer["remark"]}
        if session.exhausted:
            notes.append(f"The model's budget ran out ({session.exhausted}); the remaining clauses are left for review.")
            break
    if session.errors:
        notes.append(f"{len(session.errors)} model call(s) failed: {session.errors[-1]}")
    return session.calls, notes


def prepare(db: Session, project: Project, system_code: str, source: SpecSource, user: User | None, *,
            use_ai: bool = True, provider=None) -> ComplianceStatement:
    settings = get_settings()
    spec, _content, uploaded = load_spec(project, source)
    if not spec.clauses:
        raise ComplianceError(spec.warnings[0] if spec.warnings else "No clauses could be read from the specification.")
    verdict = verify_spec(db, project, system_code, spec, Path(source.member or source.path).name, use_ai=use_ai)

    brands = {b.lower() for b in project_brands(project, system_code)}
    pool = references.references(system_code)
    ranked = matcher.rank(spec.clauses, pool, brand=next(iter(brands), None))
    found = matcher.match(spec.clauses, ranked, hint_below=settings.compliance_hint_similarity)

    rows: list[dict] = []
    pending: list[dict] = []
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
            row.update(response="Noted", source="rule", note="Informative clause: definitions, references or related sections.")
            continue
        match = found.get(clause.id)
        if match is not None:
            row["reference"] = _reference_info(match)
            foreign = matcher.brands_in(match.remark) - brands
            if (match.similarity >= settings.compliance_reuse_similarity and match.answer in ("Comply", "Noted")
                    and not foreign):
                row.update(response=match.answer, remark=match.remark, source="reference")
                continue
        pending.append(row)

    ai_calls = 0
    notes: list[str] = []
    if pending and use_ai and assist.available(provider):
        ai_calls, notes = ask_model(db, project, system_code, spec.sha256, pending, provider=provider)
    elif pending and use_ai:
        notes.append("AI assistance is not available; clauses without a past answer are left for review.")

    for row in pending:
        if row["source"] != "none":
            continue
        ref = row["reference"]
        if ref:
            row.update(response=canonical(ref["response"]) or "", remark=ref["remark"], source="reference", state="review")
        else:
            row["state"] = "review"

    statement = ComplianceStatement(
        project_id=project.id, kind="prepare", system_code=system_code,
        spec=spec_record(source, spec, uploaded), verification=verdict.as_dict(), rows=rows,
        reference_files=[{"path": r.reference.path, "shared": r.shared, "score": round(r.score, 3),
                          "title": r.reference.title[:3], "answered": r.reference.answered} for r in ranked],
        summary=summarize(rows, notes, pool_size=len(pool)), ai_calls=ai_calls,
        created_by_id=user.id if user else None,
    )
    db.add(statement)
    db.commit()
    db.refresh(statement)
    return statement


def summarize(rows: list[dict], notes: list[str] | None = None, **extra) -> dict:
    answerable = [r for r in rows if not r.get("heading") and r.get("source") != "lead_in"]
    by_source: dict[str, int] = {}
    by_response: dict[str, int] = {}
    for row in answerable:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        key = row.get("response") or "unanswered"
        by_response[key] = by_response.get(key, 0) + 1
    return {"clauses": len(answerable), "by_source": by_source, "by_response": by_response,
            "review": sum(1 for r in answerable if r.get("state") == "review"), "notes": notes or [], **extra}


def _answerable(row: dict) -> bool:
    return not row.get("heading") and row.get("source") != "lead_in"


def _resummarize(statement: ComplianceStatement, notes: list[str] | None = None) -> None:
    kept = {k: v for k, v in statement.summary.items() if k not in ("clauses", "by_source", "by_response", "review", "notes")}
    statement.summary = summarize(statement.rows, notes if notes is not None else statement.summary.get("notes"), **kept)


def autofill(db: Session, project: Project, statement: ComplianceStatement, scope: str, *,
             provider=None) -> ComplianceStatement:
    """Fill a prepared statement's clauses from the model. Scope:
    "unanswered" -- rows with no response; "review" -- rows flagged for the
    engineer; "all" -- every row the engineer has not answered by hand."""
    if statement.kind != "prepare":
        raise ComplianceError("Only a prepared statement can be filled")
    if not assist.available(provider):
        raise ComplianceError("AI assistance is not available")
    rows = [dict(row) for row in statement.rows]
    if scope == "all":
        chosen = [r for r in rows if _answerable(r) and r["source"] != "engineer"]
    elif scope == "review":
        chosen = [r for r in rows if _answerable(r) and r["state"] == "review" and r["source"] != "engineer"]
    else:
        chosen = [r for r in rows if _answerable(r) and not r.get("response")]
    if not chosen:
        raise ComplianceError("Nothing to fill: every clause in that scope already has a response")
    calls, notes = ask_model(db, project, statement.system_code, statement.spec["sha256"], chosen, provider=provider)
    statement.rows = rows
    statement.ai_calls = (statement.ai_calls or 0) + calls
    _resummarize(statement, notes)
    db.commit()
    db.refresh(statement)
    return statement


def suggest(db: Session, project: Project, statement: ComplianceStatement, clause_id: str, *, provider=None) -> dict:
    """The model's proposed response for one clause, not written anywhere:
    the engineer applies it or not."""
    row = next((dict(r) for r in statement.rows if r["id"] == clause_id), None)
    if row is None or not _answerable(row):
        raise ComplianceError("No such clause")
    if not assist.available(provider):
        raise ComplianceError("AI assistance is not available")
    _calls, notes = ask_model(db, project, statement.system_code, statement.spec["sha256"], [row],
                              provider=provider, apply=False)
    suggestion = row.get("suggestion")
    if suggestion is None:
        raise ComplianceError(notes[-1] if notes else "The model gave no answer for this clause")
    return {"id": clause_id, **suggestion}


def ask(db: Session, project: Project, statement: ComplianceStatement, clause_id: str | None, question: str, *,
        provider=None) -> str:
    """A free question about a clause (or the statement), answered from the
    project's facts and BOQ."""
    row = next((r for r in statement.rows if r["id"] == clause_id), None) if clause_id else None
    if clause_id and row is None:
        raise ComplianceError("No such clause")
    if not assist.available(provider):
        raise ComplianceError("AI assistance is not available")
    session = assist.open_session(db, project.id, statement.spec["sha256"], provider)
    clause = (f"{row['ref']}: {row['text']}\nCurrent response: {row.get('response') or 'none'}"
              + (f" -- {row['remark']}" if row and row.get("remark") else "")) if row else "(no clause chosen)"
    answer = assist.ask_clause(session, project_facts(project, statement.system_code),
                               boq_text(project, statement.system_code), clause, question)
    if answer is None:
        raise ComplianceError(session.errors[-1] if session.errors else session.exhausted or "The model gave no answer")
    statement.ai_calls = (statement.ai_calls or 0) + session.calls
    db.commit()
    return answer


def update_rows(db: Session, statement: ComplianceStatement, changes: list[dict]) -> ComplianceStatement:
    by_id = {row["id"]: dict(row) for row in statement.rows}
    for change in changes:
        row = by_id.get(change.get("id"))
        if row is None or row.get("heading"):
            continue
        if row.get("source") == "lead_in" and not (change.get("response") or change.get("remark")):
            continue
        if "response" in change and change["response"] is not None:
            row["response"] = change["response"]
        if "remark" in change and change["remark"] is not None:
            row["remark"] = change["remark"][:500]
        row["source"] = "engineer"
        row["state"] = "ok"
        by_id[row["id"]] = row
    statement.rows = [by_id[row["id"]] for row in statement.rows]
    _resummarize(statement)
    db.commit()
    db.refresh(statement)
    return statement


# --- check -----------------------------------------------------------------------------


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
          statement_name: str, user: User | None, *, use_ai: bool = True, provider=None) -> ComplianceStatement:
    spec, _content, uploaded = load_spec(project, source)
    if not spec.clauses:
        raise ComplianceError(spec.warnings[0] if spec.warnings else "No clauses could be read from the specification.")
    try:
        submitted = read_statement(statement_bytes, statement_name)
    except ValueError as exc:
        raise ComplianceError(str(exc)) from exc
    if submitted is None:
        raise ComplianceError("No compliance table could be found in the statement.")
    verdict = verify_spec(db, project, system_code, spec, Path(source.member or source.path).name, use_ai=use_ai)

    general: list[dict] = []
    if verdict.project == "different":
        general.append(_finding("spec_other_project", "error",
                                "The specification is not this project's: " + " ".join(verdict.evidence[:1])))
    if verdict.system == "different":
        general.append(_finding("spec_other_system", "error", "The specification is not for this system."))

    title = " ".join(submitted.title_lines)
    brands = {b.lower() for b in project_brands(project, system_code)}
    other_brands = matcher.brands_in(title) - brands
    if brands and other_brands:
        general.append(_finding("statement_other_manufacturer", "error",
                                f"The statement's header names {', '.join(sorted(other_brands))}; the project offers "
                                f"{', '.join(sorted(brands))}."))
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
        foreign = matcher.brands_in(found.remark) - brands
        if brands and foreign:
            row["findings"].append(_finding("other_manufacturer", "warning", f"The remark names {', '.join(sorted(foreign))}."))

    ai_calls, notes = 0, []
    # The answers most likely wrong first: the ones that depend on the project
    # (not applicable, by others, a deviation), then a "Comply" to a clause
    # that names a product, a listing or a value.
    reviewable = [r for r in rows if r["source"] == "statement" and r["response"] and r["response"] != "Noted"
                  and (r["response"] != "Comply" or _HARD_REQUIREMENT.search(r["text"]))]
    reviewable.sort(key=lambda r: r["response"] == "Comply")  # stable: specification order within each
    reviewable = reviewable[:MAX_REVIEW_CLAUSES]
    if reviewable and use_ai and assist.available(provider):
        session = assist.open_session(db, project.id, spec.sha256, provider)
        facts, boq = project_facts(project, system_code), boq_text(project, system_code)
        size = assist.batch_size()
        for start in range(0, len(reviewable), size):
            batch = reviewable[start:start + size]
            findings = assist.review_clauses(session, facts, boq, [
                {"id": r["id"], "ref": r["ref"], "text": r["text"], "response": r["response"], "remark": r["remark"]}
                for r in batch
            ])
            for row in batch:
                verdict_ = findings.get(row["id"])
                if verdict_ and verdict_["verdict"] != "ok":
                    # A conflict with the BOQ is worth the engineer's look; "the
                    # facts cannot tell" mostly is not (EP-30784: "no evidence of
                    # 5 years' experience"), so it is shown but not flagged.
                    conflict = verdict_["verdict"] == "conflict"
                    row["findings"].append(_finding(
                        "ai_conflict" if conflict else "ai_unclear", "warning" if conflict else "info",
                        verdict_["note"] or "The answer may not match the project's BOQ."))
            if session.exhausted:
                notes.append(f"The model's budget ran out ({session.exhausted}); later clauses were not reviewed.")
                break
        ai_calls = session.calls
        if session.errors:
            notes.append(f"{len(session.errors)} model call(s) failed: {session.errors[-1]}")
    elif reviewable and use_ai:
        notes.append("AI assistance is not available; answers were checked for coverage only.")

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
        summary={**summarize(rows, notes), "general": general, "finding_counts": counts,
                 "statement_rows": len(submitted.rows), "statement_answered": len(submitted.answered),
                 "rows_not_in_spec": max(0, extra_rows), "reviewed_by_ai": len(reviewable) if ai_calls else 0},
        statement_name=statement_name, ai_calls=ai_calls, created_by_id=user.id if user else None,
    )
    db.add(statement)
    db.commit()
    db.refresh(statement)
    return statement
