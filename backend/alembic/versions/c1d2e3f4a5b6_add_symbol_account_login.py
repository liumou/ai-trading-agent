"""symbol_configs 加 account_login —— 品种配置按 MT5 账号隔离

Revision ID: c1d2e3f4a5b6
Revises: f0e1d2c3b4a5
Create Date: 2026-09-26 12:00:00.000000

问题：symbol_configs 此前全局共享（symbol 全局唯一），MT5 切换账号后
品种管理仍显示旧账号的品种，Add Symbol 的券商目录也按旧账号缓存。
修复：绑定键与 trades/bot_events 的既有约定一致，用 `account_login`
（String，对应 mt5_accounts.login）；唯一约束从 (symbol) 改为
(account_login, symbol)。

存量回填：归属到当前活跃账号；无活跃账号时回填 '0'（未知，与 trades
的既有约定一致）。
"""
from typing import Sequence, Union

from alembic import op

from app.db.schema_ddl import SYMBOL_CONFIG_ACCOUNT_LOGIN_DDL

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "f0e1d2c3b4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 main.py lifespan schema_stmts 共享同一份 DDL（见 app/db/schema_ddl.py），
    # 消除双写漂移。此处只走 upgrade 正向；downgrade 逆向逻辑见下方。
    for stmt in SYMBOL_CONFIG_ACCOUNT_LOGIN_DDL:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_symbol_configs_account_symbol")
    op.execute("DROP INDEX IF EXISTS ix_symbol_configs_account_login")
    op.execute("ALTER TABLE symbol_configs DROP CONSTRAINT IF EXISTS uq_symbol_configs_symbol")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_symbol_configs_symbol ON symbol_configs (symbol)")
    op.execute("ALTER TABLE symbol_configs DROP COLUMN IF EXISTS account_login")
