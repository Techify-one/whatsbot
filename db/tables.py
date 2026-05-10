"""SQLAlchemy Core table definitions for WhatsBot.

These ``Table`` objects are the single source of truth for the database
schema. They are NOT mapped ORM classes — there is no ``DeclarativeBase``, no
``Session``, no identity map. Repositories import the tables and build
``select()/insert()/update()`` against them directly, preserving the
explicit-SQL feel of the original ``sqlite3`` code while letting SQLAlchemy
handle dialect differences. The Alembic baseline revision is generated from
this metadata.

Timestamps are stored as Unix epoch floats for backwards compatibility with
existing data (column type ``Float``). A future migration could move them to
``TIMESTAMP``; that is intentionally out of scope.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()


config = Table(
    "config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)


contacts = Table(
    "contacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False, server_default=""),
    Column("email", Text, nullable=False, server_default=""),
    Column("profession", Text, nullable=False, server_default=""),
    Column("company", Text, nullable=False, server_default=""),
    Column("address", Text, nullable=False, server_default=""),
    Column("ai_enabled", Integer, nullable=False, server_default="1"),
    Column("is_group", Integer, nullable=False, server_default="0"),
    Column("group_name", Text, nullable=False, server_default=""),
    Column("is_archived", Integer, nullable=False, server_default="0"),
    Column("archived_by_app", Integer, nullable=False, server_default="0"),
    Column("can_send", Integer, nullable=False, server_default="1"),
    Column("unread_count", Integer, nullable=False, server_default="0"),
    Column("unread_ai_count", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_contacts_updated", contacts.c.updated_at)
Index("idx_contacts_archived", contacts.c.is_archived)


observations = Table(
    "observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("idx_obs_contact", observations.c.contact_id)


messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False, server_default=""),
    Column("ts", Float, nullable=False),
    Column("media_type", Text),
    Column("media_path", Text),
    Column("status", Text),
    Column("msg_id", Text),
)
Index("idx_msg_contact_ts", messages.c.contact_id, messages.c.ts)
Index("idx_msg_id", messages.c.msg_id)


usage = Table(
    "usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("call_type", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("total_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Float, nullable=False, server_default="0.0"),
    Column("ts", Float, nullable=False),
)
Index("idx_usage_contact_ts", usage.c.contact_id, usage.c.ts)
Index("idx_usage_ts", usage.c.ts)


tags = Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, unique=True),
    Column("color", Text, nullable=False),
)


contact_tags = Table(
    "contact_tags",
    metadata,
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
Index("idx_ct_tag", contact_tags.c.tag_id)


unread_msg_ids = Table(
    "unread_msg_ids",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("msg_id", Text, nullable=False),
)
Index("idx_unread_contact", unread_msg_ids.c.contact_id)


executions = Table(
    "executions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone", Text, nullable=False),
    Column("trigger_type", Text, nullable=False, server_default="webhook"),
    Column("status", Text, nullable=False, server_default="running"),
    Column("started_at", Float, nullable=False),
    Column("completed_at", Float),
    Column("error", Text),
)
Index("idx_exec_started", executions.c.started_at)


execution_steps = Table(
    "execution_steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("execution_id", Integer, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
    Column("step_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="ok"),
    Column("data", Text),
    Column("ts", Float, nullable=False),
)
Index("idx_step_exec", execution_steps.c.execution_id)


plugins = Table(
    "plugins",
    metadata,
    Column("id", Text, primary_key=True),
    Column("version", Text, nullable=False, server_default=""),
    Column("enabled", Integer, nullable=False, server_default="0"),
    Column("installed_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("load_error", Text),
)


plugin_migrations = Table(
    "plugin_migrations",
    metadata,
    Column("plugin_id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("applied_at", Float, nullable=False),
)


tool_overrides = Table(
    "tool_overrides",
    metadata,
    Column("name", Text, primary_key=True),
    Column("plugin_id", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("description", Text),
    Column("display_label", Text),
    Column("updated_at", Float, nullable=False),
)
Index("idx_tool_overrides_plugin", tool_overrides.c.plugin_id)


# Set of core table names — used by the SQLite → Postgres migration helper to
# distinguish what belongs to the app vs. plugin-owned tables.
CORE_TABLES = frozenset(t.name for t in metadata.sorted_tables)
