"""Repository for the ``plugins`` and ``plugin_migrations`` tables."""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import inspect, insert as sa_insert, select, text as sa_text
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import plugin_migrations, plugins
from db.upsert import upsert_ignore

logger = logging.getLogger(__name__)


_SAFE_NAME_RE = re.compile(r"^plugin_[a-z][a-z0-9_]{0,31}_[A-Za-z0-9_]+$")


def list_all() -> list[dict]:
    """Return all known plugins (one row per id, including disabled)."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(plugins).order_by(plugins.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get(plugin_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(plugins).where(plugins.c.id == plugin_id)
        ).mappings().first()
    return dict(row) if row else None


def upsert(plugin_id: str, version: str, *, enabled: bool | None = None) -> None:
    """Insert or update a plugin row, preserving ``enabled`` if not provided."""
    now = time.time()
    existing = get(plugin_id)
    with get_engine().begin() as conn:
        if existing is None:
            enabled_int = 1 if enabled else 0
            conn.execute(sa_insert(plugins).values(
                id=plugin_id,
                version=version,
                enabled=enabled_int,
                installed_at=now,
                updated_at=now,
                load_error=None,
            ))
        else:
            enabled_int = existing["enabled"] if enabled is None else (1 if enabled else 0)
            conn.execute(sa_update(plugins).where(plugins.c.id == plugin_id).values(
                version=version,
                enabled=enabled_int,
                updated_at=now,
                load_error=None,
            ))


def set_enabled(plugin_id: str, enabled: bool) -> bool:
    """Toggle ``enabled``. Returns False if the plugin is unknown."""
    if get(plugin_id) is None:
        return False
    with get_engine().begin() as conn:
        conn.execute(sa_update(plugins).where(plugins.c.id == plugin_id).values(
            enabled=1 if enabled else 0,
            updated_at=time.time(),
        ))
    return True


def set_load_error(plugin_id: str, error: str | None) -> None:
    with get_engine().begin() as conn:
        conn.execute(sa_update(plugins).where(plugins.c.id == plugin_id).values(
            load_error=error,
            updated_at=time.time(),
        ))


def delete(plugin_id: str) -> None:
    """Delete plugin row and migration history. Does NOT drop the plugin's tables."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(plugin_migrations).where(plugin_migrations.c.plugin_id == plugin_id))
        conn.execute(sa_delete(plugins).where(plugins.c.id == plugin_id))


def applied_migrations(plugin_id: str) -> set[int]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(plugin_migrations.c.version).where(plugin_migrations.c.plugin_id == plugin_id)
        ).all()
    return {r.version for r in rows}


def record_migration(plugin_id: str, version: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(upsert_ignore(
            plugin_migrations,
            {"plugin_id": plugin_id, "version": version, "applied_at": time.time()},
            conflict_cols=["plugin_id", "version"],
        ))


def drop_plugin_tables(plugin_id: str) -> list[str]:
    """Drop every table whose name starts with ``plugin_<id>_``. Returns dropped names."""
    engine = get_engine()
    prefix = f"plugin_{plugin_id}_"
    insp = inspect(engine)
    all_tables = insp.get_table_names()
    targets = [t for t in all_tables if t.startswith(prefix)]
    dropped: list[str] = []
    with engine.begin() as conn:
        for name in targets:
            # Guard against any name that wouldn't match our strict allow-list,
            # so a maliciously crafted ``plugin_id`` cannot smuggle SQL through.
            if not _SAFE_NAME_RE.match(name):
                logger.warning("Refusing to drop suspicious table name: %s", name)
                continue
            conn.execute(sa_text(f'DROP TABLE IF EXISTS "{name}"'))
            dropped.append(name)
    return dropped
