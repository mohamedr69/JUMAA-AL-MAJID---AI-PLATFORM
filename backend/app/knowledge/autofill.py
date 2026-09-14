"""Autofill: the knowledge base against a new specification, deterministically.

For each clause, in order: identify the system; find the requirements the
company answered with the SAME wording (a conservative-normalisation hash,
or an equivalence an engineer validated); take their eligible responses
(app.knowledge.policy); keep the ones whose manufacturer and models are
what THIS project's BOQ proposes, or that commit to no product; check the
commitments they carry against the project's scope; and only then decide:

- eligible   -- one answer stands: written in as a DRAFT, its historical
                status proposed, never verified;
- flagged    -- the same wording, the project's manufacturer and one answer,
                but every record of it is flagged for how the source was
                extracted (paired by position, OCR, a later revision): the
                answer and its remark are written in, and the row stays a
                candidate until the engineer has checked the source page;
- conflict   -- applicable answers disagree: shown, not chosen;
- missing_model -- the answer names models the BOQ does not: clarify the BOQ;
- scope      -- the answer commits to work the scope does not name: verify;
- candidate  -- the same or a similar requirement was answered, but not in
                a way that can be reused (a review flag, another
                manufacturer, a project-dependent status, fuzzy wording):
                for the engineer to look at;
- none       -- nothing in the knowledge base comes near.

Similarity, clause numbers, specification families and how often an answer
was given never fill a row on their own. No model is called anywhere here.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compliance.matcher import tokens
from app.models import (
    KnowledgeEquivalence,
    KnowledgeModel,
    KnowledgeRequirement,
    KnowledgeResponse,
    KnowledgeResponseSource,
    KnowledgeSource,
    Project,
)

from . import policy
from .normalize import normalize_model, normalize_requirement, requirement_hash

CANDIDATE_SIMILARITY = 0.6
MAX_CANDIDATES = 5
_FUZZY_POOL = 12
# "by others", "by the MEP contractor", "by client": the commitment is not the company's.
_CARVED_OUT_RE = re.compile(
    r"\bby\s+(?:others|(?:the\s+)?(?:mep\s+|main\s+|electrical\s+|civil\s+)?(?:contractor|client|consultant|employer|end\s+user))\b", re.I)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# --- the project's side --------------------------------------------------------------


@dataclass
class BoqLine:
    id: int
    description: str
    system: str | None
    manufacturer: str | None
    brand: str | None
    model: str | None
    quantity: str | None
    unit: str | None
    accessories: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def boq_material_map(project: Project, system_code: str) -> list[BoqLine]:
    """The BOQ lines for this system, with the manufacturer each line
    proposes: its own, else the one Project Info records for the system.
    A description alone never names a model."""
    from app.services import system_rules

    names = system_rules.drf_rows(system_code, project)
    system_brand = next(((s.brand or "").strip() for s in project.systems if s.name in names and (s.brand or "").strip()), None)
    lines: list[BoqLine] = []
    for item in sorted(project.boq_items, key=lambda i: i.position):
        if (item.system_code or "").upper() != system_code:
            continue
        maker = (item.manufacturer or "").strip() or system_brand
        model = (item.catalog_no or "").strip() or None
        lines.append(BoqLine(
            id=item.id, description=(item.description or "").strip(), system=system_code, manufacturer=maker,
            brand=maker, model=model, quantity=item.quantity, unit=item.unit,
            accessories=(item.group_heading or None),
        ))
    return lines


def project_manufacturers(project: Project, system_code: str, boq: list[BoqLine] | None = None) -> set[str]:
    boq = boq if boq is not None else boq_material_map(project, system_code)
    makers = {policy.canonical_manufacturer(line.manufacturer) for line in boq}
    from app.services import system_rules

    names = system_rules.drf_rows(system_code, project)
    makers |= {policy.canonical_manufacturer(s.brand) for s in project.systems if s.name in names}
    return {m for m in makers if m}


def boq_hash(project: Project, system_code: str) -> str:
    lines = sorted((l.manufacturer or "", l.model or "", l.description, l.quantity or "", l.unit or "")
                   for l in boq_material_map(project, system_code))
    return hashlib.sha256(repr(lines).encode()).hexdigest()[:16]


def scope_text(project: Project) -> str:
    return " ".join(t for t in (project.scope_of_work, project.other_information) if t).strip()


def scope_hash(project: Project) -> str:
    return hashlib.sha256(scope_text(project).lower().encode()).hexdigest()[:16]


# --- the knowledge base's side ------------------------------------------------------------


class MatchIndex:
    """One system's active requirements, by exact hash and by the words
    they use -- the second only to find candidates for an engineer."""

    def __init__(self, rows: list[tuple[str, str, str]]):
        self.ids = [r[0] for r in rows]
        self.by_hash: dict[str, list[str]] = defaultdict(list)
        self.words: list[list[str]] = []
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, (requirement_id, digest, normalized) in enumerate(rows):
            self.by_hash[digest].append(requirement_id)
            words = tokens(normalized)
            self.words.append(words)
            for word in set(words):
                self.postings[word].append(i)
        total = max(1, len(rows))
        self.idf = {word: math.log(total / len(ids)) + 1 for word, ids in self.postings.items()}

    def exact(self, text: str) -> list[str]:
        return list(self.by_hash.get(requirement_hash(text), []))

    def similar(self, text: str, *, floor: float = CANDIDATE_SIMILARITY, limit: int = MAX_CANDIDATES) -> list[tuple[str, float]]:
        words = tokens(normalize_requirement(text))
        if not words:
            return []
        weight: dict[int, float] = defaultdict(float)
        for word in set(words):
            for i in self.postings.get(word, ()):
                weight[i] += self.idf[word]
        pool = sorted(weight, key=weight.get, reverse=True)[:_FUZZY_POOL]
        scored = [(self.ids[i], SequenceMatcher(None, words, self.words[i], autojunk=False).ratio()) for i in pool]
        return sorted([(rid, round(s, 3)) for rid, s in scored if s >= floor], key=lambda x: -x[1])[:limit]


_indexes: dict[tuple[str, ...], MatchIndex] = {}
_index_lock = threading.Lock()


def invalidate() -> None:
    with _index_lock:
        _indexes.clear()


def _system_matches(system: str | None, labels: tuple[str, ...]) -> bool:
    text = system or ""
    return any(re.search(rf"(^|[\s(,]){re.escape(label)}([\s),]|$)", text) for label in labels)


def index_for(db: Session, system_code: str) -> MatchIndex:
    labels = policy.SYSTEM_TO_KNOWLEDGE.get(system_code, ())
    with _index_lock:
        if labels in _indexes:
            return _indexes[labels]
    rows = [
        (rid, digest, normalized)
        for rid, system, digest, normalized in db.execute(
            select(KnowledgeRequirement.requirement_id, KnowledgeRequirement.system, KnowledgeRequirement.requirement_hash,
                   KnowledgeRequirement.normalized_requirement_text).where(KnowledgeRequirement.active.is_(True))
        )
        if _system_matches(system, labels)
    ]
    index = MatchIndex(rows)
    with _index_lock:
        _indexes[labels] = index
    return index


# --- one clause ------------------------------------------------------------------------------


@dataclass
class Assessment:
    result: str                      # eligible | conflict | missing_model | scope | candidate | none
    response: str = ""
    remark: str = ""
    technical_status: str | None = None
    match: dict = field(default_factory=dict)


def _status_of(response: KnowledgeResponse) -> tuple[str, str]:
    return policy.STATUS_TO_RESPONSE.get(response.historical_compliance_status or "", ("", "insufficient_evidence"))


def draft_of(status: str | None, historical: str | None, remarks: str | None) -> tuple[str, str, str]:
    """What a past answer becomes on the statement: (response, remark,
    proposed technical status). The historical wording is the remark when it
    says more than the bare status ("Comply with the proposed SIGA-PS ...");
    "Noted." or "Comply" says nothing more and leaves the remark empty."""
    canonical, technical = policy.STATUS_TO_RESPONSE.get(status or "", ("", "insufficient_evidence"))
    text = re.sub(r"\s+", " ", historical or "").strip()
    bare = re.sub(r"[^a-z ]", "", text.lower()).strip()
    remark = "" if bare in ("", canonical.lower(), "comply", "complied", "complies", "noted", "note", "yes", "ok") else text
    if remarks and remarks.strip() and remarks.strip() not in remark:
        remark = f"{remark} {remarks.strip()}".strip()
    return canonical, remark[:500], technical


def _date_of(sources: list[dict]) -> str:
    dates = [m.group(1) for s in sources for m in [_DATE_RE.search(s.get("document_date") or "")] if m]
    return max(dates) if dates else ""


def _commitment_gaps(response: KnowledgeResponse, scope: str) -> list[str]:
    """Commitments the answer makes that the project's scope does not name.
    A commitment the answer assigns to others is not the company's."""
    text = " ".join(t for t in (response.historical_response, response.scope_conditions, response.responsible_party) if t).lower()
    if _CARVED_OUT_RE.search(text):
        return []
    scope_l = scope.lower()
    gaps = []
    for commitment, words in policy.COMMITMENTS.items():
        if any(re.search(rf"\b{re.escape(w)}", text) for w in words) and not any(re.search(rf"\b{re.escape(w)}", scope_l) for w in words):
            gaps.append(commitment)
    return gaps


