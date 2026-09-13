"""The company's past compliance statements, indexed.

Most specifications are variants of a handful of master specifications, so
most clauses of a new one have been answered before, in a statement filed
somewhere in the archive ("FAS Compliance.xlsx", "2. Compliance Statement
revised.xls"). Those answers are the cheapest and the most consistent source
there is: an engineer already decided them.

The archive is a synced drive of thousands of folders, so it is walked once
in the background and the parsed tables are kept in the cache directory,
keyed by path, size and modification time -- a later walk re-reads only what
changed. Each statement keeps its rows and a fingerprint of each clause, so
finding the statements closest to a new specification is set arithmetic,
not a read of every file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.services import company_library

from .statements import STATEMENT_PARSER_VERSION, read_statement

INDEX_VERSION = f"refs-1/{STATEMENT_PARSER_VERSION}"
SUFFIXES = (".xlsx", ".xlsm", ".xls", ".docx")
MAX_FILE_BYTES = 15 * 1024 * 1024
_NAME_RE = re.compile(r"compl(ia|ai)n", re.IGNORECASE)
_SKIP_DIR_RE = re.compile(r"^(\.|~|\$)|^(node_modules|__pycache__)$", re.IGNORECASE)

# The system a statement answers, from the words its folders and title use.
# Whole words only: "FA" in "FA Compliance.xlsx" is fire alarm, the "fa" in
# "facade" is not.
_SYSTEM_WORDS = {
    "FAS": ("fas", "fa", "fire alarm", "fire detection", "fafd", "fire alarm system", "est4", "est3"),
    "EML": ("eml", "em", "elm", "emlsc", "emergency light", "emergency lighting", "exit light", "self contained"),
    "CBS": ("cbs", "central battery"),
    "VES": ("ves", "voice evacuation", "voice evac", "ve"),
    "PAVA": ("pava", "pa", "bgm", "public address", "pa va", "background music"),
}
_NOT_A_SYSTEM = re.compile(r"cable|cabling|wire|containment|conduit", re.IGNORECASE)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def fingerprint(text: str) -> str:
    """Twelve hex digits standing for a clause's words, blind to spacing,
    punctuation and case."""
    return hashlib.sha1(normalize(text).encode()).hexdigest()[:12]


def systems_named(text: str) -> list[str]:
    padded = f" {normalize(text)} "
    return [code for code, words in _SYSTEM_WORDS.items() if any(f" {w} " in padded for w in words)]


@dataclass
class Reference:
    path: str                    # relative to the archive root
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


@dataclass
class ScanState:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    files_seen: int = 0
    files_read: int = 0
    statements: int = 0
    errors: int = 0
    current: str | None = None
    message: str | None = None


_state = ScanState()
_state_lock = threading.Lock()
_index: dict[str, Reference] | None = None
_loaded_mtime = 0.0
_index_lock = threading.Lock()


def index_path() -> Path:
    return company_library.cache_root() / "compliance-references" / "index.json.gz"


def archive_root() -> Path | None:
    configured = (get_settings().projects_root or "").strip()
    return Path(configured) if configured else None


def _load() -> dict[str, Reference]:
    """The index, read again when the file on disk is newer than the copy in
    memory -- the terminal script and the server can both write it."""
    global _index, _loaded_mtime
    with _index_lock:
        path = index_path()
        on_disk = path.stat().st_mtime if path.is_file() else None
        running = _state.running
        if _index is not None and (running or on_disk is None or on_disk <= _loaded_mtime):
            return _index
        loaded: dict[str, Reference] = {}
        _loaded_mtime = on_disk or 0.0
        if path.is_file():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if payload.get("version") == INDEX_VERSION:
                    loaded = {k: Reference(**v) for k, v in payload.get("files", {}).items()}
            except (OSError, ValueError, TypeError):
                loaded = {}
        _index = loaded
        return _index


def _save(index: dict[str, Reference]) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump({"version": INDEX_VERSION, "files": {k: v.__dict__ for k, v in index.items()}}, handle)
    temporary.replace(path)
    global _loaded_mtime
    _loaded_mtime = path.stat().st_mtime


def references(system_code: str | None = None) -> list[Reference]:
    index = _load()
    items = list(index.values())
    if system_code:
        items = [r for r in items if system_code in r.systems]
    return items


def status() -> dict:
    with _state_lock:
        state = dict(_state.__dict__)
    index = _load()
    by_system: dict[str, int] = {}
    for ref in index.values():
        for code in ref.systems:
            by_system[code] = by_system.get(code, 0) + 1
    state.update(indexed=len(index), by_system=by_system, root=str(archive_root() or ""))
    return state


def candidates(root: Path):
    """Every file under the archive that is named as a compliance statement."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _SKIP_DIR_RE.search(d)]
        for name in files:
            if name.startswith("~$") or not name.lower().endswith(SUFFIXES) or not _NAME_RE.search(name):
                continue
            yield Path(folder) / name


