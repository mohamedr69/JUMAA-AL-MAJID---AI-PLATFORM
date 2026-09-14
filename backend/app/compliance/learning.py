"""Learning from the engineer's sign-off.

Every answer an engineer signs off -- a clause marked reviewed, or the
answered clauses of an approved statement -- is kept as a learned answer
(ComplianceLearnedAnswer). The next statement uses them in two ways, both
ahead of the imported knowledge base because they are the company's answers
as its engineers reviewed them, not as a past document recorded them:

- the same wording (the knowledge base's conservative hash), for a project
  offering the same manufacturer or an answer that commits to no product,
  is drafted from the latest learned answer without a model. Learned answers
  that disagree leave the draft a candidate for review;
- the nearest learned answers to a clause travel with it when the AI answers
  it, marked as engineer-approved, so the model answers in the company's own
  wording and reuses what engineers already decided.

A learned answer never skips review: what it drafts is a draft.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.knowledge import autofill as knowledge_autofill
from app.knowledge.normalize import normalize_requirement, requirement_hash
from app.models import ComplianceLearnedAnswer, ComplianceStatement, Project, User

# Responses that commit to no product: they apply whatever the project offers.
PRODUCT_FREE = ("Noted", "Not applicable", "By others")
MIN_CLAUSE_CHARS = 20


def _makers(project: Project, system_code: str) -> set[str]:
    return knowledge_autofill.project_manufacturers(project, system_code)


def record(db: Session, project: Project, statement: ComplianceStatement, row: dict, user: User | None) -> None:
    """Keep (or refresh) the signed-off answer of one row. The caller commits."""
    text = (row.get("text") or "").strip()
    response = (row.get("response") or "").strip()
    if not response or len(text) < MIN_CLAUSE_CHARS:
        return
    existing = db.execute(select(ComplianceLearnedAnswer).where(
        ComplianceLearnedAnswer.statement_id == statement.id, ComplianceLearnedAnswer.clause_id == row["id"])).scalar_one_or_none()
    entry = existing or ComplianceLearnedAnswer(project_id=project.id, statement_id=statement.id, clause_id=row["id"])
    entry.clause_ref = (row.get("ref") or "")[:64] or None
    entry.system_code = statement.system_code
    entry.clause_text = text
    entry.clause_hash = requirement_hash(text)
    entry.response = response[:48]
    entry.remark = (row.get("remark") or "").strip()[:500]
    entry.technical_status = (row.get("technical") or {}).get("status")
    entry.manufacturers = ", ".join(sorted(_makers(project, statement.system_code)))[:255] or None
    entry.project_name = (f"{project.ep_number or ''} {project.project_name or ''}".strip())[:255] or None
    entry.approved_by_id = user.id if user else None
    entry.approved_by_name = (user.full_name or user.email) if user else None
    entry.approved_at = utc_now()
    entry.active = True
    if existing is None:
        db.add(entry)
    invalidate()


def forget(db: Session, statement: ComplianceStatement, clause_id: str) -> None:
    """The review was taken back: the answer is no longer signed off. The caller commits."""
    db.execute(update(ComplianceLearnedAnswer)
               .where(ComplianceLearnedAnswer.statement_id == statement.id, ComplianceLearnedAnswer.clause_id == clause_id)
               .values(active=False))
    invalidate()


def forget_statement(db: Session, statement: ComplianceStatement) -> None:
    db.execute(update(ComplianceLearnedAnswer).where(ComplianceLearnedAnswer.statement_id == statement.id).values(active=False))
    invalidate()


# --- lookups -----------------------------------------------------------------------------------


@dataclass
class Learned:
    id: int
    statement_id: int
    clause_ref: str | None
    clause_text: str
    clause_hash: str
    response: str
    remark: str
    technical_status: str | None
    manufacturers: set[str]
    project_name: str | None
    approved_by_name: str | None
    approved_at: object

    def as_hint(self) -> dict:
        return {"response": self.response, "remark": self.remark, "text": self.clause_text,
                "project": self.project_name, "by": self.approved_by_name}


class LearnedIndex:
    """One system's active learned answers, by exact hash and by words."""

    def __init__(self, entries: list[Learned]):
        self.by_hash: dict[str, list[Learned]] = defaultdict(list)
        for entry in entries:
            self.by_hash[entry.clause_hash].append(entry)
        # The newest wording of each hash stands for it in the similarity index.
        newest = {h: max(items, key=lambda e: e.approved_at) for h, items in self.by_hash.items()}
        self.hashes = list(newest)
        self.words = knowledge_autofill.MatchIndex([(h, h, normalize_requirement(newest[h].clause_text)) for h in self.hashes])

    def exact(self, text: str) -> list[Learned]:
        return sorted(self.by_hash.get(requirement_hash(text), []), key=lambda e: e.approved_at, reverse=True)

    def similar(self, text: str, *, limit: int = 3, floor: float = 0.55) -> list[tuple[Learned, float]]:
        out = []
        for digest, score in self.words.similar(text, floor=floor, limit=limit):
            items = self.by_hash.get(digest) or []
            if items:
                out.append((max(items, key=lambda e: e.approved_at), score))
        return out


