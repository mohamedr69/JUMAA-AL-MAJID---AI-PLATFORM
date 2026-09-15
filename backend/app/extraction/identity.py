"""Building and part-number identity, read off noisy OCR.

Buildings. A multi-building Design Sheet names each building in a banner, and
OCR reads the same banner differently on different pages: EP-30208's
"DHAID - B1 BUILDING" came back as "DH AID-BIBUILDING", "DH AID - BS BUILDING"
and "DHAID - B2 BUILDING". Held only in a free-text group heading, those are
three buildings. `canonical_buildings` settles them per document: banners
whose letters and digits match -- after the building number's own OCR
confusions (I/l -> 1, S -> 5, O -> 0) -- are one building, shown under the
spelling the document uses most, with every variant kept as an alias.

Part numbers. `clean_catalog` removes what is never part of a catalog number
-- a leading "£", "§" or quote that a scan's speck became -- and nothing else;
the OCR text is kept beside it as evidence. `match_catalog` then looks the
cleaned code up in the part libraries the platform already trusts, first
exactly and then allowing one OCR confusion, and says which: the canonical
code is for matching and display, never a silent replacement of the source.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# The confusions OCR makes in the one character a building number is.
_NUMBER_CONFUSIONS = {"I": "1", "L": "1", "|": "1", "S": "5", "O": "0", "Z": "2", "B": "8", "G": "6"}
# "B1 BUILDING", "BIBUILDING", "B 1 BUILDING", "BLOCK A"
_BUILDING_RE = re.compile(r"\b(B|BLDG|BLOCK|TOWER|VILLA|UNIT)\s*[-.]?\s*([0-9IL|SOZG]{1,3}|[A-Z])\s*(BUILDING|BLDG)?\b")


def _alnum(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


@dataclass
class Building:
    key: str
    display: str
    aliases: list[str] = field(default_factory=list)
    rule: str = "as read"

    def as_dict(self) -> dict:
        return {"key": self.key, "display": self.display, "aliases": self.aliases, "rule": self.rule}


def building_key(raw: str) -> tuple[str, str]:
    """(identity key, the rule used). The key is letters and digits only,
    with the building number's OCR confusions resolved."""
    text = raw.upper().replace("BIBUILDING", "B1 BUILDING").replace("BLBUILDING", "B1 BUILDING")
    rule = "as read"
    match = None
    for candidate in _BUILDING_RE.finditer(text):
        match = candidate
    if match:
        number = match.group(2)
        fixed = "".join(_NUMBER_CONFUSIONS.get(ch, ch) for ch in number) if len(number) > 1 or number in _NUMBER_CONFUSIONS else number
        if match.group(1) == "B" and fixed != number:
            rule = f"building number read as {number!r}, taken as {fixed!r}"
        elif match.group(1) != "B":
            fixed = number
        text = text[: match.start(2)] + fixed + text[match.end(2):]
        if "BIBUILDING" in raw.upper() or "BLBUILDING" in raw.upper():
            rule = "building number run into the word (BIBUILDING), taken as B1"
    return _alnum(text), rule


def _tidy(raw: str) -> str:
    text = re.sub(r"\s*-\s*", " - ", raw.strip())
    # "B4BUILDING": the number run into the word.
    text = re.sub(r"\b(B[0-9IL|SOZG]{1,3})(BUILDING|BLDG)\b", r"\1 \2", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text)


def canonical_buildings(raw_sections: list[str]) -> dict[str, Building]:
    """raw banner -> its Building, for every banner in one document."""
    by_key: dict[str, list[str]] = {}
    rules: dict[str, str] = {}
    for raw in raw_sections:
        if not raw:
            continue
        key, rule = building_key(raw)
        by_key.setdefault(key, []).append(raw)
        if rule != "as read":
            rules.setdefault(key, rule)

    # The document's own spelling of the prefix ("DHAID" 7 times against
    # "DH AID" twice) decides how every building under it is shown.
    prefix_votes: Counter[str] = Counter()
    for raws in by_key.values():
        for raw in raws:
            prefix = re.split(r"\s*-\s*", raw.strip(), maxsplit=1)[0]
            prefix_votes[prefix.upper()] += 1
    preferred_prefix: dict[str, str] = {}
    for spelling, _count in prefix_votes.most_common():
        preferred_prefix.setdefault(_alnum(spelling), spelling)

    result: dict[str, Building] = {}
    for key, raws in by_key.items():
        best = Counter(_tidy(r).upper() for r in raws).most_common(1)[0][0]
        parts = re.split(r"\s+-\s+", best, maxsplit=1)
        if len(parts) == 2 and _alnum(parts[0]) in preferred_prefix:
            best = f"{preferred_prefix[_alnum(parts[0])]} - {parts[1]}"
        fixed_key, _rule = building_key(best)
        if fixed_key != _alnum(best):
            # Show the resolved number ("B1"), not the misread one.
            match = list(_BUILDING_RE.finditer(best.upper().replace("BIBUILDING", "B1 BUILDING")))
            if match:
                m = match[-1]
                number = "".join(_NUMBER_CONFUSIONS.get(ch, ch) for ch in m.group(2))
                best = best[: m.start(2)] + number + best[m.end(2):]
                best = best.replace("BIBUILDING", "B1 BUILDING")
        building = Building(key=key, display=best, aliases=sorted(set(raws)), rule=rules.get(key, "as read"))
        for raw in raws:
            result[raw] = building
    return result