def _classify(relative: str, title: list[str], rows: list[list[str]]) -> tuple[list[str], list[str]]:
    from app.services.spec_finder import system_of

    head = " ".join(title[:10])
    sections = sorted({m.replace(" ", "") for m in re.findall(r"\b\d{2}\s?\d{2}\s?\d{2}\b", head + " " + " ".join(r[1] for r in rows[:6]))})
    found: list[str] = []
    for code in systems_named(" ".join(Path(relative).parts[-4:])) + systems_named(head):
        if code not in found:
            found.append(code)
    if not found:
        found = [s.code for s in system_of(head + " " + " ".join(r[1] for r in rows[:10]))]
    if _NOT_A_SYSTEM.search(Path(relative).name) and not systems_named(head):
        found = []  # a cable compliance is not the system's statement
    return found, sections


def read_reference(path: Path, relative: str) -> Reference | None:
    stat = path.stat()
    statement = read_statement(path.read_bytes(), path.name)
    if statement is None:
        return None
    rows = [[r.label, r.text, r.response, r.remark] for r in statement.rows]
    systems, sections = _classify(relative, statement.title_lines, rows)
    return Reference(
        path=relative,
        systems=systems,
        title=statement.title_lines[:12],
        rows=rows,
        fingerprints=[fingerprint(r.text) if r.answer and len(r.text) >= 12 else "" for r in statement.rows],
        mtime=stat.st_mtime,
        size=stat.st_size,
        sections=sections,
    )


def scan(root: Path | None = None, *, full: bool = False) -> dict:
    """Walk the archive and (re)read the statements that changed. Runs in the
    caller's thread; `start_scan` runs it in the background."""
    global _index
    root = root or archive_root()
    if root is None or not root.is_dir():
        with _state_lock:
            _state.running = False
            _state.message = "The project archive (PROJECTS_ROOT) is not set or not reachable."
        return status()
    index = {} if full else dict(_load())
    seen: set[str] = set()
    with _state_lock:
        _state.__dict__.update(running=True, started_at=time.time(), finished_at=None, files_seen=0, files_read=0,
                               statements=0, errors=0, message=None)
    try:
        for path in candidates(root):
            relative = str(path.relative_to(root))
            seen.add(relative)
            with _state_lock:
                _state.files_seen += 1
                _state.current = relative
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_FILE_BYTES:
                continue
            known = index.get(relative)
            if known is not None and known.size == stat.st_size and abs(known.mtime - stat.st_mtime) < 1:
                continue
            try:
                reference = read_reference(path, relative)
            except Exception:  # noqa: BLE001 -- a corrupt or online-only file is skipped, not fatal
                with _state_lock:
                    _state.errors += 1
                index.pop(relative, None)
                continue
            with _state_lock:
                _state.files_read += 1
            if reference is None:
                index.pop(relative, None)
            else:
                index[relative] = reference
            # Keep what has been read so far, so a long first walk is useful
            # before it ends.
            if _state.files_read % 50 == 0:
                with _index_lock:
                    _index = dict(index)
                _save(index)
        for gone in set(index) - seen:
            index.pop(gone, None)
        with _index_lock:
            _index = index
        _save(index)
    finally:
        with _state_lock:
            _state.running = False
            _state.finished_at = time.time()
            _state.current = None
            _state.statements = len(index)
    return status()


def start_scan(*, full: bool = False) -> bool:
    """Start a background walk. False when one is already running."""
    with _state_lock:
        if _state.running:
            return False
        _state.running = True
    threading.Thread(target=scan, kwargs={"full": full}, name="compliance-references", daemon=True).start()
    return True


def reset_for_tests(index: dict[str, Reference] | None = None) -> None:
    global _index, _loaded_mtime
    with _index_lock:
        _index = index
        # An index handed in by a test is not replaced by whatever is on disk.
        _loaded_mtime = float("inf") if index is not None else 0.0
    with _state_lock:
        _state.__dict__.update(ScanState().__dict__)
