"""Slack Web API client (conversation-centric, read-mostly).

Mirrors ``gmail_client.py``'s structure and safety posture. Auth uses the app's
OWN Slack **user OAuth token** (``xoxp-…``) read from the ``SLACK_USER_TOKEN``
env var at runtime — exactly analogous to how ``GmailClient`` shells out for the
local gcloud ADC token. Direct calls to ``https://slack.com/api/*`` via requests.

The ONLY Slack mutation this client can perform is ``mark_read`` (``conversations.mark``).
There is deliberately no post/delete/react/update method, so the app cannot write
messages regardless of what the UI asks.

On an ``ok=false`` response carrying an auth error (``invalid_auth``,
``token_expired``, ``not_authed``, ``missing_scope``, ``account_inactive``) — or a
401/403 HTTP status — we raise the shared :class:`AuthError` (imported from
``gmail_client`` so a single FastAPI exception handler covers Gmail, Drive, and
Slack) with a re-auth message the UI can surface.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import requests

# Reuse the SAME AuthError type as Gmail/Drive so the existing FastAPI exception
# handler maps 401/403 uniformly. (The plan allows importing OR mirroring it.)
from gmail_client import AuthError

log = logging.getLogger(__name__)

API = "https://slack.com/api"

# Slack error codes that mean "the token is bad / lacks scope" → re-auth.
AUTH_ERRORS = frozenset({
    "invalid_auth", "token_expired", "not_authed", "missing_scope",
    "account_inactive", "token_revoked", "no_permission",
})

# Cap unread messages pulled per conversation, to bound classification cost.
UNREAD_MSG_CAP = 50

# Max pages to follow on any cursor-paginated endpoint (safety bound).
_MAX_PAGES = 50


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


class SlackClient:
    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.environ.get("SLACK_USER_TOKEN", "")
        self._user_cache: dict[str, str] = {}   # user_id -> display name

    # --- auth / transport ---------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _call(self, method: str, params: dict | None = None,
              http_post: bool = False) -> dict:
        """Call a Slack Web API method and return the parsed JSON body.

        Raises AuthError on a 401/403 HTTP status or an ``ok=false`` auth error;
        raises RuntimeError on any other ``ok=false`` (non-auth) Slack error.
        """
        if not self.token:
            raise AuthError(
                "SLACK_USER_TOKEN is not set. Create a Slack app, add the User "
                "Token Scopes, install it, and export the User OAuth Token:\n"
                '  export SLACK_USER_TOKEN="xoxp-…"'
            )
        url = f"{API}/{method}"
        if http_post:
            r = requests.post(url, headers=self._headers(), data=params or {}, timeout=30)
        else:
            r = requests.get(url, headers=self._headers(), params=params or {}, timeout=30)

        if r.status_code in (401, 403):
            raise AuthError(
                f"Slack API returned {r.status_code}. Your Slack user token likely "
                "expired or lacks a required scope. Re-install the Slack app and "
                "re-export SLACK_USER_TOKEN.\n\n"
                f"Detail: {r.text[:300]}"
            )
        r.raise_for_status()

        data = r.json()
        if not data.get("ok", False):
            err = str(data.get("error", "unknown_error"))
            if err in AUTH_ERRORS:
                raise AuthError(
                    f"Slack API `{method}` failed with `{err}`. Your Slack user "
                    "token expired or is missing a scope. Re-install the Slack app "
                    "and re-export SLACK_USER_TOKEN."
                )
            needed = data.get("needed") or data.get("response_metadata", {}).get("messages")
            raise RuntimeError(
                f"Slack API `{method}` failed: {err}"
                + (f" (needed: {needed})" if needed else "")
            )
        return data

    def _paginate(self, method: str, params: dict, key: str) -> list[dict]:
        """Follow ``response_metadata.next_cursor`` and concatenate ``data[key]``."""
        out: list[dict] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            data = self._call(method, page_params)
            out.extend(data.get(key, []) or [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                break
        return out

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
        the list_* callers filter those out.
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
        """All direct-message conversations that currently have ≥1 unread."""
        ims = self._paginate(
            "users.conversations",
            {"types": "im", "limit": 200, "exclude_archived": "true"},
            "channels",
        )
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
