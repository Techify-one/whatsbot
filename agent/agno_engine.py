"""AGNO-based agent engine for WhatsBot.

Replaces the hand-rolled OpenAI tool-calling loop with the AGNO framework
(``agno.agent.Agent`` / ``agno.team.Team``) while preserving every WhatsBot
plugin hook (filters + events), usage accounting and execution tracking.

Design notes
------------
* **Stateless per request.** A fresh Agent/Team is built for each message so
  the tool closures can capture a per-request ``executed`` collector without
  cross-talk between concurrent contacts. AGNO objects are cheap to build.
* **WhatsBot owns history/system prompt.** We do *not* hand AGNO a ``db`` nor
  let it build its own context. The system message and the conversation are
  passed in explicitly (already run through ``filter.system_prompt`` and
  ``filter.llm.messages`` by the handler), and AGNO's context builders are
  disabled (``build_context=False`` etc.).
* **Tools** are the same registry the legacy loop used. Each tool schema is
  wrapped in an ``agno.tools.function.Function`` whose entrypoint re-applies
  ``filter.tool.args`` / ``filter.tool.result`` and emits ``tool.before`` /
  ``tool.after`` — identical semantics to the old manual dispatch.
* **Multi-agent.** When ``handler.multi_agent_enabled`` is set, a ``Team`` is
  built: a coordinator (carrying the WhatsBot system prompt) plus one member
  Agent per entry in ``handler.agents``. Members share the same wrapped tool
  Functions, so filters/events/usage keep flowing regardless of which member
  ends up calling a tool.

The engine never talks to plugins directly for the *llm.before/llm.after*
events — the handler owns those, since it also owns the surrounding
try/except and usage snapshot.
"""

import time
import logging
from dataclasses import dataclass, field

from agno.agent import Agent
from agno.team import Team, TeamMode
from agno.models.openai import OpenAILike
from agno.models.message import Message
from agno.tools.function import Function

from config.settings import LLM_API_BASE_URL
from agent.execution import track_step
from plugins.events import (
    apply_filter,
    apply_filter_sync,
    emit_with_filter,
    emit_with_filter_sync,
)

logger = logging.getLogger(__name__)

# AGNO may inject framework objects (the agent/team/session) as kwargs into a
# tool entrypoint when the parameter names match. We capture **kwargs and strip
# these reserved names so only the model-provided arguments reach the executor.
_RESERVED_TOOL_KWARGS = {
    "agent", "team", "session_state", "run_context", "dependencies",
    "run_id", "session_id", "user_id",
}

_DEFAULT_MAX_TOKENS = 1024


@dataclass
class EngineResult:
    """Outcome of one AGNO run, mapped back to WhatsBot's ProcessResult."""
    reply: str = ""
    executed_tools: list[dict] = field(default_factory=list)
    usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}


# --------------------------------------------------------------------------- #
# Model / message helpers
# --------------------------------------------------------------------------- #
def build_model(handler, model_id: str | None = None,
                model_config: dict | None = None) -> OpenAILike:
    """Build an OpenAILike model pointed at the Techify proxy.

    Techify is OpenAI-compatible, so ``OpenAILike`` (id + api_key + base_url) is
    all AGNO needs. ``telemetry`` is disabled on the Agent/Team, not here.

    ``model_config`` (config-in-DB path) may carry ``model``/``temperature``/
    ``top_p``/``max_tokens``. When absent, behaviour matches the legacy default
    (``handler.model`` + ``_DEFAULT_MAX_TOKENS``, provider-default sampling).
    """
    mc = model_config or {}
    extra: dict = {}
    if mc.get("temperature") is not None:
        extra["temperature"] = mc["temperature"]
    if mc.get("top_p") is not None:
        extra["top_p"] = mc["top_p"]
    return OpenAILike(
        id=mc.get("model") or model_id or handler.model,
        api_key=handler.api_key,
        base_url=LLM_API_BASE_URL,
        max_tokens=mc.get("max_tokens") or _DEFAULT_MAX_TOKENS,
        **extra,
    )


def split_messages(messages: list[dict]) -> tuple[str, list[Message]]:
    """Split an OpenAI-format message list into (system_prompt, conversation).

    The handler hands us the already-filtered list ``[{system}, *context]``.
    We concatenate every ``system`` entry into the system prompt and convert the
    remaining user/assistant turns into AGNO ``Message`` objects to feed as the
    run input. This keeps both ``filter.system_prompt`` and
    ``filter.llm.messages`` faithful: whatever a plugin left in the list is what
    AGNO sees.
    """
    system_parts: list[str] = []
    convo: list[Message] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
            continue
        convo.append(Message(role=role or "user", content=content or ""))
    if not convo:
        # AGNO needs at least one input message; mirror legacy behaviour of
        # always carrying the just-saved user turn.
        convo.append(Message(role="user", content=""))
    return "\n".join(system_parts), convo


# --------------------------------------------------------------------------- #
# Tool wrapping (filters + events preserved)
# --------------------------------------------------------------------------- #
def _clean_args(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k not in _RESERVED_TOOL_KWARGS}


