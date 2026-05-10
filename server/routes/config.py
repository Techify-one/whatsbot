"""Configuration endpoints (config, test-key, models, status)."""

import asyncio
import logging
import time
from typing import Any

import httpx

from server.auth import generate_salt, hash_password
from server.helpers import _ok, _err, _mask_key

logger = logging.getLogger(__name__)

# ── Models cache ──────────────────────────────────────────────
_models_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_MODELS_CACHE_TTL = 600  # 10 minutes


def get_models_cache() -> dict[str, Any]:
    """Expose models cache for pricing lookup."""
    return _models_cache


def register_routes(app, deps):
    settings = deps.settings
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager
    state = deps.state

    @app.get("/api/config")
    async def get_config():
        return _ok({
            "openrouter_api_key": _mask_key(settings.get("openrouter_api_key", "")),
            "model": settings.get("model", "deepseek/deepseek-v4-pro"),
            "audio_model": settings.get("audio_model", "google/gemini-3-flash-preview"),
            "image_model": settings.get("image_model", "google/gemini-3-flash-preview"),
            "system_prompt": settings.get("system_prompt", ""),
            "auto_reply": settings.get("auto_reply", True),
            "max_context_messages": settings.get("max_context_messages", 10),
            "message_batch_delay": settings.get("message_batch_delay", 3.0),
            "split_messages": settings.get("split_messages", True),
            "split_message_delay": settings.get("split_message_delay", 2.0),
            "audio_transcription_enabled": settings.get("audio_transcription_enabled", True),
            "image_transcription_enabled": settings.get("image_transcription_enabled", True),
            "transfer_alert_enabled": settings.get("transfer_alert_enabled", True),
            "transfer_alert_duration": settings.get("transfer_alert_duration", 5),
            "max_executions": settings.get("max_executions", 200),
            "default_ai_enabled": settings.get("default_ai_enabled", True),
            "has_password": bool(settings.get("web_password_hash", "")),
        })

    @app.put("/api/config")
    async def save_config(body: dict):
        allowed_keys = {
            "openrouter_api_key", "model", "audio_model", "image_model",
            "audio_transcription_enabled", "image_transcription_enabled",
            "system_prompt", "auto_reply",
            "max_context_messages", "message_batch_delay",
            "split_messages", "split_message_delay",
            "transfer_alert_enabled", "transfer_alert_duration",
            "group_reply_mode", "bot_phone", "bot_name",
            "max_executions", "default_ai_enabled",
        }
        for key, value in body.items():
            if key in allowed_keys:
                settings[key] = value

        # Handle password set/change/remove
        if "web_password" in body:
            raw_password = body["web_password"]
            if raw_password:
                salt = generate_salt()
                settings["web_password_hash"] = hash_password(raw_password, salt)
                settings["web_password_salt"] = salt
                logger.info("Web panel password set/changed.")
            else:
                settings["web_password_hash"] = ""
                settings["web_password_salt"] = ""
                logger.info("Web panel password removed.")

        settings.save()

        agent_handler.update_config(
            api_key=settings.get("openrouter_api_key", ""),
            system_prompt=settings.get("system_prompt", ""),
            model=settings.get("model", "deepseek/deepseek-v4-pro"),
            audio_model=settings.get("audio_model", "google/gemini-3-flash-preview"),
            image_model=settings.get("image_model", "google/gemini-3-flash-preview"),
            max_context_messages=settings.get("max_context_messages", 10),
            split_messages=settings.get("split_messages", True),
            default_ai_enabled=settings.get("default_ai_enabled", True),
        )

        await ws_manager.broadcast("config_saved", {})
        logger.info("Config saved.")
        return _ok({"message": "Configurações salvas!"})

    @app.post("/api/config/test-key")
    async def test_api_key(body: dict):
        api_key = body.get("api_key", "").strip()
        if not api_key:
            return _err("Insira uma API key primeiro.")
        ok, msg = await asyncio.to_thread(agent_handler.test_api_key, api_key)
        # Auto-save valid key
        if ok:
            settings["openrouter_api_key"] = api_key
            settings.save()
            agent_handler.update_config(
                api_key=api_key,
                system_prompt=settings.get("system_prompt", ""),
                model=settings.get("model", "deepseek/deepseek-v4-pro"),
                audio_model=settings.get("audio_model", "google/gemini-3-flash-preview"),
                image_model=settings.get("image_model", "google/gemini-3-flash-preview"),
                max_context_messages=settings.get("max_context_messages", 10),
            )
            logger.info("API key tested and auto-saved.")
        return _ok({"valid": ok, "message": msg})

    @app.get("/api/models")
    async def list_models():
        """Return OpenRouter model list (cached for 10 min)."""
        now = time.time()
        if _models_cache["data"] and now - _models_cache["fetched_at"] < _MODELS_CACHE_TTL:
            return _ok(_models_cache["data"])
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://openrouter.ai/api/v1/models")
                resp.raise_for_status()
                raw = resp.json()
            models = []
            for m in raw.get("data", []):
                arch = m.get("architecture", {})
                models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", ""),
                    "input_modalities": arch.get("input_modalities", ["text"]),
                    "pricing": m.get("pricing", {}),
                })
            models.sort(key=lambda x: x["name"].lower())
            _models_cache["data"] = models
            _models_cache["fetched_at"] = now
            return _ok(models)
        except Exception as e:
            logger.error("Failed to fetch OpenRouter models: %s", e)
            if _models_cache["data"]:
                return _ok(_models_cache["data"])
            return _err(f"Erro ao buscar modelos: {e}", status=502)

    @app.get("/api/status")
    async def get_status():
        return _ok({
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "notification": state.notification,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })
