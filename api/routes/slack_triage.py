"""Slack triage: fetch + classify unread conversations, mark-read, add-to-board.

Mirrors ``api/routes/triage.py`` for Slack. Wraps ``SlackClient`` +
``classifier.classify_slack_conversation`` + ``classifier.slack_conversation_to_task``.
The only Slack mutation exposed anywhere is mark-read (``conversations.mark``).

Groups are assembled in a fixed order: "Direct Messages" first, then each
configured category (``config.SLACK_CATEGORIES``) in its declared order. Only
conversations with ≥1 unread appear (the client filters these); every group is
emitted even when empty so the frontend has a stable structure.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import task_store
from classifier import (
    UsageStats,
    classify_slack_conversation,
    slack_conversation_to_task,
)
from api.deps import get_app_settings, get_slack_client
from api.schemas import (
    SlackAddToBoardRequest,
    SlackAddToBoardResponse,
    SlackConversationOut,
    SlackDecisionOut,
    SlackGroupOut,
    SlackMarkReadRequest,
    SlackMarkReadResponse,
    SlackMessageOut,
    SlackTriageResponse,
    TaskOut,
)

router = APIRouter(prefix="/api/slack-triage", tags=["slack-triage"])

DM_GROUP = "Direct Messages"


def _to_out(conv, settings: dict, stats: UsageStats) -> SlackConversationOut:
    """Classify one normalized conversation and serialise it for the API."""
    decision = classify_slack_conversation(conv, settings, stats=stats)  # AuthError → 401/403
    return SlackConversationOut(
        id=conv.id,
        kind=conv.kind,
        name=conv.name,
        unread_count=conv.unread_count,
        messages=[
            SlackMessageOut(author=m.author, ts=m.ts, text=m.text)
            for m in conv.messages
        ],
        latest_ts=conv.latest_ts,
        permalink=conv.permalink,
        decision=SlackDecisionOut.from_decision(decision),
    )


@router.get("", response_model=SlackTriageResponse)
def slack_triage() -> SlackTriageResponse:
    """Fetch unread DMs + configured-channel conversations and classify each."""
    settings = get_app_settings()
    client = get_slack_client()

    stats = UsageStats()
    groups: list[SlackGroupOut] = []

    # 1) Direct Messages — implicit group, rendered first.
    dms = client.list_unread_dms()  # AuthError → 401/403
    try:
        dm_out = [_to_out(c, settings, stats) for c in dms]
    except Exception as exc:  # noqa: BLE001 — classifier re-raises auth-like errors
        _reraise_or_502(exc)
    groups.append(SlackGroupOut(category=DM_GROUP, conversations=dm_out))

    # 2) Configured categories, in declared order.
    for category, channel_ids in settings["slack_categories"].items():
        convs = client.list_unread_in_channels(channel_ids)  # AuthError → 401/403
        try:
            conv_out = [_to_out(c, settings, stats) for c in convs]
        except Exception as exc:  # noqa: BLE001
            _reraise_or_502(exc)
        groups.append(SlackGroupOut(category=category, conversations=conv_out))

    return SlackTriageResponse(groups=groups, usage=stats.as_dict())


def _reraise_or_502(exc: Exception) -> None:
    """Let AuthError bubble to the 401/403 handler; wrap others as 502."""
    # AuthError is the shared gmail_client.AuthError; the app-level exception
    # handler maps it to 401/403. Anything else is a classifier/backend failure.
    from gmail_client import AuthError
    if isinstance(exc, AuthError):
        raise exc
    raise HTTPException(
        status_code=502,
        detail=f"Slack classification failed: {type(exc).__name__}: {exc}",
    )


@router.post("/mark-read", response_model=SlackMarkReadResponse)
def mark_read(body: SlackMarkReadRequest) -> SlackMarkReadResponse:
    """Move the conversation's read cursor to ``ts`` — the one Slack mutation."""
    client = get_slack_client()
    client.mark_read(body.channel_id, body.ts)  # AuthError → 401/403
    return SlackMarkReadResponse(ok=True)


@router.post("/add-to-board", response_model=SlackAddToBoardResponse)
def add_to_board(body: SlackAddToBoardRequest) -> SlackAddToBoardResponse:
    """Bridge a triaged Slack conversation into a board Task (dedup-guarded)."""
    settings = get_app_settings()

    task = slack_conversation_to_task(
        channel_id=body.channel_id,
        name=body.name,
        permalink=body.permalink,
        summary=body.summary,
        action_on_me=body.action_on_me,
        customer_related=body.customer_related,
        confidence=body.confidence,
        settings=settings,
        assignee=body.assignee,
        workstream=body.workstream,
        tags=body.tags,
    )

    # Dedup guard on (source="slack", source_ref=channel_id, title).
    existing = task_store.find_by_source("slack", body.channel_id, task.title)
    if existing is not None:
        return SlackAddToBoardResponse(created=False, task=TaskOut.from_task(existing))

    task_store.add(task)
    return SlackAddToBoardResponse(created=True, task=TaskOut.from_task(task))
