"""add backtest_formula_version to ai_optimization_logs

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-09-14 13:00:00.000000

回测口径修正（contract_size 换算、读取品种 SL/TP 配置）后，历史
AIOptimizationLog 的 suggested_params 是用旧口径算的，直接 /apply 会在
新口径下生效 —— 加版本号做门禁，不匹配则拒绝应用。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x4y5z6a7b8c9"
down_revision: str | None = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量行标记为 v1（旧口径）；新行由应用层写入当前版本。
    op.add_column(
        "ai_optimization_logs",
        sa.Column("backtest_formula_version", sa.String(16), nullable=False, server_default="v1"),
    )


def downgrade() -> None:
    op.drop_column("ai_optimization_logs", "backtest_formula_version")
