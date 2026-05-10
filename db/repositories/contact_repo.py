"""Repository for contacts table."""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import contact_tags, contacts, observations, tags, unread_msg_ids


def _br_phone_variants(phone: str) -> list[str]:
    """Return phone number variants for Brazilian numbers.

    BR mobile numbers can have 8 or 9 local digits:
    - 13 digits: 55 + 2-digit DDD + 9 + 8 digits (user-typed format)
    - 12 digits: 55 + 2-digit DDD + 8 digits (WhatsApp canonical format)
    """
    if not phone or not phone.startswith("55"):
        return [phone]
    if len(phone) == 13 and phone[4] == "9":
        alt = phone[:4] + phone[5:]
        return [phone, alt]
    if len(phone) == 12:
        alt = phone[:4] + "9" + phone[4:]
        return [phone, alt]
    return [phone]


def get_or_create(phone: str, default_ai_enabled: bool = True) -> dict:
    """Get a contact by phone, creating it if it doesn't exist."""
    variants = _br_phone_variants(phone)
    with get_engine().begin() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
        if row is not None:
            return _row_to_dict(row)
        now = time.time()
        result = conn.execute(sa_insert(contacts).values(
            phone=phone,
            ai_enabled=1 if default_ai_enabled else 0,
            created_at=now,
            updated_at=now,
        ))
        new_id = result.inserted_primary_key[0]
    return {
        "id": new_id,
        "phone": phone,
        "name": "",
        "email": "",
        "profession": "",
        "company": "",
        "address": "",
        "ai_enabled": default_ai_enabled,
        "is_group": False,
        "group_name": "",
        "is_archived": False,
        "archived_by_app": False,
        "can_send": True,
        "unread_count": 0,
        "unread_ai_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def delete(contact_id: int) -> None:
    """Delete a contact and all related data (CASCADE handles child tables)."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(contacts).where(contacts.c.id == contact_id))


def set_archived(contact_id: int, archived: bool, by_app: bool = False) -> None:
    """Set the archived status of a contact."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            is_archived=1 if archived else 0,
            archived_by_app=1 if (archived and by_app) else 0,
            updated_at=time.time(),
        ))


def get_by_phone(phone: str) -> dict | None:
    """Get a contact by phone number. Checks BR phone variants."""
    variants = _br_phone_variants(phone)
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
    return _row_to_dict(row) if row else None


def update(contact_id: int, **fields) -> None:
    """Update specific fields on a contact."""
    if not fields:
        return
    fields["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(**fields))


def increment_unread(contact_id: int, msg_id: str | None = None) -> None:
    """Increment unread_count and optionally track the msg_id."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=contacts.c.unread_count + 1,
            updated_at=time.time(),
        ))
        if msg_id:
            conn.execute(sa_insert(unread_msg_ids).values(
                contact_id=contact_id, msg_id=msg_id,
            ))


def increment_unread_ai(contact_id: int) -> None:
    """Increment unread_ai_count."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_ai_count=contacts.c.unread_ai_count + 1,
            updated_at=time.time(),
        ))


def mark_as_read(contact_id: int) -> list[str]:
    """Reset unread counts and return the unread msg_ids (for read receipts)."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(unread_msg_ids.c.msg_id).where(unread_msg_ids.c.contact_id == contact_id)
        ).all()
        msg_ids = [r.msg_id for r in rows]
        conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.contact_id == contact_id))
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=0,
            unread_ai_count=0,
            updated_at=time.time(),
        ))
    return msg_ids


def mark_user_messages_as_read(contact_id: int) -> list[str]:
    """Reset only unread_count (user messages) and return msg_ids for read receipts."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(unread_msg_ids.c.msg_id).where(unread_msg_ids.c.contact_id == contact_id)
        ).all()
        msg_ids = [r.msg_id for r in rows]
        conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.contact_id == contact_id))
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=0,
            updated_at=time.time(),
        ))
    return msg_ids


def get_observations(contact_id: int) -> list[str]:
    """Return all observations for a contact."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(observations.c.text)
            .where(observations.c.contact_id == contact_id)
            .order_by(observations.c.created_at)
        ).all()
    return [r.text for r in rows]


def set_observations(contact_id: int, observations_list: list[str]) -> None:
    """Replace all observations for a contact."""
    now = time.time()
    cleaned = [t for t in observations_list if t.strip()]
    with get_engine().begin() as conn:
        conn.execute(sa_delete(observations).where(observations.c.contact_id == contact_id))
        if cleaned:
            conn.execute(sa_insert(observations), [
                {"contact_id": contact_id, "text": t, "created_at": now} for t in cleaned
            ])


def add_observation(contact_id: int, text: str) -> None:
    """Append a single observation if it doesn't already exist."""
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(observations.c.id).where(
                (observations.c.contact_id == contact_id) & (observations.c.text == text)
            )
        ).first()
        if existing:
            return
        conn.execute(sa_insert(observations).values(
            contact_id=contact_id, text=text, created_at=time.time()
        ))


