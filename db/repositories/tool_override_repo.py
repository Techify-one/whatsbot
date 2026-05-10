"""Repository for the ``tool_overrides`` table.

Stores per-tool overrides (enabled flag, description, display_label) keyed by
the canonical tool ``name``. Rows are created eagerly when a tool is first
registered (via ``ensure``), so the table is the authoritative source for the
Tools management UI.

``description=NULL`` means "use the schema default from code". Reset is done by
sending ``description=null`` in the PUT — the repo ``upsert`` distinguishes
"do not touch" from "set to NULL" via a sentinel.
"""

import time

from db.connection import get_db


_UNSET = object()


def get(name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT name, plugin_id, enabled, description, display_label, updated_at "
        "FROM tool_overrides WHERE name = ?",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def list_all() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT name, plugin_id, enabled, description, display_label, updated_at "
        "FROM tool_overrides ORDER BY plugin_id IS NOT NULL, plugin_id, name"
    ).fetchall()
    return [dict(r) for r in rows]


def ensure(name: str, plugin_id: str | None) -> None:
    """Insert a default row on the first time a tool is registered.

    No-op when a row for ``name`` already exists. Always refreshes ``plugin_id``
    on conflict so a tool moving between core/plugin or between plugins ends up
    pointing at the current source.
    """
    conn = get_db()
    now = time.time()
    conn.execute(
        "INSERT INTO tool_overrides (name, plugin_id, enabled, description, display_label, updated_at) "
        "VALUES (?, ?, 1, NULL, NULL, ?) "
        "ON CONFLICT(name) DO UPDATE SET plugin_id = excluded.plugin_id",
        (name, plugin_id, now),
    )
    conn.commit()


def upsert(
    name: str,
    *,
    enabled=_UNSET,
    description=_UNSET,
    display_label=_UNSET,
) -> dict | None:
    """Apply a partial update. Sentinel ``_UNSET`` keeps the existing value.

    Pass ``description=None`` (or ``display_label=None``) to clear the field
    (reset to default). Returns the updated row, or ``None`` if the tool is
    unknown.
    """
    existing = get(name)
    if existing is None:
        return None
    new_enabled = existing["enabled"] if enabled is _UNSET else (1 if enabled else 0)
    new_description = existing["description"] if description is _UNSET else description
    new_label = existing["display_label"] if display_label is _UNSET else display_label
    conn = get_db()
    conn.execute(
        "UPDATE tool_overrides "
        "SET enabled = ?, description = ?, display_label = ?, updated_at = ? "
        "WHERE name = ?",
        (new_enabled, new_description, new_label, time.time(), name),
    )
    conn.commit()
    return get(name)


def delete_for_plugin(plugin_id: str) -> int:
    """Remove all overrides belonging to a plugin. Used when plugin is deleted."""
    conn = get_db()
    cur = conn.execute("DELETE FROM tool_overrides WHERE plugin_id = ?", (plugin_id,))
    conn.commit()
    return cur.rowcount or 0


def delete_orphans(known_names: set[str]) -> int:
    """Remove rows for tools that are no longer registered (renamed/removed)."""
    conn = get_db()
    rows = conn.execute("SELECT name FROM tool_overrides").fetchall()
    orphans = [r["name"] for r in rows if r["name"] not in known_names]
    for n in orphans:
        conn.execute("DELETE FROM tool_overrides WHERE name = ?", (n,))
    if orphans:
        conn.commit()
    return len(orphans)
