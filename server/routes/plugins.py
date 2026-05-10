"""Plugin management endpoints (list, manifest, enable/disable, settings, import/export)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import StreamingResponse

from db.repositories import config_repo, plugin_repo, tool_override_repo
from plugins.manifest import (
    WHATSBOT_API_VERSION,
    find_manifest_file,
    load_manifest,
)
from plugins.restart import schedule_restart
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)


_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def register_routes(app, deps):
    plugins_dir: Path = deps.plugins_dir
    registry = deps.plugins_registry

    @app.get("/api/plugins")
    async def list_plugins():
        """List every plugin known on disk, with DB metadata merged in."""
        rows = await asyncio.to_thread(plugin_repo.list_all)
        by_id = {r["id"]: r for r in rows}
        items = []
        # walk filesystem so we can also report folders the loader rejected
        for child in sorted(plugins_dir.iterdir()) if plugins_dir.is_dir() else []:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if find_manifest_file(child) is None:
                continue
            manifest_view: dict | None = None
            try:
                manifest_view = load_manifest(child).to_public_dict()
            except Exception as e:
                manifest_view = {"id": child.name, "name": child.name, "error": str(e)}
            db_row = by_id.get(child.name, {})
            loaded = registry.loaded.get(child.name) if registry else None
            items.append({
                **manifest_view,
                "enabled": bool(db_row.get("enabled")),
                "load_error": db_row.get("load_error"),
                "loaded": loaded is not None,
                "installed_at": db_row.get("installed_at"),
                "updated_at": db_row.get("updated_at"),
            })
        return _ok({
            "plugins": items,
            "whatsbot_api_version": WHATSBOT_API_VERSION,
        })

    @app.get("/api/plugins/manifest")
    async def public_manifest():
        """Return only loaded plugins, in the shape the frontend uses to mount screens."""
        out = []
        for loaded in (registry.loaded.values() if registry else []):
            out.append({
                "id": loaded.id,
                "name": loaded.manifest.name,
                "version": loaded.manifest.version,
                "screens": [
                    {**s, "pluginId": loaded.id} for s in loaded.manifest.screens
                ],
            })
        return _ok({"plugins": out})

    @app.post("/api/plugins/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str):
        if not _PLUGIN_ID_RE.match(plugin_id):
            return _err("plugin id inválido")
        ok = await asyncio.to_thread(plugin_repo.set_enabled, plugin_id, True)
        if not ok:
            return _err("plugin não encontrado", 404)
        schedule_restart(reason=f"plugin {plugin_id} enabled")
        return _ok({"id": plugin_id, "enabled": True, "restarting": True})

    @app.post("/api/plugins/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str):
        if not _PLUGIN_ID_RE.match(plugin_id):
            return _err("plugin id inválido")
        ok = await asyncio.to_thread(plugin_repo.set_enabled, plugin_id, False)
        if not ok:
            return _err("plugin não encontrado", 404)
        schedule_restart(reason=f"plugin {plugin_id} disabled")
        return _ok({"id": plugin_id, "enabled": False, "restarting": True})

    @app.delete("/api/plugins/{plugin_id}")
    async def delete_plugin(plugin_id: str):
        if not _PLUGIN_ID_RE.match(plugin_id):
            return _err("plugin id inválido")

        def _do_delete() -> dict:
            target = plugins_dir / plugin_id
            had_dir = target.is_dir()
            if had_dir:
                shutil.rmtree(target)
            dropped = plugin_repo.drop_plugin_tables(plugin_id)
            plugin_repo.delete(plugin_id)
            config_repo.delete_prefix(f"plugin.{plugin_id}.")
            overrides_removed = tool_override_repo.delete_for_plugin(plugin_id)
            return {
                "folder_removed": had_dir,
                "tables_dropped": dropped,
                "tool_overrides_removed": overrides_removed,
            }

        result = await asyncio.to_thread(_do_delete)
        schedule_restart(reason=f"plugin {plugin_id} deleted")
        return _ok({"id": plugin_id, **result, "restarting": True})

    # ── Settings ─────────────────────────────────────────────────────

    @app.get("/api/plugins/{plugin_id}/settings")
    async def get_plugin_settings(plugin_id: str):
        loaded = registry.loaded.get(plugin_id) if registry else None
        if not loaded or not loaded.settings_cls:
            return _err("plugin sem settings declaradas", 404)
        schema = loaded.settings_cls.model_json_schema()
        # current values: read namespaced keys; fall back to defaults
        prefix = f"plugin.{plugin_id}."
        all_cfg = await asyncio.to_thread(config_repo.get_all)
        values: dict = {}
        defaults = loaded.settings_cls().model_dump()
        for field, default_val in defaults.items():
            values[field] = all_cfg.get(prefix + field, default_val)
        return _ok({"schema": schema, "values": values})

    @app.put("/api/plugins/{plugin_id}/settings")
    async def update_plugin_settings(plugin_id: str, request: Request):
        loaded = registry.loaded.get(plugin_id) if registry else None
        if not loaded or not loaded.settings_cls:
            return _err("plugin sem settings declaradas", 404)
        body = await request.json()
        try:
            validated = loaded.settings_cls(**(body or {}))
        except Exception as e:
            return _err(f"valores inválidos: {e}")
        prefix = f"plugin.{plugin_id}."
        kv = {prefix + k: v for k, v in validated.model_dump().items()}
        await asyncio.to_thread(config_repo.set_many, kv)
        return _ok({"id": plugin_id, "values": validated.model_dump()})

    # ── Import / Export ───────────────────────────────────────────────

    @app.get("/api/plugins/{plugin_id}/export")
    async def export_plugin(plugin_id: str):
        if not _PLUGIN_ID_RE.match(plugin_id):
            return _err("plugin id inválido")
        target = plugins_dir / plugin_id
        if not target.is_dir():
            return _err("plugin não encontrado", 404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in target.rglob("*"):
                if path.is_dir():
                    continue
                if "__pycache__" in path.parts:
                    continue
                if path.suffix in (".db", ".db-wal", ".db-shm"):
                    continue
                arc = path.relative_to(target).as_posix()
                zf.write(path, arc)
        buf.seek(0)
        filename = f"{plugin_id}-plugin.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
        )

    @app.post("/api/plugins/import")
    async def import_plugin(file: UploadFile):
        contents = await file.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(contents))
        except zipfile.BadZipFile:
            return _err("arquivo .zip inválido")

        # find manifest at root
        names = zf.namelist()
        manifest_name = None
        for candidate in ("plugin.yaml", "plugin.yml", "plugin.json"):
            if candidate in names:
                manifest_name = candidate
                break
        if manifest_name is None:
            return _err("manifest (plugin.yaml/plugin.json) ausente na raiz do zip")
        try:
            manifest_text = zf.read(manifest_name).decode("utf-8")
        except Exception as e:
            return _err(f"falha ao ler manifest: {e}")

        # extract id without full validation (it'll be validated when loading)
        if manifest_name.endswith(".json"):
            try:
                meta = json.loads(manifest_text)
            except Exception as e:
                return _err(f"manifest JSON inválido: {e}")
        else:
            from plugins.manifest import _parse_yaml  # type: ignore
            try:
                meta = _parse_yaml(manifest_text) or {}
            except Exception as e:
                return _err(f"manifest YAML inválido: {e}")
        pid = meta.get("id") if isinstance(meta, dict) else None
        if not isinstance(pid, str) or not _PLUGIN_ID_RE.match(pid):
            return _err("manifest sem id válido")

        target = plugins_dir / pid
        if target.exists():
            return _err(f"plugin '{pid}' já instalado — desinstale antes")

        # path traversal protection
        for member in zf.infolist():
            name = member.filename
            if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                return _err(f"caminho perigoso no zip: {name}")

        target.mkdir(parents=True)
        try:
            zf.extractall(target)
        except Exception as e:
            shutil.rmtree(target, ignore_errors=True)
            return _err(f"falha ao extrair zip: {e}")

        version = str(meta.get("version") or "0.0.0") if isinstance(meta, dict) else "0.0.0"
        await asyncio.to_thread(plugin_repo.upsert, pid, version, enabled=False)
        return _ok({"id": pid, "version": version, "enabled": False})

    @app.post("/api/plugins/restart")
    async def restart_server():
        schedule_restart(reason="manual restart from /api/plugins/restart")
        return _ok({"restarting": True})
