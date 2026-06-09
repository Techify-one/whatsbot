"""REST endpoints do plugin Sons de Notificação (/api/plugins/custom_sounds).

Guarda os áudios enviados pelo usuário (base64 no banco, com prefixo de tabela
``plugin_custom_sounds_``). A tela do plugin lê o som escolhido e grava o
data URL em ``localStorage['whatsbot_notif_sound_custom']`` — chave que o core
(``web/static/js/utils/notifications.js``) toca nas notificações.
"""

import base64
import time

from fastapi import APIRouter, File, UploadFile
from sqlalchemy import text

from plugins.context import make_plugin_db

router = APIRouter()

MAX_BYTES = 1024 * 1024  # 1 MB — pequeno o suficiente pra caber num data URL no localStorage


@router.get("/sounds")
async def list_sounds():
    """List uploaded sounds (metadata only — no base64 payload)."""
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            "SELECT id, name, mimetype, ts FROM plugin_custom_sounds_sounds "
            "ORDER BY ts DESC"
        )).mappings().all()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.get("/sounds/{sid}")
async def get_sound(sid: int):
    """Return a single sound as a playable ``data:`` URL."""
    with make_plugin_db() as conn:
        row = conn.execute(text(
            "SELECT id, name, mimetype, data FROM plugin_custom_sounds_sounds "
            "WHERE id = :id"
        ), {"id": sid}).mappings().first()
    if not row:
        return {"ok": False, "error": "Som não encontrado."}
    data_url = f"data:{row['mimetype']};base64,{row['data']}"
    return {"ok": True, "data": {
        "id": row["id"], "name": row["name"],
        "mimetype": row["mimetype"], "data_url": data_url,
    }}


@router.post("/sounds")
async def upload_sound(file: UploadFile = File(...)):
    """Upload a new audio file (stored base64-encoded)."""
    raw = await file.read()
    if not raw:
        return {"ok": False, "error": "Arquivo vazio."}
    if len(raw) > MAX_BYTES:
        return {"ok": False, "error": "Arquivo muito grande (máx. 1 MB)."}
    mimetype = (file.content_type or "audio/mpeg").lower()
    if not mimetype.startswith("audio/"):
        return {"ok": False, "error": "Envie um arquivo de áudio (.mp3, .ogg, .wav...)."}
    name = (file.filename or "som").rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:80] or "som"
    b64 = base64.b64encode(raw).decode("ascii")
    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_custom_sounds_sounds (name, mimetype, data, ts) "
            "VALUES (:name, :mimetype, :data, :ts)"
        ), {"name": name, "mimetype": mimetype, "data": b64, "ts": int(time.time())})
    return {"ok": True}


@router.patch("/sounds/{sid}")
async def rename_sound(sid: int, body: dict):
    """Rename a sound (only the display name; the audio data is untouched)."""
    name = (body.get("name") or "").strip()[:80]
    if not name:
        return {"ok": False, "error": "Nome inválido."}
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_custom_sounds_sounds SET name = :name WHERE id = :id"),
            {"name": name, "id": sid},
        )
    return {"ok": True, "data": {"id": sid, "name": name}}


@router.delete("/sounds/{sid}")
async def delete_sound(sid: int):
    """Delete a sound from the library."""
    with make_plugin_db() as conn:
        conn.execute(
            text("DELETE FROM plugin_custom_sounds_sounds WHERE id = :id"),
            {"id": sid},
        )
    return {"ok": True}
