"""Central configuration. Everything tunable lives here or in env vars."""
from __future__ import annotations

import json
import os
from pathlib import Path

# Slack user OAuth token (xoxp-…), read at runtime — the app's own token, exactly
# analogous to how Gmail uses the local gcloud ADC token. Set via SLACK_USER_TOKEN.
# Needs user scopes: channels:read, groups:read, im:read, mpim:read,
# channels:history, groups:history, im:history, mpim:history, users:read.
SLACK_USER_TOKEN = os.environ.get("SLACK_USER_TOKEN", "")

# Google Cloud project used for API quota/billing on every Gmail API call
# (the gcloud Application Default Credentials auth path attributes usage to it).
# Set this to your own GCP project id via GMAIL_QUOTA_PROJECT.
QUOTA_PROJECT = os.environ.get("GMAIL_QUOTA_PROJECT", "")

# --- who "me" is (used to decide action-on-me and internal-vs-customer) ------
# Your display name, as it appears in emails. Set via CLEANUP_USER_NAME.
USER_NAME = os.environ.get("CLEANUP_USER_NAME", "")
# If unset, gmail_client fills this from the Gmail profile at runtime.
USER_EMAIL = os.environ.get("CLEANUP_USER_EMAIL", "")
# Emails where every human participant is on this domain are "internal only".
# Set to your company domain via CLEANUP_INTERNAL_DOMAIN (e.g. "example.com").
INTERNAL_DOMAIN = os.environ.get("CLEANUP_INTERNAL_DOMAIN", "")

# Backend for classification:
#   "claude_cli"    -> Claude Agent SDK (Claude Code SDK) driving the local,
#                      already-authenticated `claude` CLI. (default)
#   "databricks_fm" -> Databricks Foundation Model serving endpoint.
#   "anthropic_api" -> direct Anthropic API; requires ANTHROPIC_API_KEY.
BACKEND = os.environ.get("CLEANUP_BACKEND", "claude_cli")

# Model id.
#  - claude_cli: leave blank to use the CLI's default model; or a Claude model id.
#  - databricks_fm: the serving endpoint name.
#  - anthropic_api: a Claude model id.
MODEL = os.environ.get("CLEANUP_MODEL", "")  # blank => CLI default for claude_cli

# Databricks CLI profile used for FM serving auth (only for databricks_fm).
DATABRICKS_PROFILE = os.environ.get("CLEANUP_DATABRICKS_PROFILE", "DEFAULT")

# How many inbox threads to pull per run.
THREAD_BATCH_SIZE = int(os.environ.get("CLEANUP_THREAD_BATCH_SIZE", "30"))

# Per-message body cap in the transcript sent to Claude (full body, but bounded
# so a giant thread can't blow the context).
PER_MSG_BODY_CHARS = int(os.environ.get("CLEANUP_PER_MSG_BODY_CHARS", "6000"))


# ---------------------------------------------------------------------------
# Team roster and workstreams (Action Board)
# ---------------------------------------------------------------------------

def _load_json_list(env_var: str, default: list) -> list:
    """Load a JSON list from a file path in ``env_var``, or return ``default``.

    The escape hatch for operators who need a custom roster or workstream list:
    set the env var to an absolute path of a UTF-8 JSON file and restart.
    Silently falls back to the default on any error.
    """
    path_str = os.environ.get(env_var, "")
    if not path_str:
        return default
    try:
        return json.loads(Path(path_str).expanduser().read_text())
    except Exception:
        return default


# Default 5-person roster.
# Hanna's email is read from CLEANUP_USER_EMAIL at runtime so it stays in sync
# with the Gmail profile.  The other four are intentionally left blank — their
# real addresses are not known at config-time; populate them via a JSON file
# pointed to by CLEANUP_ROSTER_PATH (same schema as below).
_DEFAULT_ROSTER: list[dict] = [
    {"name": "Hanna",   "email": os.environ.get("CLEANUP_USER_EMAIL", "")},
    {"name": "Rick",    "email": ""},   # fill via CLEANUP_ROSTER_PATH
    {"name": "Ghaj",    "email": ""},   # fill via CLEANUP_ROSTER_PATH
    {"name": "Gustav",  "email": ""},   # fill via CLEANUP_ROSTER_PATH
    {"name": "Subash",  "email": ""},   # fill via CLEANUP_ROSTER_PATH
]

# Override entirely by pointing CLEANUP_ROSTER_PATH at a JSON file shaped as:
# [{"name": "Alice", "email": "alice@example.com"}, ...]
TEAM_ROSTER: list[dict] = _load_json_list("CLEANUP_ROSTER_PATH", _DEFAULT_ROSTER)

# Default workstreams.  Override by pointing CLEANUP_WORKSTREAMS_PATH at a
# JSON file shaped as: ["Workstream A", "Workstream B", ...]
_DEFAULT_WORKSTREAMS: list[str] = [
    "Customer Engagements",
    "Internal Projects",
    "Admin",
    "Hiring",
    "Other",
]
WORKSTREAMS: list[str] = _load_json_list(
    "CLEANUP_WORKSTREAMS_PATH", _DEFAULT_WORKSTREAMS
)


# ---------------------------------------------------------------------------
# Slack triage categories (channel-ID lists per category)
# ---------------------------------------------------------------------------

