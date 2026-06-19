"""REST endpoints for the DB-driven AI engine (config-in-DB + code-in-DB).

CRUD over ``ai_agents`` / ``ai_prompts`` / ``ai_variables`` / ``ai_tools``.

Agent / prompt / variable edits take effect on the **next message** with no
restart — ``agent.agent_factory.build_for_contact`` reads the DB per request.
Tool edits (code-in-DB) require a process restart so the installer re-runs:
mutating a tool schedules one, and ``POST /api/ai/restart`` triggers it manually.

Every mutation emits ``ai.config.changed`` so cache layers / multi-worker
deployments can invalidate.
"""

import asyncio
import logging
import time

from server.helpers import _ok, _err
from db.repositories import agent_repo, prompt_repo, variable_repo, tool_repo
from plugins.events import emit as emit_event
from plugins.restart import schedule_restart

logger = logging.getLogger(__name__)


def _emit_changed(kind: str, key: str) -> None:
    try:
        emit_event("ai.config.changed", {"kind": kind, "key": key, "ts": time.time()})
    except Exception:
        pass


def register_routes(app, deps):

    # ── Agents ──────────────────────────────────────────────────────────
    @app.get("/api/ai/agents")
    async def list_agents():
        rows = await asyncio.to_thread(agent_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/agents/{agent_key}")
    async def get_agent(agent_key: str):
        row = await asyncio.to_thread(agent_repo.get, agent_key)
        if not row:
            return _err("Agente não encontrado.", status=404)
        return _ok(row)

    @app.put("/api/ai/agents/{agent_key}")
    async def save_agent(agent_key: str, body: dict):
        model_config = body.get("model_config", {})
        if not isinstance(model_config, dict):
            return _err("model_config deve ser um objeto.")
        tool_names = body.get("tool_names")
        if tool_names is not None and not isinstance(tool_names, list):
            return _err("tool_names deve ser uma lista ou null.")
        row = await asyncio.to_thread(
            agent_repo.save, agent_key,
            display_name=body.get("display_name", ""),
            prompt_key=body.get("prompt_key", ""),
            model_config=model_config,
            tool_names=tool_names,
            enabled=bool(body.get("enabled", True)),
        )
        _emit_changed("agent", agent_key)
        logger.info("AI agent saved: %s (v%s)", agent_key, row.get("version"))
        return _ok(row)

    # ── Prompts ─────────────────────────────────────────────────────────
    @app.get("/api/ai/prompts")
    async def list_prompts():
        rows = await asyncio.to_thread(prompt_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/prompts/{prompt_key}")
    async def get_prompt(prompt_key: str):
        row = await asyncio.to_thread(prompt_repo.get, prompt_key)
        if not row:
            return _err("Prompt não encontrado.", status=404)
        return _ok(row)

    @app.put("/api/ai/prompts/{prompt_key}")
    async def save_prompt(prompt_key: str, body: dict):
        row = await asyncio.to_thread(prompt_repo.save, prompt_key, body.get("body", ""))
        _emit_changed("prompt", prompt_key)
        logger.info("AI prompt saved: %s (v%s)", prompt_key, row.get("version"))
        return _ok(row)

    # ── Variables ───────────────────────────────────────────────────────
    @app.get("/api/ai/variables")
    async def list_variables():
        rows = await asyncio.to_thread(variable_repo.list_all)
        return _ok(rows)

    @app.put("/api/ai/variables/{name}")
    async def save_variable(name: str, body: dict):
        row = await asyncio.to_thread(
            variable_repo.save, name, body.get("value", ""), body.get("category", "")
        )
        _emit_changed("variable", name)
        return _ok(row)

    @app.delete("/api/ai/variables/{name}")
    async def delete_variable(name: str):
        deleted = await asyncio.to_thread(variable_repo.delete, name)
        if not deleted:
            return _err("Variável não encontrada.", status=404)
        _emit_changed("variable", name)
        return _ok({"deleted": True})

    # ── Tools (code-in-DB) ──────────────────────────────────────────────
    @app.get("/api/ai/tools")
    async def list_ai_tools():
        rows = await asyncio.to_thread(tool_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/tools/{name}")
    async def get_ai_tool(name: str):
        row = await asyncio.to_thread(tool_repo.get, name)
        if not row:
            return _err("Tool não encontrada.", status=404)
        return _ok(row)

    @app.put("/api/ai/tools/{name}")
    async def save_ai_tool(name: str, body: dict):
        dependencies = body.get("dependencies", [])
        if dependencies is not None and not isinstance(dependencies, list):
            return _err("dependencies deve ser uma lista.")
        row = await asyncio.to_thread(
            tool_repo.save, name,
            description=body.get("description", ""),
            code=body.get("code", ""),
            dependencies=dependencies or [],
            enabled=bool(body.get("enabled", True)),
        )
        _emit_changed("tool", name)
        # Code-in-DB needs a process restart so the installer re-materialises,
        # re-imports and re-registers the tool. Opt out with restart=false.
        if body.get("restart", True):
            schedule_restart(f"ai_tool saved: {name}")
        logger.info("AI tool saved: %s (v%s)", name, row.get("version"))
        return _ok(row)

    @app.delete("/api/ai/tools/{name}")
    async def delete_ai_tool(name: str):
        deleted = await asyncio.to_thread(tool_repo.delete, name)
        if not deleted:
            return _err("Tool não encontrada.", status=404)
        _emit_changed("tool", name)
        schedule_restart(f"ai_tool deleted: {name}")
        return _ok({"deleted": True})

    @app.post("/api/ai/restart")
    async def restart_engine():
        """Restart the server so code-in-DB tool changes take effect."""
        schedule_restart("ai engine manual restart")
        return _ok({"restarting": True})
