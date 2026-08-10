#!/bin/bash
# Start the Gmail inbox triage app.
# Refreshes Google (Gmail) auth — and Databricks auth if you use the
# databricks_fm backend — before launching Streamlit.
# Tip: run `source .env` first to load your personal settings.

set -e
cd "$(dirname "$0")"

# Databricks CLI profile used only when CLEANUP_BACKEND=databricks_fm.
DATABRICKS_PROFILE="${CLEANUP_DATABRICKS_PROFILE:-DEFAULT}"

# ── 1. Google / Gmail auth ──────────────────────────────────────────────────
echo "🔐 Checking Google auth (Gmail)..."
gcloud auth application-default print-access-token &>/dev/null \
  && echo "  ✓ Google auth is valid" \
  || {
    echo "  Token missing or expired — starting login flow..."
    gcloud auth application-default login \
      --scopes="https://www.googleapis.com/auth/gmail.modify,\
https://www.googleapis.com/auth/gmail.settings.basic,\
https://www.googleapis.com/auth/cloud-platform,\
https://www.googleapis.com/auth/userinfo.email,\
https://www.googleapis.com/auth/userinfo.profile,\
openid"
  }

# The Gmail API requires a quota project on the ADC. A fresh login resets it to
# null, so (re)attach it every time. Skipped if GMAIL_QUOTA_PROJECT is unset.
if [ -n "$GMAIL_QUOTA_PROJECT" ]; then
  echo "  Setting ADC quota project: $GMAIL_QUOTA_PROJECT"
  gcloud auth application-default set-quota-project "$GMAIL_QUOTA_PROJECT" &>/dev/null \
    && echo "  ✓ Quota project set" \
    || echo "  ⚠ Could not set quota project — Gmail API may return 403"
else
  echo "  ⚠ GMAIL_QUOTA_PROJECT not set — Gmail API may return 403 (did you 'source .env'?)"
fi

# ── 2. Databricks auth (only needed for the databricks_fm backend) ──────────
if [ "$CLEANUP_BACKEND" = "databricks_fm" ]; then
  echo "🔐 Checking Databricks auth (profile: $DATABRICKS_PROFILE)..."
  databricks auth token --profile "$DATABRICKS_PROFILE" &>/dev/null \
    && echo "  ✓ Databricks token is valid" \
    || {
      echo "  Token missing or expired — starting login flow..."
      databricks auth login --profile "$DATABRICKS_PROFILE"
    }
fi

# ── 3. Activate venv and launch ─────────────────────────────────────────────
echo ""
echo "🚀 Starting Gmail Inbox Triage..."
source .venv/bin/activate
exec streamlit run app.py
