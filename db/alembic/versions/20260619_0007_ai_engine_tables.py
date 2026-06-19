"""ai engine tables (config-in-DB + code-in-DB) and executions cost columns

Revision ID: 0007_ai_engine_tables
Revises: 0006_contact_mention
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_ai_engine_tables"
down_revision: Union[str, Sequence[str], None] = "0006_contact_mention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agents",
        sa.Column("agent_key", sa.Text, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("prompt_key", sa.Text, nullable=False, server_default=""),
        sa.Column("model_config", sa.Text, nullable=False, server_default="{}"),
        sa.Column("tool_names", sa.Text),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "ai_prompts",
        sa.Column("prompt_key", sa.Text, primary_key=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "ai_variables",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("category", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "ai_tools",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("code", sa.Text, nullable=False, server_default=""),
        sa.Column("dependencies", sa.Text, nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("install_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("install_error", sa.Text),
        sa.Column("installed_deps", sa.Text, nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Float, nullable=False),
    )

    op.create_table(
        "ai_agents_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_key", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.Text, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("idx_ai_agents_hist", "ai_agents_history", ["agent_key", "version"])
    op.create_table(
        "ai_prompts_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("prompt_key", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.Text, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("idx_ai_prompts_hist", "ai_prompts_history", ["prompt_key", "version"])
    op.create_table(
        "ai_tools_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.Text, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("idx_ai_tools_hist", "ai_tools_history", ["name", "version"])

    op.add_column("executions", sa.Column("agent_key", sa.Text))
    op.add_column(
        "executions",
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "executions",
        sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("executions", "total_cost_usd")
    op.drop_column("executions", "total_tokens")
    op.drop_column("executions", "agent_key")

    op.drop_index("idx_ai_tools_hist", table_name="ai_tools_history")
    op.drop_table("ai_tools_history")
    op.drop_index("idx_ai_prompts_hist", table_name="ai_prompts_history")
    op.drop_table("ai_prompts_history")
    op.drop_index("idx_ai_agents_hist", table_name="ai_agents_history")
    op.drop_table("ai_agents_history")

    op.drop_table("ai_tools")
    op.drop_table("ai_variables")
    op.drop_table("ai_prompts")
    op.drop_table("ai_agents")
