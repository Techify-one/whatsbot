"""Plugin de exemplo: assina TODOS os eventos via wildcard.

Mostra três padrões úteis:
1. Um handler único catch-all em ``EVENT_HANDLERS = {"*": fn}`` —
   recebe todo evento emitido e ``ctx.event_name`` carrega o nome real.
2. Handler assíncrono coexistindo com handlers síncronos no mesmo dict.
3. Push em tempo real pra tela do plugin via ``plugins.context.broadcast``.

Para visualizar: abra DevTools → Network → WS frames depois de habilitar o
plugin e verá ``plugin_event_logger_tick`` chegando a cada evento.
"""

from __future__ import annotations

import logging
from collections import Counter

from plugins.context import broadcast

logger = logging.getLogger(__name__)

_counts: Counter[str] = Counter()


def on_any(ctx, payload):
    """Síncrono — roda em ``asyncio.to_thread``."""
    _counts[ctx.event_name] += 1
    # Mantém payload curto no log (raw do GOWA pode ser grande).
    summary = {k: v for k, v in payload.items() if k != "raw"}
    logger.info("[event_logger] %s %s", ctx.event_name, summary)
    broadcast("plugin_event_logger_tick", {
        "event": ctx.event_name,
        "total": sum(_counts.values()),
        "by_event": dict(_counts),
    })


async def on_llm_after(ctx, payload):
    """Async — exemplo de handler aware da loop principal."""
    usage = payload.get("usage") or {}
    logger.info(
        "[event_logger] llm done phone=%s model=%s tokens=%s latency_ms=%s",
        payload.get("phone"), payload.get("model"),
        usage.get("total_tokens"), payload.get("latency_ms"),
    )


EVENT_HANDLERS = {
    # Catch-all: cobre tudo emitido pelo bus
    "*": on_any,
    # Handler específico async pra demonstração — coexiste com o wildcard
    "llm.after": on_llm_after,
}