def _make_async_entrypoint(handler, contact, sender, tool_name, executed):
    async def entrypoint(**kwargs):
        args = _clean_args(kwargs)
        filtered = await apply_filter(
            "filter.tool.args",
            {"tool_name": tool_name, "args": args},
            {"phone": sender},
        )
        if filtered is None:
            executed.append({"tool": tool_name, "args": args, "skipped": True})
            return ""
        name = filtered.get("tool_name", tool_name)
        args = filtered.get("args", args)

        _t0 = time.monotonic()
        await emit_with_filter("tool.before", {
            "phone": sender, "tool_name": name, "args": args, "ts": time.time(),
        })
        feedback = handler._dispatch_tool(contact, name, args)
        await emit_with_filter("tool.after", {
            "phone": sender, "tool_name": name, "args": args,
            "result": feedback, "error": None,
            "latency_ms": int((time.monotonic() - _t0) * 1000), "ts": time.time(),
        })
        if feedback is not None:
            fr = await apply_filter(
                "filter.tool.result", feedback,
                {"phone": sender, "tool_name": name},
            )
            feedback = "" if fr is None else fr

        executed.append({"tool": name, "args": args})
        track_step("tool_executed", {"tool": name, "args": args})
        logger.info("Tool call for %s: %s(%s)", sender, name, args)
        return feedback or "Informações salvas com sucesso."

    return entrypoint


def _make_sync_entrypoint(handler, contact, sender, tool_name, executed):
    def entrypoint(**kwargs):
        args = _clean_args(kwargs)
        filtered = apply_filter_sync(
            "filter.tool.args",
            {"tool_name": tool_name, "args": args},
            {"phone": sender},
        )
        if filtered is None:
            executed.append({"tool": tool_name, "args": args, "skipped": True})
            return ""
        name = filtered.get("tool_name", tool_name)
        args = filtered.get("args", args)

        _t0 = time.monotonic()
        emit_with_filter_sync("tool.before", {
            "phone": sender, "tool_name": name, "args": args, "ts": time.time(),
        })
        feedback = handler._dispatch_tool(contact, name, args)
        emit_with_filter_sync("tool.after", {
            "phone": sender, "tool_name": name, "args": args,
            "result": feedback, "error": None,
            "latency_ms": int((time.monotonic() - _t0) * 1000), "ts": time.time(),
        })
        if feedback is not None:
            fr = apply_filter_sync(
                "filter.tool.result", feedback,
                {"phone": sender, "tool_name": name},
            )
            feedback = "" if fr is None else fr

        executed.append({"tool": name, "args": args})
        track_step("tool_executed", {"tool": name, "args": args})
        logger.info("Tool call for %s: %s(%s)", sender, name, args)
        return feedback or "Informações salvas com sucesso."

    return entrypoint


def build_functions(handler, contact, sender, active_tools, executed, *, is_async):
    """Wrap each active tool schema into an AGNO Function.

    ``active_tools`` is the post-``filter.llm.tools`` list of OpenAI tool
    schemas. ``executed`` is the per-request sink that collects what actually
    ran (used to build ProcessResult and detect ``save_contact_info``).
    """
    make = _make_async_entrypoint if is_async else _make_sync_entrypoint
    functions: dict[str, Function] = {}
    for schema in active_tools:
        fn = schema.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        functions[name] = Function(
            name=name,
            description=fn.get("description", ""),
            parameters=params,
            entrypoint=make(handler, contact, sender, name, executed),
            skip_entrypoint_processing=True,
        )
    return functions


# --------------------------------------------------------------------------- #
# Agent / Team construction
# --------------------------------------------------------------------------- #
# Context builders shared by Agent and Team. WhatsBot owns the system prompt
# and history, so AGNO must not prepend/resolve anything of its own.
_CONTEXT_OFF = dict(
    add_history_to_context=False,
    resolve_in_context=False,
    add_name_to_context=False,
    add_datetime_to_context=False,
    add_location_to_context=False,
    markdown=False,
    store_history_messages=False,
    telemetry=False,
    retries=0,
)
# ``build_context`` is Agent-only (Team has no such kwarg).
_AGENT_CONTEXT_OFF = dict(_CONTEXT_OFF, build_context=False)


def _resolve_team_mode(value: str) -> TeamMode:
    try:
        return TeamMode(str(value or "coordinate"))
    except ValueError:
        logger.warning("Unknown agent_team_mode %r; falling back to coordinate", value)
        return TeamMode.coordinate


def _select_member_functions(functions: dict[str, Function], spec_tools) -> list[Function]:
    """Pick the Function subset for a team member from its ``tools`` config."""
    if spec_tools in (None, "all", "*"):
        return list(functions.values())
    if isinstance(spec_tools, str):
        spec_tools = [spec_tools]
    return [functions[t] for t in spec_tools if t in functions]


def _build_single_agent(handler, system_prompt, functions, model_config=None):
    return Agent(
        model=build_model(handler, model_config=model_config),
        system_message=system_prompt,
        tools=list(functions.values()) or None,
        **_AGENT_CONTEXT_OFF,
    )


