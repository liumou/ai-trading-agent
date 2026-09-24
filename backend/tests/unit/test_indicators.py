"""
Unit tests for technical indicators — pure function tests.
"""

import numpy as np
import pandas as pd
import pytest

from app.strategy.indicators import adx, atr, bollinger_bands, ema, ichimoku, macd, rsi, rsi_wilder, sma, stochastic


class TestEMA:
    def test_ema_basic(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(series, 3)
        assert len(result) == 5
        assert result.iloc[-1] > result.iloc[0]  # uptrend

    def test_ema_constant(self):
        series = pd.Series([10.0] * 20)
        result = ema(series, 5)
        np.testing.assert_allclose(result.values, 10.0, atol=1e-10)

    def test_ema_length_1(self):
        series = pd.Series([1.0, 2.0, 3.0])
        result = ema(series, 1)
        pd.testing.assert_series_equal(result, series)

    def test_ema_follows_trend(self):
        up = pd.Series(np.linspace(100, 200, 50))
        result = ema(up, 10)
        # EMA should lag behind the uptrend
        assert result.iloc[-1] < up.iloc[-1]
        assert result.iloc[-1] > up.iloc[0]


class TestRSI:
    def test_rsi_all_gains(self):
        series = pd.Series(np.linspace(100, 200, 30))
        result = rsi(series, 14)
        # All gains → RSI near 100
        assert result.iloc[-1] > 90

    def test_rsi_all_losses(self):
        series = pd.Series(np.linspace(200, 100, 30))
        result = rsi(series, 14)
        # All losses → RSI near 0
        assert result.iloc[-1] < 10

    def test_rsi_range(self):
        np.random.seed(42)
        series = pd.Series(np.random.randn(100).cumsum() + 100)
        result = rsi(series, 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_equal_gains_losses(self):
        # Alternating up/down should give RSI around 50
        series = pd.Series([100 + (i % 2) * 2 for i in range(50)], dtype=float)
        result = rsi(series, 14)
        assert 30 < result.iloc[-1] < 70


class TestATR:
    def test_atr_basic(self):
        n = 30
        high = pd.Series(np.full(n, 105.0))
        low = pd.Series(np.full(n, 95.0))
        close = pd.Series(np.full(n, 100.0))
        result = atr(high, low, close, 14)
        # Constant range → ATR converges to 10
        assert abs(result.iloc[-1] - 10.0) < 1.0

    def test_atr_positive(self):
        np.random.seed(42)
        close = pd.Series(np.random.randn(50).cumsum() + 100)
        high = close + abs(np.random.randn(50)) * 2
        low = close - abs(np.random.randn(50)) * 2
        result = atr(high, low, close, 14)
        valid = result.dropna()
        assert (valid > 0).all()


class TestADX:
    def test_adx_trending(self):
        # Strong uptrend → ADX should be high
        n = 60
        close = pd.Series(np.linspace(100, 200, n))
        high = close + 2
        low = close - 2
        result = adx(high, low, close, 14)
        assert result["adx"].iloc[-1] > 20

    def test_adx_flat(self):
        # Flat market → ADX should be low
        n = 60
        np.random.seed(42)
        close = pd.Series(100 + np.random.randn(n) * 0.5)
        high = close + 0.5
        low = close - 0.5
        result = adx(high, low, close, 14)
        assert result["adx"].iloc[-1] < 40

    def test_adx_returns_all_keys(self):
        n = 30
        close = pd.Series(np.linspace(100, 120, n))
        high = close + 1
        low = close - 1
        result = adx(high, low, close, 14)
        assert "adx" in result
        assert "di_plus" in result
        assert "di_minus" in result


class TestBollingerBands:
    def test_bb_contains_price(self):
        np.random.seed(42)
        series = pd.Series(np.random.randn(50).cumsum() + 100)
        result = bollinger_bands(series, 20, 2.0)
        # Most prices should be within bands
        valid_idx = result["upper"].dropna().index
        within = (series[valid_idx] <= result["upper"][valid_idx]) & (series[valid_idx] >= result["lower"][valid_idx])
        assert within.sum() / len(within) > 0.8

    def test_bb_bandwidth_positive(self):
        series = pd.Series(np.linspace(100, 110, 30))
        result = bollinger_bands(series, 20, 2.0)
        valid = result["bandwidth"].dropna()
        assert (valid >= 0).all()

    def test_bb_pct_b_range(self):
        np.random.seed(42)
        series = pd.Series(np.random.randn(50).cumsum() + 100)
        result = bollinger_bands(series, 20, 2.0)
        # %B should mostly be between 0 and 1
        valid = result["pct_b"].dropna()
        assert valid.median() > 0
        assert valid.median() < 1


class TestStochastic:
    def test_stochastic_at_high(self):
        # Close at the high of the range
        n = 20
        high = pd.Series(np.full(n, 110.0))
        low = pd.Series(np.full(n, 90.0))
        close = pd.Series(np.full(n, 110.0))
        result = stochastic(high, low, close, 14, 3)
        assert result["k"].iloc[-1] == pytest.approx(100.0)

    def test_stochastic_at_low(self):
        n = 20
        high = pd.Series(np.full(n, 110.0))
        low = pd.Series(np.full(n, 90.0))
        close = pd.Series(np.full(n, 90.0))
        result = stochastic(high, low, close, 14, 3)
        assert result["k"].iloc[-1] == pytest.approx(0.0)

    def test_stochastic_returns_k_and_d(self):
        n = 20
        high = pd.Series(np.linspace(105, 115, n))
        low = pd.Series(np.linspace(95, 105, n))
        close = pd.Series(np.linspace(100, 110, n))
        result = stochastic(high, low, close, 14, 3)
        assert "k" in result
        assert "d" in result


class TestSMA:
    def test_sma_matches_rolling(self):
        np.random.seed(7)
        series = pd.Series(np.random.randn(80).cumsum() + 100)
        result = sma(series, 55)
        expected = series.rolling(55).mean()
        pd.testing.assert_series_equal(result, expected)

    def test_sma_warmup_nan(self):
        series = pd.Series(np.linspace(1, 80, 80))
        result = sma(series, 55)
        # 前 54 个值应为 NaN（预热不足）
        assert result.iloc[:54].isna().all()
        assert not result.iloc[54:].isna().any()

    def test_sma_constant(self):
        series = pd.Series([10.0] * 60)
        result = sma(series, 55)
        pd.testing.assert_series_equal(result.dropna(), pd.Series([10.0] * 6, index=range(54, 60)))


class TestRSIWilder:
    def test_rsi_wilder_all_gains_near_100(self):
        series = pd.Series(np.linspace(100, 200, 30))
        result = rsi_wilder(series, 14)
        assert result.iloc[-1] > 90

    def test_rsi_wilder_range(self):
        np.random.seed(42)
        series = pd.Series(np.random.randn(100).cumsum() + 100)
        result = rsi_wilder(series, 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_wilder_differs_from_standard_rsi(self):
        # Wilder 平滑（alpha=1/14）与 ewm(span=14) 结果不同，但方向一致
        np.random.seed(42)
        series = pd.Series(np.random.randn(100).cumsum() + 100)
        w = rsi_wilder(series, 14)
        s = rsi(series, 14)
        # 两者都是合法 RSI（0-100），且最后值接近但不必相等
        assert abs(w.iloc[-1] - s.iloc[-1]) > 1e-6


class TestMACD:
    def test_macd_constant_series_zero(self):
        # 常数序列：MACD/hist 恒为 0
        series = pd.Series([100.0] * 60)
        result = macd(series, 12, 26, 9)
        pd.testing.assert_series_equal(
            result["macd"].dropna(), pd.Series([0.0] * len(result["macd"].dropna()), index=result["macd"].dropna().index), check_dtype=False
        )
        pd.testing.assert_series_equal(
            result["histogram"].dropna(),
            pd.Series([0.0] * len(result["histogram"].dropna()), index=result["histogram"].dropna().index),
            check_dtype=False,
        )

    def test_macd_uptrend_positive(self):
        # 单调上升：macd 应 > 0（快线在上）
        series = pd.Series(np.linspace(100, 200, 60))
        result = macd(series, 12, 26, 9)
        assert result["macd"].iloc[-1] > 0

    def test_macd_histogram_relation(self):
        series = pd.Series(np.linspace(100, 200, 60))
        result = macd(series, 12, 26, 9)
        expected_hist = result["macd"] - result["signal"]
        pd.testing.assert_series_equal(result["histogram"], expected_hist)

    def test_macd_signal_is_ema_of_macd(self):
        # signal 应等于 macd 线的 EMA(9)
        series = pd.Series(np.linspace(100, 200, 60))
        result = macd(series, 12, 26, 9)
        expected_signal = result["macd"].ewm(span=9, adjust=False).mean()
        pd.testing.assert_series_equal(result["signal"], expected_signal)


class TestIchimoku:
    def _sample(self, n=80):
        np.random.seed(1)
        high = pd.Series(np.linspace(105, 200, n) + np.random.rand(n) * 5)
        low = pd.Series(np.linspace(95, 180, n) - np.random.rand(n) * 5)
        close = pd.Series(np.linspace(100, 190, n))
        # 保证 high >= close >= low
        high = high.clip(lower=close)
        low = low.clip(upper=close)
        return high, low, close

    def test_ichimoku_returns_all_keys(self):
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close)
        for key in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou"):
            assert key in result
            assert len(result[key]) == len(close)

    def test_ichimoku_tenkan_formula(self):
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, tenkan=9)
        expected = (high.rolling(9).max() + low.rolling(9).min()) / 2
        pd.testing.assert_series_equal(result["tenkan"], expected)

    def test_ichimoku_kijun_formula(self):
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, kijun=26)
        expected = (high.rolling(26).max() + low.rolling(26).min()) / 2
        pd.testing.assert_series_equal(result["kijun"], expected)

    def test_ichimoku_senkou_a_shift_forward(self):
        # senkou_a 是 (tenkan+kijun)/2 前移 26 根：senkou_a[t] == base[t-26]
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, displacement=26)
        base = (result["tenkan"] + result["kijun"]) / 2
        expected = base.shift(26)
        pd.testing.assert_series_equal(result["senkou_a"], expected, check_freq=False)

    def test_ichimoku_senkou_b_shift_forward(self):
        # senkou_b 是 (HH52+LL52)/2 前移 26 根
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, senkou_b=52, displacement=26)
        base = (high.rolling(52).max() + low.rolling(52).min()) / 2
        expected = base.shift(26)
        pd.testing.assert_series_equal(result["senkou_b"], expected, check_freq=False)

    def test_ichimoku_chikou_shift_backward(self):
        # chikou 是 close 后移 26 根：chikou[t] == close[t+26]
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, displacement=26)
        expected = close.shift(-26)
        pd.testing.assert_series_equal(result["chikou"], expected, check_freq=False)

    def test_ichimoku_senkou_b_warmup_nan(self):
        # senkou_b 预热 = 52 根滚动 + 26 位移 = 前 77 根 NaN（80 根数据时第 0..77 为 NaN）
        high, low, close = self._sample(80)
        result = ichimoku(high, low, close, senkou_b=52, displacement=26)
        # 滚动 52 在第 51 行开始有效，再 shift(26) 后有效值从 51+26=77 行开始
        assert result["senkou_b"].iloc[:77].isna().all()
        assert not result["senkou_b"].iloc[77:].isna().any()

    def test_ichimoku_short_series(self):
        # 不足 52 根：senkou_b 全 NaN
        high, low, close = self._sample(30)
        result = ichimoku(high, low, close)
        assert result["senkou_b"].isna().all()
        assert not result["tenkan"].isna().all()
