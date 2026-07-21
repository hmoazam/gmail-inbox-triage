"""Fast subject/sender pre-triage — runs before the Claude SDK evaluation.

For threads where the subject or sender makes the category obvious, we assign
a ThreadDecision directly without spending a Claude call. Only threads that
don't match any rule are forwarded to the full classifier.

Rules are plain dicts — easy to extend without touching classifier logic.
Each rule has:
  - subject_re   : regex matched against the subject (case-insensitive), optional
  - sender_re    : regex matched against all participant emails, optional
  - category     : "action_required" | "useful" | "other"
  - action_on_me : str or None (almost always None for auto-rules)
  - summary      : short explanation shown in the UI

At least one of subject_re / sender_re must be present. Both can be set (AND).
The first matching rule wins.
"""
from __future__ import annotations

import re
from models import EmailThread, ThreadDecision, CATEGORY_OTHER, CATEGORY_USEFUL
from config import INTERNAL_DOMAIN

# ─── rule table ─────────────────────────────────────────────────────────────
# Extend this list to add new fast-path rules. Rules run top-to-bottom; the
# first match wins. Be specific before general.

_RULES: list[dict] = [
    # ── calendar events & invites ────────────────────────────────────────────
    {
        "subject_re": r"^(invitation|updated invitation|accepted|tentatively accepted|"
                      r"declined|cancelled|re:\s*(invitation|updated invitation)):",
        "category": CATEGORY_OTHER,
        "summary": "Calendar invite / RSVP — no inbox action needed.",
    },
    {
        # Google Calendar notification sender
        "sender_re": r"calendar-notification@google\.com",
        "category": CATEGORY_OTHER,
        "summary": "Google Calendar notification.",
    },

    # ── out-of-office / auto-reply ───────────────────────────────────────────
    {
        "subject_re": r"^(out of office|ooo|automatic reply|auto-reply|autoreply"
                      r"|away from office|on vacation|on leave)",
        "category": CATEGORY_OTHER,
        "summary": "Out-of-office or auto-reply — no action needed.",
    },

    # ── CI / GitHub / GitLab / Jira notifications ────────────────────────────
    {
        "sender_re": r"notifications@github\.com|noreply@github\.com",
        "subject_re": r"(merged|closed|opened|re-opened|review requested|"
                      r"approved|changes requested|commented on|pushed|"
                      r"passed|failed|cancelled|workflow)",
        "category": CATEGORY_OTHER,
        "summary": "GitHub CI / PR notification.",
    },
    {
        "sender_re": r"notifications@github\.com|noreply@github\.com",
        "category": CATEGORY_USEFUL,
        "summary": "GitHub notification — worth skimming.",
    },
    {
        "sender_re": r"jira@|atlassian\.com|jira-noreply",
        "category": CATEGORY_OTHER,
        "summary": "Jira / Atlassian automated notification.",
    },
    {
        "sender_re": r"noreply@gitlab\.com|gitlab@mg\.gitlab\.com",
        "category": CATEGORY_OTHER,
        "summary": "GitLab notification.",
    },

    # ── Slack digests ────────────────────────────────────────────────────────
    {
        "sender_re": r"no-reply@slack\.com|feedback@slack\.com",
        "category": CATEGORY_OTHER,
        "summary": "Slack digest / notification email.",
    },

    # ── company internal automated mails (EXAMPLE — customize for your org) ──
    # Add senders for your own internal systems that send noise you never act on,
    # e.g. r"notifications@yourcompany\.com|noreply@yourcompany\.com".
    # {
    #     "sender_re": r"notifications@yourcompany\.com|noreply@yourcompany\.com",
    #     "category": CATEGORY_OTHER,
    #     "summary": "Automated internal system notification.",
    # },

    # ── newsletters / marketing (List-Unsubscribe header present) ───────────
    # Handled separately in _has_unsubscribe check, not a regex rule.

    # ── generic no-reply / donotreply senders ────────────────────────────────
    {
        "sender_re": r"(no.?reply|do.?not.?reply|donotreply|mailer-daemon"
                     r"|bounces\+|noreply)@",
        "category": CATEGORY_OTHER,
        "summary": "Automated email from a no-reply sender.",
    },
]

# Pre-compile regexes once at import time.
_COMPILED: list[dict] = []
for _r in _RULES:
    _COMPILED.append({
        "subject_re": re.compile(_r["subject_re"], re.IGNORECASE) if "subject_re" in _r else None,
        "sender_re":  re.compile(_r["sender_re"],  re.IGNORECASE) if "sender_re"  in _r else None,
        "category":   _r["category"],
        "action_on_me": _r.get("action_on_me"),
        "summary":    _r["summary"],
    })


def quick_triage(thread: EmailThread) -> ThreadDecision | None:
    """Return a ThreadDecision if the thread can be categorised from subject/sender alone.

    Returns None if the thread needs full Claude evaluation.
    """
    subject = thread.subject or ""
    participants = " ".join(thread.participants)

    for rule in _COMPILED:
        subj_ok = True
        sender_ok = True
        if rule["subject_re"] is not None:
            subj_ok = bool(rule["subject_re"].search(subject))
        if rule["sender_re"] is not None:
            sender_ok = bool(rule["sender_re"].search(participants))
        if subj_ok and sender_ok:
            return ThreadDecision(
                thread_id=thread.thread_id,
                category=rule["category"],
                internal_only=_all_internal(thread),
                action_on_me=rule.get("action_on_me"),
                summary=rule["summary"],
                confidence=0.95,
            )

    # Newsletters: any thread with a List-Unsubscribe header and no personal reply
    if thread.unsubscribe and not _has_personal_reply(thread):
        return ThreadDecision(
            thread_id=thread.thread_id,
            category=CATEGORY_OTHER,
            internal_only=False,
            action_on_me=None,
            summary="Newsletter / bulk mail (has unsubscribe link).",
            confidence=0.9,
        )

    return None  # needs full Claude evaluation


def _all_internal(thread: EmailThread) -> bool:
    """True if every participant looks like an internal address.

    Uses INTERNAL_DOMAIN (CLEANUP_INTERNAL_DOMAIN). If no internal domain is
    configured, we can't tell, so we conservatively return False.
    """
    if not INTERNAL_DOMAIN:
        return False
    return all(INTERNAL_DOMAIN in p for p in thread.participants if p)


def _has_personal_reply(thread: EmailThread) -> bool:
    """True if any non-first message in the thread looks hand-written."""
    return any(
        m.from_me or (
            not re.search(r"(no.?reply|donotreply|mailer-daemon|noreply)@",
                          m.sender_email, re.IGNORECASE)
            and not m.from_me
            and len(m.body.strip()) > 10
        )
        for m in thread.messages[1:]  # skip the original sender
    )
