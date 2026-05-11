"""Context objects passed to plugin entry points (tools, prompts, routes).

A ``ToolContext`` is built by ``AgentHandler._dispatch_tool`` for every tool
call, regardless of whether the tool is a core tool or comes from a plugin.
Plugins receive ``plugin_id`` set; core tools receive ``None``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from agent.handler import AgentHandler
    from agent.memory import ContactMemory, TagRegistry

logger = logging.getLogger(__name__)


# ── WebSocket broadcast bridge for plugins ────────────────────────────────
#
# Plugin tool executors run synchronously inside ``asyncio.to_thread``. To let
# a plugin push a real-time event to the frontend (e.g. "novo lembrete"), we
# expose a thread-safe ``broadcast(event, data)`` helper that schedules the
# coroutine on the main event loop. The server wires the ws_manager and loop
# at startup via ``set_runtime``.

_ws_manager: Optional[Any] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def set_runtime(ws_manager: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Called once during server startup. Plugins read these via ``broadcast``."""
    global _ws_manager, _loop
    _ws_manager = ws_manager
    _loop = loop


def broadcast(event: str, data: dict) -> None:
    """Best-effort WS broadcast from any thread. Never raises."""
    if _ws_manager is None or _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _ws_manager.broadcast(event, data), _loop
        )
    except Exception as e:
        logger.debug("plugin broadcast failed: %s", e)


# ── DB access for plugins ────────────────────────────────────────────────
#
# Plugins access their dedicated ``plugin_<id>_*`` tables through the shared
# SQLAlchemy engine — there is no separate per-plugin database. The
# ``plugin_db`` callable on ``ToolContext`` returns a context manager yielding
# a ``Connection`` so plugin code can do:
#
#     with ctx.plugin_db() as conn:
#         conn.execute(text("INSERT INTO plugin_foo_items ..."), {...})
#
# This replaces the legacy pattern of calling ``get_db()`` and operating on a
# raw ``sqlite3.Connection``.


def make_plugin_db():
    """Return a context manager that opens a transactional engine connection."""
    from db.engine import get_engine
    return get_engine().begin()


@dataclasses.dataclass
class ToolContext:
    """Context passed to a tool executor.

    Attributes:
        contact: ``ContactMemory`` of the contact that triggered the tool call.
        handler: The ``AgentHandler`` instance, exposes tag_registry, model, etc.
        tag_registry: Convenience pointer to ``handler.tag_registry``.
        plugin_id: Plugin id if the tool comes from a plugin, ``None`` for core.
        plugin_db: Optional callable returning a transactional ``Connection``
            context manager scoped to the shared engine.
    """

    contact: "ContactMemory"
    handler: "AgentHandler"
    tag_registry: "TagRegistry"
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None


@dataclasses.dataclass
class PromptContext:
    """Context passed to a prompt fragment callable.

    A prompt fragment is ``Callable[[ContactMemory, PromptContext], str]``.
    Returning an empty string means "do not inject anything for this fragment".
    """

    handler: "AgentHandler"
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None


@dataclasses.dataclass
class EventContext:
    """Context passed to a plugin event handler.

    The handler signature is ``def on_event(ctx: EventContext, payload: dict)``
    (sync or ``async``). ``event_name`` echoes the dispatched event so a single
    handler reused via ``EVENT_HANDLERS = {"*": fn}`` can branch on it.
    ``emitted_at`` is the wall time at which the bus dispatched, useful for
    end-to-end latency probing.
    """

    handler: Optional[Any] = None
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None
    event_name: str = ""
    emitted_at: float = 0.0


@dataclasses.dataclass
class FilterContext:
    """Context passed to a plugin filter.

    A filter signature is ``def fn(ctx: FilterContext, value) -> value | None``
    (sync or ``async``). Returning ``None`` aborts the wrapped action; any
    other return becomes the input for the next filter in the chain.
    ``extras`` is filled by the producer with call-site-specific data
    (e.g. the contact phone for ``filter.message.before_save``).
    """

    handler: Optional[Any] = None
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None
    filter_name: str = ""
    emitted_at: float = 0.0
    extras: dict = dataclasses.field(default_factory=dict)
