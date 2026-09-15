"""Google Drive REST client (read-focused with folder-creation support).

Auth reuses the same ADC bearer-token + x-goog-user-project pattern as
GmailClient: a token from `gcloud auth application-default print-access-token`,
plus the `x-goog-user-project` quota-project header on every call.

401/403 → AuthError (same shape as gmail_client.AuthError so the UI can handle
both with one except clause).

Drive scopes required (see start.sh / reauth.sh):
  drive.file     — create the transcript folder/subfolders, read files the app
                   itself created.
  drive.readonly — read Meet auto-saved Docs and manually-uploaded transcripts
                   that the app did not create.

Optional dependencies (soft — import failures degrade gracefully):
  pdfminer.six : PDF text extraction; falls back to raw bytes decode.
  python-docx  : .docx text extraction; falls back to raw bytes decode.
  The txt and Google Doc paths work with zero new dependencies.
"""
from __future__ import annotations

import io
import logging
import subprocess
import time
from datetime import datetime

import requests

import config
from config import QUOTA_PROJECT

log = logging.getLogger(__name__)

DRIVE_API  = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME   = "application/vnd.google-apps.document"
DOCX_MIME   = ("application/vnd.openxmlformats-officedocument"
               ".wordprocessingml.document")

# ── soft optional dependencies ───────────────────────────────────────────────
try:
    from pdfminer.high_level import extract_text as _pdf_extract  # type: ignore
    _HAS_PDFMINER = True
except ImportError:
    _HAS_PDFMINER = False

try:
    import docx as _docx_lib  # type: ignore
    _HAS_PYTHON_DOCX = True
except ImportError:
    _HAS_PYTHON_DOCX = False


class AuthError(RuntimeError):
    """Raised on 401/403 so the UI can tell the user to re-auth."""


