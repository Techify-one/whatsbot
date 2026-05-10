"""Pure helper functions for the WhatsBot server."""

import json
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse


def _get_web_dir() -> Path:
    """Locate the web/ directory."""
    return Path(__file__).resolve().parent.parent / "web"


def _ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _mask_key(key: str) -> str:
    """Mask an API key for display (show first 8 + last 4 chars)."""
    if len(key) <= 12:
        return "*" * len(key)
    return key[:8] + "*" * (len(key) - 12) + key[-4:]


def parse_split_reply(reply: str) -> list[str]:
    """Parse LLM reply as JSON array of strings. Fallback to single message."""
    text = reply.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()
    if text.startswith("["):
        try:
            parts = json.loads(text)
            if isinstance(parts, list) and all(isinstance(p, str) for p in parts):
                filtered = [p.strip() for p in parts if p.strip()]
                if filtered:
                    return filtered
        except (json.JSONDecodeError, TypeError):
            pass
    return [reply]
