"""Repository for tags and contact_tags tables."""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import contact_tags, tags
from db.upsert import upsert_ignore


def get_all() -> dict[str, dict]:
    """Return all tags as {name: {color: ...}} dict (matching old TagRegistry format)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(tags.c.name, tags.c.color).order_by(tags.c.name)
        ).all()
    return {r.name: {"color": r.color} for r in rows}


def get_by_name(name: str) -> dict | None:
    """Get a tag by name. Returns {id, name, color} or None."""
    with get_engine().connect() as conn:
        row = conn.execute(select(tags).where(tags.c.name == name)).mappings().first()
    return dict(row) if row else None


def create(name: str, color: str) -> bool:
    """Create a tag. Returns False if name already exists."""
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(tags.c.id).where(tags.c.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            return False
        conn.execute(sa_insert(tags).values(name=name, color=color))
    return True


def update(old_name: str, *, new_name: str | None = None, color: str | None = None) -> bool:
    """Update a tag's name and/or color. Returns False if not found."""
    with get_engine().begin() as conn:
        tag_id = conn.execute(
            select(tags.c.id).where(tags.c.name == old_name)
        ).scalar_one_or_none()
        if tag_id is None:
            return False
        if color:
            conn.execute(sa_update(tags).where(tags.c.name == old_name).values(color=color))
        if new_name and new_name != old_name:
            conn.execute(sa_update(tags).where(tags.c.name == old_name).values(name=new_name))
    return True


def delete(name: str) -> bool:
    """Delete a tag and remove it from all contacts. Returns False if not found."""
    with get_engine().begin() as conn:
        tag_id = conn.execute(
            select(tags.c.id).where(tags.c.name == name)
        ).scalar_one_or_none()
        if tag_id is None:
            return False
        conn.execute(sa_delete(contact_tags).where(contact_tags.c.tag_id == tag_id))
        conn.execute(sa_delete(tags).where(tags.c.id == tag_id))
    return True


def get_contact_tags(contact_id: int) -> list[str]:
    """Return tag names for a contact."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(tags.c.name)
            .join(contact_tags, contact_tags.c.tag_id == tags.c.id)
            .where(contact_tags.c.contact_id == contact_id)
            .order_by(tags.c.name)
        ).all()
    return [r.name for r in rows]


def set_contact_tags(contact_id: int, tag_names: list[str]) -> None:
    """Replace all tags for a contact with the given list."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(contact_tags).where(contact_tags.c.contact_id == contact_id))
        for name in tag_names:
            tag_id = conn.execute(
                select(tags.c.id).where(tags.c.name == name)
            ).scalar_one_or_none()
            if tag_id is not None:
                conn.execute(upsert_ignore(
                    contact_tags,
                    {"contact_id": contact_id, "tag_id": tag_id},
                    conflict_cols=["contact_id", "tag_id"],
                ))


def add_contact_tag(contact_id: int, tag_name: str) -> None:
    """Add a single tag to a contact."""
    with get_engine().begin() as conn:
        tag_id = conn.execute(
            select(tags.c.id).where(tags.c.name == tag_name)
        ).scalar_one_or_none()
        if tag_id is not None:
            conn.execute(upsert_ignore(
                contact_tags,
                {"contact_id": contact_id, "tag_id": tag_id},
                conflict_cols=["contact_id", "tag_id"],
            ))


def remove_contact_tag(contact_id: int, tag_name: str) -> None:
    """Remove a single tag from a contact."""
    with get_engine().begin() as conn:
        tag_id = conn.execute(
            select(tags.c.id).where(tags.c.name == tag_name)
        ).scalar_one_or_none()
        if tag_id is not None:
            conn.execute(sa_delete(contact_tags).where(
                (contact_tags.c.contact_id == contact_id)
                & (contact_tags.c.tag_id == tag_id)
            ))
