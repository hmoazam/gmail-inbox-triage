#!/bin/bash
# Re-authenticate Google (and Databricks, if using the databricks_fm backend)
# without restarting the app. Run this in a separate terminal tab if you see
# auth errors mid-session, then click "Fetch & classify" again in the browser.
# Tip: run `source .env` first to load your personal settings.

cd "$(dirname "$0")"

DATABRICKS_PROFILE="${CLEANUP_DATABRICKS_PROFILE:-DEFAULT}"

echo "🔐 Refreshing Google auth (Gmail)..."
gcloud auth application-default login \
  --scopes="https://www.googleapis.com/auth/gmail.modify,\
https://www.googleapis.com/auth/gmail.settings.basic,\
https://www.googleapis.com/auth/cloud-platform,\
https://www.googleapis.com/auth/userinfo.email,\
https://www.googleapis.com/auth/userinfo.profile,\
openid"

# The Gmail API requires a quota project on the ADC. The login above resets it
# to null, so (re)attach it. Skipped if GMAIL_QUOTA_PROJECT is unset.
if [ -n "$GMAIL_QUOTA_PROJECT" ]; then
  echo "🔧 Setting ADC quota project: $GMAIL_QUOTA_PROJECT"
  gcloud auth application-default set-quota-project "$GMAIL_QUOTA_PROJECT" &>/dev/null \
    && echo "  ✓ Quota project set" \
    || echo "  ⚠ Could not set quota project — Gmail API may return 403"
else
  echo "⚠ GMAIL_QUOTA_PROJECT not set — Gmail API may return 403 (did you 'source .env'?)"
fi

# Refresh Databricks auth only if you use the databricks_fm backend.
if [ "$CLEANUP_BACKEND" = "databricks_fm" ]; then
  echo ""
  echo "🔐 Refreshing Databricks auth (profile: $DATABRICKS_PROFILE)..."
  databricks auth login --profile "$DATABRICKS_PROFILE"
fi

echo ""
echo "✅ Done — go back to the browser and click Fetch & classify."
