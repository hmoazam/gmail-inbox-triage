"""Shared dependencies: cached clients, settings, and small parse helpers.

The Gmail/Drive clients are module-level singletons so the in-instance token
cache (5-minute TTL) is reused across requests instead of shelling out to
``gcloud`` on every call.
"""
from __future__ import annotations

from datetime import date

from config import get_settings
from gmail_client import GmailClient
from drive_client import DriveClient
from slack_client import SlackClient

_gmail: GmailClient | None = None
_drive: DriveClient | None = None
_slack: SlackClient | None = None


def get_gmail_client() -> GmailClient:
    global _gmail
    if _gmail is None:
        _gmail = GmailClient()
    return _gmail


def get_slack_client() -> SlackClient:
    global _slack
    if _slack is None:
        _slack = SlackClient()
    return _slack


def get_drive_client() -> DriveClient:
    global _drive
    if _drive is None:
        _drive = DriveClient()
    return _drive


def get_app_settings() -> dict:
    """Fresh settings dict from config (re-read so env edits take effect)."""
    return get_settings()


def parse_due_date(value: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` string into a ``date``; ``None``/blank → ``None``.

    Returns ``None`` on an unparseable string rather than raising, so a bad
    extracted due date degrades to "no due date" instead of failing ingestion.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
