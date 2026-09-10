# Plan: Slack Triage page (second triage tab)

Status: **approved — ready to build.** Branch: `react-frontend`.

## Summary

Add a second triage view for Slack alongside the existing email one. Rename the
current "Triage" tab to **Gmail Triage**, and add a **Slack Triage** tab with the
same experience: surface **unread direct messages** plus unread messages in
channels grouped under four categories — **AI Gateway Accounts, Team, Rolls
Royce, SME** — classify each conversation with Claude (action/useful/other), and
let the user mark-read and add items to the board.

## Confirmed decisions

- **Slack access:** the app uses its OWN Slack **user OAuth token** (env
  `SLACK_USER_TOKEN`), read at runtime — exactly analogous to how Gmail uses the
  local `gcloud` ADC token. The Slack MCP available in a Claude session is NOT
  used by the app.
- **Categories = configured channel-ID lists** (not live sidebar sections — the
  unofficial section API is unreliable). Stored like the roster/workstream
  config, with a JSON-path override.
- **Direct Messages** is an automatic group (all unread DMs) — no config needed.
- **Classification:** each unread Slack conversation is classified with Claude
  into action_required / useful / other + a one-line summary (reuses the existing
  classifier backend).
- **Actions:** mark-read (Slack `conversations.mark`) + add-to-board, at parity
  with Gmail triage.

## Slack token setup (user does this once)

Create a Slack app at api.slack.com/apps → OAuth & Permissions → add **User Token
Scopes**: `channels:read`, `groups:read`, `im:read`, `mpim:read`,
`channels:history`, `groups:history`, `im:history`, `mpim:history`, `users:read`.
Install to the Databricks workspace, copy the **User OAuth Token** (`xoxp-…`),
and put it in `.env` as `export SLACK_USER_TOKEN="xoxp-…"`. (`conversations.mark`
works with this user token; no extra scope.)

## Category config

`config.py` gains a Slack category map, default baked in below, overridable via
`CLEANUP_SLACK_CATEGORIES_PATH` (JSON `{category: [channel_id, ...]}`). Order is
the display order; "Direct Messages" is implicit and rendered first.

RESOLVED so far (from live discovery):

```json
{
  "AI Gateway Accounts": [],   // TODO: 5 Connect channels (Aon/AXA/Flutter/KPMG/Tesco - AI Gateway) — need IDs
  "Team": [
    "C0ADP69J1P0", "C07KW6R9JR4", "C08CEV945GE", "C0AGPHM9Z29",
    "C0A7AA0PMMM", "C0AH9JR5GKB", "C0ABLUBHJTF"
  ],
  "Rolls Royce": [],           // TODO: awaiting channel list from user
  "SME": [
    "C05AAPK63DK", "C0BHBLZMJV9", "C0B1K1QQNSJ", "C0BN58UJXSA",
    "C08CEFZSDE0", "C07H2H9GV7Y", "C0B3CHJ4GD6", "C09AH9FJES1",
    "C04J6F541KJ", "C0AHMQBKRCJ"
  ]
}
```

Still to resolve into the config: AI Gateway Accounts (5), Rolls Royce (list),
and 3 SME (`ai-governance-office-hours-emea`, `dspy-xfn`,
`emea-ai-governance-epls`) + the "UG L200 demo review" conversation. Empty
categories simply render no channels — safe placeholders.

## Architecture (mirrors the Gmail triage layer)

- **`slack_client.py`** (NEW) — mirror `gmail_client.py`'s structure/safety
  posture. Token from `SLACK_USER_TOKEN`; direct calls to `https://slack.com/api`
  via `requests`. Raise the shared `AuthError` on `not ok` auth errors
  (`invalid_auth`, `token_expired`, `missing_scope`) so routes return 401/403
  with a re-auth message. Methods:
  - `list_unread_dms()` — `users.conversations types=im` (paginated), keep those
    with unread (compare `last_read` vs latest ts via `conversations.info`).
  - `list_unread_in_channels(channel_ids)` — per channel, `conversations.info`
    for `last_read`, then `conversations.history oldest=last_read` for the unread
    messages; return only channels with ≥1 unread.
  - `get_unread_conversation(id)` → a normalized conversation: id, kind
    (im/channel), display name (channel name, or DM user's real name via
    `users.info`), unread messages (author, ts, text), latest ts, permalink
    (`chat.getPermalink`).
  - `mark_read(channel_id, ts)` — `conversations.mark`. The ONLY Slack mutation.
  - Cache `users.info` name lookups; paginate everything.
