"""Pydantic schemas — the API contract between backend and frontend.

Field names on the response models mirror ``Task.as_dict()`` /
``ThreadDecision.as_dict()`` exactly so the generated TypeScript types line up
one-to-one with the Python data shapes.  Date/datetime fields are exposed as the
same ISO strings the stores persist (``YYYY-MM-DD`` for ``due_date``, full
ISO-8601 for the timestamps) rather than being re-coerced by Pydantic.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from models import Task, ThreadDecision, TASK_STATUS_TODO, TASK_SOURCE_MANUAL


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TaskOut(BaseModel):
    """Serialised Task, mirroring ``Task.as_dict()`` plus the derived ``overdue``."""
    id: str
    title: str
    assignee: str
    workstream: str
    status: str
    source: str
    created_at: str
    updated_at: str
    due_date: str | None = None
    planned_day: str | None = None
    source_ref: str | None = None
    source_link: str | None = None
    context: str = ""
    customer_related: bool = False
    confidence: float = 0.0
    completed_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    overdue: bool = False

    @classmethod
    def from_task(cls, task: Task) -> "TaskOut":
        """Build from a Task: its JSON dict plus the derived ``overdue`` flag."""
        return cls(**task.as_dict(), overdue=task.overdue)


class TaskCreate(BaseModel):
    """POST /api/tasks body. Defaults match ``task_store.new_task``."""
    title: str
    assignee: str = "unassigned"
    workstream: str = "unassigned"
    status: str = TASK_STATUS_TODO
    source: str = TASK_SOURCE_MANUAL
    due_date: str | None = None          # "YYYY-MM-DD"
    planned_day: str | None = None       # PLANNED_DAYS value, or None = Backlog
    source_ref: str | None = None
    source_link: str | None = None
    context: str = ""
    customer_related: bool = False
    confidence: float = 0.0
    tags: list[str] = Field(default_factory=list)


class TaskPatch(BaseModel):
    """PATCH /api/tasks/{id} body — every field optional.

    Only fields explicitly present in the request are applied (the route uses
    ``model_dump(exclude_unset=True)``), so passing ``due_date: null`` clears the
    due date while omitting it leaves the date untouched.  ``status`` is what
    drag-and-drop sends.
    """
    title: str | None = None
    status: str | None = None
    assignee: str | None = None
    workstream: str | None = None
    context: str | None = None
    due_date: str | None = None
    planned_day: str | None = None
    tags: list[str] | None = None
    customer_related: bool | None = None
    confidence: float | None = None


# ---------------------------------------------------------------------------
# Workstreams
# ---------------------------------------------------------------------------

class WorkstreamCreate(BaseModel):
    name: str


class WorkstreamRename(BaseModel):
    new_name: str


class WorkstreamMutation(BaseModel):
    """Result of a workstream rename/delete, reporting the task cascade count."""
    ok: bool
    workstreams: list[str]
    tasks_updated: int = 0


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class TagOut(BaseModel):
    name: str
    color: str


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class TagPatch(BaseModel):
    """Rename and/or recolor a tag. Provide either, or both."""
    new_name: str | None = None
    color: str | None = None


class TagMutation(BaseModel):
    ok: bool
    tags: list[TagOut]
    tasks_updated: int = 0


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

class ThreadDecisionOut(BaseModel):
    """Mirrors ``ThreadDecision.as_dict()``."""
    thread_id: str
    category: str
    customer_related: bool = False
    internal_only: bool = False
    needs_response: bool = False
    action_on_me: str | None = None
    summary: str = ""
    confidence: float = 0.0

    @classmethod
    def from_decision(cls, d: ThreadDecision) -> "ThreadDecisionOut":
        return cls(**d.as_dict())


class ThreadMessageOut(BaseModel):
    id: str
    sender: str
    sender_email: str
    to: str
    date: str
    body: str
    from_me: bool


class TriageThreadOut(BaseModel):
    """One triaged thread: thread metadata + messages + its classification."""
    thread_id: str
    subject: str
    participants: list[str]
    message_count: int
    unread: bool
    unsubscribe: str | None = None
    source_link: str          # Gmail thread URL
    messages: list[ThreadMessageOut]
    decision: ThreadDecisionOut


class TriageResponse(BaseModel):
    threads: list[TriageThreadOut]
    usage: dict


class MarkReadRequest(BaseModel):
    """Mark messages as read. The only Gmail mutation the API exposes."""
    message_ids: list[str]


class MarkReadResponse(BaseModel):
    marked: int


class AddToBoardRequest(BaseModel):
    """ThreadDecision → Task bridge input (the decision fields from triage).

    ``thread_decision_to_task`` only reads the decision, so the full thread is
    not required here.
    """
    thread_id: str
    category: str
    action_on_me: str | None = None
    summary: str = ""
    customer_related: bool = False
    confidence: float = 0.0


class AddToBoardResponse(BaseModel):
    created: bool          # False if the task already existed (dedup hit)
    task: TaskOut | None = None


# ---------------------------------------------------------------------------
# Slack action extraction (URL-driven)
# ---------------------------------------------------------------------------

class SlackExtractRequest(BaseModel):
    """POST /api/slack-triage/extract body — a pasted Slack channel/thread URL."""
    url: str


class SlackAction(BaseModel):
    """One pending action the account owner still needs to do, per the MCP summary."""
    task: str                       # short imperative
    context: str = ""               # one line of why / what
    due: str | None = None          # "YYYY-MM-DD" or null


class SlackExtractResponse(BaseModel):
    """Extraction result. Adding an action to the board reuses POST /api/tasks
    (source="slack", source_link=source_link)."""
    source_link: str                # the URL that was extracted
    actions: list[SlackAction]


# ---------------------------------------------------------------------------
# Ingest (Meet notes / uploaded transcripts → tasks)
# ---------------------------------------------------------------------------

class MeetIngestRequest(BaseModel):
    since_days: int | None = 7    # scan Meet docs modified within N days; null = folder-only
    max_docs: int | None = None   # cap docs scanned (None = all matched)
    dry_run: bool = False         # extract but do not persist tasks


class TranscriptIngestRequest(BaseModel):
    dry_run: bool = False


class IngestResult(BaseModel):
    created: list[TaskOut]
    skipped: int = 0              # dedup hits not re-added
    scanned: int = 0             # documents/files read
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Drafts (draft-first — the API never sends mail directly)
# ---------------------------------------------------------------------------

class DraftCreate(BaseModel):
    """Draft input — accepts EITHER shape:

    - task-based: ``{"task_id": "..."}`` — the Send action on a teammate task;
      the route resolves the assignee's email, subject, body, and thread from
      the stored Task.
    - generic:    ``{"to", "subject", "body", "thread_id"?, "cc"?}`` — a
      fully-specified draft.

    All fields are optional so one model covers both; the route validates that
    exactly one shape is supplied.
    """
    task_id: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    thread_id: str | None = None
    cc: str | None = None


class DraftResponse(BaseModel):
    draft_id: str


# ---------------------------------------------------------------------------
# Roster (read-only; drives By-Person lanes + the assignee dropdown)
# ---------------------------------------------------------------------------

class RosterEntry(BaseModel):
    name: str
    email: str
