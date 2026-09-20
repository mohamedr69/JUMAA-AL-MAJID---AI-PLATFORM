"""Resolve a drawing's symbol groups against the symbol library.

Order of trust:
  1. same signature (identical drawing and letters)      -> verified / ignored
  2. same letters, drawing >= 90% alike, library device  -> verified ("library match")
  3. same Revit family and type as a library device, same letters, and
     its drawing lies on the device's (part may be clipped) -> verified ("family match")
  4. a block name already seen on a verified symbol      -> suggested (name known,
     drawing differs: exactly the wrongly-named case)
  5. same letters, drawing >= 55% alike                  -> suggested with a score;
     device look-alikes first, and never "not a device" on a device layer
  6. nothing                                              -> unknown
"""
from __future__ import annotations

import re

from . import geometry as G

SUGGEST_THRESHOLD = 0.55
AUTO_THRESHOLD = 0.90   # same letters and this close -> counted as the library device
_DEVICE_LAYER = re.compile(r"FIRE|ALARM|\bFA\b|FA[-_]|LITE|LIGHT|EMER|\bEL\b|EL[-_]|DET|SMOKE|COMM|ELEC|EXIT|SPK|SND", re.I)

_raster_cache: dict[tuple[int, str], tuple[int, int]] = {}


def _target_ints(symbol) -> tuple[int, int]:
    key = (symbol.id, symbol.raster_hex)
    hit = _raster_cache.get(key)
    if hit is None:
        syms = G.symmetries_hex(symbol.raster_hex)
        base = syms[0]
        grid = _grid_from_hex(symbol.raster_hex)
        dil = G.grid_to_int(G.dilate(grid)) if grid is not None else 0
        hit = (base, dil)
        _raster_cache[key] = hit
    return hit


def _grid_from_hex(h: str):
    import numpy as np
    if not h:
        return None
    raw = bytes.fromhex(h)
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[: G.RASTER * G.RASTER]
    return bits.reshape(G.RASTER, G.RASTER).astype(bool)


def _query_ints(raster_hex: str) -> tuple[list[int], list[int]]:
    grid = _grid_from_hex(raster_hex)
    if grid is None:
        return [0] * 8, [0] * 8
    return G.symmetries(grid), G.symmetries(G.dilate(grid))


def device_type_out(dt) -> dict | None:
    if dt is None:
        return None
    return {"id": dt.id, "code": dt.code, "name": dt.name, "category": dt.category, "unit": dt.unit}


def hint_score(group: dict) -> int:
    score = 0
    if group.get("label"):
        score += 3
    if any(_DEVICE_LAYER.search(layer or "") for layer in group.get("layers", {})):
        score += 3
    if group.get("direct_count", 0) > 0:
        score += 2
    ents = sum(group.get("entity_counts", {}).values())
    if ents > 60:
        score -= 2
    return score


def resolve(groups: list[dict], symbols: list, aliases: dict[str, int]) -> list[dict]:
    """groups: the stored group dicts of a drawing. symbols: Symbol rows with
    device_type loaded. aliases: upper block name -> symbol id."""
    by_sig = {s.signature: s for s in symbols}
    by_id = {s.id: s for s in symbols}
    out: list[dict] = []
    for g in groups:
        r = dict(g)
        occ = g.get("occurrences", [])
        r["direct_count"] = sum(1 for o in occ if o.get("space") == "Model")
        spaces: dict[str, int] = {}
        for o in occ:
            spaces[o.get("space", "Model")] = spaces.get(o.get("space", "Model"), 0) + 1
        r["spaces"] = spaces
        r["hint"] = hint_score(r)
        r["symbol_id"] = None
        r["device_type"] = None
        r["suggestion"] = None

        r["match"] = None
        exact = by_sig.get(g["signature"])
        if exact is not None:
            r["symbol_id"] = exact.id
            r["match"] = {"kind": "exact", "symbol_id": exact.id, "score": 1.0}
            if exact.is_ignored:
                r["status"] = "ignored"
            else:
                r["device_type"] = device_type_out(exact.device_type)
                r["status"] = "verified" if exact.device_type is not None else "unknown"
            out.append(r)
            continue

        # Closest library symbol by drawing. Letters are what tells a smoke
        # detector from a heat detector, a speaker or an architectural door
        # tag drawn as the same circle, so the letters must agree exactly
        # (including both having none) before shape is compared at all.
        label = G.normalise_label(g.get("label", ""))
        dev_best, dev_sim = None, 0.0   # closest verified device
        ign_best, ign_sim = None, 0.0   # closest "not a device"
        if g.get("raster_hex"):
            q, qd = _query_ints(g["raster_hex"])
            for s in symbols:
                if not s.raster_hex or G.normalise_label(s.label) != label:
                    continue
                t, td = _target_ints(s)
                sim = G.similarity(q, qd, t, td)
                if s.is_ignored:
                    if sim > ign_sim:
                        ign_best, ign_sim = s, sim
                elif s.device_type is not None and sim > dev_sim:
                    dev_best, dev_sim = s, sim

        # 2. Library match: same letters, same drawing within tolerance, and
        # the library says it is a device -> counted, no click needed. A
        # near match to a "not a device" entry is only suggested, so a
        # device is never hidden because it resembles furniture. Without
        # letters the shape alone decides, and at 32x32 a treadmill or a
        # wardrobe looks like an exit sign's box: then it is only suggested.
        if dev_best is not None and dev_sim >= AUTO_THRESHOLD and label:
            _verified(r, dev_best, "library", dev_sim)
            out.append(r)
            continue

        # 3. Revit family match: same Revit family and type as a verified
        # device, same letters, and every line of this drawing lies on the
        # verified one (allowing for a part clipped away in export).
        fam = _family_match(g, label, symbols)
        if fam is not None:
            s, inside, covered = fam
            _verified(r, s, "family", inside)
            r["match"]["coverage"] = round(covered, 3)
            out.append(r)
            continue

        on_device_layer = any(_DEVICE_LAYER.search(layer or "") for layer in g.get("layers", {}))
        best: dict | None = None
        # 4. known block name, different drawing: the wrongly-named case
        for name in g.get("block_names", {}):
            sid = aliases.get(name.upper())
            if sid and sid in by_id and not (by_id[sid].is_ignored and on_device_layer):
                best = _suggestion(by_id[sid], 0.9, f"block name '{name}' is known in the library")
                break
        # 5. similar drawing, below the automatic threshold. A device
        # look-alike is offered before a "not a device" one: a missed device
        # costs more than one more click on a piece of furniture. And a
        # symbol on a fire, lighting, comms or electrical layer is never
        # offered as "not a device", so "Accept all" cannot hide a device.
        if dev_best is not None and dev_sim >= SUGGEST_THRESHOLD and (best is None or dev_sim > best["score"]):
            best = _suggestion(dev_best, dev_sim, f"looks like a library symbol ({dev_sim:.0%} match)")
        if best is None and not on_device_layer and ign_best is not None and ign_sim >= SUGGEST_THRESHOLD:
            best = _suggestion(ign_best, ign_sim, f"looks like a library symbol ({ign_sim:.0%} match)")
        if best is not None:
            r["suggestion"] = best
            r["status"] = "suggested"
        else:
            r["status"] = "unknown"
        out.append(r)
    return out


