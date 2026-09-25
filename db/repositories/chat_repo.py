"""Persistence for the built-in Chat and plugin development projects."""

from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import delete, func, insert, select, update

from db.engine import get_engine
from db.tables import chat_conversations, chat_messages, chat_projects


def _dict(row):
    return dict(row) if row else None


def list_projects(*, include_deleted: bool = False) -> list[dict]:
    stmt = select(chat_projects)
    if not include_deleted:
        stmt = stmt.where(chat_projects.c.deleted_at.is_(None))
    stmt = stmt.order_by(chat_projects.c.sort_order, chat_projects.c.created_at)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def get_project(project_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(chat_projects).where(chat_projects.c.id == project_id)).mappings().first()
    return _dict(row)


def get_project_by_plugin(plugin_id: str, *, include_deleted: bool = False) -> dict | None:
    stmt = select(chat_projects).where(chat_projects.c.plugin_id == plugin_id)
    if not include_deleted:
        stmt = stmt.where(chat_projects.c.deleted_at.is_(None))
    with get_engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _dict(row)


def ensure_system_project() -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(chat_projects).where(
                chat_projects.c.kind == "system", chat_projects.c.deleted_at.is_(None)
            )
        ).mappings().first()
    if row:
        project = dict(row)
        # Installs created before the rename still carry the old product name.
        if project["name"] == "WhatsBot":
            update_project(project["id"], name="WhatsBot-Lite")
            project["name"] = "WhatsBot-Lite"
        return project
    return create_project("WhatsBot-Lite", "system", None, "")


def create_project(name: str, kind: str, plugin_id: str | None, workspace_path: str) -> dict:
    now = time.time()
    project_id = uuid.uuid4().hex
    with get_engine().begin() as conn:
        last_order = conn.execute(select(func.max(chat_projects.c.sort_order))).scalar()
        values = {
            "id": project_id, "kind": kind, "plugin_id": plugin_id,
            "name": name, "workspace_path": workspace_path,
            "sort_order": int(last_order or 0) + 1, "deleted_at": None,
            "created_at": now, "updated_at": now,
        }
        conn.execute(insert(chat_projects).values(**values))
    return values


def update_project(project_id: str, **values) -> None:
    values["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(update(chat_projects).where(chat_projects.c.id == project_id).values(**values))


def reorder_projects(project_ids: list[str]) -> None:
    now = time.time()
    with get_engine().begin() as conn:
        for order, project_id in enumerate(project_ids, 1):
            conn.execute(
                update(chat_projects)
                .where(chat_projects.c.id == project_id, chat_projects.c.deleted_at.is_(None))
                .values(sort_order=order, updated_at=now)
            )


def soft_delete_project(project_id: str) -> None:
    update_project(project_id, deleted_at=time.time())


def list_conversations(project_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(chat_conversations)
            .where(chat_conversations.c.project_id == project_id)
            .order_by(chat_conversations.c.updated_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def create_conversation(project_id: str, model: str, reasoning: str = "") -> dict:
    now = time.time()
    conversation_id = uuid.uuid4().hex
    values = {
        "id": conversation_id, "project_id": project_id, "title": "Nova conversa",
        "model": model, "reasoning": reasoning, "summary": "",
        "compacted_through_id": None, "created_at": now, "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(insert(chat_conversations).values(**values))
    return values


def get_conversation(conversation_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(chat_conversations).where(chat_conversations.c.id == conversation_id)
        ).mappings().first()
    return _dict(row)


def update_conversation(conversation_id: str, **values) -> None:
    values["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(
            update(chat_conversations).where(chat_conversations.c.id == conversation_id).values(**values)
        )


def delete_conversation(conversation_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(chat_conversations).where(chat_conversations.c.id == conversation_id))


def add_message(conversation_id: str, role: str, content: str, *, kind: str = "message", metadata=None) -> dict:
    now = time.time()
    values = {
        "conversation_id": conversation_id, "role": role, "content": content,
        "kind": kind, "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
        "created_at": now,
    }
    with get_engine().begin() as conn:
        result = conn.execute(insert(chat_messages).values(**values))
        message_id = result.inserted_primary_key[0]
        conn.execute(
            update(chat_conversations).where(chat_conversations.c.id == conversation_id).values(updated_at=now)
        )
    return {"id": message_id, **values, "metadata": metadata or {}}


def update_message(message_id: int, *, content: str, metadata=None) -> dict | None:
    encoded_metadata = json.dumps(metadata or {}, ensure_ascii=False, default=str)
    with get_engine().begin() as conn:
        conn.execute(
            update(chat_messages)
            .where(chat_messages.c.id == message_id)
            .values(content=content, metadata=encoded_metadata)
        )
        row = conn.execute(
            select(chat_messages).where(chat_messages.c.id == message_id)
        ).mappings().first()
    item = _dict(row)
    if item:
        item["metadata"] = metadata or {}
    return item


def list_messages(conversation_id: str, *, after_id: int | None = None) -> list[dict]:
    stmt = select(chat_messages).where(chat_messages.c.conversation_id == conversation_id)
    if after_id is not None:
        stmt = stmt.where(chat_messages.c.id > after_id)
    stmt = stmt.order_by(chat_messages.c.id)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["metadata"] = {}
        out.append(item)
    return out
