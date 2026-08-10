"""Persistent, user-editable store for Action Board workstreams.

Stored at ``~/.gmail_triage_workstreams.json`` as a flat JSON list:

    ["Customer Engagements", "Internal Projects", ...]

Mirrors the ``task_store`` pattern: private ``_load`` / ``_save`` helpers,
graceful handling of a missing or corrupt file, and a ``log.warning`` on write
errors.  The public API is a set of plain functions (no class).

The store is *seeded* from ``config.WORKSTREAMS`` the first time it is read
(when no file exists yet), so the configured defaults still apply out of the
box.  After that, edits made in the UI persist here and take precedence — the
config list becomes the initial value only.  ``"unassigned"`` is a reserved
sentinel and is never stored as a real workstream.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path.home() / ".gmail_triage_workstreams.json"

# Reserved sentinel for tasks with no workstream; never a real, editable entry.
UNASSIGNED = "unassigned"


def _defaults() -> list[str]:
    """Config-provided defaults used to seed the store on first use."""
    try:
        from config import WORKSTREAMS  # imported lazily to avoid import cycles
        return list(WORKSTREAMS)
    except Exception:  # noqa: BLE001 — config missing is non-fatal
        return []


def _load() -> list[str] | None:
    if not STORE_PATH.exists():
        return None
    try:
        data = json.loads(STORE_PATH.read_text())
        if isinstance(data, list):
            return [str(x) for x in data]
        return None
    except Exception:  # noqa: BLE001 — corrupt file falls back to seed
        return None


def _save(items: list[str]) -> None:
    try:
        STORE_PATH.write_text(json.dumps(items, indent=2))
    except OSError as exc:
        log.warning("workstream_store: could not write %s: %s", STORE_PATH, exc)


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        name = it.strip()
        if not name or name.lower() == UNASSIGNED:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def list_workstreams() -> list[str]:
    """Return the current workstreams, seeding from config on first use."""
    stored = _load()
    if stored is None:
        seeded = _dedup_preserve_order(_defaults())
        _save(seeded)
        return seeded
    return stored


def add(name: str) -> bool:
    """Add a workstream. Returns True if added, False if blank/duplicate."""
    name = (name or "").strip()
    if not name or name.lower() == UNASSIGNED:
        return False
    items = list_workstreams()
    if any(name.lower() == it.lower() for it in items):
        return False
    items.append(name)
    _save(items)
    return True


def rename(old: str, new: str) -> bool:
    """Rename a workstream. Returns True on success.

    The caller is responsible for cascading the rename onto existing tasks
    (see app.py: tasks whose ``workstream == old`` are updated to ``new``).
    """
    new = (new or "").strip()
    if not new or new.lower() == UNASSIGNED:
        return False
    items = list_workstreams()
    lowered = [it.lower() for it in items]
    if old.lower() not in lowered:
        return False
    # Reject a rename that collides with a different existing entry.
    if new.lower() in lowered and new.lower() != old.lower():
        return False
    items = [new if it.lower() == old.lower() else it for it in items]
    _save(items)
    return True


def delete(name: str) -> bool:
    """Remove a workstream. Returns True if it existed.

    The caller is responsible for reassigning affected tasks (app.py sets any
    task on the deleted workstream back to ``unassigned``).
    """
    items = list_workstreams()
    kept = [it for it in items if it.lower() != name.lower()]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True
