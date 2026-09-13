"""Validated results, keyed by content -- never by filename or EP number.

The key is a hash over the evidence itself (what was sent), the task, the
context the task used, and the versions of the parser, the prompt, the
schema, the model and the validation policy. Any of those changing gives a
new key; old rows age out. A hit returns a *validated* result, which the
caller still checks against the reviewed value before applying: the cache
knows what the model said about the evidence, not what an engineer has
since decided.

Simultaneous identical requests share one call: the first takes the key's
lock, the rest wait and read its result.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.models import ResultCache

VALIDATION_POLICY_VERSION = "1"


def cache_key(
    *,
    scope: str,
    document_sha256: str,
    evidence_fingerprint: str,
    task: str,
    context: dict[str, Any],
    parser_version: str,
    prompt_version: str,
    schema_version: str,
    model: str,
) -> str:
    material = json.dumps(
        {
            "scope": scope,
            "document": document_sha256,
            "evidence": evidence_fingerprint,
            "task": task,
            "context": context,
            "parser": parser_version,
            "prompt": prompt_version,
            "schema": schema_version,
            "model": model,
            "policy": VALIDATION_POLICY_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def get(db: Session, key: str, *, project_id: int | None, ttl_days: int,
        document_sha256: str | None = None) -> dict | None:
    """The cached value, if it is not stale and is about the document the
    caller holds.

    Access follows the document, not the project that first asked: the key
    already binds the result to the document's content hash, and a caller
    only ever asks about a document its own project holds, so the same
    sheet filed under two projects is read once. `project_id` is recorded
    for the diagnostics view.
    """
    row = db.get(ResultCache, key)
    if row is None:
        return None
    if document_sha256 is not None and row.document_sha256 != document_sha256:
        return None
    if utc_now() - row.created_at > timedelta(days=ttl_days):
        return None
    row.last_hit_at = utc_now()
    row.hits = (row.hits or 0) + 1
    db.commit()
    return dict(row.value)


def put(db: Session, key: str, value: dict, *, project_id: int | None, document_sha256: str, task: str) -> None:
    row = db.get(ResultCache, key)
    if row is None:
        row = ResultCache(key=key, project_id=project_id, document_sha256=document_sha256, task=task, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


# --- in-flight de-duplication -----------------------------------------------

_inflight: dict[str, threading.Lock] = {}
_inflight_guard = threading.Lock()


class InFlight:
    """`with InFlight(key) as first:` -- `first` is True for the request that
    should make the call; later identical requests block until it is done,
    then re-read the cache."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.first = False
        self._lock: threading.Lock | None = None

    def __enter__(self) -> bool:
        with _inflight_guard:
            lock = _inflight.get(self.key)
            if lock is None:
                lock = threading.Lock()
                _inflight[self.key] = lock
                self.first = True
        self._lock = lock
        lock.acquire()
        return self.first

    def __exit__(self, *_exc) -> None:
        assert self._lock is not None
        self._lock.release()
        if self.first:
            with _inflight_guard:
                _inflight.pop(self.key, None)
