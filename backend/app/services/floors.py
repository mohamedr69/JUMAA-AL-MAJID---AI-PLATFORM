r"""What a floor is called, and where it sits in a building.

A floor-wise schedule names its floors the way the engineer who wrote it
does -- "GROUND", "B2", "TYPICAL 2ND TO 14TH", "1 to 13" -- and two of
those are not one floor at all but a range that stands for thirteen. This
is the reading of those names: the range written out, and the ordinal
behind a single one.

It was lifted out of the drawings route when that was removed, because
the Excel schedule (`app.services.floor_schedule`) is the one thing that
still needs it.
"""

from __future__ import annotations

import dataclasses
import re

_ORDINALS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7,
    "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11, "TWELFTH": 12, "THIRTEENTH": 13,
    "FOURTEENTH": 14, "FIFTEENTH": 15,
}


@dataclasses.dataclass(frozen=True)
class Floor:
    """A floor as a schedule names it, and where it sorts in a building."""

    name: str
    order: float
    # A column issued for a range of floors ("1 to 13") stands for each of
    # them; the schedule shows a floor per member.
    covers: tuple[str, ...] = ()

    @property
    def is_typical(self) -> bool:
        return len(self.covers) > 1


def _order_of(label: str) -> float:
    """Where a floor sits in a building: basements below the ground floor,
    the roof above everything."""
    text = label.upper()
    basement = re.search(r"\bB(?:ASEMENT)?\s*[- ]?(\d+)", text)
    if basement:
        return -int(basement.group(1))
    if re.search(r"\bBASEMENT\b|\bB\d?\b", text):
        return -1
    if re.search(r"\bROOF\b|\bRF\b|\bR\.?F\b", text):
        return 900
    if re.search(r"\bPENTHOUSE\b|\bPH\b", text):
        return 800
    if re.search(r"\bGROUND\b|\bGF\b|\bG\.?F\b|\bG\b", text):
        return 0
    if re.search(r"\bMEZZANINE\b|\bMF\b|\bMEZZ\b", text):
        return 0.5
    podium = re.search(r"\bP(?:ODIUM)?\s*[- ]?(\d+)", text)
    if podium:
        return 0.6 + int(podium.group(1)) / 100
    for word, value in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return float(value)
    number = re.search(r"\b(\d{1,3})(?:ST|ND|RD|TH)?\b", text)
    if number:
        return float(number.group(1))
    return 500.0   # named but unplaceable: between the last floor and the roof


def _range_of(label: str) -> list[str]:
    """The floors a typical layout covers: "TYPICAL 2ND TO 14TH FLOOR" and
    "FLOOR 1-14" are fourteen rows, "LEVEL 3" is one."""
    text = label.upper()
    span = re.search(r"(\d{1,3})\s*(?:ST|ND|RD|TH)?\s*(?:TO|-|–|/|&)\s*(\d{1,3})\s*(?:ST|ND|RD|TH)?", text)
    if span:
        low, high = int(span.group(1)), int(span.group(2))
        if 0 <= low < high <= 200:
            return [f"Level {n}" for n in range(low, high + 1)]
    return []


def floor_of(label: str) -> Floor:
    """The floor a label names."""
    name = re.sub(r"\s+", " ", (label or "").strip()) or "Unnamed floor"
    covers = tuple(_range_of(name))
    return Floor(name=name, order=_order_of(name), covers=covers)
