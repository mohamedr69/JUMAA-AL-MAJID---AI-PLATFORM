"""Compliance statement files, and the fingerprint of a clause.

A statement in a project folder ("FAS Compliance.xlsx", "2. Compliance
Statement revised.xls") is what the Check action lays against the
specification; `candidates` finds them by name. `fingerprint` stands for a
clause's words, blind to spacing, punctuation and case, and is how a
submitted statement's rows are aligned with the specification's clauses
(app.compliance.matcher).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SUFFIXES = (".xlsx", ".xlsm", ".xls", ".docx")
_NAME_RE = re.compile(r"compl(ia|ai)n", re.IGNORECASE)
_SKIP_DIR_RE = re.compile(r"^(\.|~|\$)|^(node_modules|__pycache__)$", re.IGNORECASE)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def fingerprint(text: str) -> str:
    """Twelve hex digits standing for a clause's words, blind to spacing,
    punctuation and case."""
    return hashlib.sha1(normalize(text).encode()).hexdigest()[:12]


@dataclass
class Reference:
    """A statement read as rows, to align with a specification."""

    path: str
    systems: list[str]
    title: list[str]
    rows: list[list[str]]        # [label, text, response, remark]
    fingerprints: list[str]      # per row, "" for rows without an answer
    mtime: float
    size: int
    sections: list[str] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return sum(1 for f in self.fingerprints if f)


def candidates(root: Path):
    """Every file under `root` that is named as a compliance statement."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _SKIP_DIR_RE.search(d)]
        for name in files:
            if name.startswith("~$") or not name.lower().endswith(SUFFIXES) or not _NAME_RE.search(name):
                continue
            yield Path(folder) / name
