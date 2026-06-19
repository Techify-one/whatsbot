"""Config-in-DB factory feeding the AGNO engine.

Reads the DB-driven agent definition (``ai_agents`` + ``ai_prompts`` +
``ai_variables``), renders the prompt template, and returns an :class:`AgentSpec`
the handler/engine consume in place of the hard-coded ``handler.system_prompt``
and ``handler.model``.

Everything here is **single-agent** and behind the ``ai_engine_enabled`` flag:
when the flag is off, :func:`build_for_contact` returns ``None`` and the handler
keeps its legacy behaviour untouched (parity).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from db.repositories import agent_repo, prompt_repo, variable_repo

logger = logging.getLogger(__name__)

DEFAULT_AGENT_KEY = agent_repo.DEFAULT_AGENT_KEY
DEFAULT_PROMPT_KEY = "default"

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass
class AgentSpec:
    """Resolved, per-request agent configuration from the DB."""
    agent_key: str
    base_prompt: str
    model_config: dict = field(default_factory=dict)
    tool_names: list[str] | None = None  # None = every registered tool

    @property
    def model(self) -> str | None:
        return self.model_config.get("model") or None


def render_template(body: str, variables: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders with values from ``variables``.

    Only tokens that look like ``{identifier}`` AND match a known variable are
    replaced; unknown tokens and literal braces in the prompt text are left
    untouched (so JSON examples / stray braces survive intact).
    """
    if not body or "{" not in body:
        return body or ""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        return variables[name] if name in variables else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body)


def seed_default_agent(settings) -> None:
    """Create the default prompt + agent rows if absent (idempotent).

    Seeds with the *current* config values so flipping ``ai_engine_enabled`` on
    gives behaviour identical to today. Never overwrites existing rows (no
    version bump), so user edits in the DB are preserved across boots.
    """
    try:
        prompt_repo.ensure(
            DEFAULT_PROMPT_KEY,
            settings.get("system_prompt", ""),
        )
        agent_repo.ensure(
            DEFAULT_AGENT_KEY,
            display_name="Agente padrão",
            prompt_key=DEFAULT_PROMPT_KEY,
            model_config={"model": settings.get("model", "")},
            tool_names=None,  # all registered tools
            enabled=True,
        )
        logger.info("AI engine: default agent/prompt seeded (or already present)")
    except Exception as e:
        logger.warning("AI engine seed failed: %s", e)


def build_for_contact(handler, contact) -> AgentSpec | None:
    """Resolve the DB-driven agent for a request, or ``None`` to use legacy path.

    Returns ``None`` when the flag is off, the default agent is missing/disabled,
    or anything goes wrong — the handler then falls back to its in-code config,
    so a broken DB row never takes the agent down.
    """
    if not getattr(handler, "ai_engine_enabled", False):
        return None
    try:
        agent = agent_repo.get_default()
        if not agent or not agent.get("enabled"):
            logger.debug("AI engine: default agent missing/disabled; using legacy path")
            return None

        prompt_row = prompt_repo.get(agent.get("prompt_key") or DEFAULT_PROMPT_KEY)
        body = (prompt_row or {}).get("body") or ""
        if not body:
            # No prompt body in the DB — fall back to the in-code base prompt so
            # the agent never runs with an empty system prompt.
            body = handler.system_prompt

        variables = variable_repo.as_map()
        rendered = render_template(body, variables)

        model_config = dict(agent.get("model_config") or {})
        if not model_config.get("model"):
            model_config["model"] = handler.model

        return AgentSpec(
            agent_key=agent["agent_key"],
            base_prompt=rendered,
            model_config=model_config,
            tool_names=agent.get("tool_names"),
        )
    except Exception as e:
        logger.warning("AI engine: build_for_contact failed (%s); using legacy path", e)
        return None