class DriveClient:
    def __init__(self, quota_project: str = QUOTA_PROJECT):
        self.quota_project = quota_project
        self._token: str | None = None
        self._token_ts: float = 0.0

    # ── auth (identical pattern to GmailClient) ──────────────────────────────

    def get_token(self) -> str:
        if self._token and (time.time() - self._token_ts) < 300:
            return self._token
        try:
            out = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise AuthError(
                "gcloud not found. Install the Google Cloud SDK and run "
                "`gcloud auth application-default login`."
            )
        if out.returncode != 0:
            raise AuthError(
                "Could not get a Google access token. Run:\n"
                "  bash reauth.sh\n\n"
                f"gcloud said: {out.stderr.strip()}"
            )
        self._token = out.stdout.strip()
        self._token_ts = time.time()
        return self._token

    def _headers(self, json_body: bool = False) -> dict:
        h = {"Authorization": f"Bearer {self.get_token()}"}
        if self.quota_project:
            h["x-goog-user-project"] = self.quota_project
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _check(self, r: requests.Response) -> requests.Response:
        if r.status_code in (401, 403):
            raise AuthError(
                f"Drive API returned {r.status_code}. Your Google auth likely "
                "expired or is missing Drive scopes. Re-run:\n"
                "  bash reauth.sh\n\n"
                f"Detail: {r.text[:300]}"
            )
        r.raise_for_status()
        return r

    # ── Meet auto-saved notes ─────────────────────────────────────────────────

    def list_meet_recording_docs(self, since: datetime | None = None) -> list[dict]:
        """Return Drive Docs matching the Meet auto-save pattern.

        Constrains by modifiedTime >= `since` (if given) and by the folder
        configured as MEET_NOTES_FOLDER in config (if set). Without at least one
        constraint the result set would be all Docs in Drive — too broad — so this
        returns [] and logs a warning in that case.

        Returns a list of dicts: {id, name, created_time, modified_time, web_link}.
        Returns [] on non-auth errors (never raises except AuthError).
        """
        meet_folder: str = getattr(config, "MEET_NOTES_FOLDER", "")

        if not meet_folder and not since:
            log.warning(
                "list_meet_recording_docs: called without `since` or "
                "MEET_NOTES_FOLDER configured — returning [] to avoid fetching "
                "all Drive Docs. Set MEET_NOTES_FOLDER in config.py."
            )
            return []

        try:
            q_parts = [f"mimeType='{GDOC_MIME}'", "trashed=false"]
            if since:
                ts = since.strftime("%Y-%m-%dT%H:%M:%SZ")
                q_parts.append(f"modifiedTime >= '{ts}'")
            if meet_folder:
                folder_id = self._find_folder_id(meet_folder, parent="root")
                if folder_id:
                    q_parts.append(f"'{folder_id}' in parents")
                else:
                    log.warning(
                        "list_meet_recording_docs: folder '%s' not found in "
                        "Drive root — searching without folder constraint.",
                        meet_folder,
                    )

            results = self._paginate_files(
                " and ".join(q_parts),
                fields="id,name,createdTime,modifiedTime,webViewLink",
            )
            return [
                {
                    "id":            f.get("id"),
                    "name":          f.get("name"),
                    "created_time":  f.get("createdTime"),
                    "modified_time": f.get("modifiedTime"),
                    "web_link":      f.get("webViewLink"),
                }
                for f in results
            ]
        except AuthError:
            raise
        except Exception as exc:
            log.warning("list_meet_recording_docs: unexpected error — %s", exc)
            return []

    def export_doc_text(self, file_id: str) -> str:
        """Export a Google Doc as plain text via the Drive export endpoint."""
        r = self._check(requests.get(
            f"{DRIVE_API}/files/{file_id}/export",
            headers=self._headers(),
            params={"mimeType": "text/plain"},
            timeout=60,
        ))
        return r.text

    # ── transcript folder management ──────────────────────────────────────────

    def ensure_transcript_folder(self) -> dict:
        """Idempotently ensure the transcript parent folder + meet/ + teams/ subfolders.

        Reads TRANSCRIPT_FOLDER from config (falls back to "Meet Transcripts" if
        the foundation teammate hasn't added it yet). Does NOT overwrite existing
        folders — uses files.list before creating.

        Returns:
            {
              "parent": {"id": <str>, "link": <Drive URL>},
              "meet":   {"id": <str>, "link": <Drive URL>},
              "teams":  {"id": <str>, "link": <Drive URL>},
            }

        Raises AuthError on 401/403; re-raises other errors (folder ops are
        mutations and the caller should surface failures).
        """
        parent_name: str = getattr(config, "TRANSCRIPT_FOLDER", "Meet Transcripts")

        parent_id = self._find_folder_id(parent_name, parent="root")
        if not parent_id:
            parent_id = self._create_folder(parent_name, parent="root")

        meet_id = self._find_folder_id("meet", parent=parent_id)
        if not meet_id:
            meet_id = self._create_folder("meet", parent=parent_id)

        teams_id = self._find_folder_id("teams", parent=parent_id)
        if not teams_id:
            teams_id = self._create_folder("teams", parent=parent_id)

        def _link(fid: str) -> str:
            return f"https://drive.google.com/drive/folders/{fid}"

        return {
            "parent": {"id": parent_id, "link": _link(parent_id)},
            "meet":   {"id": meet_id,   "link": _link(meet_id)},
            "teams":  {"id": teams_id,  "link": _link(teams_id)},
        }

    def list_folder_files(self, folder_id: str) -> list[dict]:
        """Return all non-folder, non-trashed files directly under folder_id.

        Returns a list of dicts: {id, name, mime_type, created_time,
        modified_time, web_link}.
        """
        q = (
            f"'{folder_id}' in parents "
            f"and mimeType!='{FOLDER_MIME}' "
            "and trashed=false"
        )
        results = self._paginate_files(
            q,
            fields="id,name,mimeType,createdTime,modifiedTime,webViewLink",
        )
        return [
            {
                "id":            f.get("id"),
                "name":          f.get("name"),
                "mime_type":     f.get("mimeType"),
                "created_time":  f.get("createdTime"),
                "modified_time": f.get("modifiedTime"),
                "web_link":      f.get("webViewLink"),
            }
            for f in results
        ]

    def read_file_text(self, file_id: str, mime_type: str) -> str:
        """Read a Drive file as plain text.

        Dispatch rules:
          Google Doc  (GDOC_MIME)  → export via Drive export endpoint (no download)
          text/plain               → download + utf-8 decode
          application/pdf          → pdfminer.six if available, else raw decode
          .docx                    → python-docx if available, else raw decode
          anything else            → raw decode (best-effort, lossy)

        txt and Google Doc paths work with no optional dependencies installed.
        Raises AuthError on 401/403.
        """
        if mime_type == GDOC_MIME:
            return self.export_doc_text(file_id)

        raw = self._download_bytes(file_id)

        if mime_type == "text/plain":
            return raw.decode("utf-8", errors="replace")

        if mime_type == "application/pdf":
            if _HAS_PDFMINER:
                try:
                    return _pdf_extract(io.BytesIO(raw))
                except Exception as exc:
                    log.warning(
                        "read_file_text: pdfminer failed for %s (%s) — "
                        "falling back to raw decode.",
                        file_id, exc,
                    )
            return raw.decode("utf-8", errors="replace")

        if mime_type == DOCX_MIME:
            if _HAS_PYTHON_DOCX:
                try:
                    doc = _docx_lib.Document(io.BytesIO(raw))
                    return "\n".join(p.text for p in doc.paragraphs)
                except Exception as exc:
                    log.warning(
                        "read_file_text: python-docx failed for %s (%s) — "
                        "falling back to raw decode.",
                        file_id, exc,
                    )
            return raw.decode("utf-8", errors="replace")

        # Fallback for any other MIME type (e.g. .txt uploaded with wrong type)
        return raw.decode("utf-8", errors="replace")

    def infer_source(self, file_id: str, folder_structure: dict) -> str:
        """Infer transcript source tag from which subfolder the file lives in.

        Calls files.get?fields=parents to retrieve the file's parent IDs, then
        compares against the meet/ and teams/ IDs from ensure_transcript_folder().

        Returns "meet", "teams", or "manual".
        Returns "manual" on any error other than AuthError.
        """
        try:
            r = self._check(requests.get(
                f"{DRIVE_API}/files/{file_id}",
                headers=self._headers(),
                params={"fields": "parents"},
                timeout=30,
            ))
            parents: set[str] = set(r.json().get("parents") or [])
        except AuthError:
            raise
        except Exception as exc:
            log.warning("infer_source: could not fetch parents for %s — %s", file_id, exc)
            return "manual"

        meet_id  = (folder_structure.get("meet")  or {}).get("id")
        teams_id = (folder_structure.get("teams") or {}).get("id")

        if meet_id and meet_id in parents:
            return "meet"
        if teams_id and teams_id in parents:
            return "teams"
        return "manual"

    # ── private helpers ───────────────────────────────────────────────────────

    def _paginate_files(self, q: str, fields: str = "id,name") -> list[dict]:
        """Page through files.list results, following nextPageToken until done."""
        items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {
                "q": q,
                "fields": f"nextPageToken,files({fields})",
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token
            r = self._check(requests.get(
                f"{DRIVE_API}/files",
                headers=self._headers(),
                params=params,
                timeout=30,
            ))
            data = r.json()
            items.extend(data.get("files") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items

    def _find_folder_id(self, name: str, parent: str = "root") -> str | None:
        """Return the Drive ID of the first folder matching name + parent, or None."""
        escaped = name.replace("'", "\\'")
        q = (
            f"name='{escaped}' "
            f"and mimeType='{FOLDER_MIME}' "
            f"and '{parent}' in parents "
            "and trashed=false"
        )
        results = self._paginate_files(q, fields="id")
        return results[0]["id"] if results else None

    def _create_folder(self, name: str, parent: str = "root") -> str:
        """Create a Drive folder under `parent` and return its ID."""
        r = self._check(requests.post(
            f"{DRIVE_API}/files",
            headers=self._headers(json_body=True),
            json={
                "name": name,
                "mimeType": FOLDER_MIME,
                "parents": [parent],
            },
            timeout=30,
        ))
        return r.json()["id"]

    def _download_bytes(self, file_id: str) -> bytes:
        """Download a file's binary content (?alt=media)."""
        r = self._check(requests.get(
            f"{DRIVE_API}/files/{file_id}",
            headers=self._headers(),
            params={"alt": "media"},
            timeout=60,
        ))
        return r.content
