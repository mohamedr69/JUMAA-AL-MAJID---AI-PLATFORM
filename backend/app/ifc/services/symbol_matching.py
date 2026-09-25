"""What Python can say about a symbol nobody has answered yet -- before any
model is asked, and at no cost.

  * `strong_rule`: the symbol's letters and its block name, read
    independently, both name the same device type; it is placed on a fire
    alarm / lighting layer; it is not the architect's; nothing in the
    library resembles it as something else. Then it is that device, taken
    as "deterministic" (IFC_DETERMINISTIC_AUTO_VERIFY). One source alone --
    letters, or a name -- is only ever a suggestion: that is how "M + S"
    on block MSS was once answered as a call point.
  * `candidates`: the few device types a symbol could be, for the AI to
    choose among -- never the whole list. From its words (the hint and its
    family), from what the library suggested, and from the library
    symbols its drawing most resembles.
"""
from __future__ import annotations

from app.ifc import hints
from app.ifc.dxf import geometry as G
from app.ifc.dxf import matcher
from app.ifc.resolve import architecture_like

LOOK_ALIKE = 0.45   # a library device drawn this much like the symbol is a candidate


def on_device_layer(g: dict) -> bool:
    return any(matcher._DEVICE_LAYER.search(layer or "") for layer in g.get("layers", {}))


def strong_rule(g: dict, codes: dict[str, dict]) -> dict | None:
    """The device type the symbol's letters and block name both name, or
    None. `codes`: code -> device type out, active types only."""
    letters, names = hints.readings(g)
    if letters is None or not names:
        return None
    if {code for _, code in names} != {letters[1]}:
        return None
    dt = codes.get(letters[1])
    if dt is None or not on_device_layer(g) or architecture_like(g):
        return None
    sug = g.get("suggestion") or {}
    if sug and (sug.get("is_ignored") or (sug.get("device_type") or {}).get("id") != dt["id"]):
        return None     # the library resembles it as something else: an engineer decides
    return dt


def family_of(dt: dict | None) -> str | None:
    return hints.family_of_type(dt["code"], dt["name"]) if dt else None


def candidates(g: dict, types: list[dict], library_symbols: list, limit: int) -> list[dict]:
    """Up to `limit` device types the symbol could be, most likely first.
    `types`: every active device type out. Empty when nothing points
    anywhere: the symbol then goes to an engineer without costing a call."""
    by_id = {t["id"]: t for t in types}
    out: list[dict] = []

    def add(dt: dict | None) -> None:
        if dt and dt["id"] in by_id and dt["id"] not in {c["id"] for c in out}:
            out.append(by_id[dt["id"]])

    hint = g.get("name_hint") or {}
    add(hint.get("device_type"))
    sug = g.get("suggestion") or {}
    if not sug.get("is_ignored"):
        add(sug.get("device_type"))
    letters, names = hints.readings(g)
    families = {f for f, _ in ([letters] if letters else []) + list(names)} | ({hint["family"]} if hint else set())
    for t in types:
        if families and family_of(t) in families:
            add(t)
    # The library symbols its drawing most resembles, whatever their letters.
    if g.get("raster_hex") and len(out) < limit:
        q, qd = matcher._query_ints(g["raster_hex"])
        scored = []
        for s in library_symbols:
            if s.is_ignored or s.device_type is None or not s.raster_hex:
                continue
            t, td = matcher._target_ints(s)
            sim = G.similarity(q, qd, t, td)
            if sim >= LOOK_ALIKE:
                scored.append((sim, s.device_type_id))
        for _, type_id in sorted(scored, reverse=True):
            add(by_id.get(type_id))
    return out[:limit]
