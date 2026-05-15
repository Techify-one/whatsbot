"""Execution tracking — async helpers and re-exports.

Core logic lives in agent/execution.py to avoid circular imports.
This module adds async wrappers for use in server routes.

Usage pattern in async code (webhook.py, sandbox.py):
    exec_id = await astart_execution(phone, "webhook")
    try:
        await atrack_step("webhook_received", {...})
        ...  # processing with asyncio.to_thread() calls
        await aend_execution(exec_id)
    except Exception as e:
        await aend_execution(exec_id, error=str(e))
"""

import asyncio
import time

from agent.execution import (  # noqa: F401 — re-export
    set_current_execution,
    create_execution,
    complete_execution,
    track_step,
    get_current_execution_id,
    prune_executions,
)
from plugins.events import emit_with_filter

# Module-level cache: exec_id -> {"phone", "trigger_type", "started_at"}
# Used to compute duration_ms and route phone/trigger into the ``ended``
# event without an extra DB hit.
_active: dict[int, dict] = {}


async def astart_execution(phone: str, trigger_type: str = "webhook") -> int:
    """Create execution in DB (via to_thread) and set contextvar in async context."""
    exec_id = await asyncio.to_thread(create_execution, phone, trigger_type)
    # Set contextvar HERE in the async context — this is inherited by to_thread calls
    set_current_execution(exec_id)
    started_at = time.time()
    _active[exec_id] = {
        "phone": phone, "trigger_type": trigger_type,
        "started_at": started_at,
    }
    await emit_with_filter("execution.started", {
        "exec_id": exec_id, "phone": phone,
        "trigger_type": trigger_type, "ts": started_at,
    })
    return exec_id


async def aend_execution(exec_id: int, error: str | None = None) -> None:
    """Finalize execution in DB and clear the contextvar."""
    if error:
        await asyncio.to_thread(complete_execution, exec_id, "failed", error)
    else:
        await asyncio.to_thread(complete_execution, exec_id, "completed")
    set_current_execution(None)
    meta = _active.pop(exec_id, None)
    if meta is not None:
        await emit_with_filter("execution.ended", {
            "exec_id": exec_id,
            "phone": meta.get("phone"),
            "trigger_type": meta.get("trigger_type"),
            "error": error,
            "duration_ms": int((time.time() - meta["started_at"]) * 1000),
            "ts": time.time(),
        })


async def atrack_step(step_type: str, data: dict | None = None, status: str = "ok") -> None:
    """Async wrapper — delegates to track_step via to_thread."""
    await asyncio.to_thread(track_step, step_type, data, status)
