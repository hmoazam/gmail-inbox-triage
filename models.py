"""Shared data shapes (see INTERFACES.md).

Triage now works at the THREAD level: we collapse inbox messages by conversation,
read every message in the thread, and classify the conversation as a whole.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


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