_indexes: dict[str, LearnedIndex] = {}
_lock = threading.Lock()


def invalidate() -> None:
    with _lock:
        _indexes.clear()


def index_for(db: Session, system_code: str) -> LearnedIndex:
    with _lock:
        if system_code in _indexes:
            return _indexes[system_code]
    entries = [
        Learned(e.id, e.statement_id, e.clause_ref, e.clause_text, e.clause_hash, e.response, e.remark, e.technical_status,
                {m.strip() for m in (e.manufacturers or "").split(",") if m.strip()}, e.project_name, e.approved_by_name, e.approved_at)
        for e in db.execute(select(ComplianceLearnedAnswer).where(
            ComplianceLearnedAnswer.active.is_(True), ComplianceLearnedAnswer.system_code == system_code)).scalars()
    ]
    index = LearnedIndex(entries)
    with _lock:
        _indexes[system_code] = index
    return index


def applicable(entry: Learned, makers: set[str]) -> bool:
    """A learned answer fits this project when it commits to no product, or
    it was given for a manufacturer this project offers too."""
    if entry.response in PRODUCT_FREE:
        return True
    return bool(entry.manufacturers & makers) if entry.manufacturers else not makers


@dataclass
class Draft:
    response: str
    remark: str
    technical_status: str | None
    agreed: bool
    match: dict


def draft_for(index: LearnedIndex, text: str, makers: set[str], *, exclude_statement: int | None = None) -> Draft | None:
    """The learned answer to reuse for this clause's exact wording, or None."""
    if len(text.strip()) < MIN_CLAUSE_CHARS:
        return None
    entries = [e for e in index.exact(text) if e.statement_id != exclude_statement and applicable(e, makers)]
    if not entries:
        return None
    latest = entries[0]
    answers = {(e.response, e.remark) for e in entries}
    agreed = len({e.response for e in entries}) == 1
    explanation = (f"Same wording as clause {latest.clause_ref or ''} of {latest.project_name or 'an earlier project'}, "
                   f"approved by {latest.approved_by_name or 'an engineer'}"
                   + ("." if agreed else f"; {len(answers)} different approved answers exist, the latest is drafted: check it."))
    match = {
        "result": "eligible" if agreed else "conflict", "learned": True, "same_wording": True,
        "explanation": explanation, "historical_response": latest.remark or latest.response,
        "historical_status": latest.response, "manufacturer": ", ".join(sorted(latest.manufacturers)) or None,
        "unresolved": [] if agreed else ["approved answers to this wording disagree"],
        "candidates": [
            {"requirement_id": None, "requirement_text": e.clause_text[:400], "response_id": None,
             "historical_response": e.remark or e.response, "historical_status": e.response, "proposed_status": e.technical_status,
             "response": e.response, "remarks": e.remark or None, "manufacturer": ", ".join(sorted(e.manufacturers)) or None,
             "brand": None, "models": None, "eligibility": "eligible", "eligibility_reasons": None, "similarity": 1.0,
             "sources": [{"source_id": f"approved #{e.id}", "filename": f"clause {e.clause_ref or ''}", "project": e.project_name,
                          "job_number": None, "page": None, "review_status": f"approved by {e.approved_by_name or 'an engineer'}",
                          "superseded": False, "document_date": None, "document_revision": None}]}
            for e in entries[:4]
        ],
    }
    return Draft(latest.response, latest.remark, latest.technical_status, agreed, match)


def hints_for(index: LearnedIndex, text: str, makers: set[str], *, limit: int = 3) -> list[dict]:
    """The nearest engineer-approved answers for the AI, closest first."""
    hints = []
    for entry, score in index.similar(text, limit=limit + 2):
        if applicable(entry, makers):
            hints.append({**entry.as_hint(), "similarity": score})
        if len(hints) >= limit:
            break
    return hints
