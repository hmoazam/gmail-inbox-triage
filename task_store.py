"""Persistent store for Action Board tasks.

Stored at ``~/.gmail_triage_tasks.json`` as a flat dict keyed by task id:

    {
      "<uuid>": { <Task.as_dict() fields> },
      ...
    }

Mirrors the pattern in ``decision_cache.py``: private ``_load`` / ``_save``
helpers, graceful handling of a missing or corrupt file, and a ``log.warning``
on write errors.  The public API is a set of plain functions (no class).

Dedup keys
----------
``find_by_source(source, source_ref, title)`` is the ingestion-side guard.
For email tasks the natural key is ``(source="email", source_ref=thread_id,
normalised title)``; for transcripts it is ``(source, source_ref=file_id,
normalised title)``.  Normalisation: lowercase + collapse whitespace.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import Task, TASK_STATUS_DONE

log = logging.getLogger(__name__)

STORE_PATH = Path.home() / ".gmail_triage_tasks.json"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

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
        log.warning("task_store: could not write %s: %s", STORE_PATH, exc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise(title: str) -> str:
    """Lowercase and collapse whitespace for dedup comparison."""
    return re.sub(r"\s+", " ", title.strip().lower())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def new_task(
    *,
    title: str,
    assignee: str = "unassigned",
    workstream: str = "unassigned",
    status: str = "todo",
    source: str = "manual",
    due_date=None,
    source_ref: str | None = None,
    source_link: str | None = None,
    context: str = "",
    customer_related: bool = False,
    confidence: float = 0.0,
    completed_at=None,
    tags: list[str] | None = None,
) -> Task:
    """Construct a ``Task`` with a fresh UUID and now-timestamps.

    Does NOT persist — call ``add(task)`` to save.
    """
    now = _now()
    return Task(
        id=str(uuid.uuid4()),
        title=title,
        assignee=assignee,
        workstream=workstream,
        status=status,
        source=source,
        created_at=now,
        updated_at=now,
        due_date=due_date,
        source_ref=source_ref,
        source_link=source_link,
        context=context,
        customer_related=customer_related,
        confidence=confidence,
        completed_at=completed_at,
        tags=list(tags) if tags else [],
    )


def add(task: Task) -> None:
    """Persist a task.  Overwrites silently if the id already exists."""
    data = _load()
    data[task.id] = task.as_dict()
    _save(data)


def get(task_id: str) -> Task | None:
    """Return the ``Task`` for the given id, or ``None`` if not found."""
    data = _load()
    entry = data.get(task_id)
    if not entry:
        return None
    try:
        return Task.from_dict(entry)
    except Exception:
        log.warning("task_store: corrupt entry for id %s — skipping", task_id)
        return None


def update(task_id: str, **fields) -> Task | None:
    """Patch fields on an existing task and persist.

    Auto-bumps ``updated_at``.  If ``status`` is set to ``"done"`` and
    ``completed_at`` is not supplied, sets ``completed_at`` to now.
    Returns the updated ``Task``, or ``None`` if ``task_id`` is not found.
    """
    data = _load()
    entry = data.get(task_id)
    if not entry:
        return None

    try:
        task = Task.from_dict(entry)
    except Exception:
        log.warning("task_store: corrupt entry for id %s — cannot update", task_id)
        return None

    for key, value in fields.items():
        if hasattr(task, key):
            setattr(task, key, value)
        else:
            log.warning("task_store: unknown field %r on Task — ignoring", key)

    task.updated_at = _now()

    if task.status == TASK_STATUS_DONE and task.completed_at is None:
        task.completed_at = task.updated_at

    data[task_id] = task.as_dict()
    _save(data)
    return task


def delete(task_id: str) -> bool:
    """Remove a task by id.  Returns ``True`` if it existed, ``False`` if not."""
    data = _load()
    if task_id not in data:
        return False
    del data[task_id]
    _save(data)
    return True


def list_tasks(
    *,
    assignee: str | None = None,
    workstream: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
) -> list[Task]:
    """Return all tasks matching every supplied filter (``None`` = any).

    ``tags`` matches tasks that contain **all** of the requested tags (AND
    semantics); an empty list matches everything.

    Results are ordered by ``created_at`` descending (newest first).
    """
    data = _load()
    tasks: list[Task] = []
    for entry in data.values():
        try:
            task = Task.from_dict(entry)
        except Exception:
            continue
        if assignee is not None and task.assignee != assignee:
            continue
        if workstream is not None and task.workstream != workstream:
            continue
        if status is not None and task.status != status:
            continue
        if tags and not all(tag in task.tags for tag in tags):
            continue
        tasks.append(task)

    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks


def find_by_source(source: str, source_ref: str, title: str) -> Task | None:
    """Dedup guard for ingestion.

    Returns an existing ``Task`` whose ``(source, source_ref,
    normalised(title))`` triple matches, or ``None`` if no match is found.
    """
    needle = _normalise(title)
    for entry in _load().values():
        if entry.get("source") != source:
            continue
        if entry.get("source_ref") != source_ref:
            continue
        if _normalise(entry.get("title", "")) == needle:
            try:
                return Task.from_dict(entry)
            except Exception:
                return None
    return None
