"""Repository for ``ai_variables`` (global values referenceable by prompts).

Each row is a named value (``{name}`` in a prompt body resolves to ``value``).
Dedicated table rather than a ``config`` prefix, per the implementation
decision in the plan.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.tables import ai_variables
from db.upsert import upsert


def get(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_variables).where(ai_variables.c.name == name)
        ).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_variables).order_by(ai_variables.c.name)
        ).mappings().all()
    return [dict(r) for r in rows]


def as_map() -> dict[str, str]:
    """Return ``{name: value}`` for fast prompt rendering."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_variables.c.name, ai_variables.c.value)
        ).all()
    return {r.name: r.value for r in rows}


def save(name: str, value: str, category: str = "") -> dict:
    now = time.time()
    values = {"name": name, "value": value, "category": category, "updated_at": now}
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_variables, values, conflict_cols=["name"],
            update_cols=["value", "category", "updated_at"],
        ))
    return values


def delete(name: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(ai_variables).where(ai_variables.c.name == name))
    return result.rowcount or 0
