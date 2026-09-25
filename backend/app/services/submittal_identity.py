"""What makes a material submittal the one submittal it is.

A system's material submittal is one per brand: the fire alarm from
Edwards is one submittal, revised R0, R1, ... until approved; fire rated
cable offered from Fireguard, Frontier and Tianjie is three -- each brand
submitted, answered and revised on its own, and none of them to be lost
behind another. What is *not* another submittal: a revision, a reply, our
own copy of a form beside the one the consultant answered, the same brand
spelled two ways (Menvier is Eaton's), a file filed twice.

    (system, brand)   for a submittal of a system
    (reference)       for a package that names no system
"""

from __future__ import annotations

import re

# Names that list more than one party: "FIREGUARD / RAMCRO" is the maker
# and its local distributor, "EDWARDS & NOTIFIER" two makers. The brand a
# submittal is filed under is the first.
_SEVERAL = re.compile(r"\s*(?:/|&|,|\band\b|\+)\s*", re.I)


def _maker(name: str | None) -> str | None:
    """A form writes the maker as the letter does -- "M/s. EDWARDS"."""
    if not name:
        return None
    return re.sub(r"^\s*m\s*/\s*s\.?\s*", "", name, flags=re.IGNORECASE).strip() or None


def brand_key(manufacturer: str | None) -> str:
    """The brand a submittal is for, in one spelling, or "" when none is
    known: 'M/s. EATON (MENVIER)', 'MENVIER' and 'Eaton' are EATON;
    'M/S. TIANJIE / RAMCRO' and 'TIANJIE' are TIANJIE."""
    from app.knowledge.policy import canonical_manufacturer
    from app.services import brands

    name = _maker(manufacturer)
    if not name:
        return ""
    whole = canonical_manufacturer(name) or brands.normalise(name) or ""
    if not _SEVERAL.search(whole):
        return whole.upper()
    first = _SEVERAL.split(name)[0].strip(" -()")
    return (canonical_manufacturer(first) or brands.normalise(first) or whole).upper()


def submittal_key(system_code: str | None, brand: str | None, reference: str | None) -> tuple:
    """(system, brand) for a submittal of a system; its reference for one
    that names no system."""
    if system_code:
        return ("system", system_code, brand or "")
    return ("reference", (reference or "").upper())


def settle_unknown_brands(keys: list[tuple[str | None, str]]) -> dict[tuple[str | None, str], str]:
    """A form whose brand could not be read belongs to its system's brand
    when the system has only one; with several it cannot be told which, and
    stays apart ("") rather than being guessed into one. `keys` are
    (system, brand) pairs; returns what each pair's brand becomes."""
    known: dict[str | None, set[str]] = {}
    for system, brand in keys:
        if brand:
            known.setdefault(system, set()).add(brand)
    settled = {}
    for system, brand in keys:
        only = known.get(system) or set()
        settled[(system, brand)] = brand or (next(iter(only)) if len(only) == 1 else "")
    return settled
