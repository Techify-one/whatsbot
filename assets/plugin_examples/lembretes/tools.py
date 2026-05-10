"""Tool ``reminder_create`` — registra lembretes pedidos pelo contato.

O LLM é instruído a chamar essa tool APENAS quando o usuário pedir
explicitamente para lembrar de algo (ex: "me lembre de comprar pão").
Em mensagens normais, a tool não é acionada.
"""

import logging

from db.connection import get_db
from plugins.context import broadcast

logger = logging.getLogger(__name__)


REMINDER_TOOL = {
    "type": "function",
    "display_label": "Criar Lembretes",
    "function": {
        "name": "reminder_create",
        "description": (
            "Registra um lembrete pedido pelo usuário. "
            "Use APENAS quando o usuário pedir explicitamente para lembrar de algo "
            "(ex: 'me lembre de comprar pão', 'preciso lembrar de ligar pro João'). "
            "NÃO use em mensagens normais nem em saudações."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "O que o usuário quer lembrar, em texto curto e direto.",
                },
            },
            "required": ["text"],
        },
    },
}


def execute_reminder(ctx, args: dict) -> str | None:
    text = (args or {}).get("text", "").strip()
    if not text:
        return None
    phone = ctx.contact.phone
    info = getattr(ctx.contact, "info", {}) or {}
    name = info.get("name") or getattr(ctx.contact, "group_name", "") or ""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO plugin_lembretes_items (phone, name, text, ts) "
        "VALUES (?, ?, ?, strftime('%s', 'now'))",
        (phone, name, text),
    )
    conn.commit()
    rid = cur.lastrowid
    row = conn.execute(
        "SELECT id, phone, name, text, ts FROM plugin_lembretes_items WHERE id = ?",
        (rid,),
    ).fetchone()
    if row:
        broadcast("plugin_lembretes_added", dict(row))
    logger.info("Reminder saved for %s: %s", phone, text)
    return f"Lembrete anotado: {text}"


CORE_TOOLS = [
    (REMINDER_TOOL, execute_reminder),
]
