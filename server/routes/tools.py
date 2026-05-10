"""Tool management endpoints — list and override LLM tools (core + plugin)."""

import asyncio
import logging

from fastapi import Request

from db.repositories import tool_override_repo
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager

    @app.get("/api/tools")
    async def list_tools():
        """Return every registered tool with override state merged."""
        items = await asyncio.to_thread(agent_handler.list_tools)
        return _ok({"tools": items})

    @app.put("/api/tools/{name}")
    async def update_tool(name: str, request: Request):
        """Apply a partial override. ``description=null`` clears the override."""
        if name not in agent_handler.known_tool_names():
            return _err(f"Tool '{name}' não encontrada.", 404)
        body = await request.json()

        # Only forward keys that were actually present in the body. The repo's
        # default sentinel (its own _UNSET) keeps any key we don't pass.
        update_kwargs: dict = {}
        if "enabled" in body:
            update_kwargs["enabled"] = bool(body["enabled"])
        if "description" in body:
            raw = body["description"]
            if raw is None:
                update_kwargs["description"] = None
            else:
                txt = str(raw).strip()
                update_kwargs["description"] = txt if txt else None
        if "display_label" in body:
            raw = body["display_label"]
            if raw is None:
                update_kwargs["display_label"] = None
            else:
                txt = str(raw).strip()
                update_kwargs["display_label"] = txt if txt else None

        updated = await asyncio.to_thread(
            lambda: tool_override_repo.upsert(name, **update_kwargs)
        )
        if updated is None:
            return _err(f"Tool '{name}' não encontrada.", 404)
        await asyncio.to_thread(agent_handler.refresh_tool_overrides)
        await ws_manager.broadcast("tools_changed", {"name": name})
        items = await asyncio.to_thread(agent_handler.list_tools)
        match = next((t for t in items if t["name"] == name), None)
        return _ok(match or updated)
