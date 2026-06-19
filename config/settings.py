import os
from pathlib import Path
from typing import Any, Callable

from db.repositories import config_repo


def get_data_dir() -> Path:
    """Return the application data directory (project root)."""
    data_dir = Path(__file__).resolve().parent.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# LLM API base URL — OpenRouter-compatible proxy. Override via the
# LLM_API_BASE_URL env var to point back at OpenRouter or another proxy.
LLM_API_BASE_URL = os.environ.get(
    "LLM_API_BASE_URL", "https://llm.techify.one/api/v1"
).rstrip("/")

# Techify account provisioning — used by the first-run setup wizard. The
# WhatsBot fetches the current provisioning number from TECHIFY_SERVICE_NUMBER_URL
# and sends a WhatsApp message to it, Techify creates an account + API key, and
# the wizard polls TECHIFY_REQUEST_APIKEY_URL (keyed by the connected WhatsApp
# number) until the key is ready. Once the account is created the key stays
# downloadable for ~1 minute. TECHIFY_PROVISION_NUMBER is a fallback used only
# when the service-number endpoint is unreachable.
TECHIFY_SERVICE_NUMBER_URL = os.environ.get(
    "TECHIFY_SERVICE_NUMBER_URL", "https://llm.techify.one/service_number"
).rstrip("/")
TECHIFY_PROVISION_NUMBER = os.environ.get("TECHIFY_PROVISION_NUMBER", "5513981744038")
TECHIFY_REQUEST_APIKEY_URL = os.environ.get(
    "TECHIFY_REQUEST_APIKEY_URL", "https://llm.techify.one/request-apikey"
).rstrip("/")
TECHIFY_PROVISION_MESSAGE = "Quero Criar conta e receber minha Chave de API"


_ENV_OVERRIDES: dict[str, tuple[str, Callable[[str], Any]]] = {
    "OPENROUTER_API_KEY": ("openrouter_api_key", str),
    "WHATSBOT_MODEL": ("model", str),
    "WHATSBOT_AUDIO_MODEL": ("audio_model", str),
    "WHATSBOT_IMAGE_MODEL": ("image_model", str),
    "WHATSBOT_DOCUMENT_MODEL": ("document_model", str),
    "WHATSBOT_SYSTEM_PROMPT": ("system_prompt", str),
    "WHATSBOT_WEB_PORT": ("web_port", int),
    "WHATSBOT_GOWA_PORT": ("gowa_port", int),
    "WHATSBOT_AUTO_REPLY": ("auto_reply", lambda v: v.lower() in ("1", "true", "yes")),
    "WHATSBOT_MAX_CONTEXT": ("max_context_messages", int),
    "WHATSBOT_BATCH_DELAY": ("message_batch_delay", float),
    "WHATSBOT_MULTI_AGENT": ("multi_agent_enabled", lambda v: v.lower() in ("1", "true", "yes")),
    "WHATSBOT_TEAM_MODE": ("agent_team_mode", str),
    "WHATSBOT_AI_ENGINE": ("ai_engine_enabled", lambda v: v.lower() in ("1", "true", "yes")),
}

# Reverse lookup: config_key -> (env_key, cast). Used by get() to apply env overrides on-demand.
_ENV_OVERRIDES_BY_KEY: dict[str, tuple[str, Callable[[str], Any]]] = {
    cfg_key: (env_key, cast) for env_key, (cfg_key, cast) in _ENV_OVERRIDES.items()
}

