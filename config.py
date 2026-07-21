"""Central configuration. Everything tunable lives here or in env vars."""
from __future__ import annotations

import os

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
    }
