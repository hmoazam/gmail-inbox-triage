"""Draft creation over ``GmailClient.create_draft``.

DRAFT-FIRST: creating a draft is the ONLY outbound action. The API never sends
mail directly — the user reviews and sends from Gmail.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import get_gmail_client
from api.schemas import DraftCreate, DraftResponse

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


@router.post("", response_model=DraftResponse)
def create_draft(body: DraftCreate) -> DraftResponse:
    client = get_gmail_client()
    try:
        draft_id = client.create_draft(
            to=body.to,
            subject=body.subject,
            body=body.body,
            thread_id=body.thread_id,
            cc=body.cc,
        )
    except ValueError as exc:  # empty recipient guard from GmailClient
        raise HTTPException(status_code=422, detail=str(exc))
    return DraftResponse(draft_id=draft_id)
