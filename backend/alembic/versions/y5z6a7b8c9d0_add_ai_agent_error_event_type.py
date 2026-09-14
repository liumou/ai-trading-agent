"""add AI_AGENT_ERROR event type

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-09-14 13:30:00.000000

AI agent 失败（LLM 连接/sdk 异常/mcp 依赖故障）不再伪装成 AI_ANALYSIS 正常
决策落库 —— 新增 AI_AGENT_ERROR，通知中心按 error 分类红色展示。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "y5z6a7b8c9d0"
down_revision: Union[str, None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE boteventtype ADD VALUE IF NOT EXISTS 'AI_AGENT_ERROR'")


def downgrade() -> None:
    # PG 不支持从 enum 删除值；保留（与既有事件类型迁移一致）。
    pass