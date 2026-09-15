"""Workstream CRUD over ``workstream_store``.

Rename cascades onto tasks (tasks whose ``workstream == old`` become ``new``);
delete reassigns affected tasks to ``"unassigned"``.  This mirrors the cascade
app.py performed after each store mutation.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status as http_status

import task_store
import workstream_store
from api.schemas import WorkstreamCreate, WorkstreamMutation, WorkstreamRename

router = APIRouter(prefix="/api/workstreams", tags=["workstreams"])


@router.get("", response_model=list[str])
def list_workstreams() -> list[str]:
    return workstream_store.list_workstreams()


@router.post("", response_model=WorkstreamMutation, status_code=http_status.HTTP_201_CREATED)
def create_workstream(body: WorkstreamCreate) -> WorkstreamMutation:
    if not workstream_store.add(body.name):
        raise HTTPException(status_code=409, detail="workstream is blank, reserved, or already exists")
    return WorkstreamMutation(ok=True, workstreams=workstream_store.list_workstreams())


@router.patch("/{name}", response_model=WorkstreamMutation)
def rename_workstream(name: str, body: WorkstreamRename) -> WorkstreamMutation:
    if not workstream_store.rename(name, body.new_name):
        raise HTTPException(status_code=409, detail="rename failed — name blank, reserved, missing, or a duplicate")
    # Cascade the rename onto existing tasks.
    updated = 0
    for t in task_store.list_tasks(workstream=name):
        task_store.update(t.id, workstream=body.new_name.strip())
        updated += 1
    return WorkstreamMutation(
        ok=True, workstreams=workstream_store.list_workstreams(), tasks_updated=updated
    )


@router.delete("/{name}", response_model=WorkstreamMutation)
def delete_workstream(name: str) -> WorkstreamMutation:
    if not workstream_store.delete(name):
        raise HTTPException(status_code=404, detail="workstream not found")
    # Reassign affected tasks so none are orphaned.
    updated = 0
    for t in task_store.list_tasks(workstream=name):
        task_store.update(t.id, workstream="unassigned")
        updated += 1
    return WorkstreamMutation(
        ok=True, workstreams=workstream_store.list_workstreams(), tasks_updated=updated
    )
