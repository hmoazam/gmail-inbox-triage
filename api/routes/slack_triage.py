"""Slack action extraction: paste a Slack URL → the pending actions you must do.

URL-driven (supersedes the old categorized-scan design). Wraps
``SlackClient.extract_actions``, which reads ONE conversation through the dbexec
Slack MCP and returns the user's pending actions. The MCP is the only Slack
transport; adding a proposed action to the board reuses the existing
``POST /api/tasks`` (no route here creates tasks).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gmail_client import AuthError
from api.deps import get_app_settings, get_slack_client
from api.schemas import SlackAction, SlackExtractRequest, SlackExtractResponse

router = APIRouter(prefix="/api/slack-triage", tags=["slack-triage"])


@router.post("/extract", response_model=SlackExtractResponse)
def extract(body: SlackExtractRequest) -> SlackExtractResponse:
    """Extract the account owner's pending actions from a Slack channel/thread URL.

    Slow by nature (~45s–2min): the MCP summarizer reads the conversation
    server-side. ``AuthError`` → 401/403 (dbexec unavailable); a bad URL → 422;
    a timeout → 504; any other backend failure → 502.
    """
    settings = get_app_settings()
    client = get_slack_client()
    user_name = settings.get("user_name") or settings.get("user_email") or "the account owner"

    try:
        actions = client.extract_actions(body.url, user_name)  # AuthError → 401/403
    except AuthError:
        raise                                                  # → app handler 401/403
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Slack extraction failed: {type(exc).__name__}: {exc}",
        )

    return SlackExtractResponse(
        source_link=body.url,
        actions=[SlackAction(**a) for a in actions],
    )
