"""Writing the symbol library: who decided, and aliases kept honest.

Authority, highest first: an engineer's answer, then a deterministic one
(the symbol's own letters and block name), then the AI's. A symbol is
written only by its own authority or a higher one -- the AI never touches
a symbol that is in the library, and nothing but an engineer changes an
engineer's answer.

A block name is a hint, not a meaning: "SD" on one consultant's drawings
may be another's sliding door. The first symbol verified under a name
keeps it; a later symbol with the same name answered differently marks the
name ambiguous (it then suggests nothing) instead of taking it over.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.ifc.dxf.loose import LOOSE_NAME
from app.models import IfcBlockAlias, IfcSymbol, IfcSymbolReview

ENGINEER, DETERMINISTIC, AI = "engineer", "deterministic", "ai"
RANK = {AI: 1, DETERMINISTIC: 2, ENGINEER: 3}


def _decision(s: IfcSymbol) -> tuple:
    return (bool(s.is_ignored), None if s.is_ignored else s.device_type_id)


def remember(db: Session, g: dict, device_type_id: int | None, *, source: str, drawing_name: str,
             user=None, notes: str = "", confidence: float | None = None) -> IfcSymbol | None:
    """Put one symbol group's exact drawing in the library, as the device
    type or (device_type_id None) as not a device. Returns the symbol, or
    None when a higher authority already answered it (nothing is written).
    The caller commits."""
    sig = g["signature"]
    s = db.query(IfcSymbol).filter(IfcSymbol.signature == sig).first()
    if s is not None and RANK.get(s.source or ENGINEER, 3) > RANK[source]:
        return None
    if s is not None and source != ENGINEER and RANK.get(s.source or ENGINEER, 3) == RANK[source]:
        return None     # the same authority already answered: the first answer stands
    if s is None:
        s = IfcSymbol(signature=sig, source_drawing=drawing_name[:300], created_by_id=user.id if user else None)
        db.add(s)
    s.label = g.get("label", "")
    s.inner_label = g.get("inner_label", "")
    s.raster_hex = g.get("raster_hex", "")
    s.svg = g.get("svg", "")
    s.entity_counts = g.get("entity_counts", {})
    s.block_names = sorted(set(list(s.block_names or []) + list(g.get("block_names", {}).keys())))
    s.is_ignored = device_type_id is None
    s.device_type_id = device_type_id
    s.source = source
    s.confidence = confidence if source == AI else None
    if source == ENGINEER:
        s.reviewed_by_id = user.id if user else None
        s.reviewed_at = utc_now()
    if notes:
        s.notes = notes
    db.flush()
    _aliases(db, s, g.get("block_names", {}))
    return s


def _aliases(db: Session, s: IfcSymbol, names) -> None:
    for name in names:
        if name == LOOSE_NAME:
            continue  # every symbol drawn without a block shares it: not a name to know one by
        key = name.upper()
        alias = db.query(IfcBlockAlias).filter(IfcBlockAlias.block_name == key).first()
        if alias is None:
            db.add(IfcBlockAlias(block_name=key, symbol_id=s.id))
        elif alias.symbol_id != s.id and not alias.is_ambiguous:
            other = db.get(IfcSymbol, alias.symbol_id)
            if other is None:
                alias.symbol_id = s.id
            elif _decision(other) != _decision(s):
                alias.is_ambiguous = True   # two meanings: neither is overwritten, neither is suggested
    db.flush()


def ambiguous_names(db: Session) -> set[str]:
    return {name for (name,) in db.query(IfcBlockAlias.block_name).filter(IfcBlockAlias.is_ambiguous.is_(True)).all()}


def record_outcome(db: Session, signature: str, device_type_id: int | None, *, user=None) -> None:
    """What the engineer did with a symbol the AI looked at, written on the
    AI's latest review of it, so its answers are measured against theirs."""
    row = (db.query(IfcSymbolReview)
           .filter(IfcSymbolReview.signature == signature, IfcSymbolReview.error.is_(None),
                   IfcSymbolReview.invalidated_at.is_(None))
           .order_by(IfcSymbolReview.id.desc()).first())
    if row is None:
        return
    if device_type_id is None:
        outcome = "approved" if row.decision == "not_device" else "not_device"
    elif row.decision == "device":
        outcome = "approved" if row.device_type_id == device_type_id else "corrected"
    else:
        outcome = "device"
    row.outcome, row.outcome_device_type_id = outcome, device_type_id
    row.outcome_by_id, row.outcome_at = (user.id if user else None), utc_now()


def invalidate(db: Session, signatures: list[str]) -> int:
    """Forget what the AI said about these symbols: an engineer took them
    out of the library, so the AI's old answer is not reused either."""
    return (db.query(IfcSymbolReview)
            .filter(IfcSymbolReview.signature.in_(signatures), IfcSymbolReview.invalidated_at.is_(None))
            .update({IfcSymbolReview.invalidated_at: utc_now()}, synchronize_session=False))
