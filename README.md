# 📧 Gmail Inbox Triage

A **local** tool that reads your Gmail inbox as **conversations**, has **Claude**
read each full thread, and sorts your inbox into three buckets. The **only**
action you can take from the UI is **mark as read** — nothing is ever moved,
archived, labelled, or deleted. Everything runs on your machine; your email is
sent only to whichever Claude backend you configure.

## What it does

For each inbox **thread**, Claude reads every message (full bodies, whole
conversation) and — from *your* point of view — decides:

- **Is there a concrete action on me?** If so, it tells you *what* (e.g. "Reply to
  Alice approving the Q3 budget").
- **Does it need a response?** (based on who sent the last message and what it asked)
- **Is it external/customer-related or internal-only?**

…then files it into one of three sections, shown top to bottom:

| Section | Meaning |
|---|---|
| 🚨 **Action Required** | A concrete action on you — shows the action text |
| 💡 **Useful** | No action, but worth being aware of (updates, FYI, actioned threads) |
| 🗂️ **Other** | Everything else — noise, bulk mail |

A fast rule-based pre-pass (`quick_triage.py`) buckets obvious noise (calendar
invites, no-reply senders, newsletters) *without* a Claude call; only the
remaining threads go to Claude.

## The only action is mark-as-read

By design, `gmail_client.py` has **no** archive / label / trash / delete method.
The single mutation is removing the `UNREAD` label via `batchModify`. The tool
*cannot* move or delete your mail even if asked to.

## Prerequisites

1. **Python 3.11+**
2. **Google Cloud SDK** (`gcloud`) — used for Gmail auth via Application Default
   Credentials. [Install it here](https://cloud.google.com/sdk/docs/install).
3. **A Claude backend** — pick one:
   - **`claude_cli`** (default): the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)
     installed and logged in (`claude` on your PATH). No API key needed.
   - **`anthropic_api`**: an [Anthropic API key](https://console.anthropic.com/).
   - **`databricks_fm`**: a Databricks workspace with a Claude serving endpoint
     and the `databricks` CLI configured.

## Install

```bash
git clone https://github.com/hmoazam/gmail-inbox-triage.git
cd gmail-inbox-triage
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> The three backends have separate dependencies (`claude-agent-sdk`, `anthropic`,
> `databricks-sdk`+`openai`). `requirements.txt` installs all of them for
> convenience; you can trim it to just the backend you use.

## Customize

Copy the example env file, edit it, and source it before running:

```bash
cp .env.example .env
# edit .env — at minimum set CLEANUP_USER_NAME and CLEANUP_INTERNAL_DOMAIN
source .env
```

Every setting is an environment variable with a safe default (see
[Configuration](#configuration) below). The two worth setting for good results
are **`CLEANUP_USER_NAME`** (so Claude knows whose actions to detect) and
**`CLEANUP_INTERNAL_DOMAIN`** (your company domain, for internal-vs-external).

To add your own fast-path rules (senders/subjects that should skip Claude and go
straight to a bucket), edit the `_RULES` table in `quick_triage.py` — there's a
commented example for internal system notifications.

## Authenticate

The first run needs Google auth. `start.sh` does this for you, or run it manually:

```bash
gcloud auth application-default login \
  --scopes="https://www.googleapis.com/auth/gmail.modify,\
https://www.googleapis.com/auth/gmail.settings.basic,\
https://www.googleapis.com/auth/userinfo.email,\
https://www.googleapis.com/auth/userinfo.profile,openid"
```

`gmail.modify` is required for mark-as-read; the userinfo scopes let the app
auto-detect your email. (If you use the `databricks_fm` backend or MLflow
tracking, you'll also be prompted to `databricks auth login`.)

## Run

The easy way — `start.sh` checks/refreshes auth, then launches Streamlit:

```bash
./start.sh
```

Or manually:

```bash
source .venv/bin/activate
source .env          # if you created one
streamlit run app.py
```

Then in the browser:
1. Set the Gmail filter in the sidebar (default `in:inbox is:unread`).
2. Click **🔄 Fetch & classify** — progress bars show fetching, then per-thread
   classification (the slow part; Claude reads each thread in full).
3. Review the three sections. Expand **Read thread** to see the full conversation.
4. Select threads and click **✅ Mark selected as read**.

If you hit auth errors mid-session, run `./reauth.sh` in another terminal and
click Fetch & classify again — no need to restart the app.

## Configuration

All optional — sensible defaults are baked in. Set via env vars (see `.env.example`).

| Var | Default | Meaning |
|---|---|---|
| `CLEANUP_USER_NAME` | _(blank)_ | Your name — whose actions to detect. **Recommended.** |
| `CLEANUP_USER_EMAIL` | _(auto)_ | Auto-detected from your Gmail profile if blank |
| `CLEANUP_INTERNAL_DOMAIN` | _(blank)_ | Your company domain, for internal-vs-external. **Recommended.** |
| `CLEANUP_BACKEND` | `claude_cli` | `claude_cli` \| `anthropic_api` \| `databricks_fm` |
| `CLEANUP_MODEL` | _(blank)_ | Blank = CLI default model; else a model/endpoint id |
| `ANTHROPIC_API_KEY` | _(none)_ | Required only for `anthropic_api` |
| `CLEANUP_THREAD_BATCH_SIZE` | `30` | Threads per run |
| `CLEANUP_PER_MSG_BODY_CHARS` | `6000` | Per-message body cap in the transcript |
| `GMAIL_QUOTA_PROJECT` | _(none)_ | GCP project for Gmail API quota; set only if you hit a quota error |
| `CLEANUP_DATABRICKS_PROFILE` | `DEFAULT` | Databricks CLI profile — for `databricks_fm` and MLflow |
| `MLFLOW_EXPERIMENT` | _(blank)_ | Databricks MLflow experiment path; blank = tracking off |

## Classification caching

Every thread matching your Gmail filter is fetched and shown **every run** —
nothing is hidden. What's cached is the *classification*: `decision_cache.py`
stores each thread's last decision keyed by its message ids at
`~/.gmail_triage_cache.json`. If a thread's messages are unchanged, its cached
decision is reused instead of calling Claude again; as soon as a thread gets a
new reply, its fingerprint changes and it's re-classified. Clear the cache from
the sidebar to force a full re-classification.

## Optional: MLflow run tracking

If you set `MLFLOW_EXPERIMENT` to a Databricks experiment path, each run logs
token usage and per-thread decisions as an MLflow trace. It's entirely
best-effort — an auth failure never crashes a run, and leaving `MLFLOW_EXPERIMENT`
unset disables it completely.

## Project layout

| File | Role |
|---|---|
| `app.py` | Streamlit UI — 3 sections, progress bars, mark-read only |
| `classifier.py` | Per-thread Claude evaluation (external/internal, action-on-you) |
| `quick_triage.py` | Fast rule-based pre-pass; skips Claude for obvious noise |
| `gmail_client.py` | Gmail REST — threads + mark-as-read (no move/delete) |
| `decision_cache.py` | Caches decisions keyed by thread message-ids |
| `mlflow_tracking.py` | Optional MLflow trace logging |
| `models.py` | `ThreadMessage`, `EmailThread`, `ThreadDecision` |
| `config.py` | Env-driven settings |
| `start.sh` / `reauth.sh` | Auth refresh + launch helpers |
| `INTERFACES.md` | Module contract |
| `BUILD_WITH_AGENT_TEAM.md` | Prompt to rebuild this with a team of agents |

## License

MIT — see [LICENSE](LICENSE).
