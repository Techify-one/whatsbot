"""Shared state classes for the WhatsBot server."""

import asyncio
import json
import logging
import threading
import time
from collections import deque

from fastapi import WebSocket


# ── In-memory log capture ────────────────────────────────────────────────

class MemoryLogHandler(logging.Handler):
    """Stores recent log records in a bounded deque for the debug UI."""

    IGNORED_LOGGERS = {"uvicorn.access", "uvicorn.error", "watchfiles.main", "httpx", "gowa.manager"}

    def __init__(self, max_entries: int = 500):
        super().__init__()
        self.records: deque[dict] = deque(maxlen=max_entries)

    def emit(self, record: logging.LogRecord):
        try:
            if record.name in self.IGNORED_LOGGERS:
                return
            self.records.append({
                "ts": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            })
        except Exception:
            pass

    def get_logs(self, limit: int = 100) -> list[dict]:
        entries = list(self.records)
        return entries[-limit:]

    def clear(self):
        self.records.clear()


# ── WebSocket Connection Manager ─────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, event: str, data: dict):
        message = json.dumps({"event": event, "data": data})
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.active.remove(ws)


# ── App State ─────────────────────────────────────────────────────────────

class AppState:
    """Shared mutable state for background tasks."""

    def __init__(self):
        self.msg_count: int = 0
        self.connected: bool = False
        self.auto_reply_running: bool = False
        self.stop_event: threading.Event = threading.Event()
        self.processed_messages: set[str] = set()
        self.notification: str = "Iniciando..."
        # QR cache — avoid regenerating on every request
        self.qr_data: bytes | None = None
        self.qr_fetched_at: float = 0.0
        self.qr_version: int = 0  # bumped when QR changes
        # Message batching — accumulate messages per contact before responding
        # Each item: {"text": str, "image_path": str|None, "audio_path": str|None}
        self.pending_messages: dict[str, list[dict]] = {}  # phone -> [msg_dict, ...]
        self.batch_tasks: dict[str, asyncio.Task] = {}  # phone -> scheduled task (legacy)
        # Typing-aware orchestrator state
        # typing_state[phone] = {"active": bool, "media": "text"|"audio", "last_ts": float}
        self.typing_state: dict[str, dict] = {}
        # Active orchestrator task per contact (replaces batch_tasks for typing-aware flow)
        self.processing_tasks: dict[str, asyncio.Task] = {}
        # True while a reply is mid-flight to WhatsApp — webhook must NOT cancel during this phase
        self.sending: dict[str, bool] = {}
        # Track recently sent replies to filter GOWA webhook echo-backs
        self.recently_sent: dict[str, float] = {}  # "phone:content_hash" -> timestamp
        # Bot's own identity for @mention detection in groups
        self.bot_phone: str = ""
        self.bot_name: str = ""
        # Last 50 raw webhook payloads for debugging
        self.webhook_payloads: deque[dict] = deque(maxlen=50)
        # Login attempts per IP for brute-force protection
        self.login_attempts: dict[str, deque[float]] = {}
