"""add agent chat runs and audit events

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-17

Chat V2：持久任务队列（agent_chat_runs）+ 追加式审计事件（agent_chat_events），
以及会话归档字段 archived 与活动任务守卫 active_run_id。只读分析，不涉及交易。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_chat_sessions", sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("agent_chat_sessions", sa.Column("active_run_id", sa.String(36), nullable=True))

    op.create_table(
        "agent_chat_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("response", sa.Text(), server_default="", nullable=False),
        sa.Column("partial_response", sa.Text(), server_default="", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("turns", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_s", sa.Float(), server_default="0", nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("worker_token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "request_id", name="uq_chat_request"),
    )
    op.create_index("ix_agent_chat_runs_session_id", "agent_chat_runs", ["session_id"])
    op.create_index("ix_agent_chat_runs_status", "agent_chat_runs", ["status"])
    op.create_index("ix_agent_chat_runs_lease_until", "agent_chat_runs", ["lease_until"])

    op.create_table(
        "agent_chat_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("execution_id", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_chat_event_sequence"),
    )
    op.create_index("ix_agent_chat_events_run_id", "agent_chat_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_chat_events_run_id", table_name="agent_chat_events")
    op.drop_table("agent_chat_events")
    op.drop_index("ix_agent_chat_runs_lease_until", table_name="agent_chat_runs")
    op.drop_index("ix_agent_chat_runs_status", table_name="agent_chat_runs")
    op.drop_index("ix_agent_chat_runs_session_id", table_name="agent_chat_runs")
    op.drop_table("agent_chat_runs")
    op.drop_column("agent_chat_sessions", "active_run_id")
    op.drop_column("agent_chat_sessions", "archived")