def _verified(r: dict, s, kind: str, score: float) -> None:
    r["symbol_id"] = s.id
    r["device_type"] = device_type_out(s.device_type)
    r["match"] = {"kind": kind, "symbol_id": s.id, "score": round(float(score), 3), "svg": s.svg}
    r["status"] = "verified"


# Revit exports name a block "<Family> - <Type>-<element id>-<view name>",
# e.g. "INTELLIGENT MANUAL CALL POINT - MANUAL CALL POINT-2016599-FA-104-...".
# The element id and the view (sheet) change from drawing to drawing; the
# family and type are what the designer picked in Revit.
_REVIT_ID = re.compile(r"-(?:\d{3,}|V\d+)-")


def revit_family(block_name: str) -> str | None:
    if " - " not in block_name:
        return None
    m = _REVIT_ID.search(block_name, block_name.index(" - "))
    if m is None:
        return None
    return " ".join(block_name[: m.start()].upper().split())


FAMILY_INSIDE = 0.90    # share of this symbol's lines lying on the verified symbol
FAMILY_COVERED = 0.50   # share of the verified symbol those lines make up

_family_cache: dict[tuple, tuple[float, float]] = {}


def _family_match(g: dict, label: str, symbols: list):
    fams = {f for f in (revit_family(n) for n in g.get("block_names", {})) if f}
    if not fams or not g.get("raster_hex"):
        return None
    cands = [
        s for s in symbols
        if not s.is_ignored and s.device_type is not None and s.raster_hex
        and G.normalise_label(s.label) == label
        and any(revit_family(n) in fams for n in (s.block_names or []))
    ]
    # the same family verified as two different devices with the same
    # letters: the name cannot decide, leave it to a person
    if not cands or len({s.device_type_id for s in cands}) != 1:
        return None
    q = G.grid_from_hex(g["raster_hex"])
    best = None
    for s in cands:
        key = (g["raster_hex"], s.raster_hex)
        res = _family_cache.get(key)
        if res is None:
            res = G.aligned_containment(q, G.grid_from_hex(s.raster_hex))
            _family_cache[key] = res
        inside, covered = res
        if inside >= FAMILY_INSIDE and covered >= FAMILY_COVERED and (best is None or (inside, covered) > best[1:]):
            best = (s, inside, covered)
    return best


def _suggestion(s, score: float, reason: str) -> dict:
    return {
        "symbol_id": s.id,
        "score": round(float(score), 3),
        "reason": reason,
        "is_ignored": bool(s.is_ignored),
        "device_type": device_type_out(s.device_type),
        "label": s.label,
        "svg": s.svg,
    }


def totals(resolved: list[dict]) -> dict:
    t = {
        "fire_alarm": 0,
        "emergency_light": 0,
        "other": 0,
        "verified_symbols": 0,
        "suggested_symbols": 0,
        "suggested_instances": 0,
        "unknown_symbols": 0,
        "unknown_instances": 0,
        "ignored_symbols": 0,
        "not_counted_instances": 0,  # verified, but on a riser/detail sheet or outside every sheet
    }
    for r in resolved:
        st = r["status"]
        if st == "verified":
            t["verified_symbols"] += 1
            qty = r.get("boq_qty", r["count"])
            t[r["device_type"]["category"]] = t.get(r["device_type"]["category"], 0) + qty
            t["not_counted_instances"] += r.get("not_counted", 0)
        elif st == "suggested":
            t["suggested_symbols"] += 1
            t["suggested_instances"] += r["count"]
        elif st == "unknown":
            t["unknown_symbols"] += 1
            t["unknown_instances"] += r["count"]
        elif st == "ignored":
            t["ignored_symbols"] += 1
    return t
