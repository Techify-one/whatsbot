"""REST do Event Logger — expõe o buffer em memória pra tela do plugin."""

from fastapi import APIRouter

from whatsbot_plugins.event_logger import events

router = APIRouter()


@router.get("/recent")
async def list_recent(limit: int = events.BUFFER_SIZE):
    limit = max(1, min(limit, events.BUFFER_SIZE))
    return {"ok": True, "data": events.snapshot(limit)}


@router.post("/clear")
async def clear_buffer():
    events.clear()
    return {"ok": True, "data": events.snapshot()}
