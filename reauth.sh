#!/bin/bash
# Re-authenticate Google (and Databricks, if using the databricks_fm backend)
# without restarting the app. Run this in a separate terminal tab if you see
# auth errors mid-session, then click "Fetch & classify" again in the browser.

cd "$(dirname "$0")"

DATABRICKS_PROFILE="${CLEANUP_DATABRICKS_PROFILE:-DEFAULT}"

echo "🔐 Refreshing Google auth (Gmail + Drive)..."
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

# Refresh Databricks auth if you use the databricks_fm backend OR MLflow
# tracking — both authenticate to Databricks and 401 on a stale token.
if [ "$CLEANUP_BACKEND" = "databricks_fm" ] || [ -n "$MLFLOW_EXPERIMENT" ]; then
  echo ""
  echo "🔐 Refreshing Databricks auth (profile: $DATABRICKS_PROFILE)..."
  databricks auth login --profile "$DATABRICKS_PROFILE"
fi

echo ""
echo "✅ Done — go back to the browser and click Fetch & classify."
