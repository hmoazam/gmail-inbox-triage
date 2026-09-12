"""Slack client (conversation-centric, read-mostly) over the local Slack MCP.

Transport: the app drives the **local Slack MCP server via ``dbexec``** — NOT a
raw Slack HTTP token. The Slack-app install path (xoxp user token) is blocked by
ESI/IT approval, so this client launches ``dbexec repo run mcp start-single slack``
as an MCP stdio server and calls Slack Web API methods through the MCP tools
``slack_read_api_call`` / ``slack_write_api_call``. ``dbexec`` refreshes its own
auth silently — there is no token to manage here.

The public surface is UNCHANGED from the HTTP version so routes, the classifier,
schemas, and the frontend contract need no edits:
  - dataclasses ``SlackMessage`` / ``SlackConversation`` (identical fields)
  - ``get_unread_conversation`` / ``list_unread_dms`` / ``list_unread_in_channels``
  - ``mark_read`` — still the ONLY mutation (``conversations.mark``)

Session model: the MCP stdio server is expensive to launch (~3s handshake) but
sub-second per targeted call once up, so we launch it ONCE and reuse it. FastAPI
routes here are sync while MCP is async, so a dedicated background asyncio loop
runs in a daemon thread with the ``ClientSession`` held open; sync calls are
bridged onto it via ``run_coroutine_threadsafe``. ``api/main.py`` wires
connect-on-startup / close-on-shutdown via a FastAPI lifespan; the connection
also (re)connects lazily under a lock if the server ever dies.

Errors: if a tool returns ``ok=false`` with an auth-ish error, or the stdio
server fails to launch (``dbexec`` missing / unauthed), we raise the shared
:class:`AuthError` (imported from ``gmail_client`` so one FastAPI handler maps
Gmail, Drive, and Slack to 401/403 uniformly).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field

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

# Cap unread messages pulled per conversation, to bound classification cost.
UNREAD_MSG_CAP = 50

# MCP tool names exposed by the dbexec Slack server.
_READ_TOOL = "slack_read_api_call"
_WRITE_TOOL = "slack_write_api_call"


@dataclass
class SlackMessage:
    """One unread message within a conversation."""
    author: str   # resolved display / real name (or bot/username fallback)
    ts: str       # Slack message timestamp ("1712345678.123456")
    text: str     # message text


@dataclass
class SlackConversation:
    """A normalized Slack conversation with its unread messages."""
    id: str                                  # channel id (Cxxxx / Dxxxx)
    kind: str                                # "im" | "channel"
    name: str                                # channel name, or DM user's real name
    messages: list[SlackMessage] = field(default_factory=list)  # unread, chronological
    latest_ts: str = ""                      # ts of the newest unread message
    permalink: str = ""                      # chat.getPermalink for latest_ts

    @property
    def unread_count(self) -> int:
        return len(self.messages)


# ---------------------------------------------------------------------------
# Persistent MCP connection: one stdio server + one asyncio loop in a thread.
# ---------------------------------------------------------------------------

class _MCPConnection:
    """Owns a background asyncio loop (daemon thread) holding an open MCP
    ``ClientSession`` to the dbexec Slack server. Sync callers reach it via
    ``call_tool`` which bridges onto the loop with ``run_coroutine_threadsafe``.

    Launch once, reuse. Lazily (re)connects under a lock if the server died.
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
    def call_tool(self, tool: str, args: dict) -> str:
        """Call an MCP tool and return its concatenated text result.

        Re-raises AuthError verbatim; on any transport failure it resets the
        connection (so the next call reconnects) and raises RuntimeError.
        """
        self._ensure()
        loop = self._loop
        if loop is None:
            raise AuthError("Slack via dbexec is unavailable — connection not established.")
        fut = asyncio.run_coroutine_threadsafe(self._invoke(tool, args), loop)
        try:
            return fut.result(timeout=self._call_timeout + 10)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.close()          # server likely died — reconnect on next call
            raise RuntimeError(
                f"Slack MCP tool `{tool}` failed: {type(exc).__name__}: {exc}"
            )

    async def _invoke(self, tool: str, args: dict) -> str:
        assert self._session is not None
        result = await asyncio.wait_for(
            self._session.call_tool(tool, args), timeout=self._call_timeout)
        text = "".join(getattr(c, "text", "") or "" for c in (result.content or []))
        if getattr(result, "isError", False):
            raise RuntimeError(f"tool error: {text[:200]}")
        return text


