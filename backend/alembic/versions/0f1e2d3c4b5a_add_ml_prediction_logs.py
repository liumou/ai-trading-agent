"""add ml_prediction_logs table

Revision ID: 0f1e2d3c4b5a
Revises: y5z6a7b8c9d0
Create Date: 2026-09-14 13:30:00.000000

MLPredictionLog 模型早已在代码中使用（/api/ml/predict 落库、calibration 与
逐笔反馈均查询该表），但从未生成建表迁移 —— 接口一直 500
(relation "ml_prediction_logs" does not exist)，此前被共享会话中毒错误掩盖。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0f1e2d3c4b5a"
down_revision: Union[str, None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Idempotent: the table may already exist from a manual bootstrap.
    op.execute("SET lock_timeout = '30s'")
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "ml_prediction_logs" not in inspector.get_table_names():
        op.create_table(
            "ml_prediction_logs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("model_id", sa.BigInteger(), nullable=True),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("predicted_signal", sa.Integer(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("actual_outcome", sa.Integer(), nullable=True),
            sa.Column("was_correct", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

    indexes = {i["name"] for i in inspector.get_indexes("ml_prediction_logs")} if "ml_prediction_logs" in inspector.get_table_names() else set()
    if "ix_ml_prediction_logs_symbol" not in indexes:
        op.create_index("ix_ml_prediction_logs_symbol", "ml_prediction_logs", ["symbol"])

def downgrade() -> None:
    op.drop_index("ix_ml_prediction_logs_symbol", "ml_prediction_logs")
    op.drop_table("ml_prediction_logs")