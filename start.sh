#!/bin/bash
# Start the Gmail inbox triage / action-board app (React + FastAPI).
# Refreshes Google (Gmail) auth — and Databricks auth if you use the
# databricks_fm backend — then launches the FastAPI backend (uvicorn).
#
# Usage:
#   ./start.sh          build the React frontend (if needed) and serve everything
#                       from FastAPI at http://localhost:8000  (single process)
#   ./start.sh --build  force a fresh frontend build, then serve as above
#   ./start.sh --dev    live-reload dev: uvicorn on :8000 + Vite on :5173
#                       (open http://localhost:5173; /api is proxied to :8000)

set -e
cd "$(dirname "$0")"

MODE="serve"
case "${1:-}" in
  --dev)   MODE="dev" ;;
  --build) MODE="build" ;;
  "")      MODE="serve" ;;
  *) echo "Unknown option: $1 (use --dev or --build)"; exit 2 ;;
esac

# ── 0. Load .env ────────────────────────────────────────────────────────────
# The Python code reads config only from os.environ (no dotenv), and .env uses
# `export` lines — so we must source it here or GMAIL_QUOTA_PROJECT and friends
# never reach the app. Without the quota project, Gmail API returns 403.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

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

# ── 3. Activate venv; ensure backend deps ───────────────────────────────────
source .venv/bin/activate
if ! python -c "import uvicorn, fastapi" 2>/dev/null; then
  echo "📦 Installing backend deps (fastapi/uvicorn)..."
  pip install -q -r requirements.txt
fi

UVICORN="python -m uvicorn api.main:app --host 127.0.0.1 --port 8000"

# ── 4. Frontend build (serve/build modes) ────────────────────────────────────
build_frontend() {
  if ! command -v npm &>/dev/null; then
    echo "❌ npm not found — install Node.js to build the React frontend." >&2
    exit 1
  fi
  echo "📦 Building React frontend..."
  ( cd frontend && npm install && npm run build )
}

if [ "$MODE" = "build" ]; then
  build_frontend
elif [ "$MODE" = "serve" ] && [ ! -f frontend/dist/index.html ]; then
  echo "  (no build found — building once)"
  build_frontend
fi

# ── 5. Launch ────────────────────────────────────────────────────────────────
echo ""
if [ "$MODE" = "dev" ]; then
  if ! command -v npm &>/dev/null; then
    echo "❌ npm not found — install Node.js for --dev mode." >&2
    exit 1
  fi
  echo "🚀 Dev mode: uvicorn :8000 + Vite :5173 — open http://localhost:5173"
  # Start the backend in the background; stop it when this script exits.
  $UVICORN &
  API_PID=$!
  trap 'kill $API_PID 2>/dev/null' EXIT INT TERM
  ( cd frontend && npm run dev )
else
  echo "🚀 Serving at http://localhost:8000"
  exec $UVICORN
fi