class SlackClient:
    def __init__(self, token: str | None = None):
        # ``token`` is accepted for signature compatibility but unused: the MCP
        # transport authenticates through dbexec, not a Slack token.
        self._token = token
        self._user_cache: dict[str, str] = {}   # user_id -> display name

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

    # --- lifecycle (wired from api/main.py lifespan) ------------------------
    def connect(self) -> None:
        """Eagerly launch the MCP server (best-effort optimization for startup)."""
        self._conn.connect()

    def close(self) -> None:
        """Close the MCP session + background loop (shutdown)."""
        self._conn.close()

    # --- transport ----------------------------------------------------------
    @staticmethod
    def _parse(text: str, method: str) -> dict:
        """Parse the JSON Slack response out of an MCP tool's text result.

        Handles both a ``{"result": "<json string>"}`` wrapper (and the
        dict-valued variant) and a bare JSON string that is the Slack response.
        """
        try:
            obj = json.loads(text)
        except Exception:
            raise RuntimeError(
                f"Slack MCP `{method}`: could not parse tool result: {text[:200]!r}"
            )
        # Unwrap {"result": ...} — value may be a JSON string or an object.
        if isinstance(obj, dict) and "ok" not in obj and "result" in obj:
            inner = obj["result"]
            if isinstance(inner, str):
                try:
                    obj = json.loads(inner)
                except Exception:
                    raise RuntimeError(
                        f"Slack MCP `{method}`: could not parse wrapped result: {inner[:200]!r}"
                    )
            elif isinstance(inner, dict):
                obj = inner
        if not isinstance(obj, dict):
            raise RuntimeError(f"Slack MCP `{method}`: unexpected result shape.")
        return obj

    def _call(self, method: str, params: dict | None = None,
              http_post: bool = False) -> dict:
        """Call a Slack Web API ``method`` through the MCP transport.

        ``http_post`` selects the write tool (``conversations.mark`` only);
        everything else is a read. Raises AuthError on an ``ok=false`` auth
        error; RuntimeError on any other ``ok=false`` Slack error.
        """
        tool = _WRITE_TOOL if http_post else _READ_TOOL
        raw = self._conn.call_tool(tool, {"endpoint": method, "params": params or {}})
        data = self._parse(raw, method)

        if not data.get("ok", False):
            err = str(data.get("error", "unknown_error"))
            if err in AUTH_ERRORS:
                raise AuthError(
                    f"Slack via dbexec returned `{err}` for `{method}`. The dbexec "
                    "Slack auth is missing or expired — re-authenticate dbexec."
                )
            needed = data.get("needed") or (data.get("response_metadata") or {}).get("messages")
            raise RuntimeError(
                f"Slack API `{method}` failed: {err}"
                + (f" (needed: {needed})" if needed else "")
            )
        return data

    # --- name resolution (cached) -------------------------------------------
    def _resolve_user(self, user_id: str | None) -> str:
        """Resolve a Slack user id to a display name via cached ``users.info``."""
        if not user_id:
            return "unknown"
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            data = self._call("users.info", {"user": user_id})
            u = data.get("user", {}) or {}
            profile = u.get("profile", {}) or {}
            name = (profile.get("real_name") or u.get("real_name")
                    or profile.get("display_name") or u.get("name") or user_id)
        except AuthError:
            raise
        except Exception:
            name = user_id
        self._user_cache[user_id] = name
        return name

    def _message_author(self, msg: dict) -> str:
        """Best-effort author label for a history message (user, bot, or system)."""
        if msg.get("user"):
            return self._resolve_user(msg["user"])
        # Bot messages / integrations carry a username instead of a user id.
        return msg.get("username") or (msg.get("bot_profile") or {}).get("name") or "bot"

    # --- reading conversations ----------------------------------------------
    def get_unread_conversation(self, channel_id: str, *, kind: str | None = None,
                                dm_user: str | None = None) -> SlackConversation:
        """Return a normalized conversation with its unread messages.

        Reads ``conversations.info`` for ``last_read`` + name, then
        ``conversations.history oldest=last_read`` (exclusive) for the unread
        messages, capped at ``UNREAD_MSG_CAP`` (newest kept). ``latest_ts`` and a
        ``chat.getPermalink`` are attached when there is at least one unread msg.

        The returned conversation may have ``unread_count == 0`` (nothing unread);
        the list_* callers filter those out. All calls are per-channel and
        sub-second through the MCP transport.
        """
        info = self._call("conversations.info", {"channel": channel_id}).get("channel", {}) or {}

        if kind is None:
            kind = "im" if info.get("is_im") else "channel"

        if kind == "im":
            uid = dm_user or info.get("user")
            name = self._resolve_user(uid)
        else:
            name = info.get("name") or channel_id

        last_read = info.get("last_read") or "0"

        # conversations.history returns newest-first. oldest=last_read with
        # inclusive=false excludes the already-read boundary message.
        hist = self._call("conversations.history", {
            "channel": channel_id,
            "oldest": last_read,
            "inclusive": "false",
            "limit": UNREAD_MSG_CAP,
        })
        raw_msgs = hist.get("messages", []) or []

        messages: list[SlackMessage] = []
        for m in raw_msgs:
            # Skip Slack's own read-marker / tombstone rows without real content.
            if m.get("subtype") in ("channel_join", "channel_leave", "channel_topic",
                                    "channel_purpose", "channel_name"):
                continue
            messages.append(SlackMessage(
                author=self._message_author(m),
                ts=str(m.get("ts", "")),
                text=str(m.get("text", "") or ""),
            ))
        # History is newest-first; present chronologically (oldest → newest).
        messages.reverse()

        conv = SlackConversation(id=channel_id, kind=kind, name=name, messages=messages)
        if messages:
            conv.latest_ts = messages[-1].ts
            conv.permalink = self._permalink(channel_id, conv.latest_ts)
        return conv

    def _permalink(self, channel_id: str, ts: str) -> str:
        try:
            data = self._call("chat.getPermalink",
                              {"channel": channel_id, "message_ts": ts})
            return data.get("permalink", "") or ""
        except AuthError:
            raise
        except Exception:
            return ""

    def list_unread_dms(self) -> list[SlackConversation]:
        """All direct-message conversations that currently have ≥1 unread.

        PERF: full-workspace DM enumeration is the one slow Slack call, so we pull
        a single bounded page (``config.SLACK_DM_LIMIT``) rather than paginating
        the whole IM list, then fetch unread state per DM (each sub-second).
        """
        limit = int(getattr(config, "SLACK_DM_LIMIT", 100))
        data = self._call("users.conversations", {
            "types": "im",
            "limit": limit,
            "exclude_archived": "true",
        })
        ims = data.get("channels", []) or []
        out: list[SlackConversation] = []
        for im in ims:
            conv = self.get_unread_conversation(
                im["id"], kind="im", dm_user=im.get("user"))
            if conv.unread_count > 0:
                out.append(conv)
        return out

    def list_unread_in_channels(self, channel_ids: list[str]) -> list[SlackConversation]:
        """For each configured channel id, return it iff it has ≥1 unread."""
        out: list[SlackConversation] = []
        for cid in channel_ids:
            conv = self.get_unread_conversation(cid, kind="channel")
            if conv.unread_count > 0:
                out.append(conv)
        return out

    # --- the one mutation: mark as read -------------------------------------
    def mark_read(self, channel_id: str, ts: str) -> None:
        """Move the read cursor to ``ts`` (``conversations.mark``). The only write."""
        self._call("conversations.mark", {"channel": channel_id, "ts": ts}, http_post=True)
