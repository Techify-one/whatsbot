"""Admin endpoints (DB migration, etc.).

The migration to Postgres is intentionally a manual operation triggered from
the Settings → Banco de dados screen. It runs in a background thread so the
HTTP request returns immediately and the frontend tracks progress via
WebSocket events (``db_migration_progress``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from fastapi import Request

from db import engine as engine_module
from db.migration_postgres import (
    MigrationProgress,
    migrate_sqlite_to_postgres,
)
from plugins.restart import schedule_restart
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)


_migration_lock = threading.Lock()
_migration_state: dict = {"running": False, "last": None}


def register_routes(app, deps):
    ws_manager = deps.ws_manager
    settings = deps.settings

    @app.get("/api/admin/database")
    async def database_info():
        """Return the current DB URL (redacted) plus dialect."""
        url = engine_module.get_database_url()
        return _ok({
            "dialect": "postgres" if engine_module.is_postgres() else "sqlite",
            "url_redacted": _redact(url),
            "sqlite_path": str(engine_module.get_sqlite_path()) if engine_module.is_sqlite() else None,
            "config_file": str(settings.data_dir / "storages" / "database.json"),
        })

    @app.post("/api/admin/migrate-to-postgres")
    async def migrate_to_postgres(request: Request):
        """Kick off a SQLite → Postgres migration. Idempotent re: concurrent calls.

        Body: ``{"postgres_url": "...", "force_drop": false}``.
        When ``force_drop`` is true the target schema is wiped before migrating.
        """
        body = await request.json()
        target_url = (body or {}).get("postgres_url", "").strip()
        force_drop = bool((body or {}).get("force_drop", False))
        if not target_url:
            return _err("postgres_url é obrigatório.", status=400)

        # Normalize bare ``postgresql://`` / ``postgres://`` to the ``+psycopg``
        # driver — SQLAlchemy otherwise tries to load ``psycopg2`` which is
        # not in our dependencies and would crash the worker thread before any
        # progress event could be emitted.
        if target_url.startswith("postgresql://"):
            target_url = "postgresql+psycopg://" + target_url[len("postgresql://"):]
        elif target_url.startswith("postgres://"):
            target_url = "postgresql+psycopg://" + target_url[len("postgres://"):]

        if not engine_module.is_sqlite():
            return _err("A origem já não é SQLite — nada a migrar.", status=400)

        with _migration_lock:
            if _migration_state["running"]:
                return _err("Já existe uma migração em andamento.", status=409)
            _migration_state["running"] = True
            _migration_state["last"] = None

        loop = asyncio.get_running_loop()
        storages_dir = Path(settings.data_dir) / "storages"

        def _broadcast(progress: MigrationProgress) -> None:
            payload = {
                "stage": progress.stage,
                "table": progress.table,
                "rows_done": progress.rows_done,
                "rows_total": progress.rows_total,
                "tables_done": progress.tables_done,
                "tables_total": progress.tables_total,
                "message": progress.message,
                "error": progress.error,
                "per_table": progress.per_table,
                "conflicts": list(progress.conflicts),
            }
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast("db_migration_progress", payload), loop
            )

        def _runner() -> None:
            try:
                final = migrate_sqlite_to_postgres(
                    target_url, on_progress=_broadcast, force_drop=force_drop
                )
                _migration_state["last"] = {
                    "stage": final.stage,
                    "error": final.error,
                    "per_table": final.per_table,
                    "conflicts": list(final.conflicts),
                }
                if final.stage == "done":
                    try:
                        engine_module.write_url_to_file(storages_dir, target_url)
                        logger.info("Persisted new DATABASE_URL into storages/database.json")
                        schedule_restart(reason="DB migrated to Postgres")
                    except Exception as exc:
                        logger.exception("Failed to persist new URL after migration")
                        final.error = f"Migração concluiu mas não foi possível persistir a URL: {exc}"
                        _migration_state["last"]["error"] = final.error
            except Exception as exc:  # belt and suspenders — never leak from the thread
                logger.exception("Migration runner crashed outside the helper")
                _migration_state["last"] = {
                    "stage": "failed",
                    "error": f"Crash inesperado: {exc}",
                    "per_table": {},
                }
                _broadcast(MigrationProgress(stage="failed", error=str(exc)))
            finally:
                _migration_state["running"] = False

        threading.Thread(target=_runner, daemon=True).start()
        return _ok({"accepted": True})

    @app.get("/api/admin/migrate-to-postgres/status")
    async def migrate_status():
        """Polling fallback for clients that don't have a live WebSocket."""
        return _ok(_migration_state)


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            _, host_part = rest.split("@", 1)
            return f"{scheme}://***@{host_part}"
    return url
