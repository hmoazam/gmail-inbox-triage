"""Read-only team roster from ``config.TEAM_ROSTER``.

Drives the By-Person board view (empty lanes for everyone) and the assignee
dropdown.  No auth needed — this reads config only, never Google.  ``config``
already resolves ``CLEANUP_ROSTER_PATH`` / defaults and Hanna's email.
"""
from __future__ import annotations

from fastapi import APIRouter

import config
from api.schemas import RosterEntry

router = APIRouter(prefix="/api/roster", tags=["roster"])


@router.get("", response_model=list[RosterEntry])
def get_roster() -> list[RosterEntry]:
    return [
        RosterEntry(name=str(r.get("name", "")), email=str(r.get("email", "") or ""))
        for r in config.TEAM_ROSTER
    ]