def _applicability(response: KnowledgeResponse, models: list[str], makers: set[str], boq: list[BoqLine]) -> tuple[bool, str, BoqLine | None, str | None]:
    """(applies, explanation, the BOQ line it applies to, the block kind)."""
    if response.historical_compliance_status == "Noted":
        return True, "Noted: commits to no product", None, None
    maker = policy.canonical_manufacturer(response.manufacturer) or policy.canonical_manufacturer(response.brand)
    if maker is None or maker not in makers:
        offered = ", ".join(sorted(makers)) or "no manufacturer recorded"
        return False, f"answered for {response.manufacturer or 'an unconfirmed manufacturer'}; this project offers {offered}", None, "manufacturer"
    by_maker = [line for line in boq if policy.canonical_manufacturer(line.manufacturer) == maker]
    if models:
        boq_models = {normalize_model(line.model): line for line in boq if line.model}
        for model in models:
            if model in boq_models:
                return True, f"{maker}: the BOQ proposes model {boq_models[model].model}", boq_models[model], None
        return False, f"names models {response.applicable_models} that the BOQ does not propose", None, "model"
    return True, f"{maker} is the manufacturer the BOQ proposes", by_maker[0] if by_maker else None, None


def assess(db: Session, project: Project, system_code: str, clauses: list[tuple[str, str]], *,
           index: MatchIndex | None = None) -> dict[str, Assessment]:
    """Assess (clause_id, text) pairs against the knowledge base. One query
    per table for the whole batch."""
    index = index or index_for(db, system_code)
    boq = boq_material_map(project, system_code)
    makers = project_manufacturers(project, system_code, boq)
    scope = scope_text(project)

    exact: dict[str, list[str]] = {}
    fuzzy: dict[str, list[tuple[str, float]]] = {}
    for clause_id, text in clauses:
        if len(text.strip()) < policy.MIN_REQUIREMENT_CHARS:
            exact[clause_id], fuzzy[clause_id] = [], []
            continue
        exact[clause_id] = index.exact(text)
        fuzzy[clause_id] = [] if exact[clause_id] else index.similar(text)

    wanted = {rid for ids in exact.values() for rid in ids}
    equivalents: dict[str, list[str]] = defaultdict(list)
    if wanted:
        for source_id, target_id in db.execute(
            select(KnowledgeEquivalence.source_requirement_id, KnowledgeEquivalence.equivalent_requirement_id)
            .where(KnowledgeEquivalence.engineer_validation_status == "validated", KnowledgeEquivalence.active.is_(True))
        ):
            if source_id in wanted:
                equivalents[source_id].append(target_id)
            if target_id in wanted:
                equivalents[target_id].append(source_id)
    for clause_id, ids in exact.items():
        for rid in list(ids):
            for other in equivalents.get(rid, ()):
                if other not in ids:
                    ids.append(other)
    wanted = {rid for ids in exact.values() for rid in ids} | {rid for cands in fuzzy.values() for rid, _ in cands}
    responses_by_requirement: dict[str, list[KnowledgeResponse]] = defaultdict(list)
    requirement_text: dict[str, str] = {}
    models_of: dict[str, list[str]] = defaultdict(list)
    sources_of: dict[str, list[dict]] = defaultdict(list)
    if wanted:
        ids = list(wanted)
        for rid, text in db.execute(select(KnowledgeRequirement.requirement_id, KnowledgeRequirement.exact_requirement_text)
                                    .where(KnowledgeRequirement.requirement_id.in_(ids))):
            requirement_text[rid] = text
        responses = db.execute(select(KnowledgeResponse).where(KnowledgeResponse.requirement_id.in_(ids),
                                                               KnowledgeResponse.active.is_(True))).scalars().all()
        for response in responses:
            responses_by_requirement[response.requirement_id].append(response)
        response_ids = [r.response_id for r in responses]
        if response_ids:
            for response_id, model in db.execute(select(KnowledgeModel.response_id, KnowledgeModel.model)
                                                 .where(KnowledgeModel.response_id.in_(response_ids))):
                models_of[response_id].append(model)
            for link, source in db.execute(
                select(KnowledgeResponseSource, KnowledgeSource).join(KnowledgeSource, KnowledgeSource.source_id == KnowledgeResponseSource.source_id)
                .where(KnowledgeResponseSource.response_id.in_(response_ids))
            ):
                sources_of[link.response_id].append({
                    "source_id": source.source_id, "filename": source.filename, "project": source.project, "job_number": source.job_number,
                    "page": link.pdf_page, "review_status": link.historical_review_status, "superseded": bool(link.superseded_status),
                    "document_date": source.document_date, "document_revision": source.document_revision,
                })

    def summary(response: KnowledgeResponse, similarity: float | None = None, requirement_id: str | None = None) -> dict:
        canonical, technical = _status_of(response)
        return {
            "requirement_id": requirement_id or response.requirement_id,
            "requirement_text": requirement_text.get(requirement_id or response.requirement_id, "")[:400],
            "response_id": response.response_id, "historical_response": response.historical_response,
            "historical_status": response.historical_compliance_status, "proposed_status": technical, "response": canonical,
            "remarks": response.remarks, "manufacturer": response.manufacturer, "brand": response.brand,
            "models": response.applicable_models, "eligibility": response.autofill_eligibility,
            "eligibility_reasons": response.eligibility_reasons, "similarity": similarity,
            "sources": sources_of.get(response.response_id, [])[:3],
        }

    out: dict[str, Assessment] = {}
    for clause_id, text in clauses:
        ids = exact.get(clause_id, [])
        if not ids:
            cands = [summary(r, s, rid) for rid, s in fuzzy.get(clause_id, [])
                     for r in sorted(responses_by_requirement.get(rid, []), key=lambda r: r.autofill_eligibility)[:2]]
            if cands:
                out[clause_id] = Assessment("candidate", match={
                    "result": "candidate", "explanation": "A similar requirement was answered before; the wording differs, so it is for you to compare.",
                    "unresolved": ["wording differs from the past requirement"], "candidates": cands[:MAX_CANDIDATES]})
            else:
                out[clause_id] = Assessment("none", match={"result": "none", "explanation": "Nothing in the knowledge base comes near this clause.",
                                                           "unresolved": [], "candidates": []})
            continue

        all_responses = [r for rid in ids for r in responses_by_requirement.get(rid, [])]
        eligible = [r for r in all_responses if r.autofill_eligibility == "eligible"]
        # Same wording, blocked only for how the source was extracted: still
        # an answer to this clause, written in for the engineer to verify.
        flagged = [r for r in all_responses if r.autofill_eligibility != "eligible" and policy.extraction_only(r.eligibility_reasons)]
        equivalence_used = any(rid not in index.exact(text) for rid in ids)
        base = {"requirement_id": ids[0], "requirement_text": requirement_text.get(ids[0], "")[:400], "equivalence": equivalence_used,
                "same_wording": True}
        pool, verify_source = (eligible, False) if eligible else (flagged, True)
        if not pool:
            out[clause_id] = Assessment("candidate", match={
                **base, "result": "candidate",
                "explanation": "The same requirement was answered before, but not in a way that can stand for this clause as recorded.",
                "unresolved": sorted({r.eligibility_reasons or "blocked" for r in all_responses})[:4],
                "candidates": [summary(r, 1.0) for r in all_responses[:MAX_CANDIDATES]]})
            continue

        applicable: list[tuple[KnowledgeResponse, str, BoqLine | None]] = []
        blocked: list[tuple[KnowledgeResponse, str, str]] = []
        for response in pool:
            ok, why, line, kind = _applicability(response, models_of.get(response.response_id, []), makers, boq)
            (applicable.append((response, why, line)) if ok else blocked.append((response, why, kind or "")))
        if not applicable:
            kinds = {kind for _, _, kind in blocked}
            result = "missing_model" if kinds == {"model"} else "candidate"
            out[clause_id] = Assessment(result, match={
                **base, "result": result,
                "explanation": ("The past answer names models the BOQ does not propose: clarify the BOQ."
                                if result == "missing_model" else "The same requirement was answered, but for another manufacturer."),
                "unresolved": sorted({why for _, why, _ in blocked})[:4],
                "candidates": [summary(r, 1.0) for r in all_responses[:MAX_CANDIDATES]]})
            continue

        by_answer: dict[str, list[tuple[KnowledgeResponse, str, BoqLine | None]]] = defaultdict(list)
        for item in applicable:
            by_answer[_status_of(item[0])[0]].append(item)
        if len(by_answer) > 1:
            out[clause_id] = Assessment("conflict", match={
                **base, "result": "conflict",
                "explanation": "Past answers to this requirement disagree: " + " / ".join(sorted(by_answer)) + ".",
                "unresolved": ["conflicting past answers"],
                "candidates": [summary(r, 1.0) for r, _, _ in applicable[:MAX_CANDIDATES]]})
            continue

        # One answer. Several wordings of it: the latest document's.
        chosen, why, line = max(applicable, key=lambda item: (_date_of(sources_of.get(item[0].response_id, [])), item[0].n_sources))
        gaps = _commitment_gaps(chosen, scope)
        detail = {**base, "response_id": chosen.response_id, "historical_response": chosen.historical_response,
                  "historical_status": chosen.historical_compliance_status, "manufacturer": chosen.manufacturer, "brand": chosen.brand,
                  "models": chosen.applicable_models, "remarks": chosen.remarks, "scope_conditions": chosen.scope_conditions,
                  "responsible_party": chosen.responsible_party, "sources": sources_of.get(chosen.response_id, [])[:3],
                  "boq_item": line.as_dict() if line else None, "explanation": f"Same wording as {chosen.requirement_id}; {why}.",
                  "candidates": [summary(r, 1.0) for r, _, _ in applicable if r is not chosen][:MAX_CANDIDATES - 1]}
        if gaps:
            out[clause_id] = Assessment("scope", match={
                **detail, "result": "scope",
                "unresolved": [f"the answer commits to {g}, which the scope of work does not name" for g in gaps]})
            continue
        canonical, remark, technical = draft_of(chosen.historical_compliance_status, chosen.historical_response, chosen.remarks)
        if verify_source:
            flags = sorted({r.eligibility_reasons for r, _, _ in applicable if r.eligibility_reasons})
            out[clause_id] = Assessment("flagged", canonical, remark, technical, {
                **detail, "result": "flagged",
                "explanation": f"Same wording as {chosen.requirement_id}; {why}. The past record is flagged in the source: check its PDF page.",
                "unresolved": [f"source record flagged: {f}" for f in flags][:4]})
            continue
        out[clause_id] = Assessment("eligible", canonical, remark, technical, {**detail, "result": "eligible", "unresolved": []})
    return out
