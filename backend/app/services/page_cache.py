"""What was read off a page, kept by the content of the file it is in.

OCR is the slow part of reading a document: a page rendered and handed to
Tesseract takes about two seconds, and a shop drawing is OCRed for the
consultant's stamp whether or not one is there. When the rules for reading
change (`document_sync.INDEX_VERSION`), every document is read again -- but
the pages are the same, so the text Tesseract got off them is too. It is
kept here, keyed by the file's SHA-256, the page, and `OCR_VERSION`, and a
re-read after a rules change re-applies the rules to the stored text
instead of rendering and OCRing every page again.

The same goes for a drawing's ticked approval box (`document_control.
boxed_decision`): finding it means listing every vector shape on the page,
and a CAD floor plan has 400,000 of them -- six seconds a page. What was
found is kept under `document_control.BOX_VERSION`, so only a change to how
boxes are read makes the pages be looked at again.

A separate SQLite file in the cache folder (`CACHE_ROOT`), not the
application database: it is a saving, never the truth -- deleting it costs
one slow sync -- and it is written by every process that reads documents at
once (app.services.document_sync's pool), which WAL and a busy timeout
allow. Anything that goes wrong with it is a cache miss, never an error.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# What the OCR text depends on besides the page: the render resolution and
# Tesseract's settings in document_control._ocr_page. Change either, bump this.
OCR_VERSION = "ocr-1"

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None
_path: Path | None = None
_broken = False


def cache_file() -> Path:
    from app.services.company_library import cache_root

    return cache_root() / "page-cache.sqlite"


def _connect() -> sqlite3.Connection | None:
    global _connection, _path, _broken
    target = cache_file()
    if _connection is not None and _path == target:
        return _connection
    if _broken and _path == target:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target), timeout=15, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("CREATE TABLE IF NOT EXISTS ocr (key TEXT PRIMARY KEY, text TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS box (key TEXT PRIMARY KEY, found TEXT NOT NULL)")
        connection.commit()
    except Exception:  # noqa: BLE001 -- no cache is slower, not wrong
        log.warning("The page cache at %s cannot be used", target, exc_info=True)
        _broken, _path = True, target
        return None
    _connection, _path, _broken = connection, target, False
    return connection


def _key(sha256: str, page_index: int) -> str:
    return f"{sha256}:{page_index}:{OCR_VERSION}"


def get_ocr(sha256: str | None, page_index: int) -> str | None:
    if not sha256:
        return None
    with _lock:
        connection = _connect()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT text FROM ocr WHERE key = ?", (_key(sha256, page_index),)).fetchone()
        except sqlite3.Error:
            return None
    return row[0] if row else None


def put_ocr(sha256: str | None, page_index: int, text: str) -> None:
    if not sha256:
        return
    with _lock:
        connection = _connect()
        if connection is None:
            return
        try:
            connection.execute("INSERT OR REPLACE INTO ocr (key, text) VALUES (?, ?)", (_key(sha256, page_index), text))
            connection.commit()
        except sqlite3.Error:
            log.debug("Could not keep OCR text for %s page %s", sha256, page_index, exc_info=True)


def _box_key(sha256: str, page_index: int, version: str) -> str:
    return f"{sha256}:{page_index}:{version}"


MISSING = object()


def get_box(sha256: str | None, page_index: int, version: str):
    """What `boxed_decision` found on this page under `version` of the rules:
    (status, evidence), None for nothing, or `MISSING` when never looked at."""
    if not sha256:
        return MISSING
    with _lock:
        connection = _connect()
        if connection is None:
            return MISSING
        try:
            row = connection.execute("SELECT found FROM box WHERE key = ?",
                                     (_box_key(sha256, page_index, version),)).fetchone()
        except sqlite3.Error:
            return MISSING
    if row is None:
        return MISSING
    found = json.loads(row[0])
    return tuple(found) if found is not None else None


def put_box(sha256: str | None, page_index: int, version: str, found) -> None:
    if not sha256:
        return
    with _lock:
        connection = _connect()
        if connection is None:
            return
        try:
            connection.execute("INSERT OR REPLACE INTO box (key, found) VALUES (?, ?)",
                               (_box_key(sha256, page_index, version), json.dumps(found)))
            connection.commit()
        except sqlite3.Error:
            log.debug("Could not keep the box reading for %s page %s", sha256, page_index, exc_info=True)
