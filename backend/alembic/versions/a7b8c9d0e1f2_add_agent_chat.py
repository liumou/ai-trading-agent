"""add agent chat tables

Revision ID: a7b8c9d0e1f2
Revises: 0f1e2d3c4b5a
Create Date: 2026-09-17

对话式交易计划/报告功能：会话与消息两张表。只读分析，不涉及交易执行。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "0f1e2d3c4b5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_chat_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(200), server_default="新会话", nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("timeframe", sa.String(8), server_default="M15", nullable=False),
        sa.Column("mode", sa.String(20), server_default="free", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent_chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_chat_messages_session_id", "agent_chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_chat_messages_session_id", table_name="agent_chat_messages")
    op.drop_table("agent_chat_messages")
    op.drop_table("agent_chat_sessions")
