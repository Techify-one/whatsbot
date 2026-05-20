"""Low-balance monitor.

Fetches the remaining OpenRouter credit on demand or in the background after
each LLM call and, when it drops below ``low_balance_threshold``, broadcasts a
``low_balance`` WebSocket event so the frontend can show a notification modal.

The handler in ``agent/handler.py`` fires ``trigger_check_async()`` after each
billable LLM call. It's fire-and-forget — the actual HTTP fetch + broadcast
runs on the main asyncio loop, with internal rate-limiting so we don't hit
OpenRouter on every single call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config.settings import LLM_API_BASE_URL

logger = logging.getLogger(__name__)

_ws_manager: Any = None
_loop: asyncio.AbstractEventLoop | None = None
_settings: Any = None

# Rate limits — we deliberately undershoot OpenRouter's rate so a busy chat
# doesn't bombard the credits endpoint.
_MIN_CHECK_INTERVAL = 30.0   # seconds between credits fetches
_MIN_NOTIFY_INTERVAL = 60.0  # seconds between low_balance WS broadcasts

_last_check_ts: float = 0.0
_last_notify_ts: float = 0.0
_last_balance: dict | None = None
_check_in_flight: bool = False


def set_runtime(ws_manager: Any, loop: asyncio.AbstractEventLoop, settings: Any) -> None:
    """Called once during server startup so background callers can broadcast."""
    global _ws_manager, _loop, _settings
    _ws_manager = ws_manager
    _loop = loop
    _settings = settings


async def fetch_balance(api_key: str) -> dict | None:
    """Fetch remaining credit. Returns ``{total_credits, total_usage, remaining}`` or ``None``."""
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{LLM_API_BASE_URL}/credits",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            data = payload.get("data") or {}
            total = float(data.get("total_credits", 0) or 0)
            usage = float(data.get("total_usage", 0) or 0)
            return {
                "total_credits": total,
                "total_usage": usage,
                "remaining": max(0.0, total - usage),
            }
    except Exception as e:
        logger.debug("balance fetch failed: %s", e)
        return None


def get_cached() -> dict | None:
    """Return the most recent balance snapshot, if any."""
    if _last_balance is None:
        return None
    return {**_last_balance, "fetched_at": _last_check_ts}


async def _check_and_notify() -> None:
    global _last_check_ts, _last_notify_ts, _last_balance, _check_in_flight
    if _check_in_flight or _settings is None:
        return
    _check_in_flight = True
    try:
        now = time.monotonic()
        if now - _last_check_ts < _MIN_CHECK_INTERVAL:
            return
        if not _settings.get("low_balance_enabled", True):
            return
        api_key = _settings.get("openrouter_api_key", "")
        if not api_key:
            return
        balance = await fetch_balance(api_key)
        _last_check_ts = now
        if balance is None:
            return
        _last_balance = balance
        threshold = float(_settings.get("low_balance_threshold", 0.50) or 0.50)
        if balance["remaining"] < threshold and _ws_manager is not None:
            if now - _last_notify_ts >= _MIN_NOTIFY_INTERVAL:
                _last_notify_ts = now
                await _ws_manager.broadcast("low_balance", {
                    **balance,
                    "threshold": threshold,
                    "account_url": _settings.get("account_url", ""),
                })
    finally:
        _check_in_flight = False


def trigger_check_async() -> None:
    """Fire-and-forget. Safe to call from any thread; never raises."""
    if _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_check_and_notify(), _loop)
    except Exception as e:
        logger.debug("trigger_check_async failed: %s", e)
