# Plan: React + FastAPI Migration (Trello/Jira-style board)

Status: **approved — ready to build.** Execution: **agent team.**

## Summary

The Streamlit action board works but Streamlit can't do a real Trello/Jira feel:
no true drag-and-drop, and DnD can't coexist with inline card controls. This
migration rebuilds the **presentation layer** as a React + TypeScript SPA
(Vite + dnd-kit) served by a **FastAPI** backend. The entire Python business
layer is **reused unchanged** — FastAPI just wraps it.

Branch: **`react-frontend`** (off `feature-action-board`, so all Python logic is
already here as the starting point).

## Confirmed decisions

- **Deploy target:** local-only (no Databricks App). FastAPI shells out to the
  same gcloud ADC token path the clients already use.
- **Scope:** port the Inbox Triage view to React too — one React app, not a
  Streamlit + React split.
- **Stack:** Vite + React + TypeScript + **dnd-kit** (frontend); **FastAPI** +
  Pydantic (backend); `uvicorn` to serve.
- **Board semantics:** columns are the 3 real statuses (**To Do / In Progress /
  Done**); dragging a card sets its status. **Overdue** is a derived visual flag
  on cards (🔴 + red), never a column. Optional "Overdue" filter toggle.
- **NEW — Tags/labels:** tasks gain free-form, colored **tags** (Jira/Trello
  labels), shown as chips on cards and filterable. Backed by a small managed tag
  registry so colors stay consistent.
- **Auth unchanged:** same ADC token + `x-goog-user-project` quota-project
  header. (The quota-project + connector fixes from commit `d7ec7fb` carry over;
  `start.sh` gets rewritten but keeps the `.env`-sourcing and auth blocks.)

## The Python logic layer is reused as-is

Confirmed UI-agnostic and **not rewritten** — FastAPI wraps these directly:

- `task_store.py` — `new_task`, `add`, `get`, `update`, `delete`, `list_tasks`,
  `find_by_source`
- `workstream_store.py` — `list_workstreams`, `add`, `rename`, `delete`
- `models.py` — `Task` (+ `as_dict`/`from_dict`/`overdue`), `ThreadDecision`,
  `EmailThread`
- `gmail_client.py` / `drive_client.py` — `GmailClient`, `DriveClient` classes
- `classifier.py` — `classify_threads`, `extract_actions_from_transcript`,
  `thread_decision_to_task`

The **only** Python-layer edits are the additive tags changes (below).

## Tags/labels — the one logic-layer addition

- **`models.py`** — add to `Task`: `tags: list[str] = field(default_factory=list)`.
  `as_dict` (via `asdict`) already serialises lists; `from_dict` picks up the
  default for existing stored tasks with no `tags` key (backward compatible — no
  migration needed).
- **`task_store.py`** — `new_task(..., tags=None)` (default `[]`); add an
  optional `tags: list[str] | None` filter to `list_tasks` (match tasks
  containing **all** requested tags).
- **`tag_store.py`** (new) — mirrors `workstream_store.py`: persistent registry
  at `~/.gmail_triage_tags.json` as `{name: color}` (hex). `list_tags()`,
  `add(name, color=None)`, `rename(old, new)`, `set_color(name, color)`,
  `delete(name)`. Auto-assigns a color from a fixed palette when none given.
  Free-form tags typed on a card auto-register here so their color is stable.

## Architecture (local-only, two-tier)

```
gmail-inbox-cleanup/
├── (existing .py modules — REUSED as the logic layer)
├── tag_store.py          # NEW — colored tag registry (mirrors workstream_store)
├── api/
│   ├── main.py           # FastAPI app; CORS for Vite dev; serves built React in prod
│   ├── schemas.py        # Pydantic models mirroring Task / ThreadDecision / tag / workstream
│   └── routes/
│       ├── tasks.py        # CRUD over task_store (+ status PATCH for DnD, tag filter)
│       ├── workstreams.py  # CRUD over workstream_store (+ rename cascade / delete reassign)
│       ├── tags.py         # CRUD over tag_store
│       ├── triage.py       # fetch+classify threads, mark-read, add-to-board bridge
│       ├── ingest.py       # Meet/Teams scan + extract
│       └── drafts.py       # create_draft (draft-first send)
├── frontend/             # Vite + React + TypeScript
│   ├── package.json, vite.config.ts, tsconfig.json, index.html
│   └── src/
│       ├── api/client.ts       # typed fetch wrapper (mirrors schemas.py)
│       ├── types.ts            # TS types matching Pydantic schemas
│       ├── App.tsx             # shell: By-Person / By-Workstream toggle, filters, tab nav
│       ├── components/Board/       # Board, Column, Card, dnd-kit wiring, TagChip
│       ├── components/Triage/      # 3-bucket triage view + "Add to board"
│       ├── components/Workstreams/ # manage modal
│       └── components/Tags/        # tag manager (rename/recolor/delete) + tag picker
└── start.sh              # rewritten: auth → uvicorn (+ Vite dev proxy, or serve built dist)
```

