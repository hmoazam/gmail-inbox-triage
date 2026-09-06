"""Transcript ingestion: Drive Meet notes / uploaded transcripts → board tasks.

Composes existing functions (``DriveClient`` + ``classifier.extract_actions_
from_transcript`` + ``task_store``) — no extraction or classification logic is
reimplemented here.  Streamlit only had disabled stubs for these; this is the
first real wiring of them.

Each extracted action is deduped via ``task_store.find_by_source`` keyed on
``(source, source_ref=file_id, title)`` so re-scanning does not create dupes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

import task_store
import workstream_store
from classifier import extract_actions_from_transcript
from api.deps import get_app_settings, get_drive_client, parse_due_date
from api.schemas import IngestResult, MeetIngestRequest, TaskOut, TranscriptIngestRequest

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _roster_names(settings: dict) -> list[str]:
    return [r["name"] for r in settings.get("team_roster", []) if r.get("name")]


def _assignee(raw: str | None) -> str:
    """Map the extractor's ``"unknown"`` sentinel onto the board's ``"unassigned"``."""
    name = (raw or "").strip()
    if not name or name.lower() == "unknown":
        return "unassigned"
    return name


def _ingest_one(
    *, text: str, source: str, source_ref: str, source_link: str | None,
    roster: list[str], workstreams: list[str], settings: dict, dry_run: bool,
    created: list[TaskOut],
) -> int:
    """Extract actions from one transcript and materialise tasks. Returns skipped."""
    skipped = 0
    items = extract_actions_from_transcript(text, roster, workstreams, settings)
    for item in items:
        title = item["action"]
        if task_store.find_by_source(source, source_ref, title) is not None:
            skipped += 1
            continue
        task = task_store.new_task(
            title=title,
            assignee=_assignee(item.get("assignee")),
            workstream=item.get("workstream") or "unassigned",
            source=source,
            source_ref=source_ref,
            source_link=source_link,
            due_date=parse_due_date(item.get("due_date")),
            context=item.get("context", ""),
        )
        if not dry_run:
            task_store.add(task)
        created.append(TaskOut.from_task(task))
    return skipped


@router.post("/meet", response_model=IngestResult)
def ingest_meet(body: MeetIngestRequest) -> IngestResult:
    """Scan Drive for recent Meet auto-saved notes and extract action items."""
    settings = get_app_settings()
    client = get_drive_client()
    roster = _roster_names(settings)
    workstreams = workstream_store.list_workstreams()

    since = None
    if body.since_days and body.since_days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=body.since_days)

    docs = client.list_meet_recording_docs(since=since)  # AuthError → 401/403
    if body.max_docs:
        docs = docs[: body.max_docs]

    created: list[TaskOut] = []
    skipped = scanned = 0
    warnings: list[str] = []
    if not docs:
        warnings.append("No Meet notes matched — check MEET_NOTES_FOLDER or widen since_days.")

    for doc in docs:
        text = client.export_doc_text(doc["id"])
        scanned += 1
        skipped += _ingest_one(
            text=text, source="meet", source_ref=doc["id"], source_link=doc.get("web_link"),
            roster=roster, workstreams=workstreams, settings=settings, dry_run=body.dry_run,
            created=created,
        )

    return IngestResult(created=created, skipped=skipped, scanned=scanned, warnings=warnings)


@router.post("/transcripts", response_model=IngestResult)
def ingest_transcripts(body: TranscriptIngestRequest) -> IngestResult:
    """Scan the managed transcript folders (meet/ + teams/) and extract actions."""
    settings = get_app_settings()
    client = get_drive_client()
    roster = _roster_names(settings)
    workstreams = workstream_store.list_workstreams()

    struct = client.ensure_transcript_folder()  # AuthError → 401/403

    created: list[TaskOut] = []
    skipped = scanned = 0
    warnings: list[str] = []

    for sub in ("meet", "teams"):
        folder_id = struct[sub]["id"]
        files = client.list_folder_files(folder_id)
        for f in files:
            text = client.read_file_text(f["id"], f["mime_type"])
            scanned += 1
            # Source is the subfolder the file was listed from.
            skipped += _ingest_one(
                text=text, source=sub, source_ref=f["id"], source_link=f.get("web_link"),
                roster=roster, workstreams=workstreams, settings=settings, dry_run=body.dry_run,
                created=created,
            )

    if scanned == 0:
        warnings.append("No transcript files found under the meet/ or teams/ folders.")

    return IngestResult(created=created, skipped=skipped, scanned=scanned, warnings=warnings)
