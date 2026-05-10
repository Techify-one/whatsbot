"""SQL migration runner for plugins.

Each plugin can ship a ``migrations/`` folder with files named
``NNN_description.sql`` (e.g. ``001_initial.sql``). The runner:

1. Loads the set of versions already applied from ``plugin_migrations``.
2. Reads remaining files in numeric order.
3. Validates that every ``CREATE TABLE`` / ``ALTER TABLE`` references a
   table whose name starts with ``plugin_<id>_`` — guards against accidental
   collisions with core tables.
4. Executes the SQL inside a transaction and records the migration.

Plugin migration files must contain **portable** SQL (no ``strftime``,
``INSERT OR REPLACE``, ``RETURNING`` etc). The runner splits the file on
``;`` boundaries and executes each statement individually so the same file
runs against both SQLite and Postgres.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import text as sa_text

from db.engine import get_engine
from db.repositories import plugin_repo

from plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


_MIG_FILE_RE = re.compile(r"^(\d+)_.+\.sql$", re.IGNORECASE)
_TABLE_OP_RE = re.compile(
    r"\b(?:CREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TABLE|ALTER\s+TABLE|DROP\s+TABLE|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX|DROP\s+INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+|IF\s+EXISTS\s+)?"
    r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?",
    re.IGNORECASE,
)


def run_pending_migrations(manifest: PluginManifest, plugin_dir: Path) -> list[int]:
    """Apply every pending migration. Returns the list of versions applied."""
    if not manifest.migrations:
        return []
    mig_dir = plugin_dir / manifest.migrations
    if not mig_dir.is_dir():
        return []

    pid = manifest.id
    table_prefix = f"plugin_{pid}_"
    applied = plugin_repo.applied_migrations(pid)
    pending: list[tuple[int, Path]] = []
    for path in sorted(mig_dir.iterdir()):
        if not path.is_file():
            continue
        m = _MIG_FILE_RE.match(path.name)
        if not m:
            logger.warning("Plugin %s: ignoring migration file %s (bad name)", pid, path.name)
            continue
        version = int(m.group(1))
        if version in applied:
            continue
        pending.append((version, path))
    pending.sort()

    applied_now: list[int] = []
    engine = get_engine()
    for version, path in pending:
        sql = path.read_text(encoding="utf-8")
        _validate_sql_prefix(sql, pid, table_prefix, path.name)
        try:
            with engine.begin() as conn:
                for stmt in _split_statements(sql):
                    if stmt.strip():
                        conn.execute(sa_text(stmt))
        except Exception as e:
            raise RuntimeError(
                f"Plugin {pid} migration {path.name} failed: {e}"
            ) from e
        plugin_repo.record_migration(pid, version)
        applied_now.append(version)
        logger.info("Plugin %s: applied migration %s", pid, path.name)

    return applied_now


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements on ``;`` boundaries.

    Naive splitter that respects only single-quoted strings — adequate for the
    DDL/DML that plugin migrations are expected to contain. Plugins requiring
    more exotic SQL should split the work across multiple files.
    """
    out: list[str] = []
    buf: list[str] = []
    in_squote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not (in_squote and i + 1 < len(sql) and sql[i + 1] == "'"):
            in_squote = not in_squote
            buf.append(ch)
        elif ch == ";" and not in_squote:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _validate_sql_prefix(sql: str, plugin_id: str, prefix: str, filename: str) -> None:
    """Ensure every CREATE/ALTER/DROP TABLE in ``sql`` uses ``prefix``."""
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    for match in _TABLE_OP_RE.finditer(cleaned):
        target = match.group(1)
        if not target.startswith(prefix):
            raise ValueError(
                f"Plugin {plugin_id} migration {filename}: "
                f"object name '{target}' must start with '{prefix}' "
                f"(use 'plugin_{plugin_id}_<your_table>')"
            )
