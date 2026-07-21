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
