"""laya shadow reviews: manual_shadow_reviews

Revision ID: b0c1d2e3f4a5
Revises: a9b8c7d6e5f4
Create Date: 2026-09-22

Phase 3.3：laya 影子评审明细表（validation.md §1.4）。
- 影子明细进专表，order_audits.review 只加 review["laya"] 摘要（additive）。
- 建表幂等：表缺失时创建；已存在跳过（早期环境 create_all 落库）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return bind.dialect.has_table(bind, name)


def upgrade() -> None:
    if _has_table("manual_shadow_reviews"):
        return
    op.create_table(
        "manual_shadow_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=32), nullable=False, server_default="0"),
        sa.Column("symbol", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("laya_verdict", sa.String(length=20), nullable=True),
        sa.Column("laya_confidence", sa.Float(), nullable=True),
        sa.Column("laya_reasons", sa.JSON(), nullable=True),
        sa.Column("laya_checks", sa.JSON(), nullable=True),
        sa.Column("laya_answers", sa.JSON(), nullable=True),
        sa.Column("llm_verdict", sa.String(length=20), nullable=True),
        sa.Column("llm_confidence", sa.Float(), nullable=True),
        sa.Column("agreement", sa.Boolean(), nullable=True),
        sa.Column("dangerous_divergence", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("laya_latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("fallback_reason", sa.String(length=100), nullable=True),
        sa.Column("state_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manual_shadow_reviews_audit_id", "manual_shadow_reviews", ["audit_id"])
    op.create_index("ix_manual_shadow_reviews_created_at", "manual_shadow_reviews", ["created_at"])


def downgrade() -> None:
    if not _has_table("manual_shadow_reviews"):
        return
    op.drop_index("ix_manual_shadow_reviews_created_at", table_name="manual_shadow_reviews")
    op.drop_index("ix_manual_shadow_reviews_audit_id", table_name="manual_shadow_reviews")
    op.drop_table("manual_shadow_reviews")
