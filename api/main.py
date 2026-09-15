"""FastAPI application for the local React task-board.

Local-only: bind localhost, allow CORS from the Vite dev server
(http://localhost:5173), and — in prod — serve the built ``frontend/dist`` as
static files from this same process.

Auth is unchanged: the Gmail/Drive clients raise ``AuthError`` on a 401/403 from
Google.  We translate that to an HTTP 401/403 carrying the SAME message text so
the UI can show a re-auth prompt.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from gmail_client import AuthError as GmailAuthError
from drive_client import AuthError as DriveAuthError
from api.deps import get_slack_client
from api.routes import (
    drafts, ingest, roster, slack_triage, tags, tasks, triage, workstreams,
)

log = logging.getLogger(__name__)

# Vite dev server origins (localhost and 127.0.0.1 forms).
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Launch the Slack MCP stdio server once on startup, close it on shutdown.

    Startup connect is a best-effort optimization (it avoids the ~3s handshake on
    the first Slack request): if dbexec is missing/unauthed we log and continue —
    the client reconnects lazily on first use, and surfaces AuthError → 401/403
    from the route at that point. Both calls run off the event loop thread since
    the client's connect/close block on a background thread handshake.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, get_slack_client().connect)
    except Exception as exc:  # noqa: BLE001
        log.warning("Slack MCP connect on startup failed (will retry lazily): %s", exc)
    try:
        yield
    finally:
        try:
            await loop.run_in_executor(None, get_slack_client().close)
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Gmail Action Board API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- AuthError → HTTP 401/403 with the same message text --------------------

def _auth_error_response(exc: Exception) -> JSONResponse:
    """Map a client AuthError to 403 (if it names a 403) else 401, verbatim text."""
    msg = str(exc)
    code = 403 if "403" in msg else 401
    return JSONResponse(status_code=code, content={"detail": msg})


@app.exception_handler(GmailAuthError)
async def _gmail_auth_handler(_request: Request, exc: GmailAuthError) -> JSONResponse:
    return _auth_error_response(exc)


@app.exception_handler(DriveAuthError)
async def _drive_auth_handler(_request: Request, exc: DriveAuthError) -> JSONResponse:
    return _auth_error_response(exc)


# --- API routes -------------------------------------------------------------

app.include_router(tasks.router)
app.include_router(roster.router)
app.include_router(workstreams.router)
app.include_router(tags.router)
app.include_router(triage.router)
app.include_router(slack_triage.router)
app.include_router(ingest.router)
app.include_router(drafts.router)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe — does not touch Google (no auth required)."""
    return {"status": "ok"}


# --- static frontend (prod) -------------------------------------------------
# Serve the built React app if it exists; do NOT fail when it hasn't been built
# yet (dev runs Vite separately on :5173). Mounted last so /api/* wins.

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
