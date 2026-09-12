"""Slack action extraction over the local Slack MCP (URL-driven).

Transport: the app drives the **local Slack MCP server via ``dbexec``** — NOT a
raw Slack HTTP token (the xoxp install path is blocked by ESI/IT). It launches
``dbexec repo run mcp start-single slack`` as an MCP stdio server (env
``DBEXEC_NO_CERT_REFRESH=1``) and calls Slack Web API methods through the MCP
tools ``slack_read_api_call`` / ``slack_write_api_call``. dbexec refreshes its
own auth silently — there is no token to manage.

Shape (proven): the MCP privacy filter blocks raw message dumps but ALLOWS
``analysis_prompt`` answers — the summarizer reads the conversation server-side
and returns only a structured result. So the Slack tab is URL-driven:

  ``extract_actions(url, user_name)`` — resolve a pasted Slack channel/thread URL,
  read that ONE conversation with an ``analysis_prompt`` that asks for the pending
  actions the user personally still needs to do, and return them as a list of
  ``{task, context, due}`` dicts. Latency is ~45s–2min, so a generous per-call
  timeout is used and a slow call surfaces as a clean timeout error.

Session model: the MCP stdio server is expensive to launch (~3s handshake) but
usable per call once up, so we launch it ONCE and reuse it. FastAPI routes are
sync while MCP is async, so a dedicated background asyncio loop runs in a daemon
thread holding the ``ClientSession`` open; sync calls bridge onto it via
``run_coroutine_threadsafe``. ``api/main.py`` wires connect-on-startup /
close-on-shutdown; the connection also (re)connects lazily under a lock if the
server ever dies.

Errors: if a tool returns an auth-ish failure, or the stdio server fails to
launch (dbexec missing / unauthed), we raise the shared :class:`AuthError`
(imported from ``gmail_client`` so one FastAPI handler maps 401/403 uniformly).
A malformed / non-Slack URL raises ``ValueError``; a slow extraction raises
``TimeoutError``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config
# Reuse the SAME AuthError type as Gmail/Drive so the existing FastAPI exception
# handler maps 401/403 uniformly.
from gmail_client import AuthError

log = logging.getLogger(__name__)

# Slack error codes that mean "auth is bad / lacks scope" → surface as AuthError.
AUTH_ERRORS = frozenset({
    "invalid_auth", "token_expired", "not_authed", "missing_scope",
    "account_inactive", "token_revoked", "no_permission",
})

# MCP tool names exposed by the dbexec Slack server.
_READ_TOOL = "slack_read_api_call"
_WRITE_TOOL = "slack_write_api_call"

# How many recent messages to feed the summarizer for a channel URL.
HISTORY_LIMIT = 40

# The MCP wraps content-derived answers with this marker; strip it before parsing.
_PRIVACY_MARKER = "[MCP_PRIVACY_SUMMARIZED]"

# A Slack archive URL: /archives/<CHANNEL>[/p<10 digits><6 digits>] (thread ts).
_ARCHIVE_RE = re.compile(r"/archives/([A-Z][A-Z0-9]{2,})(?:/p(\d{10})(\d{6,}))?")


# ---------------------------------------------------------------------------
# URL parsing + answer parsing (module-level; unit-tested directly)
# ---------------------------------------------------------------------------

def parse_slack_url(url: str) -> tuple[str, str | None]:
    """Parse a Slack archive URL into ``(channel_id, thread_ts | None)``.

    Accepts:
      - channel: ``https://<team>.slack.com/archives/C0XXXX``
      - thread:  ``https://<team>.slack.com/archives/C0XXXX/p1712345678123456``
        (``pXXXXXXXXXXYYYYYY`` → ts ``XXXXXXXXXX.YYYYYY``)

    Raises ``ValueError`` on a non-Slack or malformed archive URL.
    """
    if not url or "slack.com/archives/" not in url:
        raise ValueError(
            f"Not a Slack conversation URL: {url!r}. Expected a link like "
            "https://<team>.slack.com/archives/C0XXXX[/p...]."
        )
    m = _ARCHIVE_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse a Slack channel id from URL: {url!r}.")
    channel_id = m.group(1)
    thread_ts = f"{m.group(2)}.{m.group(3)}" if m.group(2) else None
    return channel_id, thread_ts


def _extract_json_array(text: str) -> list:
    """Pull the first top-level JSON array out of an answer string.

    Tries a bare array, then a ```json … ``` fence, then any ``[ … ]`` span.
    Returns ``[]`` on any failure so callers always get a list.
    """
    s = (text or "").strip()
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return val
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    if fenced:
        try:
            val = json.loads(fenced.group(1))
            if isinstance(val, list):
                return val
        except Exception:
            pass
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            val = json.loads(s[start:end + 1])
            if isinstance(val, list):
                return val
        except Exception:
            pass
    return []


def parse_extracted_actions(text: str) -> list[dict]:
    """Parse the MCP analysis answer into a list of ``{task, context, due}`` dicts.

    Strips a leading ``[MCP_PRIVACY_SUMMARIZED] …`` marker and any ```json fence,
    parses the JSON array, and coerces each item to ``{task: str, context: str,
    due: str | None}``. Items without a real ``task`` are dropped. Returns ``[]``
    when there are no actions or the answer cannot be parsed.
    """
    if not text:
        return []
    s = text.strip()

    # Unwrap a {"result": "<answer>"} envelope if the tool returned one.
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and "result" in obj and isinstance(obj["result"], str):
            s = obj["result"].strip()
    except Exception:
        pass

    # Strip the privacy marker (it precedes the actual answer).
    if _PRIVACY_MARKER in s:
        s = s.split(_PRIVACY_MARKER, 1)[1].strip()

    out: list[dict] = []
    for item in _extract_json_array(s):
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        due = item.get("due")
        if isinstance(due, str):
            due = due.strip() or None
        elif due is not None:
            due = None
        out.append({
            "task": task,
            "context": str(item.get("context") or "").strip(),
            "due": due,
        })
    return out


def _analysis_prompt(user_name: str | None) -> str:
    who = user_name or "the account owner"
    return (
        f"Read this Slack conversation and identify ONLY the PENDING action items "
        f"that {who} personally still needs to do — exclude anything already done "
        f"and anything that is someone else's responsibility. "
        f"Respond with ONLY a JSON array and no other prose. Each element must be "
        f'an object: {{"task": "<short imperative>", "context": "<one line of '
        f'why/what>", "due": "YYYY-MM-DD" or null}}. '
        f"If there are no pending actions for {who}, respond with exactly []."
    )


# ---------------------------------------------------------------------------
# Persistent MCP connection: one stdio server + one asyncio loop in a thread.
# ---------------------------------------------------------------------------

class _MCPConnection:
    """Owns a background asyncio loop (daemon thread) holding an open MCP
    ``ClientSession`` to the dbexec Slack server. Sync callers reach it via
    ``call_tool`` which bridges onto the loop with ``run_coroutine_threadsafe``.

    Launch once, reuse. Lazily (re)connects under a lock if the server died.
    ``call_tool`` accepts a per-call ``timeout`` override (extraction is slow).
    """

    def __init__(self, params: StdioServerParameters,
                 connect_timeout: float, call_timeout: float):
        self._params = params
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout

        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._shutdown: asyncio.Event | None = None
        self._ready: threading.Event | None = None
        self._connect_error: BaseException | None = None

    # --- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        """Eagerly establish the session (idempotent). Raises AuthError on failure."""
        self._ensure()

    def close(self) -> None:
        """Tear down the session + loop + thread (idempotent)."""
        with self._lock:
            self._teardown()

    def _ensure(self) -> None:
        with self._lock:
            if self._session is not None and self._thread is not None and self._thread.is_alive():
                return
            self._teardown()          # clear any half-dead state
            self._connect_locked()

    def _connect_locked(self) -> None:
        self._ready = threading.Event()
        self._connect_error = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="slack-mcp", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=self._connect_timeout + 15):
            self._teardown()
            raise AuthError(
                "Slack via dbexec is unavailable — timed out launching the MCP "
                "server. Ensure dbexec is installed and authenticated."
            )
        if self._connect_error is not None:
            err = self._connect_error
            self._teardown()
            raise AuthError(
                "Slack via dbexec is unavailable — ensure dbexec is installed and "
                f"authenticated. Detail: {type(err).__name__}: {err}"
            )

    def _teardown(self) -> None:
        """Best-effort shutdown; assumes the caller holds ``self._lock``."""
        loop, thread, shutdown = self._loop, self._thread, self._shutdown
        self._session = None
        self._loop = None
        self._thread = None
        self._shutdown = None
        if loop is not None and shutdown is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(shutdown.set)
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001
            self._connect_error = self._connect_error or exc
            if self._ready is not None:
                self._ready.set()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        self._shutdown = asyncio.Event()
        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=self._connect_timeout)
                    self._session = session
                    assert self._ready is not None
                    self._ready.set()
                    await self._shutdown.wait()
        except Exception as exc:  # noqa: BLE001
            self._connect_error = exc
            self._session = None
            if self._ready is not None:
                self._ready.set()

    # --- invocation ---------------------------------------------------------
    def call_tool(self, tool: str, args: dict, timeout: float | None = None) -> str:
        """Call an MCP tool and return its concatenated text result.

        ``timeout`` overrides the default per-call budget for this call (used for
        the slow extraction). Re-raises AuthError verbatim; raises ``TimeoutError``
        on a timeout and ``RuntimeError`` on any other transport failure — both
        after resetting the connection so the next call reconnects.
        """
        self._ensure()
        loop = self._loop
        if loop is None:
            raise AuthError("Slack via dbexec is unavailable — connection not established.")
        call_timeout = self._call_timeout if timeout is None else timeout
        fut = asyncio.run_coroutine_threadsafe(self._invoke(tool, args, call_timeout), loop)
        try:
            return fut.result(timeout=call_timeout + 10)
        except AuthError:
            raise
        except TimeoutError:
            # asyncio.TimeoutError and concurrent.futures.TimeoutError both alias
            # the builtin TimeoutError on Python 3.11+.
            self.close()
            raise TimeoutError(
                f"Slack MCP tool `{tool}` timed out after {call_timeout:.0f}s "
                "(the summarizer is slow — try again)."
            )
        except Exception as exc:  # noqa: BLE001
            self.close()          # server likely died — reconnect on next call
            raise RuntimeError(
                f"Slack MCP tool `{tool}` failed: {type(exc).__name__}: {exc}"
            )

    async def _invoke(self, tool: str, args: dict, timeout: float) -> str:
        assert self._session is not None
        result = await asyncio.wait_for(
            self._session.call_tool(tool, args), timeout=timeout)
        text = "".join(getattr(c, "text", "") or "" for c in (result.content or []))
        if getattr(result, "isError", False):
            raise RuntimeError(f"tool error: {text[:200]}")
        return text


class SlackClient:
    def __init__(self, token: str | None = None):
        # ``token`` is accepted for signature compatibility but unused: the MCP
        # transport authenticates through dbexec, not a Slack token.
        self._token = token

        params = StdioServerParameters(
            command=getattr(config, "SLACK_MCP_COMMAND", "dbexec"),
            args=list(getattr(config, "SLACK_MCP_ARGS",
                              ["repo", "run", "mcp", "start-single", "slack"])),
            env={**os.environ, "DBEXEC_NO_CERT_REFRESH": "1"},
        )
        self._conn = _MCPConnection(
            params,
            connect_timeout=float(getattr(config, "SLACK_MCP_CONNECT_TIMEOUT", 120)),
            call_timeout=float(getattr(config, "SLACK_MCP_CALL_TIMEOUT", 30)),
        )
        self._extract_timeout = float(getattr(config, "SLACK_EXTRACT_TIMEOUT", 180))

    # --- lifecycle (wired from api/main.py lifespan) ------------------------
    def connect(self) -> None:
        """Eagerly launch the MCP server (best-effort optimization for startup)."""
        self._conn.connect()

    def close(self) -> None:
        """Close the MCP session + background loop (shutdown)."""
        self._conn.close()

    # --- the one operation: extract my pending actions from a URL -----------
    def extract_actions(self, url: str, user_name: str | None = None) -> list[dict]:
        """Read one Slack conversation (by URL) and extract the user's pending actions.

        Resolves ``url`` to a channel (``conversations.history``) or thread
        (``conversations.replies``) and passes an ``analysis_prompt`` that asks the
        MCP summarizer for the pending actions ``user_name`` personally still needs
        to do. Returns a list of ``{task, context, due}`` dicts (``[]`` if none).

        Raises ``ValueError`` on a bad URL, ``AuthError`` on a dbexec/auth failure,
        and ``TimeoutError`` if the (slow) extraction exceeds the budget.
        """
        channel_id, thread_ts = parse_slack_url(url)   # ValueError on bad URL
        prompt = _analysis_prompt(user_name)

        if thread_ts is not None:
            endpoint = "conversations.replies"
            params = {"channel": channel_id, "ts": thread_ts,
                      "limit": HISTORY_LIMIT, "analysis_prompt": prompt}
        else:
            endpoint = "conversations.history"
            params = {"channel": channel_id,
                      "limit": HISTORY_LIMIT, "analysis_prompt": prompt}

        raw = self._conn.call_tool(
            _READ_TOOL, {"endpoint": endpoint, "params": params},
            timeout=self._extract_timeout,
        )  # AuthError / TimeoutError / RuntimeError propagate
        return parse_extracted_actions(raw)
