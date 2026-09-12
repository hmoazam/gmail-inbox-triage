# Plan: Slack Action Extraction (URL-driven)

Status: **approved — ready to build.** Branch: `react-frontend`.

> **Supersedes the earlier categorized-scan design.** We proved the sanctioned
> `dbexec` Slack MCP (1) redacts raw message content behind a privacy filter and
> (2) is too slow/unreliable to auto-scan many conversations. But it *does* allow
> **content-derived answers** via `analysis_prompt`, and returns them on-demand
> (~45s read, ~2 min extraction). So the Slack tab pivots from "auto-scan
> categorized unread" to **"paste a Slack URL → extract the actions I need to
> do."** The old channel-ID category config is retired (kept in git history).

## Summary

Rename the existing email tab to **Gmail Triage** (already done). The **Slack
Triage** tab becomes a URL-driven extractor: the user pastes a Slack channel or
thread URL; the app reads that one conversation through the `dbexec` Slack MCP,
asks the MCP (via `analysis_prompt`) to extract the pending actions/tasks *the
user personally still needs to do*, and shows them as proposed tasks with an
"Add to board" affordance.

## Why this shape (proven)

- The MCP privacy filter blocks raw message dumps but **allows `analysis_prompt`
  answers** — extraction happens server-side; only the structured result comes
  back. No token, no ESI/IT approval; reuses the already-authenticated `dbexec`.
- Verified live on a real chat: extraction returned a clean JSON array of one
  real action in 122.9s. Read-only path returned in ~45s.
- On-demand + one-at-a-time makes the ~1–2 min latency acceptable (spinner);
  auto-scanning many conversations was not.

## Transport (unchanged from the MCP rework, commit 16d16af)

`slack_client.py` already speaks the `dbexec` Slack MCP over stdio
(`dbexec repo run mcp start-single slack`, env `DBEXEC_NO_CERT_REFRESH=1`) via the
`mcp` lib, with a persistent session + lazy reconnect. Auth is handled by dbexec
(silent refresh); failures raise the shared `AuthError` → HTTP 401/403.

## Backend changes

- **`slack_client.py`** — add:
  - `parse_slack_url(url) -> (channel_id, thread_ts | None)` — accept
    `.../archives/C0XXXX` (channel) and `.../archives/C0XXXX/p1712345678123456`
    (thread; `pXXXXXXXXXXYYYYYY` → ts `XXXXXXXXXX.YYYYYY`). Reject non-Slack URLs.
  - `extract_actions(url, user_name) -> list[dict]` — resolve the URL, call
    `conversations.replies` (thread) or `conversations.history` (channel,
    `limit≈40`) with an `analysis_prompt` that asks for the user's PENDING
    actions as a JSON array `[{task, context, due}]`. Strip the
    `[MCP_PRIVACY_SUMMARIZED]` marker and any ```json fence, parse the array,
    coerce to `{task:str, context:str, due:YYYY-MM-DD|null}`. Return `[]` on none.
  - Use a **generous per-call timeout (~180s)** for the extraction call (it's the
    slow one) and surface a clear timeout error. A fresh connection per extract is
    fine (calls are infrequent) if the persistent session proves flaky.
  - Keep `mark_read` etc. only if still referenced; the scan methods
    (`list_unread_dms`, `list_unread_in_channels`) can be removed.
- **`api/routes/slack_triage.py`** — replace the scan routes with:
  - `POST /api/slack-triage/extract` — body `{ url }` → `{ source_link, actions:
    [{ task, context, due }] }`. `AuthError` → 401/403 as today.
  - Remove `GET /api/slack-triage`, `/mark-read`, `/add-to-board` (superseded).
    Adding a task reuses the existing `POST /api/tasks`.
- **`config.py`** — the Slack category map + `CLEANUP_SLACK_CATEGORIES_PATH` are
  no longer used; remove (or leave dormant). Keep the dbexec/MCP settings.
- **`api/schemas.py`** — `SlackExtractRequest { url }`, `SlackExtractResponse
  { source_link, actions: [SlackAction{ task, context, due }] }`.

## Frontend changes

- **`components/SlackTriage/`** — replace the grouped/bucketed view with a simple
  extractor: a URL input + "Extract my actions" button; a loading state that says
  it can take up to ~2 minutes; then a list of proposed actions. Each action is
  editable (title ← task, context, due date, workstream, tags) with **Add to
  board** → `POST /api/tasks` (`source:"slack"`, `source_link` = the URL,
  `assignee` = account owner). Show "✓ Added" per row; surface 401/403 as the
  re-auth banner (dbexec unavailable).
- **`api/client.ts` / `types.ts`** — add `extractSlackActions(url)` +
  `SlackAction` / `SlackExtractResponse`; drop the retired Slack triage types.
- The **Gmail Triage** tab and the board are untouched.

## Verification

- Backend: unit-test `parse_slack_url` (channel + thread + reject) and the
  privacy-marker/JSON parsing (feed a canned `[MCP_PRIVACY_SUMMARIZED] …```json…````
  string). Route test with a mocked `slack_client.extract_actions`.
- Frontend: `npm run build` green.
- Live: one real extraction via the reworked client (the lead will run this).

## Team split

- **backend** — `slack_client.py` (url parse + extract), `api/routes/slack_triage.py`,
  `api/schemas.py`, `config.py` cleanup, tests.
- **frontend** — `components/SlackTriage/` rewrite + client/types.

## Risks / limitations

- **Latency ~1–2 min per extraction** — acceptable for an explicit action with a
  spinner; must not block the UI (disable the button, show progress).
- **MCP reliability** — in-session tool calls timed out, but a dedicated dbexec
  instance (what the app uses) returns; keep the generous timeout + a clear error
  if it doesn't.
- **Redaction** — names/details are scrubbed in the raw path, but the extracted
  *actions* come through fine (proven). Extraction quality depends on the MCP's
  summarizer.
</content>
