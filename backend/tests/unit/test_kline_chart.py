"""
K 线图生成单元测试 — 校验 PNG 渲染、均线计算与夜窗判定。
"""

import os

os.environ.setdefault("MPLBACKEND", "Agg")

from datetime import datetime

import pandas as pd
import pytest

from app.notifications.kline_chart import build_kline_png, moving_average
from app.services.kline_sender import is_night_window


def _make_df(n: int = 120, seed: int = 1) -> pd.DataFrame:
    """生成单调递增 time 索引 + OHLCV 的测试 DataFrame。

    成交量列用 ``tick_volume``（与生产 ``MarketDataService.get_ohlcv`` 一致），
    保证测试覆盖真实数据形态 —— mplfinance 的 volume=True 要求列名恰为 volume，
    渲染入口负责归一化。
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rng.uniform(0, 0.3, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.3, n)
    tick_volume = rng.integers(100, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "tick_volume": tick_volume},
        index=idx,
    )


# ─── 均线计算 ────────────────────────────────────────────────────────────


class TestMovingAverage:
    def test_ma_matches_rolling_mean(self):
        df = _make_df(n=60)
        for period in (5, 10, 20, 55):
            ma = moving_average(df["close"], period)
            assert ma.iloc[period - 1] == pytest.approx(df["close"].iloc[:period].mean())
            # 前 period-1 个为 NaN
            assert ma.iloc[: period - 1].isna().all()

    def test_ma_short_series_returns_all_nan(self):
        s = pd.Series([1.0, 2.0, 3.0])
        assert moving_average(s, 10).isna().all()


# ─── PNG 渲染 ─────────────────────────────────────────────────────────────


class TestBuildKlinePng:
    def test_returns_nonempty_png_bytes(self):
        df = _make_df(n=120)
        png = build_kline_png("GOLD", "M15", df, display_name="Gold (XAUUSD)")
        assert png is not None
        assert len(png) > 1000
        # PNG 魔数
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_tick_volume_column_is_normalized(self):
        # 生产数据列名是 tick_volume；build_kline_png 应归一化为 volume 渲染成功
        df = _make_df(n=120)
        png = build_kline_png("GOLD", "M15", df)
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_default_ma_periods_include_55(self):
        from app.notifications.kline_chart import DEFAULT_MA_PERIODS

        assert DEFAULT_MA_PERIODS == (5, 10, 20, 55)

    def test_empty_df_returns_none(self):
        assert build_kline_png("GOLD", "M15", pd.DataFrame()) is None

    def test_missing_ohlc_returns_none(self):
        df = pd.DataFrame({"open": [1], "high": [2]})
        assert build_kline_png("GOLD", "M15", df) is None

    def test_single_bar_returns_none(self):
        df = _make_df(n=1)
        assert build_kline_png("GOLD", "M15", df) is None

    def test_few_bars_still_renders(self):
        # 20 根不足 MA55，但应能渲染（均线尾部为空）
        df = _make_df(n=20)
        png = build_kline_png("GOLD", "M15", df)
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ─── 夜窗判定 ────────────────────────────────────────────────────────────


class TestIsNightWindow:
    def test_midnight_is_night(self):
        assert is_night_window(datetime(2026, 1, 1, 0, 0))

    def test_3am_is_night(self):
        assert is_night_window(datetime(2026, 1, 1, 3, 0))

    def test_7_59_is_night(self):
        assert is_night_window(datetime(2026, 1, 1, 7, 59))

    def test_8am_is_not_night(self):
        assert not is_night_window(datetime(2026, 1, 1, 8, 0))

    def test_noon_is_not_night(self):
        assert not is_night_window(datetime(2026, 1, 1, 12, 0))

    def test_11_59pm_is_not_night(self):
        assert not is_night_window(datetime(2026, 1, 1, 23, 59))

    def test_tz_naive_treated_as_shanghai(self):
        # 上海 12:00 = UTC 04:00；naive 输入按上海墙钟判定
        assert not is_night_window(datetime(2026, 1, 1, 12, 0))
