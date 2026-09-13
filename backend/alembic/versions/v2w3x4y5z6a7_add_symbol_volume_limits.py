"""add broker volume limits to symbol_configs

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-09-13 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2w3x4y5z6a7"
down_revision: str | None = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 刻意可空：存量/既有行保持 NULL，订单侧手数防线会跳过它们，
    # 直到该品种经 /symbols 页面重新校验。
    # 新建行在创建/更新时从实时 MT5 规格回填。
    op.add_column("symbol_configs", sa.Column("volume_min", sa.Float(), nullable=True))
    op.add_column("symbol_configs", sa.Column("volume_max", sa.Float(), nullable=True))
    op.add_column("symbol_configs", sa.Column("volume_step", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("symbol_configs", "volume_step")
    op.drop_column("symbol_configs", "volume_max")
    op.drop_column("symbol_configs", "volume_min")