- **`config.py`** — `SLACK_USER_TOKEN`, the category map + `_load_json`-style
  override, and reuse the existing roster for assignee mapping.
- **`classifier.py`** — add `classify_slack_conversation(conv, settings)` that
  builds a transcript from the unread messages and reuses the existing backend
  plumbing (`_via_cli` / `_via_databricks_fm` / `_via_anthropic`) to return the
  same `ThreadDecision`-shaped result (category/summary/action_on_me/…). Add a
  Slack→Task bridge (assignee = the account owner; source = a new
  `TASK_SOURCE_SLACK = "slack"`; source_link = permalink).
- **`models.py`** — add `TASK_SOURCE_SLACK`.
- **`api/routes/slack_triage.py`** (NEW), prefix `/api/slack-triage`:
  - `GET ""` → `{ groups: [{ category, conversations: [SlackConversationOut] }],
    usage }`. Groups in order: Direct Messages, then the four configured
    categories. Each conversation carries the normalized fields + nested
    `decision` (same shape as the Gmail triage thread's `decision`).
  - `POST "/mark-read"` `{ channel_id, ts }` → `{ ok }`.
  - `POST "/add-to-board"` (conversation id + decision fields, optional
    assignee/workstream/tags) → `{ created, task }`, dedup-guarded on
    `(source="slack", source_ref=channel_id, title)`.
- **`api/schemas.py`** — `SlackConversationOut`, `SlackTriageResponse`, request
  bodies. Reuse the existing `ThreadDecision` schema shape for `decision`.

## Frontend

- **Rename** the "Triage" tab → **Gmail Triage** (label in `App.tsx`; the tab key
  can stay `triage` or become `gmail`).
- **Add** a **Slack Triage** tab + `components/SlackTriage/` — same visual
  language as `components/Triage/`, but grouped by category (Direct Messages +
  the four sections), each section listing conversations in action/useful/other
  buckets with a preview, mark-read, and add-to-board.
- `api/client.ts` + `types.ts` — add the Slack triage methods/types mirroring the
  Gmail ones.
- On 401/403, surface the backend's re-auth message (Slack token expired/missing
  scope), same pattern as Gmail.

## Verification

- Backend: TestClient with a MOCKED `slack_client` (no live Slack) — group
  assembly + ordering, classify path (stub the classifier), mark-read, add-to-board
  dedup, and `AuthError → 401/403`. Confirm imports/ast clean.
- Frontend: `npm run build` green (strict tsc).
- LIVE end-to-end (unread DMs, real categories, mark-read) requires the user's
  `SLACK_USER_TOKEN` and the completed category IDs — done by the user after
  handoff; not verifiable in this environment.

## Team split (clean file boundaries)

- **backend** — `slack_client.py`, `classifier.py` (Slack fn + bridge),
  `models.py` (source const), `config.py` (token + categories),
  `api/routes/slack_triage.py`, `api/schemas.py`, `api/main.py` (register router).
- **frontend** — the `frontend/` tree: tab rename + `components/SlackTriage/` +
  client/types.

## Risks / limitations

- **Token expiry / scopes:** a missing scope or expired token surfaces as the
  re-auth message; the user re-installs / re-pastes the token.
- **Unread semantics:** `last_read` + `conversations.history` is the documented
  path; very active channels may need pagination (handled) — we cap unread pulled
  per conversation to keep classification bounded.
- **Slack Connect (AI Gateway Accounts) channels:** the user token must be a
  member; IDs must be supplied (name-matching can't resolve them reliably).
- **Cost:** classifying every unread conversation costs LLM calls, like Gmail;
  the batch size is bounded.
</content>
