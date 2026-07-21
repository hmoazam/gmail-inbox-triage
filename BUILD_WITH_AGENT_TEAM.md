# Agent-team build prompt

This is the prompt you asked for: paste it into a fresh Claude Code session to
have a **team of agents** build this tool from scratch (or rebuild it in another
language / stack). It follows the agent-teams template at
`~/claude-config/agent-teams/agent-teams-prompt-template.md`.

> The working reference implementation already lives in this directory. This
> prompt is written so a team could reproduce it independently. If you just want
> to *run* the existing tool, see `README.md` — you don't need a team for that.

---

```text
CONTEXT
We're building a LOCAL, manually-triggered Gmail inbox cleanup tool (a small
Streamlit app) that uses Claude to triage inbox emails and propose cleanup
actions, which the user approves before anything is applied. This runs on the
user's Mac, not on Databricks. Key facts a fresh session won't know:

- Gmail access uses the gcloud Application Default Credentials auth path: a bearer
  token from `gcloud auth application-default print-access-token`. If your GCP
  project requires it, Gmail REST calls also send the header
  `x-goog-user-project: <your-gcp-project-id>` (set via GMAIL_QUOTA_PROJECT).
  Gmail REST recipes: list messages, get full message, /modify for labels,
  batchModify. Gmail API base: https://gmail.googleapis.com/gmail/v1/users/me
- Claude backend is pluggable (see config.py BACKEND). Default is the local
  `claude` CLI via the Claude Agent SDK. Alternatives: the Anthropic API
  (ANTHROPIC_API_KEY), or Databricks Foundation Model serving via the
  databricks-sdk — `WorkspaceClient(profile=<profile>).serving_endpoints
  .get_open_ai_client()` returns an OpenAI-compatible client; call
  `.chat.completions.create(model=<serving-endpoint-name>, ...)`.
- Design principle #1: PROPOSE-THEN-APPROVE. Nothing mutates the inbox until the
  user clicks Apply in the UI. "keep" is always a no-op. Be conservative: when in
  doubt, keep. Never trash anything personal, financial, legal, or account-security.
- Actions supported: keep / archive (remove INBOX label) / label (create+apply,
  then archive) / trash (recoverable). Plus flagging unsubscribe candidates from
  the List-Unsubscribe header.
- Target: Python 3.11+. Stack: Streamlit + requests + databricks-sdk + openai.

GOAL
A runnable local Streamlit app: fetch a batch of inbox emails, have Claude
propose one cleanup action per email with a rationale, let the user review /
edit / deselect, then apply only the approved actions via the Gmail API.

TEAM
Spawn 3 teammates (Sonnet is fine for all three). Name them so I can reference
them later. They must agree on a shared contract FIRST (data shapes +
function signatures in an INTERFACES.md), then each owns a DIFFERENT file so
they never edit the same one:
- gmail-client: owns gmail_client.py + models.py. Gmail REST wrapper — auth
  (gcloud ADC token + quota header), list_inbox, get_email (decode MIME body,
  parse List-Unsubscribe), list/ensure labels, archive, trash, apply_label,
  apply_decision. Raise a clear AuthError on 401/403.
- classifier: owns classifier.py + config.py. Databricks FM serving call
  (default backend), a strict-JSON triage system prompt, robust JSON extraction,
  and coercion that DEFAULTS EVERY UNCERTAIN OR MISSING EMAIL TO "keep". Include
  optional claude_cli (Claude Agent SDK) and anthropic_api fallback backends.
- ui: owns app.py + README.md. Streamlit review UI: fetch & classify button,
  one card per email (editable action dropdown, label field, rationale,
  confidence, unsubscribe link, body preview), select/deselect, and an "Apply
  approved actions" button that calls gmail_client per approved row.

HOW TO WORK
Have the three teammates first co-author INTERFACES.md and agree on the
EmailSummary and Decision data shapes + each module's function signatures BEFORE
writing implementation. Then implement in parallel. gmail-client and classifier
should each expose a tiny offline self-test (pure functions: JSON parsing, MIME
decode, unsubscribe parsing) that runs without network or credentials. ui waits
until the other two expose their contracts, then integrates. Require plan
approval before any teammate writes code.

APPROVAL CRITERIA (for plan approval)
Approve a plan only if it: (1) keeps propose-then-approve — no inbox mutation
before explicit user approval; (2) defaults to "keep" on any classification
uncertainty or parse failure; (3) sends the x-goog-user-project header when a
quota project is configured; (4) uses the local `claude` CLI (Claude Agent SDK)
as the default Claude backend, with anthropic_api and databricks_fm as options;
(5) includes offline self-tests for the pure parsing logic. Reject plans that
auto-apply actions, that permanently delete (DELETE endpoint) instead of trash,
or that hardcode an Anthropic API key.

DELIVERABLE
A working project directory with: config.py, models.py, gmail_client.py,
classifier.py, app.py, requirements.txt, INTERFACES.md, and README.md with setup
(gcloud auth, databricks profile, pip install) and run instructions
(`streamlit run app.py`). Confirm all modules byte-compile and the offline
self-tests pass before declaring done.
```

---

## Notes on why the shipped version was built directly (not by a team)

The user was logging off and wanted unattended progress. Agent-team teammates'
permission prompts bubble up to the lead and can stall a headless run, so the
working implementation in this directory was built in a single session for
reliability. This prompt is provided as the requested deliverable and is the
recommended path when you're present to steer the team (approve plans, redirect,
synthesize).
