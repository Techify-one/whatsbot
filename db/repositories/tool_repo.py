"""Repository for ``ai_tools`` (code-in-DB tools).

Each row holds Python source plus install metadata. The installer materialises
``code`` to disk, resolves ``dependencies`` and registers the tool when the
contract validates. ``dependencies`` and ``installed_deps`` are JSON arrays.
Every ``save`` (from the CRUD path) bumps ``version`` and snapshots to history;
the installer uses the lighter status setters which do NOT bump version.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy import delete as sa_delete

from db.engine import get_engine
from db.tables import ai_tools, ai_tools_history
from db.upsert import upsert


def _decode_list(value):
    if value is None:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["dependencies"] = _decode_list(d.get("dependencies"))
    d["installed_deps"] = _decode_list(d.get("installed_deps"))
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def get(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_tools).where(ai_tools.c.name == name)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(ai_tools).order_by(ai_tools.c.name)).mappings().all()
    return [_row_to_dict(r) for r in rows]


def list_enabled() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_tools).where(ai_tools.c.enabled == 1).order_by(ai_tools.c.name)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def save(
    name: str,
    *,
    description: str,
    code: str,
    dependencies: list[str] | None,
    enabled: bool,
) -> dict:
    """Upsert a tool (CRUD path). Resets install_status to ``pending`` so the
    next boot re-validates, bumps version and snapshots to history."""
    now = time.time()
    existing = get(name)
    version = (existing["version"] + 1) if existing else 1
    deps = dependencies or []
    values = {
        "name": name,
        "description": description,
        "code": code,
        "dependencies": json.dumps(deps, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "install_status": "pending",
        "install_error": None,
        # Preserve the install cache marker so unchanged deps skip pip.
        "installed_deps": json.dumps(
            (existing or {}).get("installed_deps", []), ensure_ascii=False
        ),
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_tools, values, conflict_cols=["name"],
            update_cols=["description", "code", "dependencies", "enabled",
                         "install_status", "install_error", "version", "updated_at"],
        ))
        conn.execute(ai_tools_history.insert().values(
            name=name,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
    return _row_to_dict(values)


def set_status(name: str, status: str, error: str | None = None) -> None:
    """Update install_status/install_error without bumping version."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(install_status=status, install_error=error, updated_at=time.time())
        )


def set_installed_deps(name: str, deps: list[str]) -> None:
    """Mark the dependency specs that were successfully installed (cache marker)."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(installed_deps=json.dumps(deps, ensure_ascii=False),
                    updated_at=time.time())
        )


def set_enabled(name: str, enabled: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(enabled=1 if enabled else 0, updated_at=time.time())
        )


def delete(name: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(ai_tools).where(ai_tools.c.name == name))
    return result.rowcount or 0
