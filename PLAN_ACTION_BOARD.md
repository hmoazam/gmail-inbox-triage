# Plan: Multi-source Action Board

Status: **approved — ready to build (Phase 0/1).**

## Summary

Today the app is stateless: fetch inbox → classify → mark read, nothing
persisted. This turns it into a **persistent, board-style task tracker**
(Trello/Jira-inspired) that pulls action items from **email, Google Meet notes,
and uploaded transcripts (Teams + non-owned Meet calls)**, tracks them per person
and per workstream, and keeps the existing triage view as a second tab.

## Confirmed decisions

- **Meet source (owned calls):** read the Google Docs Meet auto-saves to Drive.
- **Meet source (non-owned calls):** uploaded manually into the shared transcript
  folder, under a `meet/` subfolder (Teams transcripts go under `teams/`). The
  subfolder determines the source tag — no per-file tagging needed.
- **Triage view:** the new board becomes home; the current 3-bucket triage view
  stays as a second tab. Action-required emails flow into the board.
- **Task storage:** local JSON (`~/.gmail_triage_tasks.json`), mirroring the
  existing `decision_cache.py` pattern.
- **Teammate tasks:** track + optional send.
- **Card status UX:** dropdown/button status change first (no new deps).
  Drag-and-drop (`streamlit-sortables`) is a possible later enhancement.
- **Drive scope:** narrower `drive.file` + `drive.readonly` combo (not full
  `drive`).
- **Send behaviour:** draft-first — Send creates a Gmail draft for manual review.

## Two board views (top-level toggle)

Same task data, pivoted two ways via a `group_by` parameter on one shared
renderer. Both views use the same Kanban columns (**Overdue · To Do · In
Progress · Done**) and the same cards; only the swimlane grouping and which field
renders as a tag flips.

### View A — By Person
Owner is the grouping (Hanna = primary full-width board; Rick, Ghaj, Gustav,
Subash = collapsible expanders with count badges). **Workstream shows as a tag**
on each card.

### View B — By Workstream
Workstream is the grouping (collapsible per workstream, incl. an `unassigned`
lane). **Owner shows as a tag** on each card.

Overdue is a **derived** column (status ≠ done and past due date) — the in-app
"reminder". Cards show: title/action, source icon (📧 email / 🎥 meet /
🧑‍💻 teams / ✍️ manual), due date (red if overdue), customer badge, workstream
or owner tag, and a link back to the source (Gmail thread / Drive doc). Card
actions: change status, edit due date, delete, and (teammates) Send.

## Files

- **`models.py`** — new `Task` dataclass: `id, title, assignee, workstream,
  status (todo|in_progress|done), due_date, source (email|meet|teams|manual),
  source_ref, source_link, context, customer_related, confidence, created_at,
  updated_at, completed_at`. Overdue is derived, not stored.
- **`task_store.py`** (new) — JSON store at `~/.gmail_triage_tasks.json`; CRUD +
  dedup keys (email: `thread_id`+action; transcript: `file_id`+normalized title).
- **`drive_client.py`** (new) — reuses the ADC-token pattern from `GmailClient`:
  `list_meet_recording_docs(since)`, `export_doc_text(file_id)`,
  `ensure_transcript_folder()` (creates the parent + `meet/` and `teams/`
  subfolders if missing), `list_folder_files(folder_id)`, `read_file_text(...)`.
  Source is inferred from which subfolder a file sits in.
- **`classifier.py`** — add `extract_actions_from_transcript(text, roster,
  workstreams, settings)` (reuses existing backend plumbing), returning a JSON
  array of actions each with roster-matched assignee, suggested workstream, action
  text, optional due date, and context. Add an email `ThreadDecision` → `Task`
  bridge (assignee = Hanna).
- **`app.py`** — tabs: **📋 Board** (home; view toggle, ingestion buttons, manual
  add, filters) and **📧 Inbox Triage** (existing 3-bucket view + "Add to
  board"). Board renderer parameterized by `group_by`.
- **`config.py`** — team roster (names + emails for the 5 people), workstreams
  list (both with add-new escape hatch), Meet folder name, transcript folder name.
- **`gmail_client.py`** — add `create_draft(...)` (and optionally `send_message`)
  for the Send action.
- **`start.sh` / `reauth.sh`** — add the new OAuth scopes (Drive; `gmail.send`).

## OAuth scope changes (requires re-login)

Current: Gmail (`gmail.modify`, `gmail.settings.basic`) + `cloud-platform` +
userinfo + openid. This feature adds:
- **`drive.file`** — create the transcript folder/subfolders and read files the
  app creates.
- **`drive.readonly`** — read Meet Docs and manually-uploaded transcripts the app
  did not create.
- **`gmail.send`** — only for the optional teammate send (`gmail.modify` does not
  permit sending).

## Safety-model note

The app's safety story today is *"the only mutation is mark-as-read; nothing is
sent, moved, or deleted."* The optional **Send** feature is the first outbound
action and breaks that invariant. **Decided: draft-first** — Send creates a Gmail
draft for manual review; the app never sends mail directly.

## Build sequence

- **Phase 0** — add scopes; factor the shared ADC-token helper out of
  `GmailClient` so `drive_client` reuses it.
- **Phase 1** — `Task` model (incl. `workstream`) + `task_store` + board UI (both
  views via `group_by`) + manual add + email→task bridge + overdue logic +
  filters. *Delivers a working board with manual + email tasks.*
- **Phase 2** — `drive_client` + owned-call Meet-notes ingestion + transcript
  action extraction.
- **Phase 3** — transcript folder + `meet/`/`teams/` subfolder creation + manual
  upload ingestion, reusing the extractor; source inferred from subfolder.
- **Phase 4** — optional Send (draft-first).

## Risks / limitations

- **Reminders are in-app only** (overdue column/badges). Push-when-closed would
  need a scheduler; possible later stretch: a cron "daily overdue digest" email.
- **Meet notes only exist** if transcription/notes were enabled for the meeting;
  non-owned calls rely on manual upload.
- **Extraction assignee/workstream accuracy** — ambiguous items land in
  `unknown`/`unassigned` for manual correction rather than being guessed.
- **Dedup on re-scan** handled by source keys; edited/re-run transcripts may need
  an "already imported" per-file marker.
