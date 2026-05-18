"""First-run setup wizard endpoints — Techify API key provisioning.

The setup wizard (frontend) connects WhatsApp, then triggers
``POST /api/setup/request-key`` which makes the WhatsBot send a WhatsApp
message to the Techify provisioning number. Techify creates an account +
API key keyed by the sender's number. The wizard then polls
``GET /api/setup/key-status``, which in turn POSTs to Techify's
``/request-apikey`` endpoint (body ``{"number": ...}``) server-side and
saves the key to the config once ready. Techify keeps the key downloadable
for ~1 minute after the account is created.
"""

import asyncio
import logging
import time

import httpx

from config.settings import (
    TECHIFY_PROVISION_MESSAGE,
    TECHIFY_PROVISION_NUMBER,
    TECHIFY_REQUEST_APIKEY_URL,
)
from gowa.client import GOWASendError
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    settings = deps.settings
    gowa_client = deps.gowa_client
    agent_handler = deps.agent_handler
    state = deps.state

    @app.post("/api/setup/request-key")
    async def request_key():
        """Send the Techify provisioning message and arm the key polling."""
        # Resolve the connected WhatsApp number (digits only).
        number = (state.bot_phone or "").split(":")[0].strip()
        if not number:
            number = (await asyncio.to_thread(gowa_client.get_own_number) or "").strip()
            if number:
                state.bot_phone = number
        if not number:
            return _err(
                "Não foi possível identificar seu número. "
                "Aguarde a conexão concluir e tente de novo."
            )

        try:
            await asyncio.to_thread(
                gowa_client.send_message, TECHIFY_PROVISION_NUMBER, TECHIFY_PROVISION_MESSAGE
            )
        except GOWASendError as e:
            logger.error("Setup: failed to send provisioning message: %s", e)
            return _err(f"Não foi possível enviar a mensagem: {e}")

        state.setup_key_number = number
        state.setup_key_requested_at = time.time()
        logger.info("Setup: provisioning message sent, polling key for %s", number)
        return _ok({"number": number})

    @app.get("/api/setup/key-status")
    async def key_status():
        """Poll Techify for the provisioned API key; save it once ready."""
        number = state.setup_key_number
        if not number:
            return _ok({"status": "pending"})

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    TECHIFY_REQUEST_APIKEY_URL, json={"number": number}
                )
            if resp.status_code != 200:
                logger.warning("Setup: Techify returned HTTP %s", resp.status_code)
                return _ok({"status": "error"})
            data = resp.json()
        except Exception as e:
            logger.warning("Setup: key-status poll failed: %s", e)
            return _ok({"status": "error"})

        if not isinstance(data, dict):
            logger.warning("Setup: Techify returned a non-object body")
            return _ok({"status": "error"})

        status = data.get("status", "pending")
        api_key = data.get("api_key", "")

        if status == "ready" and api_key:
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
            logger.info("Setup: API key provisioned and saved.")
            return _ok({"status": "ready"})

        return _ok({"status": status})
