"""
Shared test fixtures for ai-trading-agent backend.
"""

import os
from unittest.mock import AsyncMock

# Disable auth for tests (prevent .env from enabling it)
os.environ["AUTH_PASSWORD_HASH"] = ""

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import BigInteger, Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

# ─── Database ──────────────────────────────────────────────────────────────────


def _remap_bigint_for_sqlite(metadata):
    """Replace BigInteger with Integer for SQLite compatibility."""
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        _remap_bigint_for_sqlite(Base.metadata)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Provide a transactional DB session that rolls back after each test."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ─── Redis ─────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def redis_client():
    """Provide a fake Redis client for testing."""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


# ─── Mock MT5 Connector ───────────────────────────────────────────────────────


@pytest.fixture
def mock_connector():
    """Mock MT5BridgeConnector with predictable responses."""
    from app.mt5.connector import MT5BridgeConnector

    connector = AsyncMock(spec=MT5BridgeConnector)
    connector.get_account.return_value = {
        "success": True,
        "data": {
            "balance": 10000.0,
            "equity": 10000.0,
            "margin": 0.0,
            "free_margin": 10000.0,
            "profit": 0.0,
            "currency": "USD",
        },
    }
    connector.get_ohlcv.return_value = {"success": True, "data": []}
    connector.get_positions.return_value = {"success": True, "data": []}
    connector.place_order.return_value = {
        "success": True,
        "data": {"ticket": 12345, "price": 2000.0, "lot": 0.1, "type": "BUY"},
    }
    connector.close_position.return_value = {"success": True}
    connector.close_all_positions.return_value = {"success": True, "closed": 0}
    connector.get_tick.return_value = {"success": True, "data": {"ask": 2001.0, "bid": 2000.0}}
    connector.get_history.return_value = {"success": True, "data": []}
    return connector


# ─── Mock AI Client ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_ai_client():
    """Mock AIClient for testing without API calls."""
    from app.ai.client import AIClient

    client = AsyncMock(spec=AIClient)
    client.complete.return_value = '{"label": "bullish", "score": 0.8}'
    client.client = True  # simulate configured
    return client


# ─── OHLCV DataFrame Factory ──────────────────────────────────────────────────


@pytest.fixture
def make_ohlcv_df():
    """Factory fixture for generating OHLCV DataFrames with known patterns."""

    def _factory(
        rows: int = 100,
        trend: str = "up",
        base_price: float = 2000.0,
        volatility: float = 5.0,
        include_volume: bool = True,
    ) -> pd.DataFrame:
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=rows, freq="15min")

        if trend == "up":
            drift = np.linspace(0, volatility * 10, rows)
        elif trend == "down":
            drift = np.linspace(0, -volatility * 10, rows)
        else:  # flat
            drift = np.zeros(rows)

        noise = np.random.randn(rows) * volatility
        close = base_price + drift + noise

        high = close + np.abs(np.random.randn(rows)) * volatility * 0.5
        low = close - np.abs(np.random.randn(rows)) * volatility * 0.5
        open_ = close + np.random.randn(rows) * volatility * 0.3

        data = {
            "time": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
        if include_volume:
            data["tick_volume"] = np.random.randint(100, 1000, rows).astype(float)

        df = pd.DataFrame(data)
        return df

    return _factory


@pytest.fixture
def make_crossover_df():
    """Generate a DataFrame that produces an EMA crossover at a known point."""

    def _factory(
        crossover_type: str = "bullish",
        fast_period: int = 20,
        slow_period: int = 50,
        base_price: float = 2000.0,
    ) -> pd.DataFrame:
        rows = slow_period + 30  # enough bars for both EMAs to stabilize
        dates = pd.date_range("2025-01-01", periods=rows, freq="15min")

        if crossover_type == "bullish":
            # Start below (downtrend), then switch to uptrend
            prices = np.concatenate(
                [
                    np.linspace(base_price, base_price - 50, rows // 2),
                    np.linspace(base_price - 50, base_price + 20, rows - rows // 2),
                ]
            )
        else:
            # Start above (uptrend), then switch to downtrend
            prices = np.concatenate(
                [
                    np.linspace(base_price, base_price + 50, rows // 2),
                    np.linspace(base_price + 50, base_price - 20, rows - rows // 2),
                ]
            )

        noise = np.random.RandomState(42).randn(rows) * 2
        close = prices + noise
        high = close + 3
        low = close - 3
        open_ = close + np.random.RandomState(42).randn(rows) * 1

        return pd.DataFrame(
            {
                "time": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": np.random.RandomState(42).randint(100, 1000, rows).astype(float),
            }
        )

    return _factory


@pytest.fixture(autouse=True)
def _pin_guardrail_defaults(monkeypatch):
    """.env 里的 GUARDRAILS_* 覆盖会让依赖默认硬限的测试失真
    （如 lot 0.1 被 max=0.01 拒、3 仓并发被 max=1 拒）——全局钉回默认；
    需要自定义硬限的用例自行 monkeypatch。"""
    from app.config import settings

    monkeypatch.setattr(settings, "guardrails_max_lot_per_trade", 1.0)
    monkeypatch.setattr(settings, "guardrails_max_concurrent_per_symbol", 3)
    monkeypatch.setattr(settings, "guardrails_max_concurrent_total", 5)
    monkeypatch.setattr(settings, "guardrails_consecutive_loss_halt", 5)
    # 纪律门禁（M2）：不在此 pin —— 纪律门禁专项测试用 enable_discipline 显式
    # 启用；其他单元测试的 Redis mock（switching 等）会读到 gate_enabled 回退
    # settings 默认 True，可能误触发 WEEKEND_CLOSE —— 统一在这里钉为 False，
    # 纪律专项用例通过 Redis cfg key 覆盖为 True。
    monkeypatch.setattr(settings, "discipline_gate_enabled", False)
    monkeypatch.setattr(settings, "engine_discipline_enabled", False)