def _load_json_dict(env_var: str, default: dict) -> dict:
    """Load a JSON object (``{category: [channel_id, ...]}``) from a file path in
    ``env_var``, or return ``default``.

    Same escape-hatch pattern as ``_load_json_list``: set the env var to an
    absolute path of a UTF-8 JSON file and restart. Silently falls back to the
    default on any error (missing file, bad JSON, wrong root type).
    """
    path_str = os.environ.get(env_var, "")
    if not path_str:
        return default
    try:
        loaded = json.loads(Path(path_str).expanduser().read_text())
        return loaded if isinstance(loaded, dict) else default
    except Exception:
        return default


# Default Slack category map: {category: [channel_id, ...]}. Dict insertion order
# IS the display order the API returns groups in ("Direct Messages" is implicit and
# rendered first by the route). Inline comments map channel id → channel name so
# this list is maintainable. Override entirely by pointing
# CLEANUP_SLACK_CATEGORIES_PATH at a JSON file with the same shape.
_DEFAULT_SLACK_CATEGORIES: dict[str, list[str]] = {
    # TODO: 5 Slack Connect channels (Aon / AXA / Flutter / KPMG / Tesco -
    # AI Gateway). Awaiting channel IDs from the user — empty renders no channels.
    "AI Gateway Accounts": [],
    "Team": [
        "C0ADP69J1P0", "C07KW6R9JR4", "C08CEV945GE", "C0AGPHM9Z29",
        "C0A7AA0PMMM", "C0AH9JR5GKB", "C0ABLUBHJTF",
    ],
    "Rolls Royce": [
        "C04RQJ27SN9",  # team-rolls-royce
        "C07KUHKR78B",  # team-rolls-royce-extended
        "C097UPNJ3CK",  # team-rolls-royce-mro
        "C08U97CAPEH",  # team-rolls-royce-one
        "C08KTN6KWKG",  # team-rolls-royce-sap
        "C096U18PVHU",  # team-rolls-royce-airr
        "C0AJZK3GE3C",  # team-rolls-royce-bz-serverless
        "C0AHR51BKEV",  # team-rolls-royce-abac-mvp
        "C0AESU50C04",  # team-rolls-royce-abac-mvp-2
        "C08JNNM6NV6",  # rolls-royce-core
        "C0B2K137DK9",  # rolls-royce-fe-core
        "C0AAG897K24",  # rolls-royce-ai-strategy-day
        "C0B0Q1GPFCJ",  # rollsroyce-interop
        "C099TJA76N7",  # rollsroyce-obo-cross-workspace
        "C0BG957JFDH",  # azure-natgw-rolls-royce
        "C0AFS7SEKFA",  # esc687-rolls-royce
        "C0BF0TLDJ5Q",  # rr-pbi-takeout
        "C0BPBLSDSKT",  # genie-ppai-security-rolls-royce
    ],
    "SME": [
        "C05AAPK63DK", "C0BHBLZMJV9", "C0B1K1QQNSJ", "C0BN58UJXSA",
        "C08CEFZSDE0", "C07H2H9GV7Y", "C0B3CHJ4GD6", "C09AH9FJES1",
        "C04J6F541KJ", "C0AHMQBKRCJ",
        "C0BPTP65AHJ",  # emea-ai-governance-epls
        "C0BRLBK6L83",  # ai-governance-office-hours-emea
    ],
}
SLACK_CATEGORIES: dict[str, list[str]] = _load_json_dict(
    "CLEANUP_SLACK_CATEGORIES_PATH", _DEFAULT_SLACK_CATEGORIES
)

# ---------------------------------------------------------------------------
# Drive folder names (Phase 2/3 — Meet notes + transcript ingestion)
# ---------------------------------------------------------------------------

# Name of the Google Drive folder where Google Meet auto-saved notes land.
MEET_NOTES_FOLDER: str = os.environ.get(
    "CLEANUP_MEET_NOTES_FOLDER", "Meet Recordings"
)

# Parent folder for manually-uploaded transcripts.  Subfolders ``meet/`` and
# ``teams/`` are created under this; the subfolder determines the source tag.
TRANSCRIPT_FOLDER: str = os.environ.get(
    "CLEANUP_TRANSCRIPT_FOLDER", "Transcripts"
)


def get_settings() -> dict:
    """Resolve config into a plain dict the other modules read."""
    return {
        "quota_project": QUOTA_PROJECT,
        "user_name": USER_NAME,
        "user_email": USER_EMAIL,
        "internal_domain": INTERNAL_DOMAIN,
        "backend": BACKEND,
        "model": MODEL,
        "databricks_profile": DATABRICKS_PROFILE,
        "thread_batch_size": THREAD_BATCH_SIZE,
        "per_msg_body_chars": PER_MSG_BODY_CHARS,
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY"),
        # Slack triage
        "slack_user_token": SLACK_USER_TOKEN,
        "slack_categories": SLACK_CATEGORIES,
        # Action Board
        "team_roster": TEAM_ROSTER,
        "workstreams": WORKSTREAMS,
        "meet_notes_folder": MEET_NOTES_FOLDER,
        "transcript_folder": TRANSCRIPT_FOLDER,
    }
