"""HistoricalDataCollector 时区归一回归测试。

覆盖 2026-10-11 修复：新 bridge 输出的 OHLCV time 带 +00:00（offset-aware），
collector 写入 ohlcv_data（TIMESTAMP WITHOUT TIME ZONE）时必须归一为 naive UTC，
否则 asyncpg 抛 "can't subtract offset-naive and offset-aware datetimes"。

SQLite 测试库默认没有生产 PG 的唯一约束 uq_ohlcv_symbol_timeframe_time
（迁移 b2c3d4e5f6g7 所建），本文件用 autouse fixture 补建，使 ON CONFLICT
与生产语义一致。
"""

from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.collector import HistoricalDataCollector
from app.db.models import OHLCVData


@pytest.fixture(autouse=True)
async def _ohlcv_unique_constraint(db_engine):
    """为 ohlcv_data 补建生产同款唯一约束（symbol, timeframe, time）。"""
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_ohlcv_symbol_timeframe_time
                ON ohlcv_data (symbol, timeframe, time)
                """
            )
        )
    yield


def _df_with(index: pd.DatetimeIndex) -> pd.DataFrame:
    """构造与 market_data.get_ohlcv_range 输出同构的 DataFrame（time 为索引）。

    数据行数与索引等长，price 随索引偏移，便于区分每根 bar。
    """
    n = len(index)
    base = 100.0
    return pd.DataFrame(
        {
            "open": [base + i for i in range(n)],
            "high": [base + i + 1.0 for i in range(n)],
            "low": [base + i - 1.0 for i in range(n)],
            "close": [base + i + 0.5 for i in range(n)],
            "tick_volume": [10.0 + i for i in range(n)],
        },
        index=index,
    )


def _collector(db_session: AsyncSession) -> HistoricalDataCollector:
    # 仅测 _upsert_bars，无需 market_data；显式 None 以便不误触 collect 链路
    return HistoricalDataCollector(market_data=None, db_session=db_session)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_upsert_bars_normalizes_aware_time(db_session):
    """新 bridge 输出 offset-aware（+00:00）时间 → 写入 naive UTC（回归 bug）。"""
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2025-04-01 01:00:00", tz=UTC),
            pd.Timestamp("2025-04-01 01:15:00", tz=UTC),
        ],
        name="time",
    )
    collector = _collector(db_session)

    inserted = await collector._upsert_bars(db_session, "GOLD", "M15", _df_with(index))
    assert inserted == 2

    result = await db_session.execute(
        select(OHLCVData).where(OHLCVData.symbol == "GOLD", OHLCVData.timeframe == "M15")
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    # 落库必须是 naive（无 tzinfo），否则 asyncpg 在 PG 上会 DataError
    assert all(r.time.tzinfo is None for r in rows)
    # UTC 数值不变
    assert sorted(r.time for r in rows) == [
        datetime(2025, 4, 1, 1, 0),
        datetime(2025, 4, 1, 1, 15),
    ]


@pytest.mark.asyncio
async def test_upsert_bars_keeps_naive_time(db_session):
    """旧 bridge 输出 naive 时间 → 原样保留（向后兼容，不误加时区）。"""
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2025-04-01 01:00:00"),
            pd.Timestamp("2025-04-01 01:15:00"),
        ],
        name="time",
    )
    collector = _collector(db_session)

    inserted = await collector._upsert_bars(db_session, "GOLD", "M15", _df_with(index))
    assert inserted == 2

    result = await db_session.execute(
        select(OHLCVData).where(OHLCVData.symbol == "GOLD", OHLCVData.timeframe == "M15")
    )
    rows = result.scalars().all()
    assert sorted(r.time for r in rows) == [
        datetime(2025, 4, 1, 1, 0),
        datetime(2025, 4, 1, 1, 15),
    ]


@pytest.mark.asyncio
async def test_upsert_bars_idempotent_on_conflict(db_session):
    """ON CONFLICT DO NOTHING：同 (symbol, timeframe, time) 重复插入返回 0。"""
    index = pd.DatetimeIndex([pd.Timestamp("2025-04-01 01:00:00", tz=UTC)], name="time")
    collector = _collector(db_session)

    assert await collector._upsert_bars(db_session, "GOLD", "M15", _df_with(index)) == 1
    # 同键再插（此时为已落库的 naive）→ 冲突跳过
    index_naive = pd.DatetimeIndex([pd.Timestamp("2025-04-01 01:00:00")], name="time")
    assert await collector._upsert_bars(db_session, "GOLD", "M15", _df_with(index_naive)) == 0

    result = await db_session.execute(
        select(OHLCVData).where(OHLCVData.symbol == "GOLD", OHLCVData.timeframe == "M15")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
