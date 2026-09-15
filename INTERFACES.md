# Interfaces & shared contracts

This file is the contract between modules. Triage works at the **thread**
(conversation) level. The only inbox mutation anywhere is **mark-as-read**.

## Data shapes (models.py)

### ThreadMessage — one message in a thread
```python
id, sender, sender_email, to, date, body, from_me: bool
```

### EmailThread — a whole conversation
```python
thread_id, subject, messages: list[ThreadMessage],
label_ids: list[str], unread: bool, unsubscribe: str | None
# properties: last_message, participants, message_ids
# transcript(per_msg_chars) -> full-thread text for the classifier
```

### ThreadDecision — classifier output, one per thread
```python
thread_id
category: str            # "action_required" | "useful" | "other"
customer_related: bool
internal_only: bool
needs_response: bool
action_on_me: str | None # concrete action for the user, else None
summary: str
confidence: float
```
Invariant: `action_on_me` set  <=>  `category == "action_required"`.
`CATEGORIES = ("action_required", "useful", "other")`.

## config.py
```python
QUOTA_PROJECT (blank => header omitted), USER_NAME (blank => "account owner"),
USER_EMAIL (auto-detected if blank), INTERNAL_DOMAIN (blank => internal
detection off), BACKEND ("claude_cli" default), MODEL (blank => CLI default),
DATABRICKS_PROFILE, THREAD_BATCH_SIZE, PER_MSG_BODY_CHARS
def get_settings() -> dict
```

## gmail_client.py — thread-centric, read-mostly
```python
class GmailClient:
    get_token() -> str
    get_profile_email() -> str                 # the account's own address
    list_inbox_threads(max_results, query) -> list[str]
    get_thread(thread_id, me=None) -> EmailThread   # all messages, full bodies
    get_threads(ids, progress=None) -> list[EmailThread]  # progress(done,total)
    mark_read(message_ids: list[str]) -> None  # batchModify remove UNREAD
    mark_thread_read(thread) -> None
```
There is deliberately **no** archive / label / trash / delete method. Auth is a
gcloud ADC bearer token + `x-goog-user-project` header; 401/403 -> `AuthError`.

## classifier.py — per-thread Claude evaluation
```python
def classify_thread(thread, settings) -> ThreadDecision   # never raises
def classify_threads(threads, settings, progress=None) -> list[ThreadDecision]
```
Reads the full thread transcript. Determines customer-vs-internal, needs_response,
and the concrete action on the user, then buckets into a category. Default
backend `claude_cli` (Claude Code SDK on the local `claude` CLI); `databricks_fm`
and `anthropic_api` are fallbacks. Any error or unparseable reply -> safe default
(`other`, no action). `progress(done, total)` fires after each thread.

## app.py (Streamlit)
Fetch inbox threads (progress bar) -> classify (progress bar) -> render three
sections: **Action Required** (shows `action_on_me`), **Useful**, **Other**. Each
row shows subject, participants, message count, customer/internal/needs-response
badges, unread dot, summary, and a "Read thread" expander. Per-section and global
select; the single action button is **Mark selected as read**. Nothing is moved,
archived, labelled, or deleted.

---

## Action Board additions (Phase 1)

### Task (models.py)

```python
@dataclass
class Task:
    # required
    id: str                     # uuid4 string; use task_store.new_task()
    title: str                  # short action text
    assignee: str               # roster name, or "unassigned"
    workstream: str             # workstreams list entry, or "unassigned"
    status: str                 # "todo" | "in_progress" | "done"
    source: str                 # "email" | "meet" | "teams" | "manual"
    created_at: datetime        # UTC
    updated_at: datetime        # UTC; bumped on every write
    # optional
    due_date: date | None       # date only (no time)
    source_ref: str | None      # thread_id (email) or file_id (transcript)
    source_link: str | None     # Gmail thread URL or Drive doc URL
    context: str                # short explanation / excerpt  (default "")
    customer_related: bool      # default False
    confidence: float           # 0.0–1.0; 0 for manual tasks
    completed_at: datetime | None  # set when status -> "done"
    # derived (never stored)
    overdue: bool               # status != done AND due_date < today

# serialisation helpers
task.as_dict() -> dict          # ISO strings for all date/datetime fields;
                                # due_date -> "YYYY-MM-DD"; overdue excluded
Task.from_dict(d) -> Task       # inverse of as_dict; parses ISO strings back
```

Constants: `TASK_STATUSES = ("todo", "in_progress", "done")`
           `TASK_SOURCES  = ("email", "meet", "teams", "manual")`

### task_store.py

Store path: `~/.gmail_triage_tasks.json` — flat `{task_id: task_dict}`.

```python
def new_task(*, title, assignee="unassigned", workstream="unassigned",
             status="todo", source="manual", due_date=None,
             source_ref=None, source_link=None, context="",
             customer_related=False, confidence=0.0,
             completed_at=None) -> Task
    # Constructs Task with a fresh UUID + now-timestamps. Does NOT persist.

def add(task: Task) -> None
    # Persist. Overwrites silently if id already exists.

def get(task_id: str) -> Task | None
    # Look up by id. None if not found or corrupt.

def update(task_id: str, **fields) -> Task | None
    # Patch fields; auto-bumps updated_at.
    # If status -> "done" and completed_at is None, sets completed_at = now.
    # Returns updated Task, or None if not found.

def delete(task_id: str) -> bool
    # Remove task. True if existed, False if not.

def list_tasks(*, assignee=None, workstream=None, status=None) -> list[Task]
    # All tasks matching all supplied filters (None = any).
    # Ordered by created_at DESCENDING (newest first).

def find_by_source(source: str, source_ref: str, title: str) -> Task | None
    # Dedup guard. Matches on (source, source_ref, normalised(title)).
    # Normalisation: lowercase + collapse whitespace.
    # Returns matching Task or None.
```

### config.py additions

```python
TEAM_ROSTER: list[dict]   # [{"name": str, "email": str}, ...]
                           # 5-person default; override via CLEANUP_ROSTER_PATH
                           # pointing at a JSON file of the same shape.
                           # Rick/Ghaj/Gustav/Subash have email="" by default.

WORKSTREAMS: list[str]    # default 5 entries; override via CLEANUP_WORKSTREAMS_PATH

MEET_NOTES_FOLDER: str    # default "Meet Recordings"; override CLEANUP_MEET_NOTES_FOLDER
TRANSCRIPT_FOLDER: str    # default "Transcripts"; override CLEANUP_TRANSCRIPT_FOLDER
```

`get_settings()` gains keys: `team_roster`, `workstreams`, `meet_notes_folder`,
`transcript_folder`.
