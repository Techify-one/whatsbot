"""Context objects passed to plugin entry points (tools, prompts, routes).

A ``ToolContext`` is built by ``AgentHandler._dispatch_tool`` for every tool
call, regardless of whether the tool is a core tool or comes from a plugin.
Plugins receive ``plugin_id`` set; core tools receive ``None``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from pathlib import Path
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
_gowa_client: Optional[Any] = None
_agent_handler: Optional[Any] = None


def set_runtime(
    ws_manager: Any, loop: asyncio.AbstractEventLoop, gowa_client: Any = None,
    agent_handler: Any = None,
) -> None:
    """Wire the public runtime services used by plugin helpers."""
    global _ws_manager, _loop, _gowa_client, _agent_handler
    _ws_manager = ws_manager
    _loop = loop
    _gowa_client = gowa_client
    _agent_handler = agent_handler


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


def send_whatsapp_message(
    phone: str,
    text: str,
    mentions: list[str] | None = None,
    reply_message_id: str | None = None,
) -> dict:
    """Send a text through the active WhatsBot/GOWA connection.

    This is the supported outbound API for plugin tools, event handlers and
    routes. Async callers should invoke it with ``asyncio.to_thread`` because
    the underlying GOWA client is synchronous.
    """
    phone = str(phone or "").strip()
    text = str(text or "").strip()
    if not phone:
        raise ValueError("telefone não informado")
    if not text:
        raise ValueError("mensagem vazia")
    if _gowa_client is None or _agent_handler is None:
        raise RuntimeError("WhatsApp indisponível: runtime do plugin não inicializado")

    from db.repositories import config_repo
    from gowa.client import extract_msg_id
    from plugins.events import apply_filter_sync, emit_with_filter_sync

    filtered = apply_filter_sync(
        "filter.reply.part", text,
        {"phone": phone, "index": 0, "total": 1, "source": "plugin"},
    )
    if filtered is None:
        raise RuntimeError("mensagem bloqueada por plugin")
    text = str(filtered)

    sandbox = bool(config_repo.get(f"sandbox_contact.{phone}"))
    response = None
    if not sandbox:
        response = _gowa_client.send_message(
            phone, text, mentions=mentions, reply_message_id=reply_message_id,
        )
    msg_id = extract_msg_id(response)
    message = _agent_handler.save_assistant_message(
        phone, text, msg_id=msg_id, status="sent",
    )
    if _ws_manager is not None and _loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(
                _ws_manager.broadcast("new_message", {"phone": phone, "message": message}),
                _loop,
            )
        except Exception as exc:
            logger.debug("plugin message broadcast failed: %s", exc)
    emit_with_filter_sync("message.sent", {
        "phone": phone, "text": text, "msg_id": msg_id,
        "media_type": None, "media_path": None,
        "source": "plugin", "status": "sent",
        "reply_to_msg_id": reply_message_id,
        "ts": time.time(),
    })
    return {
        "ok": True, "msg_id": msg_id, "sandbox": sandbox,
        "message": message,
    }


def get_plugin_setting(plugin_id: str, key: str, default: Any = None) -> Any:
    """Read one declarative plugin setting from its namespaced config key."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", plugin_id or ""):
        raise ValueError("invalid plugin id")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key or ""):
        raise ValueError("invalid plugin setting key")
    from db.repositories import config_repo
    return config_repo.get(f"plugin.{plugin_id}.{key}", default)


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


def plugin_data_dir(plugin_id: str) -> Path:
    """Return durable storage that survives plugin code updates.

    Plugins should keep uploads, generated images and other user-owned files
    here instead of inside ``storages/plugins/<id>`` (the replaceable code
    directory).
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", plugin_id or ""):
        raise ValueError("invalid plugin id")
    from config.settings import get_data_dir
    path = get_data_dir() / "storages" / "plugin_data" / plugin_id
    path.mkdir(parents=True, exist_ok=True)
    return path


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
