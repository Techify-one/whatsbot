import os
from pathlib import Path
from typing import Any, Callable

from db.repositories import config_repo


def get_data_dir() -> Path:
    """Return the application data directory (project root)."""
    data_dir = Path(__file__).resolve().parent.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


_ENV_OVERRIDES: dict[str, tuple[str, Callable[[str], Any]]] = {
    "OPENROUTER_API_KEY": ("openrouter_api_key", str),
    "WHATSBOT_MODEL": ("model", str),
    "WHATSBOT_AUDIO_MODEL": ("audio_model", str),
    "WHATSBOT_IMAGE_MODEL": ("image_model", str),
    "WHATSBOT_SYSTEM_PROMPT": ("system_prompt", str),
    "WHATSBOT_WEB_PORT": ("web_port", int),
    "WHATSBOT_GOWA_PORT": ("gowa_port", int),
    "WHATSBOT_AUTO_REPLY": ("auto_reply", lambda v: v.lower() in ("1", "true", "yes")),
    "WHATSBOT_MAX_CONTEXT": ("max_context_messages", int),
    "WHATSBOT_BATCH_DELAY": ("message_batch_delay", float),
}

DEFAULT_CONFIG = {
    "openrouter_api_key": "",
    "model": "deepseek/deepseek-v4-pro",
    "audio_model": "google/gemini-3-flash-preview",
    "image_model": "google/gemini-3-flash-preview",
    "system_prompt": (
        "Você é um assistente útil e amigável. Responda de forma clara e concisa. "
        "Use português brasileiro."
    ),
    "auto_reply": False,
    "max_context_messages": 10,
    "inactivity_timeout_min": 30,
    "message_batch_delay": 3.0,
    "response_delay_min": 1.0,
    "response_delay_max": 3.0,
    "gowa_port": 64999,
    "web_port": 8080,
    "usd_brl_rate": 5.50,
    "split_messages": True,
    "split_message_delay": 2.0,
    "audio_transcription_enabled": True,
    "image_transcription_enabled": True,
    "transfer_alert_enabled": True,
    "transfer_alert_duration": 5,
    "group_reply_mode": "mention_only",
    "bot_phone": "",
    "bot_name": "",
    "default_ai_enabled": True,
    "web_password_hash": "",
    "web_password_salt": "",
}


class Settings:
    def __init__(self):
        self.data_dir = get_data_dir()
        self.logs_dir = self.data_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        self._config: dict = {}
        self.load()

    def load(self):
        self._config = config_repo.get_all()
        # Merge defaults for missing keys
        added = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._config:
                self._config[key] = value
                added = True
        # Persist on first run or when new defaults were added
        if added or not self._config:
            self.save()
        self._apply_env_overrides()

    def _apply_env_overrides(self):
        """Override config values with environment variables when present."""
        for env_key, (config_key, cast) in _ENV_OVERRIDES.items():
            value = os.environ.get(env_key)
            if value:
                try:
                    self._config[config_key] = cast(value)
                except (ValueError, TypeError):
                    pass

    def save(self):
        config_repo.set_many(self._config)

    def get(self, key: str, default=None):
        return self._config.get(key, default)

    def set(self, key: str, value):
        self._config[key] = value

    def __getitem__(self, key):
        return self._config[key]

    def __setitem__(self, key, value):
        self._config[key] = value
