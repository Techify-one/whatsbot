"""Tag CRUD and contact-tag assignment endpoints."""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import contact_repo
from plugins.events import emit as emit_event
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager

    @app.get("/api/tags")
    async def list_tags():
        """Return all global tags."""
        return _ok(agent_handler.tag_registry.all())

    @app.post("/api/tags")
    async def create_tag(request: Request):
        """Create a new global tag."""
        body = await request.json()
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "").strip()
        if not name:
            return _err("Nome da tag é obrigatório.")
        if len(name) > 30:
            return _err("Nome da tag deve ter no máximo 30 caracteres.")
        if not color:
            return _err("Cor da tag é obrigatória.")
        if not agent_handler.tag_registry.create(name, color):
            return _err(f"Tag '{name}' já existe.")
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        emit_event("tag.created", {"name": name, "color": color, "ts": time.time()})
        return _ok({"name": name, "color": color})

    @app.put("/api/tags/{name}")
    async def update_tag(name: str, request: Request):
        """Update a global tag (rename and/or change color)."""
        body = await request.json()
        new_name = (body.get("name") or "").strip() or None
        color = (body.get("color") or "").strip() or None
        if new_name and len(new_name) > 30:
            return _err("Nome da tag deve ter no máximo 30 caracteres.")
        if new_name and new_name != name and agent_handler.tag_registry.get(new_name):
            return _err(f"Tag '{new_name}' já existe.")
        if not agent_handler.tag_registry.update(name, new_name=new_name, color=color):
            return _err(f"Tag '{name}' não encontrada.", 404)
        # Tag rename in contact_tags is handled automatically by tag_repo.update()
        # which updates the tags table — contact_tags references tag_id, not name.
        # Update in-memory cache for loaded contacts
        if new_name and new_name != name:
            for contact in agent_handler._contacts.values():
                if name in contact.tags:
                    idx = contact.tags.index(name)
                    contact.tags[idx] = new_name
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        final_name = new_name or name
        tag_data = agent_handler.tag_registry.get(final_name)
        emit_event("tag.updated", {
            "old_name": name, "name": final_name,
            "color": tag_data["color"] if tag_data else color,
            "ts": time.time(),
        })
        return _ok({"name": final_name, "color": tag_data["color"] if tag_data else color})

    @app.delete("/api/tags/{name}")
    async def delete_tag(name: str):
        """Delete a global tag and remove it from all contacts."""
        if not agent_handler.tag_registry.delete(name):
            return _err(f"Tag '{name}' não encontrada.", 404)
        # tag_repo.delete() already handles DELETE FROM contact_tags
        # Update in-memory cache
        for contact in agent_handler._contacts.values():
            if name in contact.tags:
                contact.tags.remove(name)
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        emit_event("tag.deleted", {"name": name, "ts": time.time()})
        return _ok({"deleted": name})

    @app.put("/api/contacts/{phone}/tags")
    async def set_contact_tags(phone: str, request: Request):
        """Set the tags for a specific contact."""
        body = await request.json()
        tags = body.get("tags", [])
        if not isinstance(tags, list):
            return _err("Tags deve ser uma lista.")

        def _update():
            c = contact_repo.get_by_phone(phone)
            if c is None:
                return None
            contact = agent_handler._get_contact(phone)
            contact.set_tags(tags)
            return contact.tags

        result = await asyncio.to_thread(_update)
        if result is None:
            return _err("Contato não encontrado.", 404)
        await ws_manager.broadcast("contact_tags_updated", {"phone": phone, "tags": result})
        emit_event("contact.tagged", {
            "phone": phone, "tags": list(result), "ts": time.time(),
        })
        return _ok({"phone": phone, "tags": result})
