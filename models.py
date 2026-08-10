"""Shared data shapes (see INTERFACES.md).

Triage now works at the THREAD level: we collapse inbox messages by conversation,
read every message in the thread, and classify the conversation as a whole.

The `Task` dataclass (below) is the unit of the Action Board: one actionable item
extracted from email, a meeting transcript, or added manually.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime


@dataclass
class ThreadMessage:
    """One message within a thread."""
    id: str
    sender: str            # raw From header, "Name <addr>"
    sender_email: str      # parsed bare address, lowercased
    to: str                # raw To header
    date: str
    body: str              # decoded text body (plain preferred, html stripped)
    from_me: bool          # did the account owner send this message?


@dataclass
class EmailThread:
    """A whole conversation collapsed from the inbox."""
    thread_id: str
    subject: str
    messages: list[ThreadMessage] = field(default_factory=list)
    label_ids: list[str] = field(default_factory=list)  # union across messages
    unread: bool = False
    unsubscribe: str | None = None

    @property
    def last_message(self) -> ThreadMessage | None:
        return self.messages[-1] if self.messages else None

    @property
    def participants(self) -> list[str]:
        seen, out = set(), []
        for m in self.messages:
            if m.sender_email and m.sender_email not in seen:
                seen.add(m.sender_email)
                out.append(m.sender_email)
        return out

    @property
    def message_ids(self) -> list[str]:
        return [m.id for m in self.messages]

    def transcript(self, per_msg_chars: int) -> str:
        """Readable full-thread transcript handed to the classifier."""
        lines = [f"Subject: {self.subject}", ""]
        for i, m in enumerate(self.messages, 1):
            who = "ME (account owner)" if m.from_me else m.sender
            lines.append(f"--- Message {i}/{len(self.messages)} ---")
            lines.append(f"From: {who}")
            lines.append(f"To: {m.to}")
            lines.append(f"Date: {m.date}")
            lines.append("")
            lines.append((m.body or "").strip()[:per_msg_chars])
            lines.append("")
        return "\n".join(lines)


# Triage categories, in the order the UI renders them.
CATEGORY_ACTION = "action_required"
CATEGORY_USEFUL = "useful"
CATEGORY_OTHER = "other"
CATEGORIES = (CATEGORY_ACTION, CATEGORY_USEFUL, CATEGORY_OTHER)


@dataclass
class ThreadDecision:
    """Classifier output for one thread."""
    thread_id: str
    category: str                    # one of CATEGORIES
    customer_related: bool = False
    internal_only: bool = False
    needs_response: bool = False
    action_on_me: str | None = None  # concrete action for the account owner, else None
    summary: str = ""                # one-line why / what it is
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Action Board — Task
# ---------------------------------------------------------------------------

TASK_STATUS_TODO = "todo"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_DONE = "done"
TASK_STATUSES = (TASK_STATUS_TODO, TASK_STATUS_IN_PROGRESS, TASK_STATUS_DONE)

TASK_SOURCE_EMAIL = "email"
TASK_SOURCE_MEET = "meet"
TASK_SOURCE_TEAMS = "teams"
TASK_SOURCE_MANUAL = "manual"
TASK_SOURCES = (TASK_SOURCE_EMAIL, TASK_SOURCE_MEET, TASK_SOURCE_TEAMS, TASK_SOURCE_MANUAL)


@dataclass
class Task:
    """One actionable item on the Action Board.

    Created from email (``ThreadDecision``), a meeting transcript, or manually.
    ``overdue`` is a derived property — never stored — so it always reflects
    today's date at read time.
    """
    # --- required at construction ---
    id: str                            # uuid4 string; use task_store.new_task()
    title: str                         # short action text
    assignee: str                      # name from roster, or "unassigned"
    workstream: str                    # from workstreams list, or "unassigned"
    status: str                        # TASK_STATUS_* constant
    source: str                        # TASK_SOURCE_* constant
    created_at: datetime               # UTC
    updated_at: datetime               # UTC; bumped on every write

    # --- optional ---
    due_date: date | None = None       # date only (no time); YYYY-MM-DD in JSON
    source_ref: str | None = None      # thread_id (email) or file_id (transcript)
    source_link: str | None = None     # Gmail thread URL or Drive doc URL
    context: str = ""                  # short explanation / excerpt
    customer_related: bool = False
    confidence: float = 0.0            # 0.0–1.0; set by classifier, 0 for manual
    completed_at: datetime | None = None  # set when status flips to "done"

    @property
    def overdue(self) -> bool:
        """True when status is not done and due_date is in the past."""
        if self.status == TASK_STATUS_DONE or self.due_date is None:
            return False
        return self.due_date < date.today()

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict.

        ``due_date`` is serialised as a YYYY-MM-DD string; ``created_at``,
        ``updated_at``, and ``completed_at`` are full ISO-8601 UTC strings.
        ``overdue`` is intentionally excluded (it is derived).
        """
        d = asdict(self)
        if d["due_date"] is not None:
            # asdict preserves date objects; convert explicitly for JSON safety
            d["due_date"] = self.due_date.isoformat()          # YYYY-MM-DD
        if d["created_at"] is not None:
            d["created_at"] = self.created_at.isoformat()
        if d["updated_at"] is not None:
            d["updated_at"] = self.updated_at.isoformat()
        if d["completed_at"] is not None:
            d["completed_at"] = self.completed_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        """Reconstruct a Task from a dict produced by ``as_dict``."""
        d = dict(d)  # shallow copy — do not mutate caller's dict
        if d.get("due_date") is not None:
            d["due_date"] = date.fromisoformat(d["due_date"])
        if d.get("created_at") is not None:
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        if d.get("updated_at") is not None:
            d["updated_at"] = datetime.fromisoformat(d["updated_at"])
        if d.get("completed_at") is not None:
            d["completed_at"] = datetime.fromisoformat(d["completed_at"])
        return cls(**d)
