"""SQLAlchemy engine factory with dialect-aware setup.

The DATABASE_URL is resolved with the priority (first match wins):

1. Environment variable ``DATABASE_URL`` (covers Docker/Coolify deployments).
2. Per-app config file ``storages/database.json`` (managed by the Settings UI on
   Windows — see ``server.app`` migration endpoint).
3. Default SQLite file ``storages/whatsbot.db`` (zero-config fallback).

Only the engine is exposed; everything else (connection pooling, PRAGMAs,
dialect detection) is internal.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ── Module-level state ────────────────────────────────────────────────────

_engine: Optional[Engine] = None
_db_url: Optional[str] = None
_sqlite_path: Optional[Path] = None  # only set when dialect is sqlite


# ── URL resolution ────────────────────────────────────────────────────────

CONFIG_FILENAME = "database.json"


def _config_file_path(storages_dir: Path) -> Path:
    return storages_dir / CONFIG_FILENAME


def _read_url_from_file(storages_dir: Path) -> Optional[str]:
    """Return a DATABASE_URL declared in ``storages/database.json`` if any.

    Schema:
        {"url": "postgresql+psycopg://user:pass@host:5432/db"}
    Returns ``None`` if the file is missing or has no ``url`` key.
    """
    path = _config_file_path(storages_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        url = (data or {}).get("url", "").strip()
        return url or None
    except Exception as exc:
        logger.warning("Could not parse %s: %s — falling back to defaults", path, exc)
        return None


def resolve_database_url(storages_dir: Path) -> str:
    """Apply the ENV > file > sqlite-default precedence to pick a DB URL."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        logger.info("Using DATABASE_URL from environment")
        return url
    url = _read_url_from_file(storages_dir)
    if url:
        logger.info("Using DATABASE_URL from %s", _config_file_path(storages_dir))
        return url
    storages_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = storages_dir / "whatsbot.db"
    return f"sqlite:///{sqlite_path}"


def write_url_to_file(storages_dir: Path, url: str) -> None:
    """Persist a DATABASE_URL into the local config file."""
    storages_dir.mkdir(parents=True, exist_ok=True)
    path = _config_file_path(storages_dir)
    path.write_text(json.dumps({"url": url}, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Engine lifecycle ──────────────────────────────────────────────────────

def init_engine(url: str) -> Engine:
    """Create the module-level engine. Idempotent if called with the same URL."""
    global _engine, _db_url, _sqlite_path

    if _engine is not None and _db_url == url:
        return _engine

    if _engine is not None:
        _engine.dispose()
        _engine = None

    is_sqlite = url.startswith("sqlite")
    is_psycopg = url.startswith("postgresql+psycopg")
    connect_args: dict = {}

    if is_sqlite:
        # Match the legacy threading model: a single connection may be reused
        # across threads (FastAPI runs blocking calls via asyncio.to_thread).
        connect_args["check_same_thread"] = False
        # Cache the resolved file path for diagnostics / migrate-to-postgres.
        if url.startswith("sqlite:///"):
            _sqlite_path = Path(url.removeprefix("sqlite:///"))
        else:
            _sqlite_path = None
    else:
        _sqlite_path = None
        if is_psycopg:
            # Disable client-side prepared statements. Managed Postgres services
            # like Neon and Supabase expose a PgBouncer pooler in transaction
            # mode by default, which assigns a different backend per
            # transaction. psycopg3's prepared statement cache (default
            # ``prepare_threshold=5``) breaks under that routing: a name
            # registered on backend A is referenced from backend B and either
            # "does not exist" or collides. The cost of disabling prepared
            # statements is a small per-query overhead (re-parsing on the
            # server) — irrelevant at WhatsBot's scale and worth the
            # plug-and-play compatibility with any pooled endpoint.
            connect_args["prepare_threshold"] = None

    _engine = create_engine(
        url,
        future=True,
        connect_args=connect_args,
        # pool_pre_ping helps survive Postgres connection drops (idle timeouts).
        pool_pre_ping=not is_sqlite,
    )

    if is_sqlite:
        _attach_sqlite_pragmas(_engine)

    _db_url = url
    logger.info("Database engine initialized (dialect=%s)", _engine.dialect.name)
    return _engine


def _attach_sqlite_pragmas(engine: Engine) -> None:
    """Apply WAL/foreign_keys/busy_timeout on every new SQLite connection."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def get_engine() -> Engine:
    """Return the initialized engine. Raises if ``init_engine`` was never called."""
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    return _engine


def get_database_url() -> str:
    """Return the URL the engine is bound to."""
    if _db_url is None:
        raise RuntimeError("Engine not initialized.")
    return _db_url


def get_sqlite_path() -> Optional[Path]:
    """Return the on-disk SQLite path if the engine is SQLite, else ``None``."""
    return _sqlite_path


def is_sqlite() -> bool:
    return _engine is not None and _engine.dialect.name == "sqlite"


def is_postgres() -> bool:
    return _engine is not None and _engine.dialect.name == "postgresql"


def dispose_engine() -> None:
    """Close the pool — used by the migration flow before swapping URLs."""
    global _engine, _db_url, _sqlite_path
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _db_url = None
    _sqlite_path = None