# --- part numbers --------------------------------------------------------------------

_EDGE_JUNK_RE = re.compile(r"^[^A-Za-z0-9(]+|[^A-Za-z0-9)]+$")
# Confusions allowed when matching a code against a library, one at a time.
_CODE_CONFUSIONS = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z",
                    "G": "6", "6": "G", "L": "1"}


@dataclass(frozen=True)
class CatalogMatch:
    source: str | None        # as OCR read it
    cleaned: str | None       # edge junk removed; what the BOQ shows
    canonical: str | None     # the library's spelling, when one matched
    reason: str               # how cleaned and canonical were arrived at

    def as_dict(self) -> dict:
        return {"source": self.source, "cleaned": self.cleaned, "canonical": self.canonical, "reason": self.reason}


def clean_catalog(raw: str | None) -> tuple[str | None, str | None]:
    """(the code without edge junk, what was removed -- None when nothing)."""
    if raw is None:
        return None, None
    text = re.sub(r"\s+", " ", raw.strip())
    # Symbols a scan turns a leading letter into: "£232 301H" is "E-232 301H"
    # on the rows OCR read cleanly, "§C-630M" is "SC-630M". Mapped, not dropped.
    lead = re.match(r"^[\s|~_'`‘’\-]*([£€§$])", text)
    mapped = None
    if lead:
        letter = {"£": "E", "€": "E", "§": "S", "$": "S"}[lead.group(1)]
        text = text[: lead.start(1)] + letter + text[lead.end(1):]
        mapped = f"{lead.group(1)!r} read as {letter!r}"
    cleaned = _EDGE_JUNK_RE.sub("", text).strip()
    cleaned = re.sub(r"\s*-\s*", "-", cleaned) if re.search(r"[A-Za-z0-9]\s+-|-\s+[A-Za-z0-9]", cleaned) else cleaned
    if not cleaned:
        return None, f"removed {text!r}: no letters or digits"
    removed = None if cleaned == text else f"removed {text.replace(cleaned, '').strip()!r} around the code"
    if mapped:
        removed = f"{mapped}; {removed}" if removed else mapped
    return cleaned, removed


def part_key(code: str) -> str:
    return _alnum(code)


def match_catalog(raw: str | None, library: dict[str, str]) -> CatalogMatch:
    """`library` maps part_key -> the library's spelling."""
    cleaned, removed = clean_catalog(raw)
    if not cleaned:
        return CatalogMatch(raw, None, None, removed or "empty")
    key = part_key(cleaned)
    prefix = f"{removed}; " if removed else ""
    if key in library:
        return CatalogMatch(raw, cleaned, library[key], prefix + "matches the part library exactly")
    candidates = set()
    for index, ch in enumerate(key):
        swap = _CODE_CONFUSIONS.get(ch)
        if swap:
            variant = key[:index] + swap + key[index + 1:]
            if variant in library:
                candidates.add((library[variant], f"{ch}->{swap} at position {index + 1}"))
    if len(candidates) == 1:
        canonical, how = candidates.pop()
        return CatalogMatch(raw, cleaned, canonical, prefix + f"matches the part library with one OCR confusion ({how}); check it")
    if len(candidates) > 1:
        return CatalogMatch(raw, cleaned, None, prefix + f"could be any of {', '.join(sorted(c for c, _ in candidates))}")
    return CatalogMatch(raw, cleaned, None, prefix + "not in the part library")