DEFAULT_CONFIG = {
    "openrouter_api_key": "",
    "model": "deepseek/deepseek-v4-pro",
    "audio_model": "google/gemini-2.5-flash",
    "image_model": "google/gemini-2.5-flash",
    "document_model": "google/gemini-2.5-flash",
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
    "audio_transcription_mode": "received",
    "audio_transcription_target": "private",
    "audio_transcription_chat_prefix": "",
    "image_transcription_enabled": True,
    "document_transcription_enabled": True,
    "transfer_alert_enabled": True,
    "transfer_alert_duration": 5,
    "group_reply_mode": "mention_only",
    # --- Motor de agente (AGNO) ---------------------------------------------
    # O WhatsBot usa o framework AGNO como motor de LLM. Quando
    # ``multi_agent_enabled`` é True, em vez de um único agente é montado um
    # AGNO Team: um coordenador (que carrega o ``system_prompt`` principal) +
    # um agente especialista por entrada em ``agents``. Os especialistas
    # compartilham as mesmas tools (filters/events/usage continuam valendo).
    #
    # ``agent_team_mode`` ∈ {coordinate, route, broadcast, tasks}:
    #   - coordinate: o líder orquestra os membros e sintetiza a resposta.
    #   - route: o líder roteia para UM especialista que responde direto.
    # ``agents[].tools``: "all" (todas as tools) ou lista de nomes de tool.
    # ``agents[].model``: "" usa o modelo default; senão um id de modelo.
    # Os exemplos abaixo ficam INERTES enquanto multi_agent_enabled=False.
    "multi_agent_enabled": False,
    "agent_team_mode": "coordinate",
    # --- Motor de agente dirigido pelo banco (config-in-DB + code-in-DB) -----
    # Quando ``ai_engine_enabled`` é True, prompt/modelo/tools do agente são
    # lidos do banco (tabelas ``ai_*``) em vez das constantes do AgentHandler,
    # e tools podem ser criadas/editadas como código Python no próprio banco.
    # Off (default) → caminho legado intacto (paridade total). Override por env
    # ``WHATSBOT_AI_ENGINE``.
    "ai_engine_enabled": False,
    "agents": [
        {
            "id": "vendas",
            "name": "Vendas",
            "role": "Especialista em vendas, preços, planos e informações comerciais",
            "instructions": (
                "Você é o especialista de vendas. Responda dúvidas sobre produtos, "
                "planos, preços e condições comerciais de forma objetiva e persuasiva, "
                "sempre em português brasileiro."
            ),
            "tools": "all",
            "model": "",
        },
        {
            "id": "suporte",
            "name": "Suporte",
            "role": "Especialista em suporte, dúvidas técnicas e pós-venda",
            "instructions": (
                "Você é o especialista de suporte. Ajude o cliente a resolver "
                "problemas técnicos e dúvidas de uso com paciência e clareza, "
                "sempre em português brasileiro."
            ),
            "tools": "all",
            "model": "",
        },
    ],
    "bot_phone": "",
    "bot_name": "",
    "default_ai_enabled": True,
    "web_password_hash": "",
    "web_password_salt": "",
    "setup_completed": False,
    # Techify account — returned by /request-apikey alongside the API key.
    # account_url is the customer's account/recharge page; access_token is
    # the credential for that account (kept server-side only).
    "account_url": "",
    "access_token": "",
    # Low-balance notification — broadcast a "low_balance" WS event when the
    # remaining OpenRouter credit drops below the threshold (USD). The frontend
    # opens a modal pointing to ``account_url`` for the user to recharge.
    "low_balance_enabled": True,
    "low_balance_threshold": 0.50,
}


_MISSING = object()


class Settings:
    def __init__(self):
        self.data_dir = get_data_dir()
        self.logs_dir = self.data_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        self.load()

    def load(self):
        """Seed missing defaults into the DB. No in-memory cache is kept — reads are write-through to config_repo."""
        current = config_repo.get_all()
        # Migrate legacy audio_transcription_enabled → audio_transcription_mode
        if "audio_transcription_enabled" in current:
            legacy_enabled = current.pop("audio_transcription_enabled")
            if "audio_transcription_mode" not in current:
                migrated = "received" if legacy_enabled else "off"
                config_repo.set("audio_transcription_mode", migrated)
                current["audio_transcription_mode"] = migrated
            config_repo.delete_prefix("audio_transcription_enabled")
        # Persist defaults for any key missing in the DB
        missing = {k: v for k, v in DEFAULT_CONFIG.items() if k not in current}
        if missing:
            # An install that already has an API key configured is NOT a
            # first run — seed setup_completed=True so the setup wizard does
            # not ambush existing users after an update.
            if "setup_completed" in missing:
                env_key = os.environ.get("OPENROUTER_API_KEY", "")
                missing["setup_completed"] = bool(
                    current.get("openrouter_api_key") or env_key
                )
            config_repo.set_many(missing)

    @staticmethod
    def _env_override(key: str):
        """Return env-overridden value for ``key`` if present, else ``_MISSING``."""
        mapping = _ENV_OVERRIDES_BY_KEY.get(key)
        if mapping is None:
            return _MISSING
        env_key, cast = mapping
        raw = os.environ.get(env_key)
        if not raw:
            return _MISSING
        try:
            return cast(raw)
        except (ValueError, TypeError):
            return _MISSING

    def save(self):
        """No-op. Kept for backward compatibility — writes are write-through via set()/__setitem__."""
        return

    def get(self, key: str, default=None):
        override = self._env_override(key)
        if override is not _MISSING:
            return override
        value = config_repo.get(key, _MISSING)
        if value is _MISSING:
            return DEFAULT_CONFIG.get(key, default)
        return value

    def set(self, key: str, value):
        config_repo.set(key, value)

    def __getitem__(self, key):
        override = self._env_override(key)
        if override is not _MISSING:
            return override
        value = config_repo.get(key, _MISSING)
        if value is _MISSING:
            if key in DEFAULT_CONFIG:
                return DEFAULT_CONFIG[key]
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        config_repo.set(key, value)