## What maps to what

| Streamlit today | React / FastAPI |
|---|---|
| `app.py` board renderer | React `Board` + dnd-kit; **drag between columns → `PATCH /tasks/{id}` status** |
| rich cards (status/due/delete/send) | React `Card` — DnD **and** inline controls coexist |
| (no tags) | `TagChip` row on cards + tag picker; **new** |
| By-Person / By-Workstream toggle | client-side view state, same task data |
| workstream manager | React modal → `/workstreams` routes |
| triage tab | React `Triage` view → `/triage` routes |
| `st.session_state` | React state; server (JSON stores) is source of truth |

## API contract (defined first, both sides code against it)

- `GET/POST /api/tasks`, `GET/PATCH/DELETE /api/tasks/{id}` — PATCH carries
  status (DnD), due_date, assignee, workstream, tags, title, context.
- `GET/POST /api/workstreams`, `PATCH/DELETE /api/workstreams/{name}` (rename
  cascades onto tasks; delete reassigns tasks to `unassigned`).
- `GET/POST /api/tags`, `PATCH/DELETE /api/tags/{name}`.
- `GET /api/triage?max_results=&query=` (fetch+classify), `POST /api/triage/mark-read`,
  `POST /api/triage/add-to-board`.
- `POST /api/ingest/meet`, `POST /api/ingest/transcripts`.
- `POST /api/drafts` (draft-first send).
- Errors: `AuthError` from the clients → HTTP 401/403 with the same re-auth
  message so the UI can surface it.

## Build sequence (phased, reviewable)

1. **Contract + tags plumbing** — write `api/schemas.py` + this endpoint spec;
   add tags to `models.py` / `task_store.py`; add `tag_store.py`. Verify Python
   with a quick import/ast check.
2. **Backend API** — FastAPI app + all routes over the existing modules; verify
   with curl (no UI yet).
3. **Frontend scaffold** — Vite/React/TS, typed `client.ts` + `types.ts`, app
   shell + view toggle + tab nav.
4. **Board + DnD + tags** — Board/Column/Card, dnd-kit status-on-drop, inline
   card actions, tag chips + picker, filters, overdue styling.
5. **Workstreams + Tags management** — modals wired to routes.
6. **Triage view + integration** — 3-bucket view, fetch/classify, mark-read,
   add-to-board; rewrite `start.sh`; end-to-end run + build.

## Agent-team execution

Clean layer boundaries so no two teammates edit the same file:

- **backend** — owns `api/` (main, schemas, all routes) **and** the tags
  plumbing (`models.py`, `task_store.py`, `tag_store.py`). Delivers a
  curl-verifiable API. Publishes the final schema shapes for the frontend.
- **frontend** — owns the entire `frontend/` tree (scaffold, client, board +
  dnd-kit + tags, workstream/tag managers, triage view). Codes against the
  contract in this doc + backend's published schemas.
- **integration** — owns `start.sh` rewrite + end-to-end verification (curl the
  API, build the frontend, launch, smoke-test each view). Keeps the contract
  honest between backend and frontend; does not edit their files.

Sequencing: backend defines/locks schemas first; frontend builds against the
contract in parallel using mocked responses where needed; integration verifies
once both land. Contract-first + plan approval before any teammate edits code
(the approach that caught the bridge bug on the original board).

## Risks / limitations

- **Two-tier now means two processes** (uvicorn + Vite in dev). `start.sh`
  handles both; prod serves the built `frontend/dist` from FastAPI (one process).
- **No auth on the local API** — it binds localhost only; fine for a local
  single-user tool, but the API must not be exposed beyond localhost.
- **The Streamlit `app.py` stays on `feature-action-board`** as the working
  fallback; it is removed from this branch only once the React app reaches parity.
- **Tags are free-form** — typos create near-duplicate labels; the tag manager
  (rename/merge) is the mitigation.
</content>