def list_contacts(q: str = "", archived: bool = False) -> list[dict]:
    """List contacts with last message preview, tags, and unread counts."""
    from sqlalchemy import text as sql_text

    # Single SQL statement — easier to read than building it via Core.
    # Only standard SQL (MAX, GROUP BY, INNER JOIN, LEFT JOIN, COALESCE),
    # works in both SQLite and Postgres unchanged.
    sql = sql_text("""
        SELECT c.*,
               lm.content   AS last_msg_content,
               lm.role      AS last_msg_role,
               lm.ts        AS last_msg_ts,
               lm.media_type AS last_msg_media_type,
               lm.status    AS last_msg_status,
               lm.msg_id    AS last_msg_id,
               (SELECT COUNT(*) FROM messages WHERE contact_id = c.id) AS msg_count
        FROM contacts c
        LEFT JOIN (
            SELECT m1.contact_id, m1.content, m1.role, m1.ts, m1.media_type, m1.status, m1.msg_id
            FROM messages m1
            INNER JOIN (
                SELECT contact_id, MAX(ts) AS max_ts
                FROM messages
                WHERE role NOT IN ('transcription', 'system_notice')
                GROUP BY contact_id
            ) m2 ON m1.contact_id = m2.contact_id AND m1.ts = m2.max_ts
        ) lm ON lm.contact_id = c.id
        WHERE c.is_archived = :archived
        ORDER BY COALESCE(lm.ts, c.updated_at) DESC
    """)

    with get_engine().connect() as conn:
        rows = conn.execute(sql, {"archived": 1 if archived else 0}).mappings().all()

        results = []
        for row in rows:
            contact_id = row["id"]
            tag_rows = conn.execute(
                select(tags.c.name)
                .join(contact_tags, contact_tags.c.tag_id == tags.c.id)
                .where(contact_tags.c.contact_id == contact_id)
            ).all()
            tags_list = [t.name for t in tag_rows]

            last_content = ""
            lmt = row["last_msg_media_type"]
            if row["last_msg_content"] is not None:
                if lmt == "image":
                    last_content = (row["last_msg_content"] or "")[:80] or "\U0001f4f7 Imagem"
                elif lmt == "audio":
                    last_content = "\U0001f3a4 Áudio"
                else:
                    last_content = (row["last_msg_content"] or "")[:80]

            is_group = bool(row["is_group"])
            group_name = row["group_name"] or ""
            name = group_name if is_group else (row["name"] or "")

            results.append({
                "id": contact_id,
                "phone": row["phone"],
                "name": name,
                "last_message": last_content,
                "last_message_role": row["last_msg_role"] or "",
                "last_message_ts": row["last_msg_ts"] or 0,
                "last_message_status": row["last_msg_status"] or "",
                "last_message_msg_id": row["last_msg_id"] or "",
                "msg_count": row["msg_count"] or 0,
                "unread_count": row["unread_count"],
                "unread_ai_count": row["unread_ai_count"],
                "ai_enabled": bool(row["ai_enabled"]),
                "is_group": is_group,
                "group_name": group_name,
                "is_archived": bool(row["is_archived"]),
                "archived_by_app": bool(row["archived_by_app"]) if row["archived_by_app"] is not None else False,
                "can_send": bool(row["can_send"]) if row["can_send"] is not None else True,
                "tags": tags_list,
                "updated_at": row["updated_at"],
            })

    if q:
        ql = q.lower()
        results = [
            c for c in results
            if ql in c["name"].lower()
            or ql in c["phone"]
            or ql in c.get("group_name", "").lower()
            or any(ql in t.lower() for t in c.get("tags", []))
        ]

    return results


def get_full_contact(phone: str) -> dict | None:
    """Get full contact data for API response (contact + info + observations)."""
    variants = _br_phone_variants(phone)
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
        if row is None:
            return None
        contact_id = row["id"]
        obs_rows = conn.execute(
            select(observations.c.text)
            .where(observations.c.contact_id == contact_id)
            .order_by(observations.c.created_at)
        ).all()
        tag_rows = conn.execute(
            select(tags.c.name)
            .join(contact_tags, contact_tags.c.tag_id == tags.c.id)
            .where(contact_tags.c.contact_id == contact_id)
        ).all()

    data = _row_to_dict(row)
    data["info"] = {
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "observations": [r.text for r in obs_rows],
    }
    data["tags"] = [t.name for t in tag_rows]
    return data


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy mapping row to a plain dict with Python types."""
    return {
        "id": row["id"],
        "phone": row["phone"],
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "ai_enabled": bool(row["ai_enabled"]),
        "is_group": bool(row["is_group"]),
        "group_name": row["group_name"],
        "is_archived": bool(row["is_archived"]),
        "archived_by_app": bool(row["archived_by_app"]) if row["archived_by_app"] is not None else False,
        "can_send": bool(row["can_send"]) if row["can_send"] is not None else True,
        "unread_count": row["unread_count"],
        "unread_ai_count": row["unread_ai_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
