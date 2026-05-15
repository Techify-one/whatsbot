"""Plugin event bus + filter pipeline.

Two complementary mechanisms, both populated at startup by ``plugins.loader``
and reachable from any thread or coroutine:

* **Events** — broadcast, fire-and-forget. Plugins subscribe by exporting
  ``EVENT_HANDLERS = {"message.received": fn, ...}`` in their ``events.py``.
  ``emit(name, payload)`` schedules every subscriber as an isolated
  ``asyncio.Task``; an exception in one handler never reaches the producer.

* **Filters** — interceptive, synchronous in the pipeline. Plugins export
  ``FILTERS = {"filter.reply.part": fn, ...}`` (optionally
  ``{"filter.reply.part": (fn, priority)}``). ``await apply_filter(name, value)``
  chains every filter in priority order; returning ``None`` aborts the cascade
  and the producer should skip the wrapped action.

Design references inspected before settling on this surface: Baileys
EventEmitter (Node WhatsApp), WAHA webhook taxonomy (wildcard ``*``,
``message.any`` alias), Home Assistant ``bus.async_listen``, WordPress
``do_action``/``apply_filters`` (events vs filters split), and Python libs
``pyee``/``blinker``/``fastapi-events``. We do not pull a library because
``plugins.context.broadcast`` already follows a hand-rolled, thread-safe
pattern and the EXE bundle benefits from fewer deps.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Optional[Awaitable[None]]]
FilterFn = Callable[..., Any]

KNOWN_EVENTS: set[str] = {
    # GOWA inbound / outbound message lifecycle
    "message.received", "message.sent", "message.saved",
    "message.reaction", "message.edited", "message.revoked", "message.deleted",
    # Presence / receipts
    "presence.changed", "receipt.changed",
    # Group / call / newsletter
    "group.participants_changed", "group.joined",
    "call.received",
    "newsletter.event",
    # Chat-level
    "chat.archived",
    # Connection / lifecycle
    "connection.changed",
    "app.startup", "app.shutdown",
    "plugin.loaded", "plugin.enabled", "plugin.disabled",
    "plugin.settings.changed",
    # LLM / tools
    "llm.before", "llm.after",
    "tool.before", "tool.after",
    # Internal CRUD
    "contact.updated", "contact.ai_toggled",
    "contact.tagged", "contact.untagged",
    "tag.created", "tag.updated", "tag.deleted",
    "config.changed",
    "tool_override.changed",
    "execution.started", "execution.ended",
}

# Subscription keys that are dispatch targets, not emission sources.
# ``message.any`` is re-dispatched automatically by :func:`emit` whenever
# ``message.received`` or ``message.sent`` fires, with ``direction`` added
# to the payload — plugins should subscribe to it rather than emit it.
# ``*`` is the wildcard catch-all that receives every emitted event.
_DISPATCH_ONLY_KEYS: set[str] = {"*", "message.any"}

# Lifecycle events that must NOT be interceptable via
# ``filter.event.before_emit`` — plugins should not be able to block
# their own load/disable or the app's startup/shutdown.
_LIFECYCLE_EVENTS: set[str] = {
    "app.startup", "app.shutdown",
    "plugin.loaded", "plugin.enabled", "plugin.disabled",
    "plugin.settings.changed",
}

# name -> [(plugin_id, handler), ...] in registration order
_handlers: dict[str, list[tuple[str, EventHandler]]] = {}
# name -> [(priority, plugin_id, fn), ...] sorted ascending by priority
_filters: dict[str, list[tuple[int, str, FilterFn]]] = {}

_loop: Optional[asyncio.AbstractEventLoop] = None
_agent_handler: Optional[Any] = None


def set_runtime(loop: asyncio.AbstractEventLoop, agent_handler: Any) -> None:
    """Wire the bus at server lifespan start. Idempotent."""
    global _loop, _agent_handler
    _loop = loop
    _agent_handler = agent_handler


def reset() -> None:
    """Clear all handlers and filters. For tests only."""
    _handlers.clear()
    _filters.clear()


# ── Events ───────────────────────────────────────────────────────────────


def register(plugin_id: str, event_name: str, handler: EventHandler) -> None:
    if event_name not in KNOWN_EVENTS and event_name not in _DISPATCH_ONLY_KEYS:
        logger.warning(
            "Plugin %s subscribed to unknown event %r — will receive nothing unless emit() is added",
            plugin_id, event_name,
        )
    _handlers.setdefault(event_name, []).append((plugin_id, handler))


def register_plugin_events(plugin_id: str, handlers: dict[str, EventHandler]) -> None:
    """Bulk register the EVENT_HANDLERS dict exported by a plugin."""
    for name, fn in (handlers or {}).items():
        if not callable(fn):
            logger.warning(
                "Plugin %s: EVENT_HANDLERS[%r] is not callable, skipped", plugin_id, name,
            )
            continue
        register(plugin_id, str(name), fn)


def emit(event_name: str, payload: dict) -> None:
    """Fire-and-forget dispatch. Safe to call from any thread or coroutine.

    Dispatch order: subscribers of the exact event → subscribers of
    ``message.any`` (if applicable) → subscribers of ``*``. Wildcard
    receives the original ``ctx.event_name`` to allow generic handlers.
    """
    if event_name in _DISPATCH_ONLY_KEYS:
        logger.warning("emit() called with dispatch-only key %r; ignored", event_name)
        return

    targeted = list(_handlers.get(event_name, ()))
    any_subs = (
        list(_handlers.get("message.any", ()))
        if event_name in ("message.received", "message.sent")
        else []
    )
    wildcard_subs = list(_handlers.get("*", ()))
    if not (targeted or any_subs or wildcard_subs):
        return
    if _loop is None:
        logger.debug("event bus not initialized; dropping %s", event_name)
        return

    direction: Optional[str] = None
    if event_name == "message.received":
        direction = "in"
    elif event_name == "message.sent":
        direction = "out"

    async def _fanout() -> None:
        for plugin_id, handler in targeted:
            asyncio.create_task(_run_one(plugin_id, event_name, handler, payload))
        if any_subs:
            any_payload = dict(payload)
            if direction is not None:
                any_payload["direction"] = direction
            for plugin_id, handler in any_subs:
                asyncio.create_task(_run_one(plugin_id, event_name, handler, any_payload))
        for plugin_id, handler in wildcard_subs:
            asyncio.create_task(_run_one(plugin_id, event_name, handler, payload))

    try:
        asyncio.run_coroutine_threadsafe(_fanout(), _loop)
    except Exception as e:
        logger.debug("emit %s failed to schedule: %s", event_name, e)


async def emit_with_filter(event_name: str, payload: dict) -> None:
    """Emit an event after letting plugins veto/rewrite the payload.

    The payload is passed through ``filter.event.before_emit`` first.
    Any plugin can return ``None`` to suppress the event entirely or
    return a modified payload to reshape what subscribers see. Lifecycle
    events (see ``_LIFECYCLE_EVENTS``) bypass the filter — plugins are
    not allowed to block their own load/disable or app startup/shutdown.

    Use this in async paths where you want plugin interception; use
    :func:`emit_with_filter_sync` in sync paths, or :func:`emit`
    directly for lifecycle / perf-sensitive sync paths.
    """
    if event_name in _LIFECYCLE_EVENTS:
        emit(event_name, payload)
        return
    filtered = await apply_filter(
        "filter.event.before_emit", payload, {"event_name": event_name}
    )
    if filtered is None:
        return
    emit(event_name, filtered if isinstance(filtered, dict) else payload)


def emit_with_filter_sync(event_name: str, payload: dict) -> None:
    """Sync sibling of :func:`emit_with_filter`.

    Use from worker threads (e.g. inside ``asyncio.to_thread`` or in
    legacy sync code like ``AgentHandler.process_message``). On the
    event-loop thread it short-circuits (filter is skipped) — same
    semantics as :func:`apply_filter_sync`.
    """
    if event_name in _LIFECYCLE_EVENTS:
        emit(event_name, payload)
        return
    filtered = apply_filter_sync(
        "filter.event.before_emit", payload, {"event_name": event_name}
    )
    if filtered is None:
        return
    emit(event_name, filtered if isinstance(filtered, dict) else payload)


async def _run_one(
    plugin_id: str, event_name: str, handler: EventHandler, payload: dict
) -> None:
    from plugins.context import EventContext, make_plugin_db  # late import to avoid cycle
    ctx = EventContext(
        handler=_agent_handler,
        plugin_id=plugin_id,
        plugin_db=make_plugin_db,
        event_name=event_name,
        emitted_at=time.time(),
    )
    try:
        if inspect.iscoroutinefunction(handler):
            await handler(ctx, payload)
        else:
            await asyncio.to_thread(handler, ctx, payload)
    except Exception as e:
        logger.warning(
            "plugin %s handler for %s raised %s: %s",
            plugin_id, event_name, type(e).__name__, e,
        )


# ── Filters ──────────────────────────────────────────────────────────────


def register_filter(
    plugin_id: str, filter_name: str, fn: FilterFn, priority: int = 100
) -> None:
    bucket = _filters.setdefault(filter_name, [])
    bucket.append((int(priority), plugin_id, fn))
    bucket.sort(key=lambda t: t[0])


def register_plugin_filters(
    plugin_id: str, filters: dict[str, Union[FilterFn, tuple[FilterFn, int]]],
) -> None:
    """Bulk register the FILTERS dict exported by a plugin.

    Each value may be a callable or a ``(callable, priority)`` tuple. Lower
    priority numbers run earlier in the chain.
    """
    for name, entry in (filters or {}).items():
        if isinstance(entry, tuple) and len(entry) == 2 and callable(entry[0]):
            fn, priority = entry
        elif callable(entry):
            fn, priority = entry, 100
        else:
            logger.warning(
                "Plugin %s: FILTERS[%r] must be callable or (callable, int), skipped",
                plugin_id, name,
            )
            continue
        register_filter(plugin_id, str(name), fn, priority=priority)


async def apply_filter(
    filter_name: str, value: Any, ctx_extras: Optional[dict] = None,
) -> Any:
    """Chain every filter registered for ``filter_name``.

    Each filter receives ``(FilterContext, value)`` and returns the modified
    value, or ``None`` to abort. ``None`` short-circuits the chain and is
    returned to the caller. Exceptions log a warning and the value passes
    through unchanged to the next filter (broken filter never traps the
    pipeline).
    """
    bucket = _filters.get(filter_name)
    if not bucket:
        return value

    from plugins.context import FilterContext, make_plugin_db  # late import to avoid cycle

    current = value
    for priority, plugin_id, fn in list(bucket):
        ctx = FilterContext(
            handler=_agent_handler,
            plugin_id=plugin_id,
            plugin_db=make_plugin_db,
            filter_name=filter_name,
            emitted_at=time.time(),
        )
        if ctx_extras:
            ctx.extras = dict(ctx_extras)
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(ctx, current)
            else:
                result = fn(ctx, current)
        except Exception as e:
            logger.warning(
                "plugin %s filter %s raised %s: %s — value passed through",
                plugin_id, filter_name, type(e).__name__, e,
            )
            continue
        if result is None:
            logger.info(
                "filter %s aborted by plugin %s (priority=%d)",
                filter_name, plugin_id, priority,
            )
            return None
        current = result
    return current


def apply_filter_sync(
    filter_name: str,
    value: Any,
    ctx_extras: Optional[dict] = None,
    timeout: float = 5.0,
) -> Any:
    """Synchronous wrapper for :func:`apply_filter`.

    Only safe to call from a *non-event-loop* thread — typically code running
    inside ``asyncio.to_thread`` or in a worker thread that has no running
    asyncio loop. If invoked from the event-loop thread it would deadlock, so
    this helper falls back to returning the value unchanged in that case.
    """
    if _loop is None or not _filters.get(filter_name):
        return value
    # Detect "we're on the loop thread" to avoid deadlock.
    try:
        running = asyncio.get_running_loop()
        if running is _loop:
            logger.debug(
                "apply_filter_sync(%s) called from the event loop thread; "
                "skipping (use apply_filter directly there)",
                filter_name,
            )
            return value
    except RuntimeError:
        pass
    try:
        future = asyncio.run_coroutine_threadsafe(
            apply_filter(filter_name, value, ctx_extras), _loop
        )
        return future.result(timeout=timeout)
    except Exception as e:
        logger.warning(
            "apply_filter_sync(%s) failed: %s — value passed through",
            filter_name, e,
        )
        return value