def _build_team(handler, system_prompt, functions):
    members: list[Agent] = []
    for spec in (handler.agents or []):
        if not isinstance(spec, dict):
            continue
        member_fns = _select_member_functions(functions, spec.get("tools", "all"))
        members.append(Agent(
            name=spec.get("name") or spec.get("id") or "Especialista",
            role=spec.get("role") or spec.get("description") or "",
            instructions=spec.get("instructions") or None,
            model=build_model(handler, spec.get("model") or None),
            tools=member_fns or None,
            **_AGENT_CONTEXT_OFF,
        ))
    if not members:
        # Multi-agent enabled but no specialists configured — degrade to a
        # single agent rather than constructing an empty (invalid) team.
        logger.warning("multi_agent_enabled but no agents configured; using single agent")
        return _build_single_agent(handler, system_prompt, functions)

    return Team(
        members=members,
        model=build_model(handler),
        mode=_resolve_team_mode(handler.agent_team_mode),
        system_message=system_prompt,
        # The coordinator can also reach the shared tools directly (e.g. in
        # route mode where a member responds) — keeps behaviour predictable.
        tools=list(functions.values()) or None,
        **_CONTEXT_OFF,
    )


def build_runner(handler, system_prompt, functions, model_config=None):
    """Return a ready-to-run Agent or Team based on handler config.

    ``model_config`` (config-in-DB) applies to the single-agent path only; the
    dormant multi-agent Team keeps using ``handler.model`` / per-member models.
    """
    if getattr(handler, "multi_agent_enabled", False):
        return _build_team(handler, system_prompt, functions)
    return _build_single_agent(handler, system_prompt, functions, model_config=model_config)


# --------------------------------------------------------------------------- #
# Run result extraction
# --------------------------------------------------------------------------- #
def _extract_usage(run_output) -> dict | None:
    metrics = getattr(run_output, "metrics", None)
    if not metrics:
        return None
    pt = getattr(metrics, "input_tokens", 0) or 0
    ct = getattr(metrics, "output_tokens", 0) or 0
    tt = getattr(metrics, "total_tokens", 0) or (pt + ct)
    if not (pt or ct or tt):
        return None
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


def _extract_reply(run_output) -> str:
    """Return the agent's final user-facing reply.

    Prefer the *last* assistant message that carries no tool calls. AGNO's
    aggregated ``run_output.content`` can concatenate a pre-tool "chatter" turn
    with the post-tool final turn when the model emits text alongside a tool
    call — for WhatsApp we only want the final message, and with split_messages
    on (JSON array output) the concatenation would otherwise corrupt the JSON.
    """
    messages = getattr(run_output, "messages", None) or []
    for m in reversed(messages):
        if getattr(m, "role", None) != "assistant":
            continue
        if getattr(m, "tool_calls", None):
            continue
        content = getattr(m, "content", None)
        if content:
            return content.strip() if isinstance(content, str) else str(content).strip()

    content = getattr(run_output, "content", None)
    if content is None:
        return ""
    if not isinstance(content, str):
        # Structured output is not used by WhatsBot's text agent; stringify
        # defensively so a misconfigured model never crashes the pipeline.
        content = str(content)
    return content.strip()


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
async def run_async(handler, contact, sender, messages, active_tools,
                    model_config=None) -> EngineResult:
    """Run the AGNO agent/team for one message (cancellable async path)."""
    system_prompt, convo = split_messages(messages)
    executed: list[dict] = []
    functions = build_functions(handler, contact, sender, active_tools, executed, is_async=True)
    runner = build_runner(handler, system_prompt, functions, model_config=model_config)
    model_id = (model_config or {}).get("model") or handler.model

    track_step("llm_request", {
        "model": model_id,
        "engine": "agno",
        "multi_agent": bool(getattr(handler, "multi_agent_enabled", False)),
        "context_messages": len(convo),
        "tools": list(functions.keys()),
    })
    run_output = await runner.arun(input=convo)

    reply = _extract_reply(run_output)
    usage = _extract_usage(run_output)
    track_step("llm_response", {
        "model": model_id, "engine": "agno",
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "has_tool_calls": bool(executed),
    })
    return EngineResult(reply=reply, executed_tools=executed, usage=usage)


def run_sync(handler, contact, sender, messages, active_tools,
             model_config=None) -> EngineResult:
    """Run the AGNO agent/team for one message (synchronous path)."""
    system_prompt, convo = split_messages(messages)
    executed: list[dict] = []
    functions = build_functions(handler, contact, sender, active_tools, executed, is_async=False)
    runner = build_runner(handler, system_prompt, functions, model_config=model_config)
    model_id = (model_config or {}).get("model") or handler.model

    track_step("llm_request", {
        "model": model_id,
        "engine": "agno",
        "multi_agent": bool(getattr(handler, "multi_agent_enabled", False)),
        "context_messages": len(convo),
        "tools": list(functions.keys()),
    })
    run_output = runner.run(input=convo)

    reply = _extract_reply(run_output)
    usage = _extract_usage(run_output)
    track_step("llm_response", {
        "model": model_id, "engine": "agno",
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "has_tool_calls": bool(executed),
    })
    return EngineResult(reply=reply, executed_tools=executed, usage=usage)
