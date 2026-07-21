"""Gmail REST client (thread-centric, read-mostly).

Auth follows the vibe fe-google-tools CLI path: a bearer token from
`gcloud auth application-default print-access-token`, plus the
`x-goog-user-project` quota-project header on every call.

The ONLY inbox mutation this client can perform is mark-as-read (remove the
UNREAD label). There is deliberately no archive / label / trash / delete method,
so the app cannot move mail regardless of what the UI asks.
"""
from __future__ import annotations

import base64
import re
import subprocess
import time
from email.utils import parseaddr

import requests

from config import QUOTA_PROJECT, PER_MSG_BODY_CHARS
from models import ThreadMessage, EmailThread

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class AuthError(RuntimeError):
    """Raised on 401/403 so the UI can tell the user to re-auth."""


class GmailClient:
    def __init__(self, quota_project: str = QUOTA_PROJECT):
        self.quota_project = quota_project
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._profile_email: str | None = None

    # --- auth ---------------------------------------------------------------
    def get_token(self) -> str:
        if self._token and (time.time() - self._token_ts) < 300:
            return self._token
        try:
            out = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise AuthError("gcloud not found. Install the Google Cloud SDK and run "
                            "`gcloud auth application-default login`.")
        if out.returncode != 0:
            raise AuthError(
                "Could not get a Google access token. Run:\n"
                "  gcloud auth application-default login\n\n"
                f"gcloud said: {out.stderr.strip()}"
            )
        self._token = out.stdout.strip()
        self._token_ts = time.time()
        return self._token

    def _headers(self, json_body: bool = False) -> dict:
        h = {
            "Authorization": f"Bearer {self.get_token()}",
        }
        # Only send the quota-project header when one is configured. Some
        # projects require it for Gmail API billing/quota; if you hit a
        # "user project" error, set GMAIL_QUOTA_PROJECT to your GCP project id.
        if self.quota_project:
            h["x-goog-user-project"] = self.quota_project
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _check(self, r: requests.Response) -> requests.Response:
        if r.status_code in (401, 403):
            raise AuthError(
                f"Gmail API returned {r.status_code}. Your Google auth likely "
                "expired or lacks the quota project. Re-run:\n"
                "  gcloud auth application-default login\n\n"
                f"Detail: {r.text[:300]}"
            )
        r.raise_for_status()
        return r

    def get_profile_email(self) -> str:
        """The authenticated account's own email address (for from_me / internal)."""
        if self._profile_email is not None:
            return self._profile_email
        r = self._check(requests.get(
            f"{API}/profile", headers=self._headers(), timeout=30))
        self._profile_email = (r.json().get("emailAddress") or "").lower()
        return self._profile_email

    # --- reading threads ----------------------------------------------------
    def list_inbox_threads(self, max_results: int | None = None,
                           query: str = "in:inbox is:unread") -> list[str]:
        """Return thread ids matching `query`.

        If max_results is None (the default), paginates through ALL results.
        Pass an integer to cap at that many threads (e.g. for testing).
        Gmail returns at most 500 per page, so we follow nextPageToken until
        we have everything (or hit max_results).
        """
        ids: list[str] = []
        page_token: str | None = None
        while True:
            page_size = 500 if max_results is None else min(500, max_results - len(ids))
            params: dict = {"maxResults": page_size, "q": query}
            if page_token:
                params["pageToken"] = page_token
            r = self._check(requests.get(
                f"{API}/threads", headers=self._headers(),
                params=params, timeout=30,
            ))
            data = r.json()
            ids.extend(t["id"] for t in data.get("threads", []))
            page_token = data.get("nextPageToken")
            if not page_token or (max_results is not None and len(ids) >= max_results):
                break
        return ids

    def get_thread(self, thread_id: str, me: str | None = None) -> EmailThread:
        me = (me or self.get_profile_email()).lower()
        r = self._check(requests.get(
            f"{API}/threads/{thread_id}",
            headers=self._headers(),
            params={"format": "full"},
            timeout=30,
        ))
        data = r.json()
        messages, label_union, unread, subject, unsub = [], set(), False, "", None
        for msg in data.get("messages", []):
            payload = msg.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            sender = headers.get("from", "")
            sender_email = parseaddr(sender)[1].lower()
            labels = msg.get("labelIds", [])
            label_union.update(labels)
            if "UNREAD" in labels:
                unread = True
            if not subject:
                subject = headers.get("subject", "(no subject)")
            if unsub is None:
                unsub = self._parse_unsubscribe(headers.get("list-unsubscribe"))
            messages.append(ThreadMessage(
                id=msg["id"],
                sender=sender,
                sender_email=sender_email,
                to=headers.get("to", ""),
                date=headers.get("date", ""),
                body=self._extract_body(payload)[:PER_MSG_BODY_CHARS],
                from_me=(sender_email == me),
            ))
        return EmailThread(
            thread_id=data.get("id", thread_id),
            subject=subject or "(no subject)",
            messages=messages,
            label_ids=sorted(label_union),
            unread=unread,
            unsubscribe=unsub,
        )

    def get_threads(self, thread_ids: list[str], progress=None) -> list[EmailThread]:
        """Fetch each thread. `progress(done, total)` is called after each."""
        me = self.get_profile_email()
        out = []
        for i, tid in enumerate(thread_ids, 1):
            out.append(self.get_thread(tid, me=me))
            if progress:
                progress(i, len(thread_ids))
        return out

    # --- the one mutation: mark as read -------------------------------------
    def mark_read(self, message_ids: list[str]) -> None:
        """Remove UNREAD from the given messages (batch). The only write op."""
        if not message_ids:
            return
        self._check(requests.post(
            f"{API}/messages/batchModify",
            headers=self._headers(json_body=True),
            json={"ids": message_ids, "removeLabelIds": ["UNREAD"]},
            timeout=30,
        ))

    def mark_thread_read(self, thread: EmailThread) -> None:
        self.mark_read(thread.message_ids)

    # --- parsing helpers ----------------------------------------------------
    @staticmethod
    def _parse_unsubscribe(header: str | None) -> str | None:
        if not header:
            return None
        urls = re.findall(r"<([^>]+)>", header)
        if not urls:
            return None
        for u in urls:
            if u.lower().startswith("http"):
                return u
        return urls[0]

    def _extract_body(self, payload: dict) -> str:
        plain = self._find_part(payload, "text/plain")
        if plain:
            return plain
        html = self._find_part(payload, "text/html")
        if html:
            return re.sub(r"<[^>]+>", " ", html)
        return ""

    def _find_part(self, part: dict, mime: str) -> str:
        if part.get("mimeType") == mime and part.get("body", {}).get("data"):
            return self._decode(part["body"]["data"])
        for sub in part.get("parts", []) or []:
            found = self._find_part(sub, mime)
            if found:
                return found
        return ""

    @staticmethod
    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
        except Exception:
            return ""
