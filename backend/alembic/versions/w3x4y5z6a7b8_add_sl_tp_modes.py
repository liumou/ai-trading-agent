"""add SL/TP clamp & R-multiple modes to symbol_configs

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-09-14 12:00:00.000000

新增止损止盈的两组可选模式（默认全部等价于旧行为，可逐品种灰度）：
  - sl_mode="atr"（默认）/ "clamped"：clamped 时
    sl_distance = clamp(ATR × sl_atr_mult × regime, sl_floor, sl_cap)
  - tp_mode="atr"（默认）/ "rr"：rr 时
    tp_distance = target_r_multiple × sl_distance（盈亏比恒等于 R）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w3x4y5z6a7b8"
down_revision: str | None = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("symbol_configs", sa.Column("sl_mode", sa.String(8), nullable=False, server_default="atr"))
    op.add_column("symbol_configs", sa.Column("sl_floor", sa.Float(), nullable=True))
    op.add_column("symbol_configs", sa.Column("sl_cap", sa.Float(), nullable=True))
    op.add_column("symbol_configs", sa.Column("tp_mode", sa.String(8), nullable=False, server_default="atr"))
    op.add_column("symbol_configs", sa.Column("target_r_multiple", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("symbol_configs", "target_r_multiple")
    op.drop_column("symbol_configs", "tp_mode")
    op.drop_column("symbol_configs", "sl_cap")
    op.drop_column("symbol_configs", "sl_floor")
    op.drop_column("symbol_configs", "sl_mode")
