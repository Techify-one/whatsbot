"""Per-group transcription opt-out — clean version using new core hooks.

Hooks the plugin attaches to (added in WhatsBot 1.1):

* ``filter.transcription.should_run`` — returning ``False`` skips the
  transcribe / describe call entirely for a specific group. The audio /
  image still reaches the chat history with its native player.
"""

import logging
from typing import Any

from sqlalchemy import text

from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)


def _load_override(chat_jid: str) -> dict[str, Any] | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text(
                "SELECT audio_mode, image_enabled "
                "FROM plugin_transcricao_grupos_settings "
                "WHERE chat_jid = :jid"
            ),
            {"jid": chat_jid},
        ).mappings().first()
    return dict(row) if row else None


def on_should_run(ctx, should: bool) -> bool:
    if not should:
        return should
    extras = ctx.extras
    if not extras.get("is_group"):
        return should
    override = _load_override(extras.get("group_jid", "") or "")
    if not override:
        return should
    kind = extras.get("media_kind")
    if kind == "audio" and override.get("audio_mode") == "off":
        logger.info(
            "[transcricao_grupos] áudio bloqueado pro grupo %s",
            extras.get("group_jid"),
        )
        return False
    if kind == "image" and override.get("image_enabled") == 0:
        logger.info(
            "[transcricao_grupos] imagem bloqueada pro grupo %s",
            extras.get("group_jid"),
        )
        return False
    return should


FILTERS = {
    "filter.transcription.should_run": on_should_run,
}
