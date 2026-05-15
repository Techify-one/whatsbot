"""REST endpoints for the transcricao_grupos plugin.

Mounted under /api/plugins/transcricao_grupos/...
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)

router = APIRouter()


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _read_global_defaults() -> dict[str, Any]:
    """Read the global transcription defaults from the ``config`` table."""
    defaults = {
        "audio_transcription_mode": "received",
        "image_transcription_enabled": True,
    }
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text(
                    "SELECT key, value FROM config "
                    "WHERE key IN ('audio_transcription_mode', "
                    "'image_transcription_enabled')"
                )
            ).mappings().all()
    except Exception as exc:
        logger.warning(
            "[transcricao_grupos] failed reading global defaults: %s", exc
        )
        return defaults

    import json
    for row in rows:
        raw = row.get("value")
        if raw is None:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = raw
        defaults[row["key"]] = parsed
    return defaults


@router.get("/defaults")
async def get_defaults() -> dict:
    return _ok(_read_global_defaults())


@router.get("/groups")
async def list_groups() -> dict:
    """List all groups together with their per-group override (if any)."""
    defaults = _read_global_defaults()
    audio_default = defaults.get("audio_transcription_mode", "received")
    image_default = bool(defaults.get("image_transcription_enabled", True))

    with make_plugin_db() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    c.phone,
                    c.name,
                    c.group_name,
                    c.is_archived,
                    s.audio_mode AS override_audio,
                    s.image_enabled AS override_image,
                    s.updated_at AS override_updated_at
                FROM contacts c
                LEFT JOIN plugin_transcricao_grupos_settings s
                    ON s.chat_jid = c.phone
                WHERE c.is_group = 1
                ORDER BY
                    CASE WHEN c.group_name IS NULL OR c.group_name = ''
                         THEN 1 ELSE 0 END,
                    LOWER(COALESCE(c.group_name, c.name, c.phone))
                """
            )
        ).mappings().all()

    items = []
    for row in rows:
        override_audio = row.get("override_audio")
        override_image = row.get("override_image")
        effective_audio = "off" if override_audio == "off" else audio_default
        effective_image = (
            image_default if override_image is None else bool(override_image)
        )
        items.append({
            "chat_jid": row["phone"],
            "name": (row.get("group_name") or row.get("name")
                     or row["phone"]),
            "is_archived": bool(row.get("is_archived")),
            "override_audio": override_audio,
            "override_image": (
                None if override_image is None else int(override_image)
            ),
            "effective_audio_mode": effective_audio,
            "effective_image_enabled": effective_image,
            "updated_at": row.get("override_updated_at"),
        })

    return _ok({
        "defaults": {
            "audio_transcription_mode": audio_default,
            "image_transcription_enabled": image_default,
        },
        "groups": items,
    })


@router.put("/groups/{chat_jid:path}")
async def update_group(chat_jid: str, body: dict) -> dict:
    if "@g.us" not in chat_jid:
        raise HTTPException(status_code=400, detail="chat_jid inválido")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body inválido")

    audio_mode: Any = body.get("audio_mode", "__no_change__")
    image_enabled: Any = body.get("image_enabled", "__no_change__")

    def _norm_audio(value: Any) -> Any:
        if value in (None, "", "default", "padrao", "padrão"):
            return None
        if value == "off":
            return "off"
        raise HTTPException(
            status_code=400, detail="audio_mode deve ser 'off' ou null"
        )

    def _norm_image(value: Any) -> Any:
        if value in (None, "", "default", "padrao", "padrão"):
            return None
        if value in (0, False, "off", "0"):
            return 0
        raise HTTPException(
            status_code=400, detail="image_enabled deve ser 0/false ou null"
        )

    with make_plugin_db() as conn:
        existing = conn.execute(
            text(
                "SELECT audio_mode, image_enabled "
                "FROM plugin_transcricao_grupos_settings "
                "WHERE chat_jid = :jid"
            ),
            {"jid": chat_jid},
        ).mappings().first()

        new_audio = (existing["audio_mode"] if existing else None) \
            if audio_mode == "__no_change__" else _norm_audio(audio_mode)
        new_image = (existing["image_enabled"] if existing else None) \
            if image_enabled == "__no_change__" else _norm_image(image_enabled)

        if new_audio is None and new_image is None:
            conn.execute(
                text(
                    "DELETE FROM plugin_transcricao_grupos_settings "
                    "WHERE chat_jid = :jid"
                ),
                {"jid": chat_jid},
            )
        else:
            now = time.time()
            if existing:
                conn.execute(
                    text(
                        "UPDATE plugin_transcricao_grupos_settings "
                        "SET audio_mode = :audio, image_enabled = :image, "
                        "    updated_at = :ts WHERE chat_jid = :jid"
                    ),
                    {"audio": new_audio, "image": new_image,
                     "ts": now, "jid": chat_jid},
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO plugin_transcricao_grupos_settings "
                        "(chat_jid, audio_mode, image_enabled, updated_at) "
                        "VALUES (:jid, :audio, :image, :ts)"
                    ),
                    {"jid": chat_jid, "audio": new_audio,
                     "image": new_image, "ts": now},
                )

    return _ok({
        "chat_jid": chat_jid,
        "audio_mode": new_audio,
        "image_enabled": new_image,
    })


@router.delete("/groups/{chat_jid:path}")
async def reset_group(chat_jid: str) -> dict:
    with make_plugin_db() as conn:
        conn.execute(
            text(
                "DELETE FROM plugin_transcricao_grupos_settings "
                "WHERE chat_jid = :jid"
            ),
            {"jid": chat_jid},
        )
    return _ok({"chat_jid": chat_jid, "reset": True})
