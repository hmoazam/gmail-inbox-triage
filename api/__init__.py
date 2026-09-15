"""FastAPI backend for the React task-board migration.

Wraps the existing Python logic layer (task_store, workstream_store, tag_store,
gmail_client, drive_client, classifier) — no business logic is reimplemented
here.  Local-only: binds localhost, serves the built React app in prod.
"""
