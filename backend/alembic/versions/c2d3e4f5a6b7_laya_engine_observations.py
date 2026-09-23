"""laya engine observations: laya_engine_observations

Revision ID: c2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-09-22

Phase 4：engine 开仓侧 laya 影子观测专表（phase4-observation.md）。
- 与 manual_shadow_reviews 并列，互不干扰；观测写入 best-effort。
- 建表幂等：表缺失时创建；已存在跳过。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return bind.dialect.has_table(bind, name)


def upgrade() -> None:
    if _has_table("laya_engine_observations"):
        return
    op.create_table(
        "laya_engine_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("signal_label", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("signal", sa.Integer(), nullable=True),
        sa.Column("balance", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("chain_can_trade", sa.Boolean(), nullable=True),
        sa.Column("chain_prob", sa.Float(), nullable=True),
        sa.Column("allowed", sa.Boolean(), nullable=True),
        sa.Column("laya_verdict", sa.String(length=20), nullable=True),
        sa.Column("laya_confidence", sa.Float(), nullable=True),
        sa.Column("laya_reasons", sa.JSON(), nullable=True),
        sa.Column("laya_checks", sa.JSON(), nullable=True),
        sa.Column("laya_answers", sa.JSON(), nullable=True),
        sa.Column("divergence_gate", sa.String(length=30), nullable=True),
        sa.Column("divergence_final", sa.String(length=30), nullable=True),
        sa.Column("laya_latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_laya_engine_observations_created_at", "laya_engine_observations", ["created_at"])
    op.create_index("ix_laya_engine_observations_signal_label", "laya_engine_observations", ["signal_label"])


def downgrade() -> None:
    if not _has_table("laya_engine_observations"):
        return
    op.drop_index("ix_laya_engine_observations_signal_label", table_name="laya_engine_observations")
    op.drop_index("ix_laya_engine_observations_created_at", table_name="laya_engine_observations")
    op.drop_table("laya_engine_observations")
