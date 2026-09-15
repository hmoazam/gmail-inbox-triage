"""Tag CRUD over ``tag_store``.

Rename cascades onto tasks (the old tag on any task is replaced with the new
name); delete strips the tag off every task that carries it — the tag analog of
the workstream cascade, keeping the board consistent with the registry.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status as http_status

import tag_store
import task_store
from api.schemas import TagCreate, TagMutation, TagOut, TagPatch

router = APIRouter(prefix="/api/tags", tags=["tags"])


def _tag_list() -> list[TagOut]:
    return [TagOut(name=n, color=c) for n, c in tag_store.list_tags().items()]


@router.get("", response_model=list[TagOut])
def list_tags() -> list[TagOut]:
    return _tag_list()


@router.post("", response_model=TagMutation, status_code=http_status.HTTP_201_CREATED)
def create_tag(body: TagCreate) -> TagMutation:
    if not tag_store.add(body.name, body.color):
        raise HTTPException(status_code=409, detail="tag is blank or already exists")
    return TagMutation(ok=True, tags=_tag_list())


@router.patch("/{name}", response_model=TagMutation)
def patch_tag(name: str, body: TagPatch) -> TagMutation:
    if body.new_name is None and body.color is None:
        raise HTTPException(status_code=422, detail="provide new_name and/or color")

    updated = 0
    # Recolor first (independent of rename).
    if body.color is not None and not tag_store.set_color(name, body.color):
        raise HTTPException(status_code=404, detail="tag not found")

    # Rename + cascade onto tasks.
    if body.new_name is not None and body.new_name.strip() != name:
        new = body.new_name.strip()
        if not tag_store.rename(name, new):
            raise HTTPException(status_code=409, detail="rename failed — name blank, missing, or a duplicate")
        for t in task_store.list_tasks(tags=[name]):
            new_tags = [new if tag == name else tag for tag in t.tags]
            task_store.update(t.id, tags=new_tags)
            updated += 1

    return TagMutation(ok=True, tags=_tag_list(), tasks_updated=updated)


@router.delete("/{name}", response_model=TagMutation)
def delete_tag(name: str) -> TagMutation:
    if not tag_store.delete(name):
        raise HTTPException(status_code=404, detail="tag not found")
    # Strip the tag off any task that carries it.
    updated = 0
    for t in task_store.list_tasks(tags=[name]):
        task_store.update(t.id, tags=[tag for tag in t.tags if tag != name])
        updated += 1
    return TagMutation(ok=True, tags=_tag_list(), tasks_updated=updated)
