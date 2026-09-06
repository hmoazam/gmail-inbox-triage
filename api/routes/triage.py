"""Inbox triage: fetch + classify threads, mark-read, and the add-to-board bridge.

Wraps ``GmailClient`` + ``classifier.classify_threads`` +
``classifier.thread_decision_to_task``.  The only Gmail mutation exposed anywhere
is mark-read.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import task_store
from classifier import classify_threads, thread_decision_to_task
from models import ThreadDecision
from api.deps import get_app_settings, get_gmail_client
from api.schemas import (
    AddToBoardRequest,
    AddToBoardResponse,
    MarkReadRequest,
    MarkReadResponse,
    TaskOut,
    ThreadDecisionOut,
    ThreadMessageOut,
    TriageResponse,
    TriageThreadOut,
)

router = APIRouter(prefix="/api/triage", tags=["triage"])


@router.get("", response_model=TriageResponse)
def triage(
    max_results: int | None = Query(default=None, description="cap threads; blank = config batch size"),
    query: str = Query(default="in:inbox is:unread"),
) -> TriageResponse:
    """Fetch inbox threads matching ``query`` and classify each with Claude."""
    settings = get_app_settings()
    client = get_gmail_client()

    cap = max_results if (max_results and max_results > 0) else settings["thread_batch_size"]

    ids = client.list_inbox_threads(max_results=cap, query=query)  # AuthError → 401/403
    if not ids:
        return TriageResponse(threads=[], usage={})

    threads = client.get_threads(ids)

    try:
        decisions, stats = classify_threads(threads, settings)
    except Exception as exc:  # noqa: BLE001 — classifier re-raises auth-like errors
        raise HTTPException(status_code=502, detail=f"Classification failed: {type(exc).__name__}: {exc}")

    by_id = {d.thread_id: d for d in decisions}
    out: list[TriageThreadOut] = []
    for t in threads:
        d = by_id[t.thread_id]
        out.append(TriageThreadOut(
            thread_id=t.thread_id,
            subject=t.subject,
            participants=t.participants,
            message_count=len(t.messages),
            unread=t.unread,
            unsubscribe=t.unsubscribe,
            messages=[
                ThreadMessageOut(
                    id=m.id, sender=m.sender, sender_email=m.sender_email,
                    to=m.to, date=m.date, body=m.body, from_me=m.from_me,
                )
                for m in t.messages
            ],
            decision=ThreadDecisionOut.from_decision(d),
        ))
    return TriageResponse(threads=out, usage=stats.as_dict())


@router.post("/mark-read", response_model=MarkReadResponse)
def mark_read(body: MarkReadRequest) -> MarkReadResponse:
    """Remove UNREAD from the given message ids — the one Gmail mutation."""
    client = get_gmail_client()
    client.mark_read(body.message_ids)  # AuthError → 401/403
    return MarkReadResponse(marked=len(body.message_ids))


@router.post("/add-to-board", response_model=AddToBoardResponse)
def add_to_board(body: AddToBoardRequest) -> AddToBoardResponse:
    """Bridge an action-required ThreadDecision into a board Task (dedup-guarded)."""
    if body.category != "action_required" or not body.action_on_me:
        raise HTTPException(status_code=422, detail="thread is not action-required; nothing to add")

    # Dedup guard — avoid duplicates on repeated calls.
    existing = task_store.find_by_source("email", body.thread_id, body.action_on_me)
    if existing is not None:
        return AddToBoardResponse(created=False, task=TaskOut.from_task(existing))

    decision = ThreadDecision(
        thread_id=body.thread_id,
        category=body.category,
        customer_related=body.customer_related,
        action_on_me=body.action_on_me,
        summary=body.summary,
        confidence=body.confidence,
    )
    # thread_decision_to_task reads only the decision, so no EmailThread is needed.
    task = thread_decision_to_task(decision, None, get_app_settings())
    if task is None:
        raise HTTPException(status_code=422, detail="thread is not action-required; nothing to add")

    task_store.add(task)
    return AddToBoardResponse(created=True, task=TaskOut.from_task(task))
