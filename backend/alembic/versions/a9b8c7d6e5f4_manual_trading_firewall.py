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

建表兜底：order_audits 从未被任何历史迁移创建（早期开发环境经
create_all 落库，部署库缺失导致 ALTER TABLE 报 relation does not exist）。
upgrade 在表缺失时按当前 OrderAudit 模型完整建表（含新列），表已存在时
仅补列；均带存在性守卫，保证幂等。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "z0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增列与索引（表已存在时的加列清单）
_NEW_COLUMNS = [
    ("source", sa.Column("source", sa.String(length=20), nullable=False, server_default="strategy")),
    ("account_login", sa.Column("account_login", sa.String(length=32), nullable=False, server_default="0")),
    ("order_kind", sa.Column("order_kind", sa.String(length=10), nullable=False, server_default="market")),
    ("order_price", sa.Column("order_price", sa.Float(), nullable=True)),
    ("review", sa.Column("review", sa.JSON(), nullable=True)),
]


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return bind.dialect.has_table(bind, name)


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [r[0] for r in bind.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column})]
    return bool(cols)


def _has_index(name: str) -> bool:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"
    ), {"n": name}).fetchall()
    return bool(rows)


def upgrade() -> None:
    if not _has_table("order_audits"):
        # 表从未被迁移创建：按当前 OrderAudit 模型完整建表（含本次新增列与索引）。
        op.create_table(
            "order_audits",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("order_type", sa.String(length=10), nullable=False),  # BUY / SELL
            sa.Column("requested_lot", sa.Float(), nullable=False),
            sa.Column("requested_sl", sa.Float(), nullable=False),
            sa.Column("requested_tp", sa.Float(), nullable=False),
            sa.Column("expected_price", sa.Float(), nullable=False),
            sa.Column("fill_price", sa.Float(), nullable=True),
            sa.Column("ticket", sa.BigInteger(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),  # FILLED / REJECTED / TIMEOUT / ERROR / ...
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("signal_source", sa.String(length=50), nullable=False),  # strategy name
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="strategy"),
            sa.Column("account_login", sa.String(length=32), nullable=False, server_default="0"),
            sa.Column("order_kind", sa.String(length=10), nullable=False, server_default="market"),  # market/pending
            sa.Column("order_price", sa.Float(), nullable=True),  # 挂单价
            sa.Column("review", sa.JSON(), nullable=True),  # LLM verdict + 情绪规则明细
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_order_audits_source", "order_audits", ["source"])
        op.create_index("ix_order_audits_status", "order_audits", ["status"])
        op.create_index("ix_order_audits_account_created", "order_audits", ["account_login", "created_at"])
        return

    for name, column in _NEW_COLUMNS:
        if not _has_column("order_audits", name):
            op.add_column("order_audits", column)
    if not _has_index("ix_order_audits_source"):
        op.create_index("ix_order_audits_source", "order_audits", ["source"])
    if not _has_index("ix_order_audits_status"):
        op.create_index("ix_order_audits_status", "order_audits", ["status"])
    if not _has_index("ix_order_audits_account_created"):
        op.create_index("ix_order_audits_account_created", "order_audits", ["account_login", "created_at"])


def downgrade() -> None:
    if not _has_table("order_audits"):
        return
    if _has_column("order_audits", "source"):
        if _has_index("ix_order_audits_account_created"):
            op.drop_index("ix_order_audits_account_created", table_name="order_audits")
        if _has_index("ix_order_audits_status"):
            op.drop_index("ix_order_audits_status", table_name="order_audits")
        if _has_index("ix_order_audits_source"):
            op.drop_index("ix_order_audits_source", table_name="order_audits")
    for name, _ in reversed(_NEW_COLUMNS):
        if _has_column("order_audits", name):
            op.drop_column("order_audits", name)
