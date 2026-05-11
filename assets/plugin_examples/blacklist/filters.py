"""Plugin de exemplo: bloqueia mensagens de números na lista.

Retorna ``None`` do filter para abortar o pipeline. Quando isso acontece:
- A mensagem NÃO é salva no banco
- O LLM NÃO é chamado
- O bot NÃO responde

Configure a lista de telefones via Settings na tela do plugin
(/plugins → Blacklist → editar `phones`).
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)


def _load_settings() -> tuple[set[str], bool]:
    """Lê settings persistidas via PUT /api/plugins/blacklist/settings.

    Acessa a tabela ``config`` direto pelo engine do WhatsBot — não há
    dependência circular porque os campos viram chaves namespaceadas
    ``plugin.blacklist.<field>``.
    """
    phones: set[str] = set()
    block_groups = False
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text(
                    "SELECT key, value FROM config "
                    "WHERE key LIKE 'plugin.blacklist.%'"
                )
            ).all()
        for key, raw_value in rows:
            field = key.split(".", 2)[-1]
            if field == "phones":
                import json
                try:
                    val = json.loads(raw_value) if raw_value else ""
                except Exception:
                    val = raw_value or ""
                phones = {p.strip() for p in str(val).split(",") if p.strip()}
            elif field == "block_groups":
                import json
                try:
                    block_groups = bool(json.loads(raw_value))
                except Exception:
                    block_groups = str(raw_value).lower() in ("true", "1")
    except Exception as e:
        logger.warning("[blacklist] failed to load settings: %s", e)
    return phones, block_groups


def block_blacklisted(ctx, msg: dict) -> dict | None:
    phones, block_groups = _load_settings()
    if not phones:
        return msg

    phone = msg.get("phone", "")
    individual = msg.get("individual_phone") or phone
    is_group = msg.get("is_group", False)

    blocked = False
    if is_group and block_groups:
        if individual in phones:
            blocked = True
    elif not is_group:
        if phone in phones:
            blocked = True

    if blocked:
        logger.info(
            "[blacklist] blocked inbound from %s (group=%s)", individual, is_group,
        )
        return None
    return msg


FILTERS = {
    "filter.message.before_save": block_blacklisted,
}
