"""Re-read stored drawings with the current extraction rules.

Drawings keep the symbol groups read at upload time. When the extraction
improves (for example, reading the "wp" beside a call point), re-reading
changes some symbols' letters and therefore their fingerprints. Nothing the
user decided is lost:

  * a new group whose instances were all verified as one device, or all
    marked "not a device", before the re-read inherits that decision;
  * if its letters now carry the weatherproof mark (WP, W/P) and the old
    device type has a weatherproof version ("MCP" -> "MCP-WP"), it becomes
    that version, which is the point of reading the mark;
  * groups whose instances had mixed or no decisions stay for review --
    after the same identification a new drawing gets
    (app.ifc.services.classification): a signature the library knows costs
    nothing, and only new, still unanswered ones reach the AI, through its
    cache first. `use_ai` False (the admin endpoint answered in the
    request) stops at the deterministic rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import IfcBlockAlias as BlockAlias
from app.models import IfcDeviceType as DeviceType
from app.models import IfcSymbol as Symbol
from app.models import ProjectIfcDrawing as Drawing

from .dxf import matcher
from .dxf.extract import extract
from .dxf.loose import LOOSE_NAME
from .resolve import library
from .storage import dxf_path

WP_MARKS = {"WP", "W/P", "W.P", "W.P."}


@dataclass
class ReprocessReport:
    drawings: int = 0
    carried_over: int = 0
    weatherproof: list[str] = field(default_factory=list)
    left_for_review: int = 0
    totals: dict[int, dict] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    deterministic: int = 0
    ai_verified: int = 0


def _occ_key(o: dict) -> tuple:
    return (o.get("space"), o.get("handle"), o.get("block_name"), round(o.get("x", 0), 1), round(o.get("y", 0), 1))


def _has_wp(label: str) -> bool:
    return any(part.strip() in WP_MARKS for part in label.split("+"))


def _save_symbol(db: Session, g: dict, source: str, device_type_id: int | None, ignored: bool, note: str) -> None:
    s = db.query(Symbol).filter(Symbol.signature == g["signature"]).first()
    if s is None:
        s = Symbol(signature=g["signature"], source_drawing=source)
        db.add(s)
    s.label = g.get("label", "")
    s.inner_label = g.get("inner_label", "")
    s.raster_hex = g.get("raster_hex", "")
    s.svg = g.get("svg", "")
    s.entity_counts = g.get("entity_counts", {})
    s.block_names = sorted(set(list(s.block_names or []) + list(g.get("block_names", {}).keys())))
    s.is_ignored = ignored
    s.device_type_id = None if ignored else device_type_id
    s.notes = note
    db.flush()
    for name in g.get("block_names", {}):
        if name == LOOSE_NAME:
            continue  # shared by every symbol drawn without a block
        key = name.upper()
        if db.query(BlockAlias).filter(BlockAlias.block_name == key).first() is None:
            db.add(BlockAlias(block_name=key, symbol_id=s.id))


def reprocess_all(db: Session, *, use_ai: bool = False, check=None, progress=None) -> ReprocessReport:
    from app.ifc.services import classification

    rep = ReprocessReport()
    types = {t.id: t for t in db.query(DeviceType).all()}
    by_code = {t.code.upper(): t for t in types.values()}

    drawings = db.query(Drawing).filter(Drawing.deleted_at.is_(None)).order_by(Drawing.id).all()
    for index, d in enumerate(drawings):
        if check is not None:
            check()
        if progress is not None:
            progress(index, len(drawings), d.filename)
        symbols, aliases = library(db)
        old = matcher.resolve(d.groups or [], symbols, aliases)
        decision_of: dict[tuple, tuple] = {}
        for g in old:
            if g["status"] == "verified":
                dec = ("device", g["device_type"]["id"])
            elif g["status"] == "ignored":
                dec = ("ignored", None)
            else:
                continue
            for o in g.get("occurrences", []):
                decision_of[_occ_key(o)] = dec
        try:
            result = extract(str(dxf_path(d))).to_dict()
        except Exception as exc:
            rep.errors[d.id] = str(exc)
            continue

        meta = dict(d.meta or {})
        meta.update({"containers": result["containers"], "skipped_empty_blocks": result["skipped_empty_blocks"], "layouts": result["layouts"], "sheets": result["sheets"], "architecture": result["architecture"], "loose_symbols": result["loose_symbols"]})
        d.meta = meta
        d.groups = result["groups"]
        d.seconds = result["seconds"]
        rep.drawings += 1

        known = {s.signature for s in symbols}
        before = {g["signature"] for g in old}
        new = matcher.resolve(result["groups"], symbols, aliases)
        for g in new:
            if g["signature"] in before:
                continue  # same symbol as before the re-read: it keeps its own state
            if g["signature"] in known or g["status"] == "verified":
                continue  # exact or library match: nothing to carry
            decs = {decision_of.get(_occ_key(o)) for o in g.get("occurrences", [])}
            if len(decs) != 1 or None in decs:
                if g["status"] in ("unknown", "suggested"):
                    rep.left_for_review += 1
                continue
            kind, type_id = decs.pop()
            if kind == "ignored":
                _save_symbol(db, g, d.filename, None, True, "Carried over after re-reading the drawing")
                rep.carried_over += 1
                continue
            dt = types[type_id]
            note = "Carried over after re-reading the drawing"
            if _has_wp(g.get("label", "")) and not dt.code.upper().endswith("-WP"):
                wp = by_code.get(dt.code.upper() + "-WP")
                if wp is not None:
                    rep.weatherproof.append(f"{d.filename}: {g['count']} x {dt.code} -> {wp.code}")
                    dt = wp
                    note = "Weatherproof: 'wp' marked beside the symbol on the plan"
            _save_symbol(db, g, d.filename, dt.id, False, note)
            rep.carried_over += 1
        db.commit()
        # What is still unanswered after the carry-over: the rules, the cache, then the AI.
        queue, summary = classification.classify(db, groups=d.groups or [], meta=d.meta or {}, project_id=d.project_id,
                                                 drawing_name=d.filename, use_ai=use_ai, check=check)
        rep.deterministic += summary.deterministic
        rep.ai_verified += summary.ai_verified
        meta = dict(d.meta or {})
        meta["symbol_review"] = queue
        meta["classified"] = {**(meta.get("classified") or {}), **summary.classified}
        d.meta = meta
        db.commit()

    symbols, aliases = library(db)
    if progress is not None:
        progress(len(drawings), len(drawings), "")
    for d in drawings:
        rep.totals[d.id] = matcher.totals(matcher.resolve(d.groups or [], symbols, aliases))
    return rep
