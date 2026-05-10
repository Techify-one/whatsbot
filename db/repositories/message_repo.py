"""Repository for messages table."""

from __future__ import annotations

import time

from sqlalchemy import and_, delete as sa_delete, insert as sa_insert, select, update as sa_update

from db.engine import get_engine
from db.tables import messages


def add(contact_id: int, role: str, content: str, *,
        media_type: str | None = None, media_path: str | None = None,
        status: str | None = None, msg_id: str | None = None,
        ts: float | None = None) -> dict:
    """Insert a message and return it as a dict."""
    ts = ts or time.time()
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(messages).values(
            contact_id=contact_id,
            role=role,
            content=content,
            ts=ts,
            media_type=media_type,
            media_path=media_path,
            status=status,
            msg_id=msg_id,
        ))
        new_id = result.inserted_primary_key[0]
    return {
        "id": new_id,
        "role": role,
        "content": content,
        "ts": ts,
        "media_type": media_type,
        "media_path": media_path,
        "status": status,
        "msg_id": msg_id,
    }


def get_all(contact_id: int) -> list[dict]:
    """Return all messages for a contact ordered by timestamp."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages)
            .where(messages.c.contact_id == contact_id)
            .order_by(messages.c.ts)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def get_context(contact_id: int, limit: int) -> list[dict]:
    """Return the last N eligible messages for LLM context."""
    excluded = ("transcription", "tool_call", "system_notice")
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages)
            .where(
                (messages.c.contact_id == contact_id)
                & (~messages.c.role.in_(excluded))
                & ((messages.c.status.is_(None)) | (messages.c.status != "failed"))
            )
            .order_by(messages.c.ts.desc())
            .limit(limit)
        ).mappings().all()
    return [_row_to_dict(r) for r in reversed(rows)]


def get_last(contact_id: int) -> dict | None:
    """Return the most recent message for a contact."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages)
            .where(messages.c.contact_id == contact_id)
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def get_last_user_message(contact_id: int) -> dict | None:
    """Return the most recent user message (for updating with transcription etc)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages)
            .where((messages.c.contact_id == contact_id) & (messages.c.role == "user"))
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def update_content(message_id: int, content: str) -> None:
    """Update the content of a specific message."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(messages).where(messages.c.id == message_id).values(content=content))


def update_status(contact_id: int, content: str, new_status: str | None,
                   msg_id: str | None = None) -> None:
    """Update status of the most recent message matching content (for retry-send)."""
    with get_engine().begin() as conn:
        target_id = conn.execute(
            select(messages.c.id)
            .where(
                (messages.c.contact_id == contact_id)
                & (messages.c.content == content)
                & (messages.c.status == "failed")
            )
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if target_id is None:
            return
        values = {"status": new_status}
        if msg_id:
            values["msg_id"] = msg_id
        conn.execute(sa_update(messages).where(messages.c.id == target_id).values(**values))


def update_status_by_msg_id(msg_id: str, new_status: str) -> list[str]:
    """Update delivery status by GOWA msg_id. Forward-only: sent → delivered → read.

    Does not overwrite 'operator' or 'failed' statuses. Cascades to all prior
    outgoing messages for the same contact (delivered/read are monotonic).
    """
    updated_msg_ids: list[str] = []
    with get_engine().begin() as conn:
        # Update the specific message.
        result = conn.execute(
            sa_update(messages)
            .where(
                (messages.c.msg_id == msg_id)
                & (messages.c.status.is_not(None))
                & (messages.c.status.in_(("sent", "delivered")))
            )
            .values(status=new_status)
        )
        if (result.rowcount or 0) > 0:
            updated_msg_ids.append(msg_id)

        # Find the anchor row to drive the cascade.
        anchor = conn.execute(
            select(messages.c.contact_id, messages.c.ts).where(messages.c.msg_id == msg_id)
        ).first()
        if anchor:
            prior_statuses = ("sent",) if new_status == "delivered" else ("sent", "delivered")
            # Gather IDs first so we can return them to the caller.
            prior_rows = conn.execute(
                select(messages.c.msg_id)
                .where(
                    (messages.c.contact_id == anchor.contact_id)
                    & (messages.c.role == "assistant")
                    & (messages.c.ts <= anchor.ts)
                    & (messages.c.status.in_(prior_statuses))
                    & (messages.c.msg_id.is_not(None))
                    & (messages.c.msg_id != msg_id)
                )
            ).all()
            cascaded = [r.msg_id for r in prior_rows]
            if cascaded:
                conn.execute(
                    sa_update(messages)
                    .where(
                        (messages.c.contact_id == anchor.contact_id)
                        & (messages.c.role == "assistant")
                        & (messages.c.ts <= anchor.ts)
                        & (messages.c.status.in_(prior_statuses))
                    )
                    .values(status=new_status)
                )
                updated_msg_ids.extend(cascaded)
    return updated_msg_ids


def get_contact_id_by_msg_id(msg_id: str) -> int | None:
    """Look up the contact_id for a given GOWA msg_id."""
    with get_engine().connect() as conn:
        cid = conn.execute(
            select(messages.c.contact_id).where(messages.c.msg_id == msg_id).limit(1)
        ).scalar_one_or_none()
    return cid


def update_msg_id_and_status(message_id: int, msg_id: str, status: str) -> None:
    """Set msg_id and status on a message (used after retry-send)."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(messages).where(messages.c.id == message_id).values(msg_id=msg_id, status=status)
        )


def delete_all(contact_id: int) -> None:
    """Delete all messages for a contact."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(messages).where(messages.c.contact_id == contact_id))


def _row_to_dict(row) -> dict:
    d = {
        "role": row["role"],
        "content": row["content"],
        "ts": row["ts"],
        "status": row["status"],
        "msg_id": row["msg_id"],
    }
    if row["media_type"]:
        d["media_type"] = row["media_type"]
    if row["media_path"]:
        d["media_path"] = row["media_path"]
    d["_id"] = row["id"]
    return d
