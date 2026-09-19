"""manual trading firewall: order_audits source/account/order_kind/order_price/review

Revision ID: a9b8c7d6e5f4
Revises: z0a1b2c3d4e5
Create Date: 2026-09-19 18:30:00.000000

手动交易 + Agent 风控防火墙（Phase 2）：
- `order_audits` 加 source（strategy/ai_agent/manual）、account_login、
  order_kind（market/pending）、order_price（挂单价）、review（JSON：LLM
  审查结论 + 情绪规则明细）——三条下单通道统一审计，不另建 order_reviews 表
  （评审决定：与 OrderAudit 职责重叠，verdict 细节进 JSON 防两列漂移）。
- status 扩展生命周期值（PENDING_REVIEW/PENDING_CONFIRM/REJECTED/EXPIRED/
  CANCELLED/EXECUTED）——字符串列无需 ALTER TYPE。
- 索引 (account_login, created_at)：审查历史按账号倒序查询。

存量回填：source='strategy'（order_audits 此前唯一写入方是引擎的
_log_order_audit）；account_login='0'（与 trades 账号隔离迁移同口径）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "z0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_audits",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="strategy"),
    )
    op.add_column(
        "order_audits",
        sa.Column("account_login", sa.String(length=32), nullable=False, server_default="0"),
    )
    op.add_column(
        "order_audits",
        sa.Column("order_kind", sa.String(length=10), nullable=False, server_default="market"),
    )
    op.add_column("order_audits", sa.Column("order_price", sa.Float(), nullable=True))
    op.add_column("order_audits", sa.Column("review", sa.JSON(), nullable=True))
    op.create_index("ix_order_audits_source", "order_audits", ["source"])
    op.create_index("ix_order_audits_status", "order_audits", ["status"])
    op.create_index("ix_order_audits_account_created", "order_audits", ["account_login", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_order_audits_account_created", table_name="order_audits")
    op.drop_index("ix_order_audits_status", table_name="order_audits")
    op.drop_index("ix_order_audits_source", table_name="order_audits")
    op.drop_column("order_audits", "review")
    op.drop_column("order_audits", "order_price")
    op.drop_column("order_audits", "order_kind")
    op.drop_column("order_audits", "account_login")
    op.drop_column("order_audits", "source")
