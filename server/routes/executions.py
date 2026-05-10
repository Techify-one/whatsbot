"""Execution tracking endpoints."""

import asyncio

from db.repositories import execution_repo
from server.helpers import _ok, _err


def register_routes(app, deps):

    @app.get("/api/executions")
    async def list_executions(
        limit: int = 50,
        offset: int = 0,
        phone: str | None = None,
        status: str | None = None,
    ):
        """List executions with pagination."""
        items = await asyncio.to_thread(
            execution_repo.list_executions, limit, offset, phone, status
        )
        total = await asyncio.to_thread(execution_repo.count, phone, status)
        return _ok({"items": items, "total": total})

    @app.get("/api/executions/{execution_id}")
    async def get_execution(execution_id: int):
        """Get execution details with all steps."""
        result = await asyncio.to_thread(execution_repo.get_by_id, execution_id)
        if not result:
            return _err("Execução não encontrada.", status=404)
        return _ok(result)

    @app.delete("/api/executions")
    async def cleanup_executions(days: int = 30):
        """Delete executions older than N days."""
        import time
        cutoff = time.time() - (days * 86400)
        deleted = await asyncio.to_thread(execution_repo.delete_older_than, cutoff)
        return _ok({"deleted": deleted})
