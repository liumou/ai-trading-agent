"""手动交易页图表指标：GET /api/market-data/ohlcv 附加 indicators + /day-range 端点测试。

锁住三个契约：
  1) /ohlcv 默认附带 ``indicators`` 数组（与 candles 对齐、NaN→None、向后兼容）；
  2) /ohlcv 加 ``indicators=false`` 时只返回 candles（payload 更小，兼容旧调用）；
  3) /day-range 返回当日高/低 + ``is_current`` 当日对齐判定；空数据返回 null 不报错。

全部离线 mock：不联网、不需要真实 MT5 桥（CI 是 ubuntu 无桥环境）。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.routes.market_data import is_current_day


def make_ohlcv_df(n=100, start="2026-09-20 00:00:00", timeframe_h=0.25):
    """构造与真实 get_ohlcv 返回结构一致的 DataFrame（time 为 DatetimeIndex）。"""
    idx = pd.date_range(start=start, periods=n, freq=f"{int(timeframe_h * 60)}min")
    close = np.linspace(2300, 2400, n)
    high = close + 5
    low = close - 5
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


@pytest.fixture
def fake_engine():
    engine = MagicMock()
    engine.symbol = "GOLD"
    engine.market_data.get_ohlcv = AsyncMock(return_value=make_ohlcv_df())
    return engine


@pytest.fixture
def app(fake_engine):
    from fastapi import FastAPI

    from app.api.routes import market_data
    from app.bot.manager import set_global_manager

    manager = MagicMock()
    manager.engines = {"GOLD": fake_engine}
    manager.get_engine = MagicMock(side_effect=lambda sym: manager.engines.get(sym))
    manager.resolve_symbol = MagicMock(return_value=None)
    set_global_manager(manager)
    app = FastAPI()
    app.include_router(market_data.router)
    try:
        yield app
    finally:
        set_global_manager(None)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestOHLCVIndicators:
    async def test_ohlcv_returns_indicators_aligned(self, client, fake_engine):
        resp = await client.get("/api/market-data/ohlcv", params={"symbol": "GOLD", "count": 100})
        assert resp.status_code == 200
        data = resp.json()
        candles = data["candles"]
        indicators = data["indicators"]
        # 指标数组与蜡烛等长对齐
        assert len(indicators) == len(candles)
        # 各字段存在且为数值或 null
        keys = {
            "sma55", "ema20", "ema50", "rsi14",
            "macd", "macd_signal", "macd_histogram",
            "ichimoku_tenkan", "ichimoku_kijun",
            "ichimoku_senkou_a", "ichimoku_senkou_b", "ichimoku_chikou",
        }
        assert keys == set(indicators[0].keys())
        # candles 保持既有结构（向后兼容）
        assert set(candles[0]) == {"time", "open", "high", "low", "close"}

    async def test_ohlcv_indicators_nan_to_none(self, client, fake_engine):
        """SMA55 预热不足的前 54 根应为 null，而非 NaN/报错。"""
        resp = await client.get("/api/market-data/ohlcv", params={"symbol": "GOLD", "count": 100})
        indicators = resp.json()["indicators"]
        assert indicators[0]["sma55"] is None
        assert indicators[0]["rsi14"] is None
        # 最后若干根 SMA55 已预热（非 null）
        assert indicators[-1]["sma55"] is not None

    async def test_ohlcv_indicators_false_omits_field(self, client):
        """indicators=false → 只返回 candles，无 indicators 字段（旧调用方零影响）。"""
        resp = await client.get("/api/market-data/ohlcv", params={"symbol": "GOLD", "indicators": "false"})
        data = resp.json()
        assert "candles" in data
        assert "indicators" not in data

    async def test_ohlcv_empty_df_returns_candles(self, client, fake_engine):
        fake_engine.market_data.get_ohlcv.return_value = pd.DataFrame()
        resp = await client.get("/api/market-data/ohlcv", params={"symbol": "GOLD"})
        assert resp.status_code == 200
        assert resp.json() == {"candles": []}


class TestDayRange:
    def _d1_df(self, open_, high, low, day):
        return pd.DataFrame(
            {"open": [open_], "high": [high], "low": [low], "close": [high]},
            index=pd.DatetimeIndex([pd.Timestamp(day)]),
        )

    async def test_day_range_returns_current(self, client, fake_engine):
        # D1 最新 K = 今天（GOLD 是 metal，reset 22 UTC；用今天 00:00 开盘即当日）
        today = datetime.utcnow().date().isoformat()
        fake_engine.market_data.get_ohlcv.return_value = self._d1_df(2350.0, 2400.0, 2300.0, today)

        resp = await client.get("/api/market-data/day-range", params={"symbol": "GOLD"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_current"] is True
        assert body["day_range"] == {"open": 2350.0, "high": 2400.0, "low": 2300.0}
        assert body["date"] == today

    async def test_day_range_empty_returns_null(self, client, fake_engine):
        """桥离线 → day_range null 不报 500（对齐 /tick 语义）。"""
        fake_engine.market_data.get_ohlcv.return_value = pd.DataFrame()
        resp = await client.get("/api/market-data/day-range", params={"symbol": "GOLD"})
        assert resp.status_code == 200
        assert resp.json()["day_range"] is None
        assert resp.json()["is_current"] is False

    async def test_day_range_prior_day_not_current(self, client, fake_engine):
        """周末/盘前最新 D1 是上一交易日 → is_current=false（前端标注"上一交易日"）。"""
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
        fake_engine.market_data.get_ohlcv.return_value = self._d1_df(2300.0, 2350.0, 2250.0, yesterday)
        resp = await client.get("/api/market-data/day-range", params={"symbol": "GOLD"})
        body = resp.json()
        assert body["is_current"] is False
        assert body["day_range"] is not None  # 仍返回上一交易日数据供展示
        assert body["date"] == yesterday


class TestIsCurrentDay:
    def test_today_bar_is_current(self):
        # 今日 D1 K 开盘 → 属当日（无论资产类别与具体时刻）
        now = datetime(2026, 9, 21, 23, 0, 0)
        bar = pd.Timestamp("2026-09-21 00:00:00")
        assert is_current_day("metal", bar, now) is True

    def test_today_bar_any_hour_is_current(self):
        now = datetime(2026, 9, 21, 8, 0, 0)
        bar = pd.Timestamp("2026-09-21 00:00:00")
        assert is_current_day("metal", bar, now) is True

    def test_yesterday_bar_not_current(self):
        # 周末/盘前最新 D1 = 昨日开盘 → 非当日
        now = datetime(2026, 9, 21, 23, 0, 0)
        bar = pd.Timestamp("2026-09-20 00:00:00")
        assert is_current_day("metal", bar, now) is False

    def test_friday_bar_saturday_not_current(self):
        # 周六查询，最新 D1 = 周五 → 非当日（前端标注"上一交易日"）
        now = datetime(2026, 9, 26, 12, 0, 0)  # 周六
        bar = pd.Timestamp("2026-09-25 00:00:00")  # 周五
        assert is_current_day("metal", bar, now) is False

    def test_crypto_today_is_current(self):
        # 24/7 品种今日 K 恒为当日
        now = datetime(2026, 9, 21, 12, 0, 0)
        bar = pd.Timestamp("2026-09-21 00:00:00")
        assert is_current_day("crypto", bar, now) is True

    def test_nat_returns_false(self):
        assert is_current_day("metal", pd.NaT, datetime(2026, 9, 21, 12, 0, 0)) is False