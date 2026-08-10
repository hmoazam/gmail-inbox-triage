#!/bin/bash
# Start the Gmail inbox triage app.
# Refreshes Google (Gmail) auth — and Databricks auth if you use the
# databricks_fm backend — before launching Streamlit.

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
    # NOTE: If the token is valid but was issued before the Drive/gmail.send
    # scopes were added, run ./reauth.sh once to grant them.
    gcloud auth application-default login \
      --scopes="https://www.googleapis.com/auth/gmail.modify,\
https://www.googleapis.com/auth/gmail.settings.basic,\
https://www.googleapis.com/auth/gmail.send,\
https://www.googleapis.com/auth/drive.file,\
https://www.googleapis.com/auth/drive.readonly,\
https://www.googleapis.com/auth/cloud-platform,\
https://www.googleapis.com/auth/userinfo.email,\
https://www.googleapis.com/auth/userinfo.profile,\
openid"
  }

# ── 2. Databricks auth ──────────────────────────────────────────────────────
# Needed if you use the databricks_fm backend OR MLflow tracking (both hit
# Databricks with the CLI profile's OAuth token; a stale token => 401 errors).
if [ "$CLEANUP_BACKEND" = "databricks_fm" ] || [ -n "$MLFLOW_EXPERIMENT" ]; then
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
