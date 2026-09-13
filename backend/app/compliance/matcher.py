"""Finding the past answer to a clause.

First the statements closest to the new specification: the ones that share
the most clauses word for word (a fingerprint match), which is how a
statement written against the same master specification shows itself. Then,
per clause, the closest clause among those statements, by the words they
share in order. When several statements answered the same clause, the answer
most of them gave wins -- one engineer's slip does not become the rule.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from .references import Reference, fingerprint, normalize
from .spec_text import Clause
from .statements import canonical

MAX_REFERENCES = 6
MAX_CANDIDATES = 10
_STOP = {
    "the", "shall", "be", "and", "of", "to", "a", "an", "in", "for", "with", "all", "as", "by", "or", "on", "is",
    "are", "any", "other", "such", "that", "this", "which", "at", "from", "each", "its", "it", "not", "include",
    "including", "following", "provide", "section", "system", "systems", "requirements",
}


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in _STOP and len(t) > 1]


@dataclass
class RankedReference:
    reference: Reference
    shared: int          # clauses of the new specification it answered word for word
    score: float


@dataclass
class ClauseMatch:
    similarity: float
    response: str        # as the past statement wrote it
    answer: str | None   # canonical; None only for an unanswered row
    remark: str
    reference_path: str
    reference_label: str
    reference_text: str
    agreeing: int        # statements that gave this answer to a clause this close
    disagreeing: int


def rank(clauses: list[Clause], pool: list[Reference], *, brand: str | None = None,
         exclude: set[str] | None = None) -> list[RankedReference]:
    wanted = {fingerprint(c.text) for c in clauses if not c.heading and len(c.text) >= 12}
    vocabulary = Counter(t for c in clauses for t in set(tokens(c.text)))
    ranked: list[RankedReference] = []
    for ref in pool:
        if exclude and ref.path in exclude:
            continue
        shared = len(wanted & set(f for f in ref.fingerprints if f))
        if shared == 0:
            # Nothing word for word: fall back to the vocabulary they share,
            # which still tells a fire alarm statement from a lighting one.
            ref_words = {t for row, f in zip(ref.rows, ref.fingerprints) if f for t in tokens(row[1])}
            overlap = sum(1 for t in vocabulary if t in ref_words) / max(1, len(vocabulary))
            score = overlap * 0.5
        else:
            score = shared / max(1, len(wanted)) + 0.5
        if brand and any(brand.lower() in line.lower() for line in ref.title):
            score += 0.05
        if score > 0.05:
            ranked.append(RankedReference(ref, shared, score))
    ranked.sort(key=lambda r: (-r.score, -r.reference.answered))
    return ranked[:MAX_REFERENCES]


def _similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def match(clauses: list[Clause], ranked: list[RankedReference], *, hint_below: float = 0.6,
          require_answer: bool = True) -> dict[str, ClauseMatch]:
    """The best past answer for every clause that has one at least
    `hint_below` alike, keyed by clause id. With `require_answer` off, a row
    left unanswered still matches (a statement being checked)."""
    rows: list[tuple[Reference, list[str], list[str]]] = []   # (reference, row, tokens)
    by_fingerprint: dict[str, list[int]] = defaultdict(list)
    postings: dict[str, list[int]] = defaultdict(list)
    for item in ranked:
        ref = item.reference
        for row, fp in zip(ref.rows, ref.fingerprints):
            if not fp:
                continue
            index = len(rows)
            words = tokens(row[1])
            rows.append((ref, row, words))
            by_fingerprint[fp].append(index)
            for word in set(words):
                postings[word].append(index)
    if not rows:
        return {}
    idf = {word: math.log(len(rows) / len(ids)) + 1 for word, ids in postings.items()}

    found: dict[str, ClauseMatch] = {}
    for clause in clauses:
        if clause.heading or len(clause.text) < 6:
            continue
        words = tokens(clause.text)
        exact = by_fingerprint.get(fingerprint(clause.text), [])
        scored: list[tuple[float, int]] = [(1.0, i) for i in exact]
        if not exact:
            weight: dict[int, float] = defaultdict(float)
            for word in set(words):
                for i in postings.get(word, ()):
                    weight[i] += idf[word]
            best = sorted(weight, key=weight.get, reverse=True)[:MAX_CANDIDATES]
            scored = [(_similarity(words, rows[i][2]), i) for i in best]
        scored = [(s, i) for s, i in scored if s >= hint_below]
        if not scored:
            continue
        top = max(s for s, _ in scored)
        close = [(s, i) for s, i in scored if s >= top - 0.03]
        votes = Counter(canonical(rows[i][1][2]) for _, i in close)
        answer, agreeing = votes.most_common(1)[0]
        if answer is None and require_answer:
            continue
        s, i = max(((s, i) for s, i in close if canonical(rows[i][1][2]) == answer), key=lambda x: x[0])
        ref, row, _ = rows[i]
        found[clause.id] = ClauseMatch(
            similarity=round(s, 3),
            response=row[2],
            answer=answer,
            remark=row[3],
            reference_path=ref.path,
            reference_label=row[0],
            reference_text=row[1][:400],
            agreeing=agreeing,
            disagreeing=sum(votes.values()) - agreeing,
        )
    return found


_BRAND_RE = re.compile(r"\b(edwards|notifier|simplex|siemens|honeywell|morley|esser|hochiki|apollo|kidde|bosch|"
                       r"gent|advanced|cooper|menvier|eaton|ctec|c-tec|zeta|nittan|toa|bose|inter-m|dsppa|"
                       r"schneider|legrand|zemper|kentec|teknim|ziton|aritech|mircom|firelite|fire-lite|system sensor)\b",
                       re.IGNORECASE)


def brands_in(text: str) -> set[str]:
    return {m.lower() for m in _BRAND_RE.findall(text or "")}
