"""Migrate existing JSON files to the database.

Can be run standalone: python -m db.migrate_json
Or called from main.py on first boot.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sqlalchemy import func, insert, select

from db.engine import get_engine
from db.tables import contact_tags, contacts, messages, observations, tags, unread_msg_ids
from db.tables import usage as usage_t
from db.upsert import upsert, upsert_ignore

logger = logging.getLogger(__name__)


def _count_contacts() -> int:
    with get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(contacts)).scalar() or 0


def needs_migration(data_dir: Path) -> bool:
    """Check if migration is needed: DB is empty and JSON files exist."""
    if _count_contacts() > 0:
        return False
    contacts_dir = data_dir / "contacts"
    if not contacts_dir.exists():
        return False
    return any(
        f.suffix == ".json" and not f.stem.startswith("_")
        for f in contacts_dir.iterdir()
    )


def migrate(data_dir: Path) -> None:
    """Migrate all JSON data to the bound database."""
    if _count_contacts() > 0:
        logger.info("Database already has data, skipping migration")
        return

    logger.info("Starting JSON → database migration...")
    start = time.time()

    contacts_migrated = 0
    messages_migrated = 0
    usage_migrated = 0
    tags_migrated = 0

    config_path = data_dir / "config.json"
    if not config_path.exists():
        config_path = data_dir / "storages" / "config.json"

    with get_engine().begin() as conn:
        # ── config.json ───────────────────────────────────────────
        if config_path.exists():
            try:
                config_data = json.loads(config_path.read_text(encoding="utf-8"))
                from db.tables import config as config_t
                rows = [
                    {"key": k, "value": json.dumps(v, ensure_ascii=False)}
                    for k, v in config_data.items()
                ]
                for row in rows:
                    conn.execute(upsert(
                        config_t, row,
                        conflict_cols=["key"], update_cols=["value"],
                    ))
                logger.info("Migrated %d config keys", len(config_data))
            except Exception as e:
                logger.error("Failed to migrate config.json: %s", e)

        # ── _tags.json ────────────────────────────────────────────
        contacts_dir = data_dir / "contacts"
        tags_file = contacts_dir / "_tags.json"
        tag_name_to_id: dict[str, int] = {}
        if tags_file.exists():
            try:
                tags_data = json.loads(tags_file.read_text(encoding="utf-8"))
                for name, info in tags_data.items():
                    color = info.get("color", "#6b7280")
                    result = conn.execute(insert(tags).values(name=name, color=color))
                    tag_name_to_id[name] = result.inserted_primary_key[0]
                    tags_migrated += 1
                logger.info("Migrated %d tags", tags_migrated)
            except Exception as e:
                logger.error("Failed to migrate _tags.json: %s", e)

        # ── per-contact files ────────────────────────────────────
        if contacts_dir.exists():
            for f in sorted(contacts_dir.glob("*.json")):
                if f.stem.startswith("_"):
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    phone = data.get("phone", f.stem)
                    info = data.get("info", {})
                    old_id = data.get("id")

                    base_values = {
                        "phone": phone,
                        "name": info.get("name", ""),
                        "email": info.get("email", ""),
                        "profession": info.get("profession", ""),
                        "company": info.get("company", ""),
                        "address": info.get("address", ""),
                        "ai_enabled": 1 if data.get("ai_enabled", True) else 0,
                        "is_group": 1 if data.get("is_group", False) else 0,
                        "group_name": data.get("group_name", ""),
                        "is_archived": 1 if data.get("is_archived", False) else 0,
                        "unread_count": data.get("unread_count", 0),
                        "unread_ai_count": data.get("unread_ai_count", 0),
                        "created_at": data.get("created_at", time.time()),
                        "updated_at": data.get("updated_at", time.time()),
                    }

                    if old_id is not None:
                        conn.execute(insert(contacts).values(id=old_id, **base_values))
                        contact_id = old_id
                    else:
                        result = conn.execute(insert(contacts).values(**base_values))
                        contact_id = result.inserted_primary_key[0]

                    obs_list = info.get("observations", [])
                    if obs_list:
                        now = time.time()
                        conn.execute(insert(observations), [
                            {"contact_id": contact_id, "text": obs, "created_at": now}
                            for obs in obs_list if obs
                        ])

                    msgs = data.get("messages", [])
                    if msgs:
                        conn.execute(insert(messages), [
                            {
                                "contact_id": contact_id,
                                "role": m.get("role", "user"),
                                "content": m.get("content", ""),
                                "ts": m.get("ts", 0),
                                "media_type": m.get("media_type"),
                                "media_path": m.get("media_path"),
                                "status": m.get("status"),
                                "msg_id": m.get("msg_id"),
                            }
                            for m in msgs
                        ])
                        messages_migrated += len(msgs)

                    usage_records = data.get("usage", [])
                    if usage_records:
                        conn.execute(insert(usage_t), [
                            {
                                "contact_id": contact_id,
                                "call_type": u.get("call_type", "text"),
                                "model": u.get("model", ""),
                                "prompt_tokens": u.get("prompt_tokens", 0),
                                "completion_tokens": u.get("completion_tokens", 0),
                                "total_tokens": u.get("total_tokens", 0),
                                "cost_usd": u.get("cost_usd", 0.0),
                                "ts": u.get("ts", 0),
                            }
                            for u in usage_records
                        ])
                        usage_migrated += len(usage_records)

                    contact_tag_names = data.get("tags", [])
                    for tag_name in contact_tag_names:
                        tag_id = tag_name_to_id.get(tag_name)
                        if tag_id:
                            conn.execute(upsert_ignore(
                                contact_tags,
                                {"contact_id": contact_id, "tag_id": tag_id},
                                conflict_cols=["contact_id", "tag_id"],
                            ))

                    unread_ids = data.get("unread_msg_ids", [])
                    if unread_ids:
                        conn.execute(insert(unread_msg_ids), [
                            {"contact_id": contact_id, "msg_id": mid} for mid in unread_ids
                        ])

                    contacts_migrated += 1
                except Exception as e:
                    logger.error("Failed to migrate contact %s: %s", f.name, e)

    elapsed = time.time() - start
    logger.info(
        "Migration complete in %.1fs: %d contacts, %d messages, %d usage records, %d tags",
        elapsed, contacts_migrated, messages_migrated, usage_migrated, tags_migrated,
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    data_dir = Path(__file__).resolve().parent.parent
    from db.connection import init_db
    storages_dir = data_dir / "storages"
    storages_dir.mkdir(exist_ok=True)
    init_db(storages_dir / "whatsbot.db")
    migrate(data_dir)
