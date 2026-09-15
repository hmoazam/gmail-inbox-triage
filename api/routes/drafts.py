"""Draft creation over ``GmailClient.create_draft``.

DRAFT-FIRST: creating a draft is the ONLY outbound action. The API never sends
mail directly — the user reviews and sends from Gmail.

Two request shapes are accepted (see ``DraftCreate``):
  - task-based ``{"task_id"}`` — the Send action on a teammate task; the route
    resolves the assignee's email + subject/body/thread from the stored Task.
  - generic ``{"to","subject","body","thread_id"?,"cc"?}`` — a full draft.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import config
import task_store
from api.deps import get_gmail_client
from api.schemas import DraftCreate, DraftResponse

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


def _email_for(name: str) -> str:
    """Resolve a roster name to its email (case-insensitive), or '' if none."""
    for r in config.TEAM_ROSTER:
        if str(r.get("name", "")).lower() == name.lower():
            return str(r.get("email", "") or "")
    return ""


def _draft_from_task(task_id: str) -> dict:
    """Build create_draft kwargs from a stored Task, or raise HTTP 404/422."""
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")

    email = _email_for(task.assignee)
    if not email:
        raise HTTPException(
            status_code=422,
            detail=f"assignee {task.assignee} has no email in the roster",
        )

    body = task.context or task.title
    if task.source_link:
        body = f"{body}\n{task.source_link}"

    return {
        "to": email,
        "subject": f"Follow-up: {task.title}",
        "body": body,
        # Attach to the original Gmail thread only for email-sourced tasks.
        "thread_id": task.source_ref if task.source == "email" else None,
        "cc": None,
    }


@router.post("", response_model=DraftResponse)
def create_draft(body: DraftCreate) -> DraftResponse:
    if body.task_id:
        kwargs = _draft_from_task(body.task_id)
    else:
        # Generic shape — to/subject/body are required here.
        missing = [f for f in ("to", "subject", "body") if not (getattr(body, f) or "").strip()]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"provide task_id, or all of to/subject/body (missing: {', '.join(missing)})",
            )
        kwargs = {
            "to": body.to,
            "subject": body.subject,
            "body": body.body,
            "thread_id": body.thread_id,
            "cc": body.cc,
        }

    client = get_gmail_client()
    try:
        draft_id = client.create_draft(**kwargs)
    except ValueError as exc:  # empty recipient guard from GmailClient
        raise HTTPException(status_code=422, detail=str(exc))
    return DraftResponse(draft_id=draft_id)
