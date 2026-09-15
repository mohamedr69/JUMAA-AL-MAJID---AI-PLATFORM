"""Making the knowledge base's eligibility explainable, and repairable by review.

56,533 responses imported and none eligible: almost all are blocked for "no
high-confidence source read from native text", because the extraction paired
table answers (A_table) with medium, not high, confidence. The policy is
right not to trust an unverified pairing -- and wrong to leave no way to
verify one. This module does not loosen the policy. It:

  - counts why records are blocked: by reason, extraction method, pairing
    confidence, manufacturer and system (`summary`);
  - gives administrators a queue of source mappings to check, the most
    valuable first: native-text methods at medium confidence, whose
    responses are blocked for that reason alone (`review_queue`);
  - records each verdict on the mapping itself (`KnowledgeMappingReview`),
    which survives re-imports: a verified mapping counts as a high-confidence
    pairing, a rejected one never does;
  - recomputes the eligibility of every response the reviewed mappings
    touch with the unchanged policy (`recompute`).

A project-dependent status, an unconfirmed manufacturer or a review issue
still blocks a response however its pairing was verified.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.knowledge import policy
from app.models import (
    KnowledgeIssue,
    KnowledgeMapping,
    KnowledgeMappingReview,
    KnowledgeRequirement,
    KnowledgeResponse,
    KnowledgeResponseSource,
    KnowledgeSource,
    User,
)

PAIRING_REASON = "no high-confidence source read from native text"
VERDICTS = ("verified", "rejected")


def reviewed_confidence(confidence: str | None, verdict: str | None) -> str | None:
    """The pairing confidence the policy sees: a verified mapping is high, a
    rejected one never is, anything else is as extracted."""
    if verdict == "verified":
        return "high"
    if verdict == "rejected":
        return "rejected"
    return confidence


def summary(db: Session) -> dict:
    active = db.query(KnowledgeResponse).filter(KnowledgeResponse.active.is_(True))
    total = active.count()
    eligible = active.filter(KnowledgeResponse.autofill_eligibility == "eligible").count()
    reasons: Counter[str] = Counter()
    only_pairing = 0
    for (text,) in db.query(KnowledgeResponse.eligibility_reasons).filter(
            KnowledgeResponse.active.is_(True), KnowledgeResponse.autofill_eligibility == "blocked"):
        parts = [p.strip() for p in (text or "").split("; ") if p.strip()]
        for part in parts:
            # Group "status depends on the project: Not Applicable" under its kind.
            reasons[part.split(":")[0] if ":" in part else part] += 1
        if parts == [PAIRING_REASON]:
            only_pairing += 1

    def grouped(column) -> list[dict]:
        rows = (db.query(column, KnowledgeResponse.autofill_eligibility, func.count())
                .filter(KnowledgeResponse.active.is_(True)).group_by(column, KnowledgeResponse.autofill_eligibility).all())
        out: dict[str, dict] = {}
        for key, eligibility, count in rows:
            entry = out.setdefault(key or "unknown", {"key": key or "unknown", "eligible": 0, "blocked": 0})
            entry[eligibility] = entry.get(eligibility, 0) + count
        return sorted(out.values(), key=lambda e: -(e["eligible"] + e["blocked"]))

    methods = (db.query(KnowledgeMapping.extraction_method, KnowledgeMapping.pairing_confidence, func.count())
               .filter(KnowledgeMapping.active.is_(True))
               .group_by(KnowledgeMapping.extraction_method, KnowledgeMapping.pairing_confidence).all())
    reviews = dict(db.query(KnowledgeMappingReview.verdict, func.count()).group_by(KnowledgeMappingReview.verdict).all())
    return {
        "responses": total,
        "eligible": eligible,
        "blocked": total - eligible,
        "blocked_only_by_pairing": only_pairing,
        "reasons": [{"reason": reason, "count": count} for reason, count in reasons.most_common()],
        "by_manufacturer": grouped(KnowledgeResponse.manufacturer)[:25],
        "by_system": grouped(KnowledgeResponse.system)[:25],
        "mappings_by_method": [{"method": m or "unknown", "confidence": c or "unknown", "count": n}
                               for m, c, n in sorted(methods, key=lambda row: -row[2])],
        "mapping_reviews": {"verified": reviews.get("verified", 0), "rejected": reviews.get("rejected", 0)},
    }


def review_queue(db: Session, *, method: str = "A_table", confidence: str = "medium", manufacturer: str | None = None,
                 limit: int = 25, offset: int = 0) -> dict:
    """Unreviewed mappings to check, with what they pair: the requirement as
    the specification printed it, the source document and page, and the
    responses the pairing carries."""
    reviewed = select(KnowledgeMappingReview.mapping_id)
    query = (db.query(KnowledgeMapping)
             .filter(KnowledgeMapping.active.is_(True), KnowledgeMapping.extraction_method == method,
                     KnowledgeMapping.pairing_confidence == confidence, KnowledgeMapping.mapping_id.not_in(reviewed)))
    if manufacturer:
        query = query.join(KnowledgeSource, KnowledgeSource.source_id == KnowledgeMapping.source_id).filter(
            KnowledgeSource.manufacturer == manufacturer)
    total = query.count()
    mappings = query.order_by(KnowledgeMapping.source_id, KnowledgeMapping.pdf_page, KnowledgeMapping.mapping_id).offset(offset).limit(limit).all()
    ids = [m.mapping_id for m in mappings]
    links = db.query(KnowledgeResponseSource).filter(KnowledgeResponseSource.mapping_id.in_(ids)).all() if ids else []
    responses = {r.response_id: r for r in db.query(KnowledgeResponse).filter(
        KnowledgeResponse.response_id.in_([l.response_id for l in links]))} if links else {}
    sources = {s.source_id: s for s in db.query(KnowledgeSource).filter(
        KnowledgeSource.source_id.in_([m.source_id for m in mappings]))} if mappings else {}
    requirements = {r.requirement_id: r for r in db.query(KnowledgeRequirement).filter(
        KnowledgeRequirement.requirement_id.in_([m.requirement_id for m in mappings]))} if mappings else {}
    by_mapping: dict[str, list] = defaultdict(list)
    for link in links:
        if link.response_id in responses:
            by_mapping[link.mapping_id].append(responses[link.response_id])
    items = []
    for mapping in mappings:
        source = sources.get(mapping.source_id)
        items.append({
            "mapping_id": mapping.mapping_id,
            "method": mapping.extraction_method, "confidence": mapping.pairing_confidence,
            "clause": mapping.clause_label or mapping.clause_number, "section": mapping.section_number,
            "requirement": mapping.original_requirement_text or (requirements.get(mapping.requirement_id).exact_requirement_text
                                                                  if mapping.requirement_id in requirements else ""),
            "source": {"filename": source.filename if source else None, "project": source.project if source else None,
                       "manufacturer": source.manufacturer if source else None, "revision": source.document_revision if source else None,
                       "pdf_page": mapping.pdf_page, "printed_page": mapping.printed_page},
            "responses": [{"response_id": r.response_id, "response": r.historical_response[:300],
                           "status": r.historical_compliance_status, "eligibility": r.autofill_eligibility,
                           "reasons": r.eligibility_reasons} for r in by_mapping.get(mapping.mapping_id, [])],
        })
    return {"total": total, "offset": offset, "items": items}


def review(db: Session, mapping_ids: list[str], verdict: str, user: User, note: str | None = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError("verdict must be verified or rejected")
    known = {m for (m,) in db.query(KnowledgeMapping.mapping_id).filter(KnowledgeMapping.mapping_id.in_(mapping_ids))}
    now = utc_now()
    for mapping_id in known:
        row = db.get(KnowledgeMappingReview, mapping_id) or KnowledgeMappingReview(mapping_id=mapping_id)
        row.verdict, row.note = verdict, (note or None)
        row.reviewed_by_id, row.reviewed_by_name, row.reviewed_at = user.id, user.full_name, now
        db.merge(row)
    db.commit()
    affected = [r for (r,) in db.query(KnowledgeResponseSource.response_id).filter(
        KnowledgeResponseSource.mapping_id.in_(list(known))).distinct()]
    changed = recompute(db, affected)
    return {"reviewed": len(known), "unknown": sorted(set(mapping_ids) - known), "responses_checked": len(affected), **changed}


def recompute(db: Session, response_ids: list[str] | None = None) -> dict:
    """Eligibility again, under the unchanged policy, with the reviews in."""
    query = db.query(KnowledgeResponse).filter(KnowledgeResponse.active.is_(True))
    if response_ids is not None:
        if not response_ids:
            return {"now_eligible": 0, "now_blocked": 0}
        query = query.filter(KnowledgeResponse.response_id.in_(response_ids))
    responses = query.all()
    ids = [r.response_id for r in responses]
    links = db.query(KnowledgeResponseSource).filter(KnowledgeResponseSource.response_id.in_(ids),
                                                     KnowledgeResponseSource.active.is_(True)).all()
    mappings = {m.mapping_id: m for m in db.query(KnowledgeMapping).filter(
        KnowledgeMapping.mapping_id.in_({l.mapping_id for l in links if l.mapping_id}))}
    verdicts = dict(db.query(KnowledgeMappingReview.mapping_id, KnowledgeMappingReview.verdict).filter(
        KnowledgeMappingReview.mapping_id.in_(list(mappings))))
    requirement_chars = {r.requirement_id: len(r.exact_requirement_text or "") for r in db.query(KnowledgeRequirement).filter(
        KnowledgeRequirement.requirement_id.in_({r.requirement_id for r in responses}))}
    issues: dict[str, set[str]] = defaultdict(set)
    record_ids = set(ids) | {r.requirement_id for r in responses} | {l.source_id for l in links}
    for issue in db.query(KnowledgeIssue).filter(KnowledgeIssue.active.is_(True), KnowledgeIssue.record_id.in_(record_ids)):
        issues[issue.record_id].add(issue.issue_type)

    by_response: dict[str, list] = defaultdict(list)
    for link in links:
        by_response[link.response_id].append(link)
    now_eligible = now_blocked = 0
    for response in responses:
        response_links = []
        issue_types = issues.get(response.response_id, set()) | issues.get(response.requirement_id, set())
        for link in by_response.get(response.response_id, []):
            mapping = mappings.get(link.mapping_id)
            confidence = reviewed_confidence(mapping.pairing_confidence if mapping else None, verdicts.get(link.mapping_id))
            superseded = bool(link.superseded_status)
            response_links.append((confidence, mapping.extraction_method if mapping else None, superseded))
            if not superseded:
                issue_types |= {t for t in issues.get(link.source_id, ()) if t.startswith(policy.SOURCE_BLOCKING_ISSUES)}
        reasons = policy.eligibility_reasons(
            review_flag=response.review_flag, status=response.historical_compliance_status, manufacturer=response.manufacturer,
            requirement_chars=requirement_chars.get(response.requirement_id, 0), links=response_links, issue_types=issue_types)
        eligibility = "blocked" if reasons else "eligible"
        if eligibility != response.autofill_eligibility:
            if eligibility == "eligible":
                now_eligible += 1
            else:
                now_blocked += 1
        response.autofill_eligibility = eligibility
        response.eligibility_reasons = "; ".join(reasons) or None
    db.commit()
    from app.knowledge import autofill

    autofill.invalidate()
    return {"now_eligible": now_eligible, "now_blocked": now_blocked}
