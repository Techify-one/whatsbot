"""Repository for execution tracking tables."""

from __future__ import annotations

import json
import time

from sqlalchemy import and_, delete as sa_delete, func, insert as sa_insert, select, update as sa_update

from db.engine import get_engine
from db.tables import execution_steps, executions


def create(phone: str, trigger_type: str = "webhook") -> int:
    """Create a new execution and return its ID."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(executions).values(
            phone=phone, trigger_type=trigger_type, started_at=time.time(),
        ))
        return result.inserted_primary_key[0]


def add_step(execution_id: int, step_type: str,
             data: dict | None = None, status: str = "ok") -> int:
    """Add a step to an execution and return step ID."""
    data_json = json.dumps(data, ensure_ascii=False) if data else None
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(execution_steps).values(
            execution_id=execution_id,
            step_type=step_type,
            status=status,
            data=data_json,
            ts=time.time(),
        ))
        return result.inserted_primary_key[0]


def complete(execution_id: int, status: str = "completed",
             error: str | None = None) -> None:
    """Mark an execution as completed or failed."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(
            status=status,
            completed_at=time.time(),
            error=error,
        ))


def get_by_id(execution_id: int) -> dict | None:
    """Return an execution with all its steps."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(executions).where(executions.c.id == execution_id)
        ).mappings().first()
        if not row:
            return None
        execution = dict(row)
        step_rows = conn.execute(
            select(execution_steps)
            .where(execution_steps.c.execution_id == execution_id)
            .order_by(execution_steps.c.ts)
        ).mappings().all()
    execution["steps"] = []
    for s in step_rows:
        step = dict(s)
        if step.get("data"):
            try:
                step["data"] = json.loads(step["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        execution["steps"].append(step)
    return execution


def list_executions(limit: int = 50, offset: int = 0,
                    phone: str | None = None,
                    status: str | None = None) -> list[dict]:
    """List executions (newest first) with step count and duration."""
    step_count = (
        select(func.count())
        .where(execution_steps.c.execution_id == executions.c.id)
        .correlate(executions)
        .scalar_subquery()
        .label("step_count")
    )
    stmt = (
        select(executions, step_count)
        .order_by(executions.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    where_clauses = []
    if phone:
        where_clauses.append(executions.c.phone == phone)
    if status:
        where_clauses.append(executions.c.status == status)
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    results = []
    for r in rows:
        d = dict(r)
        if d.get("started_at") and d.get("completed_at"):
            d["duration_ms"] = round((d["completed_at"] - d["started_at"]) * 1000)
        else:
            d["duration_ms"] = None
        results.append(d)
    return results


def count(phone: str | None = None, status: str | None = None) -> int:
    """Count total executions for pagination."""
    stmt = select(func.count()).select_from(executions)
    where_clauses = []
    if phone:
        where_clauses.append(executions.c.phone == phone)
    if status:
        where_clauses.append(executions.c.status == status)
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar() or 0


def prune(max_keep: int) -> int:
    """Delete oldest executions keeping only the most recent ``max_keep``."""
    with get_engine().begin() as conn:
        total = conn.execute(select(func.count()).select_from(executions)).scalar() or 0
        if total <= max_keep:
            return 0
        keep_ids = conn.execute(
            select(executions.c.id).order_by(executions.c.id.desc()).limit(max_keep)
        ).scalars().all()
        result = conn.execute(sa_delete(executions).where(executions.c.id.notin_(keep_ids)))
    return result.rowcount or 0


def delete_older_than(cutoff_ts: float) -> int:
    """Delete executions whose ``started_at`` is before ``cutoff_ts``."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(executions).where(executions.c.started_at < cutoff_ts))
    return result.rowcount or 0


def get_webhook_payloads(limit: int = 50) -> list[dict]:
    """Get recent webhook payloads from execution steps (replaces in-memory deque)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(execution_steps.c.ts, execution_steps.c.data, executions.c.phone)
            .join(executions, executions.c.id == execution_steps.c.execution_id)
            .where(execution_steps.c.step_type == "webhook_received")
            .order_by(execution_steps.c.ts.desc())
            .limit(limit)
        ).mappings().all()

    results = []
    for r in rows:
        entry = {"ts": r["ts"], "phone": r["phone"]}
        if r["data"]:
            try:
                entry["payload"] = json.loads(r["data"])
            except (json.JSONDecodeError, TypeError):
                entry["payload"] = r["data"]
        else:
            entry["payload"] = {}
        results.append(entry)
    results.reverse()
    return results
