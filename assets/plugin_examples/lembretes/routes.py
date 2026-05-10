"""REST endpoints do plugin Lembretes (mountados em /api/plugins/lembretes)."""

from fastapi import APIRouter

from db.connection import get_db
from plugins.context import broadcast

router = APIRouter()


@router.get("/items")
async def list_items(limit: int = 100):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, phone, name, text, ts FROM plugin_lembretes_items "
        "ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.delete("/items/{rid}")
async def delete_item(rid: int):
    conn = get_db()
    conn.execute("DELETE FROM plugin_lembretes_items WHERE id = ?", (rid,))
    conn.commit()
    broadcast("plugin_lembretes_deleted", {"id": rid})
    return {"ok": True}
