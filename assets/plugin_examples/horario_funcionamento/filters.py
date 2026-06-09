"""Horário de funcionamento + mensagem de ausência (away).

Fora do horário configurado, três filters cooperam para substituir a resposta
normal da IA por uma mensagem de ausência fixa:

* ``filter.system_prompt`` — troca o prompt do sistema por uma instrução estrita
  que obriga a IA a devolver EXATAMENTE o texto de ausência (o WhatsBot não dá
  ao plugin acesso direto ao envio; a entrega reusa o pipeline normal da IA).
* ``filter.llm.tools``     — zera as ferramentas (a IA não transfere nem usa
  tools enquanto fechado).
* ``filter.llm.messages``  — enxuga o contexto enviado ao modelo (determinismo)
  e, se a ausência já foi enviada a este contato dentro do ``cooldown_min``,
  retorna ``None`` (silêncio total, sem custo de LLM).

Estado de cooldown persiste em ``plugin_horario_funcionamento_away_log`` (via
``ctx.plugin_db`` indireto: ``make_plugin_db``), nunca em variáveis globais.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time

from sqlalchemy import text

from db.repositories import config_repo, contact_repo
from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)

_PREFIX = "plugin.horario_funcionamento."
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# High priority so the away rule wins over other plugins' prompt/tools tweaks
# when the establishment is closed (lower number = earlier; we want decisive).
_PRIORITY = 900


# ── Settings access ───────────────────────────────────────────────────────

def _cfg(key: str, default):
    return config_repo.get(_PREFIX + key, default)


# ── Business-hours math ───────────────────────────────────────────────────

def _parse_range(spec: str) -> tuple[int, int] | None:
    """'08:00-18:00' -> (480, 1080) in minutes. None if empty/invalid."""
    spec = (spec or "").strip()
    if not spec or "-" not in spec:
        return None
    try:
        start_s, end_s = spec.split("-", 1)
        oh, om = (int(x) for x in start_s.strip().split(":", 1))
        ch, cm = (int(x) for x in end_s.strip().split(":", 1))
    except (ValueError, TypeError):
        return None
    open_m, close_m = oh * 60 + om, ch * 60 + cm
    if not (0 <= open_m <= 1440 and 0 <= close_m <= 1440):
        return None
    return open_m, close_m


def _is_open_now() -> bool:
    """True if the current local time falls inside today's configured range."""
    offset = float(_cfg("tz_offset_hours", -3.0) or 0.0)
    local = _dt.datetime.fromtimestamp(time.time() + offset * 3600, _dt.timezone.utc)
    minute_of_day = local.hour * 60 + local.minute

    rng = _parse_range(str(_cfg(_DAYS[local.weekday()], "")))
    if rng:
        open_m, close_m = rng
        if open_m != close_m:
            if open_m < close_m:
                if open_m <= minute_of_day < close_m:
                    return True
            elif minute_of_day >= open_m or minute_of_day < close_m:
                # Range crosses midnight (e.g. 18:00-02:00).
                return True

    # A range that crosses midnight is "open" in the early hours of the NEXT day.
    prev = _parse_range(str(_cfg(_DAYS[(local.weekday() - 1) % 7], "")))
    if prev:
        open_m, close_m = prev
        if open_m > close_m and minute_of_day < close_m:
            return True
    return False


# ── Cooldown (away dedupe) ────────────────────────────────────────────────

def _away_recently_sent(phone: str, cooldown_min: int) -> bool:
    if cooldown_min <= 0:
        return False
    try:
        with make_plugin_db() as conn:
            row = conn.execute(
                text("SELECT last_ts FROM plugin_horario_funcionamento_away_log "
                     "WHERE phone = :p"),
                {"p": phone},
            ).first()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[horario_funcionamento] cooldown read failed: %s", e)
        return False
    if not row:
        return False
    return (time.time() - float(row[0])) < cooldown_min * 60


def _record_away(phone: str) -> None:
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_horario_funcionamento_away_log (phone, last_ts) "
                     "VALUES (:p, :t) ON CONFLICT(phone) DO UPDATE SET last_ts = :t"),
                {"p": phone, "t": time.time()},
            )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[horario_funcionamento] cooldown write failed: %s", e)


# ── Decision shared by the three filters ──────────────────────────────────

# Per-request decision: "open" (let the AI work), "away" (force the notice),
# "silent" (closed but the notice was already sent within the cooldown).

def _decide(phone: str) -> str:
    if not bool(_cfg("enabled", True)):
        return "open"
    if not phone:
        return "open"
    if not bool(_cfg("apply_to_groups", False)):
        row = contact_repo.get_by_phone(phone)
        if row and row.get("is_group"):
            return "open"
    if _is_open_now():
        return "open"
    cooldown = int(_cfg("cooldown_min", 60) or 0)
    return "silent" if _away_recently_sent(phone, cooldown) else "away"


def _away_text() -> str:
    return str(_cfg("away_message", "") or "").strip()


# ── Filters ───────────────────────────────────────────────────────────────

def on_system_prompt(ctx, value: str):
    """Replace the system prompt with a strict away-only instruction when closed."""
    phone = (getattr(ctx, "extras", None) or {}).get("phone", "")
    if _decide(phone) != "away":
        return value
    away = _away_text()
    if not away:
        return value
    return (
        "Você é o atendente automático de um estabelecimento que está FECHADO "
        "neste momento (fora do horário de funcionamento).\n"
        "Sua ÚNICA tarefa é responder com a mensagem de ausência abaixo, "
        "EXATAMENTE como está escrita, sem traduzir, resumir, completar, "
        "explicar nem responder a qualquer pergunta do cliente. Não use "
        "ferramentas. Não invente horários. Responda somente o texto a seguir:\n\n"
        f"{away}"
    )


def on_llm_tools(ctx, value):
    """No tools while closed (don't transfer/save/etc. — just deliver the away)."""
    phone = (getattr(ctx, "extras", None) or {}).get("phone", "")
    if _decide(phone) == "away":
        return []
    return value


def on_llm_messages(ctx, value):
    """Silence (None) on cooldown; trim context to force the exact away text."""
    phone = (getattr(ctx, "extras", None) or {}).get("phone", "")
    decision = _decide(phone)
    if decision == "open":
        return value
    if decision == "silent":
        return None
    # decision == "away": commit point — record it, then minimize the context
    # so the model has nothing to do except emit the away message.
    _record_away(phone)
    if isinstance(value, list) and value:
        trimmed = [value[0]]  # system message (already forced by on_system_prompt)
        if len(value) > 1:
            trimmed.append(value[-1])  # keep the triggering user turn
        return trimmed
    return value


FILTERS = {
    "filter.system_prompt": (on_system_prompt, _PRIORITY),
    "filter.llm.tools": (on_llm_tools, _PRIORITY),
    "filter.llm.messages": (on_llm_messages, _PRIORITY),
}
