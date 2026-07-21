"""Persistent cache of classification results, keyed by thread content.

Stored at ~/.gmail_triage_cache.json as:
    {thread_id: {"fingerprint": "...", "decision": {...}, "timestamp": iso}}

The fingerprint is derived from the thread's message ids. If a thread gets a
new reply, the fingerprint changes and the cache entry is treated as stale —
so re-classification happens automatically when a thread's content changes.

This is purely a cost-saving cache, NOT a visibility filter: every thread
matching the Gmail query is still fetched and shown in the UI every run.
Only the (expensive) Claude classification step is skipped on a cache hit —
the previously computed decision is reused and surfaced as normal.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

from models import EmailThread, ThreadDecision

STORE_PATH = Path.home() / ".gmail_triage_cache.json"


def _fingerprint(thread: EmailThread) -> str:
    """Changes whenever the thread's messages change (new reply, etc.)."""
    return "|".join(m.id for m in thread.messages)


def _load() -> dict[str, dict]:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict[str, dict]) -> None:
    try:
        STORE_PATH.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        log.warning("decision_cache: could not write %s: %s", STORE_PATH, exc)


def get(thread: EmailThread) -> ThreadDecision | None:
    """Return the cached decision for this thread if its content is unchanged."""
    data = _load()
    entry = data.get(thread.thread_id)
    if not entry:
        return None
    if entry.get("fingerprint") != _fingerprint(thread):
        return None  # thread changed (e.g. new reply) — cache is stale
    try:
        return ThreadDecision(**entry["decision"])
    except Exception:
        return None


def put(thread: EmailThread, decision: ThreadDecision) -> None:
    """Cache a freshly computed decision for this thread's current content."""
    data = _load()
    data[thread.thread_id] = {
        "fingerprint": _fingerprint(thread),
        "decision": decision.as_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _save(data)


def count() -> int:
    return len(_load())


def clear() -> None:
    """Wipe the cache — every thread will be re-classified on the next run."""
    _save({})
