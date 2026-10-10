"""trade_reviews —— 历史订单 AI 深度复盘表

Revision ID: ab1c2d3e4f50
Revises: c1d2e3f4a5b6
Create Date: 2026-10-10 12:00:00.000000

功能：单笔历史订单（bot 自动单 + 手动单）的 AI 深度复盘持久化。
物理自然键 (ticket, account_login) —— bot 单与手动单都有 ticket；trade_id
仅作 bot 单的冗余关联列（手动单为 NULL）。status 状态机 pending → running →
completed / failed，由 worker 用 DB 原子 UPDATE 转移（复刻 chat_runs）。

唯一约束（评审 B-1）：SQLAlchemy 无 PartialUniqueIndex，partial unique index
是 PG-only、SQLite 不支持（测试 create_all 会抛错）。故 Model 只声明普通索引，
PG 生产去重靠本迁移的两个 partial unique index，SQLite 测试依赖应用层幂等
（service 层 trigger 幂等返回 + IntegrityError→409）。与 main.py lifespan
schema_stmts 共享 TRADE_REVIEWS_DDL（app/db/schema_ddl.py），消除双写漂移。

review 用 JSON（不追 JSONB：新表不锁表，且 JSONB 迁移 t0u1v2w3x4y5 曾需
维护窗口）。open_time 存 naive UTC（对齐 trades 时区约定）。
"""
from typing import Sequence, Union

from alembic import op

from app.db.schema_ddl import TRADE_REVIEWS_DDL

revision: str = "ab1c2d3e4f50"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 main.py lifespan schema_stmts 共享同一份 DDL（见 app/db/schema_ddl.py），
    # 消除双写漂移。此处只走 upgrade 正向；downgrade 逆向逻辑见下方。
    for stmt in TRADE_REVIEWS_DDL:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_trade_reviews_ticket_account_excl_null")
    op.execute("DROP INDEX IF EXISTS uq_trade_reviews_trade_id_excl_null")
    op.execute("DROP INDEX IF EXISTS ix_trade_reviews_open_time")
    op.execute("DROP INDEX IF EXISTS ix_trade_reviews_status")
    op.execute("DROP INDEX IF EXISTS ix_trade_reviews_natural_key")
    op.execute("DROP TABLE IF EXISTS trade_reviews")
