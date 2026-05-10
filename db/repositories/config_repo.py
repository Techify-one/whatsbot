"""Repository for config key-value storage."""

from __future__ import annotations

import json

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.tables import config
from db.upsert import upsert


def _decode(value):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def get_all() -> dict:
    """Return all config key-value pairs as a dict (values JSON-decoded)."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(config.c.key, config.c.value)).all()
    return {row.key: _decode(row.value) for row in rows}


def get(key: str, default=None):
    """Get a single config value by key."""
    with get_engine().connect() as conn:
        value = conn.execute(
            select(config.c.value).where(config.c.key == key)
        ).scalar_one_or_none()
    if value is None:
        return default
    return _decode(value)


def set(key: str, value) -> None:
    """Set a single config value (JSON-encoded)."""
    encoded = json.dumps(value, ensure_ascii=False)
    with get_engine().begin() as conn:
        conn.execute(upsert(
            config,
            {"key": key, "value": encoded},
            conflict_cols=["key"],
            update_cols=["value"],
        ))


def set_many(data: dict) -> None:
    """Set multiple config values at once."""
    if not data:
        return
    rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False)} for k, v in data.items()]
    with get_engine().begin() as conn:
        for row in rows:
            conn.execute(upsert(
                config,
                row,
                conflict_cols=["key"],
                update_cols=["value"],
            ))


def delete_prefix(prefix: str) -> int:
    """Delete every config key starting with ``prefix``. Returns row count."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(config).where(config.c.key.like(prefix + "%")))
    return result.rowcount or 0
