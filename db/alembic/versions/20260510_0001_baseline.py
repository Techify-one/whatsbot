"""baseline schema (initial revision generated from db/tables.py)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "config",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("phone", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("email", sa.Text, nullable=False, server_default=""),
        sa.Column("profession", sa.Text, nullable=False, server_default=""),
        sa.Column("company", sa.Text, nullable=False, server_default=""),
        sa.Column("address", sa.Text, nullable=False, server_default=""),
        sa.Column("ai_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_group", sa.Integer, nullable=False, server_default="0"),
        sa.Column("group_name", sa.Text, nullable=False, server_default=""),
        sa.Column("is_archived", sa.Integer, nullable=False, server_default="0"),
        sa.Column("archived_by_app", sa.Integer, nullable=False, server_default="0"),
        sa.Column("can_send", sa.Integer, nullable=False, server_default="1"),
        sa.Column("unread_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unread_ai_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_index("idx_contacts_updated", "contacts", ["updated_at"])
    op.create_index("idx_contacts_archived", "contacts", ["is_archived"])

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("idx_obs_contact", "observations", ["contact_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("ts", sa.Float, nullable=False),
        sa.Column("media_type", sa.Text),
        sa.Column("media_path", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("msg_id", sa.Text),
    )
    op.create_index("idx_msg_contact_ts", "messages", ["contact_id", "ts"])
    op.create_index("idx_msg_id", "messages", ["msg_id"])

    op.create_table(
        "usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_type", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("ts", sa.Float, nullable=False),
    )
    op.create_index("idx_usage_contact_ts", "usage", ["contact_id", "ts"])
    op.create_index("idx_usage_ts", "usage", ["ts"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("color", sa.Text, nullable=False),
    )

    op.create_table(
        "contact_tags",
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("idx_ct_tag", "contact_tags", ["tag_id"])

    op.create_table(
        "unread_msg_ids",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("msg_id", sa.Text, nullable=False),
    )
    op.create_index("idx_unread_contact", "unread_msg_ids", ["contact_id"])

    op.create_table(
        "executions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("trigger_type", sa.Text, nullable=False, server_default="webhook"),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("completed_at", sa.Float),
        sa.Column("error", sa.Text),
    )
    op.create_index("idx_exec_started", "executions", ["started_at"])

    op.create_table(
        "execution_steps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.Integer, sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="ok"),
        sa.Column("data", sa.Text),
        sa.Column("ts", sa.Float, nullable=False),
    )
    op.create_index("idx_step_exec", "execution_steps", ["execution_id"])

    op.create_table(
        "plugins",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("version", sa.Text, nullable=False, server_default=""),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("installed_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("load_error", sa.Text),
    )

    op.create_table(
        "plugin_migrations",
        sa.Column("plugin_id", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("applied_at", sa.Float, nullable=False),
    )

    op.create_table(
        "tool_overrides",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("plugin_id", sa.Text),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("description", sa.Text),
        sa.Column("display_label", sa.Text),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_index("idx_tool_overrides_plugin", "tool_overrides", ["plugin_id"])


def downgrade() -> None:
    op.drop_index("idx_tool_overrides_plugin", table_name="tool_overrides")
    op.drop_table("tool_overrides")
    op.drop_table("plugin_migrations")
    op.drop_table("plugins")
    op.drop_index("idx_step_exec", table_name="execution_steps")
    op.drop_table("execution_steps")
    op.drop_index("idx_exec_started", table_name="executions")
    op.drop_table("executions")
    op.drop_index("idx_unread_contact", table_name="unread_msg_ids")
    op.drop_table("unread_msg_ids")
    op.drop_index("idx_ct_tag", table_name="contact_tags")
    op.drop_table("contact_tags")
    op.drop_table("tags")
    op.drop_index("idx_usage_ts", table_name="usage")
    op.drop_index("idx_usage_contact_ts", table_name="usage")
    op.drop_table("usage")
    op.drop_index("idx_msg_id", table_name="messages")
    op.drop_index("idx_msg_contact_ts", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_obs_contact", table_name="observations")
    op.drop_table("observations")
    op.drop_index("idx_contacts_archived", table_name="contacts")
    op.drop_index("idx_contacts_updated", table_name="contacts")
    op.drop_table("contacts")
    op.drop_table("config")
