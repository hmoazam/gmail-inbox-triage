"""Task CRUD over ``task_store``.

The status PATCH is what drag-and-drop calls.  Free-form tags supplied on
create/patch auto-register in ``tag_store`` so their color stays stable.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status as http_status

import tag_store
import task_store
from models import TASK_STATUSES
from api.deps import parse_due_date
from api.schemas import TaskCreate, TaskOut, TaskPatch

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _register_tags(tags: list[str] | None) -> None:
    """Auto-register free-form tags so each has a stable color in the registry."""
    for name in tags or []:
        tag_store.add(name)  # no-op if blank or already present


@router.get("", response_model=list[TaskOut])
def list_tasks(
    assignee: str | None = None,
    workstream: str | None = None,
    status: str | None = None,
    tags: list[str] = Query(default=[]),
) -> list[TaskOut]:
    """List tasks (newest first). ``tags`` matches tasks containing ALL given tags."""
    tasks = task_store.list_tasks(
        assignee=assignee,
        workstream=workstream,
        status=status,
        tags=tags or None,
    )
    return [TaskOut.from_task(t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=http_status.HTTP_201_CREATED)
def create_task(body: TaskCreate) -> TaskOut:
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    if body.status not in TASK_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status {body.status!r}")
    _register_tags(body.tags)
    task = task_store.new_task(
        title=body.title.strip(),
        assignee=body.assignee,
        workstream=body.workstream,
        status=body.status,
        source=body.source,
        due_date=parse_due_date(body.due_date),
        source_ref=body.source_ref,
        source_link=body.source_link,
        context=body.context,
        customer_related=body.customer_related,
        confidence=body.confidence,
        tags=body.tags,
    )
    task_store.add(task)
    return TaskOut.from_task(task)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: str) -> TaskOut:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskOut.from_task(task)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: str, body: TaskPatch) -> TaskOut:
    fields = body.model_dump(exclude_unset=True)

    if "status" in fields and fields["status"] not in TASK_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status {fields['status']!r}")
    # due_date arrives as a string (or explicit null to clear it).
    if "due_date" in fields:
        fields["due_date"] = parse_due_date(fields["due_date"])
    if "tags" in fields:
        _register_tags(fields["tags"])

    task = task_store.update(task_id, **fields)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskOut.from_task(task)


@router.delete("/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str) -> Response:
    if not task_store.delete(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
