"""REST endpoints do plugin Lembretes (mountados em /api/plugins/lembretes)."""

from fastapi import APIRouter
from sqlalchemy import text

from plugins.context import broadcast, make_plugin_db

router = APIRouter()


@router.get("/items")
async def list_items(limit: int = 100):
    with make_plugin_db() as conn:
        rows = conn.execute(
            text(
                "SELECT id, phone, name, text, ts FROM plugin_lembretes_items "
                "ORDER BY ts DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.delete("/items/{rid}")
async def delete_item(rid: int):
    with make_plugin_db() as conn:
        conn.execute(
            text("DELETE FROM plugin_lembretes_items WHERE id = :id"),
            {"id": rid},
        )
    broadcast("plugin_lembretes_deleted", {"id": rid})
    return {"ok": True}
