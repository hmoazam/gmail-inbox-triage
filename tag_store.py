"""Persistent, user-editable registry for Action Board tags (colored labels).

Stored at ``~/.gmail_triage_tags.json`` as a flat JSON object mapping tag name
to a hex color string::

    {"urgent": "#e5484d", "blocked": "#f76b15", ...}

Mirrors the ``workstream_store`` pattern: private ``_load`` / ``_save`` helpers,
graceful handling of a missing or corrupt file, and a ``log.warning`` on write
errors.  The public API is a set of plain functions (no class).

Tags are free-form: a label typed on a card auto-registers here (via ``add``)
so its color stays stable across the board.  When no color is supplied, one is
auto-assigned from a fixed palette so distinct tags get distinct, consistent
colors.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path.home() / ".gmail_triage_tags.json"

# Fixed palette used to auto-assign colors when the caller does not supply one.
# Distinct, reasonably accessible hues; we walk the list and fall back to
# cycling once every color is in use.
PALETTE: list[str] = [
    "#e5484d",  # red
    "#f76b15",  # orange
    "#ffb224",  # amber
    "#46a758",  # green
    "#12a594",  # teal
    "#0091ff",  # blue
    "#3e63dd",  # indigo
    "#8e4ec6",  # purple
    "#e93d82",  # pink
    "#889096",  # gray
]


def _load() -> dict[str, str] | None:
    if not STORE_PATH.exists():
        return None
    try:
        data = json.loads(STORE_PATH.read_text())
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return None
    except Exception:  # noqa: BLE001 — corrupt file falls back to empty
        return None


def _save(tags: dict[str, str]) -> None:
    try:
        STORE_PATH.write_text(json.dumps(tags, indent=2))
    except OSError as exc:
        log.warning("tag_store: could not write %s: %s", STORE_PATH, exc)


def _pick_color(existing: dict[str, str]) -> str:
    """Return the next palette color not already in use, else cycle by count."""
    used = set(existing.values())
    for color in PALETTE:
        if color not in used:
            return color
    return PALETTE[len(existing) % len(PALETTE)]


def list_tags() -> dict[str, str]:
    """Return the current tag registry as a ``{name: color}`` dict."""
    return _load() or {}


def add(name: str, color: str | None = None) -> bool:
    """Register a tag. Returns True if added, False if blank or already present.

    When ``color`` is omitted, one is auto-assigned from ``PALETTE``.  An
    existing tag is never recolored here (use ``set_color`` for that) — calling
    ``add`` for a tag that already exists is a harmless no-op returning False.
    """
    name = (name or "").strip()
    if not name:
        return False
    tags = list_tags()
    if any(name.lower() == existing.lower() for existing in tags):
        return False
    tags[name] = color or _pick_color(tags)
    _save(tags)
    return True


def rename(old: str, new: str) -> bool:
    """Rename a tag, preserving its color. Returns True on success.

    The caller is responsible for cascading the rename onto existing tasks
    (tasks whose ``tags`` contain ``old`` should have it replaced with ``new``).
    Rejects a blank ``new`` or a rename that collides with a different tag.
    """
    new = (new or "").strip()
    if not new:
        return False
    tags = list_tags()
    match = next((k for k in tags if k.lower() == old.lower()), None)
    if match is None:
        return False
    # Reject a rename that collides with a *different* existing tag.
    if any(new.lower() == k.lower() and k.lower() != old.lower() for k in tags):
        return False
    color = tags.pop(match)
    tags[new] = color
    _save(tags)
    return True


def set_color(name: str, color: str) -> bool:
    """Set the color of an existing tag. Returns True on success, False if absent."""
    color = (color or "").strip()
    if not color:
        return False
    tags = list_tags()
    match = next((k for k in tags if k.lower() == name.lower()), None)
    if match is None:
        return False
    tags[match] = color
    _save(tags)
    return True


def delete(name: str) -> bool:
    """Remove a tag from the registry. Returns True if it existed, False if not.

    The caller is responsible for stripping the tag off any tasks that carry it.
    """
    tags = list_tags()
    match = next((k for k in tags if k.lower() == name.lower()), None)
    if match is None:
        return False
    del tags[match]
    _save(tags)
    return True
