"""Tool ``reminder_create`` — registra lembretes pedidos pelo contato.

O LLM é instruído a chamar essa tool APENAS quando o usuário pedir
explicitamente para lembrar de algo (ex: "me lembre de comprar pão").
Em mensagens normais, a tool não é acionada.

Plugin de referência da nova camada de dados: usa SQLAlchemy Core via
``plugins.context.make_plugin_db()`` e bind params (``:name``) em
``sqlalchemy.text``. Funções SQLite-only como ``strftime('%s','now')`` foram
substituídas por ``int(time.time())`` em Python.
"""

import logging
import time

from sqlalchemy import text

from plugins.context import broadcast, make_plugin_db

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
    body = (args or {}).get("text", "").strip()
    if not body:
        return None
    phone = ctx.contact.phone
    info = getattr(ctx.contact, "info", {}) or {}
    name = info.get("name") or getattr(ctx.contact, "group_name", "") or ""
    now_ts = int(time.time())

    with make_plugin_db() as conn:
        # RETURNING is supported by both SQLite (>= 3.35) and Postgres, giving
        # a dialect-agnostic way to fetch the generated row without ``lastrowid``.
        rid = conn.execute(
            text(
                "INSERT INTO plugin_lembretes_items (phone, name, text, ts) "
                "VALUES (:phone, :name, :text, :ts) RETURNING id"
            ),
            {"phone": phone, "name": name, "text": body, "ts": now_ts},
        ).scalar_one()
        row = conn.execute(
            text(
                "SELECT id, phone, name, text, ts FROM plugin_lembretes_items "
                "WHERE id = :id"
            ),
            {"id": rid},
        ).mappings().first()

    if row:
        broadcast("plugin_lembretes_added", dict(row))
    logger.info("Reminder saved for %s: %s", phone, body)
    return f"Lembrete anotado: {body}"


CORE_TOOLS = [
    (REMINDER_TOOL, execute_reminder),
]
