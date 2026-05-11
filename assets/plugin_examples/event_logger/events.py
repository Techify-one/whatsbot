"""Plugin de exemplo: assina TODOS os eventos via wildcard.

Mostra três padrões úteis:
1. Um handler único catch-all em ``EVENT_HANDLERS = {"*": fn}`` —
   recebe todo evento emitido e ``ctx.event_name`` carrega o nome real.
2. Handler assíncrono coexistindo com handlers síncronos no mesmo dict.
3. Push em tempo real pra tela do plugin via ``plugins.context.broadcast``
   + ring buffer em memória servido por ``routes.py``.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from threading import Lock

from plugins.context import broadcast

logger = logging.getLogger(__name__)

# Estado compartilhado com routes.py — buffer circular + contagem agregada.
BUFFER_SIZE = 200
_buffer: deque[dict] = deque(maxlen=BUFFER_SIZE)
_counts: Counter[str] = Counter()
_lock = Lock()
_seq = 0


def _summarize(payload: dict) -> dict:
    """Remove campos pesados (raw GOWA pode ter base64 de áudio)."""
    return {k: v for k, v in payload.items() if k != "raw"}


def on_any(ctx, payload):
    """Síncrono — roda em ``asyncio.to_thread``."""
    global _seq
    summary = _summarize(payload)
    with _lock:
        _seq += 1
        entry = {
            "id": _seq,
            "ts": time.time(),
            "event": ctx.event_name,
            "summary": summary,
        }
        _buffer.appendleft(entry)
        _counts[ctx.event_name] += 1
        totals = {"total": sum(_counts.values()), "by_event": dict(_counts)}
    logger.info("[event_logger] %s %s", ctx.event_name, summary)
    broadcast("plugin_event_logger_tick", {**entry, **totals})


async def on_llm_after(ctx, payload):
    """Async — exemplo de handler aware da loop principal."""
    usage = payload.get("usage") or {}
    logger.info(
        "[event_logger] llm done phone=%s model=%s tokens=%s latency_ms=%s",
        payload.get("phone"), payload.get("model"),
        usage.get("total_tokens"), payload.get("latency_ms"),
    )


def snapshot(limit: int = BUFFER_SIZE) -> dict:
    """Retorna estado atual do buffer — usado pelo endpoint /recent."""
    with _lock:
        items = list(_buffer)[:limit]
        return {
            "items": items,
            "total": sum(_counts.values()),
            "by_event": dict(_counts),
            "buffer_size": BUFFER_SIZE,
        }


def clear() -> None:
    """Zera buffer e contadores — usado pelo endpoint /clear."""
    with _lock:
        _buffer.clear()
        _counts.clear()


EVENT_HANDLERS = {
    # Catch-all: cobre tudo emitido pelo bus
    "*": on_any,
    # Handler específico async pra demonstração — coexiste com o wildcard
    "llm.after": on_llm_after,
}
