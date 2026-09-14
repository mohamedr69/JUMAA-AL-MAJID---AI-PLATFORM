"""Conservative normalisation of requirement wording.

Two clauses are "the same requirement" only when they read the same after
removing what printing adds: line wrapping, runs of spaces, typographic
quotes and dashes, letter case, and a leading outline label ("A.", "1)").
Nothing that carries meaning is touched -- numbers, units, comparison
operators, negation, standards editions, model suffixes, qualifications --
so "not less than 24 hours" and "not less than 48 hours" stay different, and
so do "SIGA-PS" and "SIGA-PD". Fuzzy similarity is a way to find candidates
for an engineer to look at, never grounds to reuse an answer.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_TYPOGRAPHY = {
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", " ": " ", "•": " ", "·": " ",
}
# An outline label at the start: "A.", "1.", "a)", "(iv)", "1.2.3" -- followed
# by whitespace, so "1.5 mm" keeps its "1.5".
_LABEL_RE = re.compile(r"^(?:\(?(?:[A-Za-z]|[0-9]{1,3}|[ivxIVX]{1,4})(?:\.[0-9]{1,3}){0,3}[.)]\s+)+")
_SPACE_RE = re.compile(r"\s+")


def normalize_requirement(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    text = _SPACE_RE.sub(" ", text).strip()
    text = _LABEL_RE.sub("", text)
    # Spacing around punctuation is printing, not meaning.
    text = re.sub(r"\s+([,;:.)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return text.lower()


def requirement_hash(text: str | None) -> str:
    return hashlib.sha1(normalize_requirement(text).encode("utf-8")).hexdigest()[:16]


_MODEL_SPLIT_RE = re.compile(r"[,;/&+]|\band\b|\bor\b|\s{2,}", re.IGNORECASE)


def normalize_model(model: str | None) -> str:
    """A model number as it compares: case, spaces and hyphens do not
    distinguish models ("SIGA PS", "siga-ps"); a suffix does ("SIGA-PS-1")."""
    return re.sub(r"[\s\-_.]+", "", (model or "").upper())


def split_models(text: str | None) -> list[str]:
    """The model numbers a free-text field names, as written."""
    found: list[str] = []
    for part in _MODEL_SPLIT_RE.split(text or ""):
        part = part.strip(" .:;()")
        # A model number has a letter and either a digit or a hyphen
        # ("EST3", "SIGA-PS", "MP2HI3H-M"); a word has neither.
        if 2 <= len(part) <= 40 and re.search(r"[A-Za-z]", part) and (re.search(r"\d", part) or "-" in part) and part not in found:
            found.append(part)
    return found
