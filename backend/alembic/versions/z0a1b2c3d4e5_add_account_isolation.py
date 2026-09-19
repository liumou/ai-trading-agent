"""add account isolation (trades/bot_events account_login) + mt5_accounts

Revision ID: z0a1b2c3d4e5
Revises: y5z6a7b8c9d0
Create Date: 2026-09-19 09:00:00.000000

MT5 账号实时切换功能（Phase 4）：
- `trades` 加 `account_login` 列，`ticket` 唯一约束从全局改复合
  `(account_login, ticket)` —— 跨账号 ticket 可重复（H4）。
- `bot_events` 加 `account_login` 列（可空，切换前事件无归属）。
- 新增 `mt5_accounts` 表：账号凭据独立于 secrets 表存储（H5）。

存量数据回填：后端此前无账号概念，无法确定历史 trades 属于哪个账号，
统一回填 `'0'`（"未知/切换前"）。切换功能上线后新交易写真实 account_login。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z0a1b2c3d4e5"
down_revision: Union[str, None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── trades：加 account_login 列 + 回填 + 唯一约束改复合 ──────────────
    op.add_column("trades", sa.Column("account_login", sa.String(length=32), nullable=False, server_default="0"))
    # 先删全局唯一索引（存在则删），再建复合唯一
    op.execute("DROP INDEX IF EXISTS ix_trades_ticket")
    op.create_index("ix_trades_account_login", "trades", ["account_login"])
    op.create_unique_constraint("uq_trades_account_ticket", "trades", ["account_login", "ticket"])
    op.create_index("ix_trades_ticket", "trades", ["ticket"])

    # ── bot_events：加 account_login 列（可空） ────────────────────────────
    op.add_column("bot_events", sa.Column("account_login", sa.String(length=32), nullable=True))
    op.create_index("ix_bot_events_account_login", "bot_events", ["account_login"])

    # ── mt5_accounts 表 ───────────────────────────────────────────────────
    op.create_table(
        "mt5_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("login", sa.BigInteger(), nullable=False),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("password_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("server", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("broker_name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_switched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login", name="uq_mt5_accounts_login"),
    )
    op.create_index("ix_mt5_accounts_login", "mt5_accounts", ["login"])
    op.create_index("ix_mt5_accounts_is_deleted", "mt5_accounts", ["is_deleted"])


def downgrade() -> None:
    op.drop_table("mt5_accounts")
    op.drop_index("ix_bot_events_account_login", table_name="bot_events")
    op.drop_column("bot_events", "account_login")
    # 恢复 trades 全局唯一 ticket（删除复合唯一与 account_login 列）
    op.drop_index("ix_trades_ticket", table_name="trades")
    op.drop_constraint("uq_trades_account_ticket", "trades", type_="unique")
    op.drop_index("ix_trades_account_login", table_name="trades")
    op.drop_column("trades", "account_login")
    op.create_unique_constraint("uq_trades_ticket", "trades", ["ticket"])
    op.create_index("ix_trades_ticket", "trades", ["ticket"])
