"""Repository for ``ai_agents`` (config-in-DB agent definitions).

One row per agent; in the single-agent MVP there is exactly one (``default``).
``model_config`` and ``tool_names`` are stored as JSON-encoded TEXT and decoded
on read. Every ``save`` bumps ``version`` and writes a snapshot to
``ai_agents_history`` for rollback / change trail.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from db.engine import get_engine
from db.tables import ai_agents, ai_agents_history
from db.upsert import upsert, upsert_ignore

DEFAULT_AGENT_KEY = "default"


def _decode_json(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["model_config"] = _decode_json(d.get("model_config"), {})
    d["tool_names"] = _decode_json(d.get("tool_names"), None)
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def get(agent_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents).where(ai_agents.c.agent_key == agent_key)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def get_default() -> dict | None:
    return get(DEFAULT_AGENT_KEY)


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agents).order_by(ai_agents.c.agent_key)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def ensure(
    agent_key: str,
    *,
    display_name: str = "",
    prompt_key: str = "",
    model_config: dict | None = None,
    tool_names: list[str] | None = None,
    enabled: bool = True,
) -> None:
    """Insert the agent only if it does not exist yet (no version bump).

    Used to seed the default agent at boot without clobbering user edits.
    """
    now = time.time()
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt_key": prompt_key,
        "model_config": json.dumps(model_config or {}, ensure_ascii=False),
        "tool_names": None if tool_names is None else json.dumps(tool_names, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "version": 1,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert_ignore(ai_agents, values, conflict_cols=["agent_key"]))


def save(
    agent_key: str,
    *,
    display_name: str,
    prompt_key: str,
    model_config: dict,
    tool_names: list[str] | None,
    enabled: bool,
) -> dict:
    """Upsert an agent, bump version and snapshot to history. Returns the row."""
    now = time.time()
    existing = get(agent_key)
    version = (existing["version"] + 1) if existing else 1
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt_key": prompt_key,
        "model_config": json.dumps(model_config or {}, ensure_ascii=False),
        "tool_names": None if tool_names is None else json.dumps(tool_names, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_agents, values, conflict_cols=["agent_key"],
            update_cols=["display_name", "prompt_key", "model_config",
                         "tool_names", "enabled", "version", "updated_at"],
        ))
        conn.execute(ai_agents_history.insert().values(
            agent_key=agent_key,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
    return _row_to_dict(values)
