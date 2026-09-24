"""
Technical indicators — pure pandas/numpy, no external dependencies.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """简单移动平均（Simple Moving Average），rolling 窗口均值。"""
    return series.rolling(length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=length, adjust=False).mean()
    avg_loss = loss.ewm(span=length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_wilder(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder 平滑 RSI，alpha=1/length，与 TradingView RMA 对齐。

    与现有 ``rsi``（ewm(span=length)）不同：``rsi`` 被策略/风控消费，改动有
    回归风险，故图表展示新增本函数。同行情下两值可差 1-3。
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD（指数平滑，EMA-based，与 TradingView/talib 一致）。

    返回 dict：macd = EMA(fast) - EMA(slow)；signal = EMA(signal) of macd；
    histogram = macd - signal。注意 signal 用 ewm(span=signal)，勿改 RMA。
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> dict:
    """Ichimoku 云图（标准定义，TradingView 对齐）。

    返回 dict：
    - tenkan = (HH(9) + LL(9)) / 2
    - kijun  = (HH(26) + LL(26)) / 2
    - senkou_a = (tenkan + kijun) / 2，投影未来 displacement 根（shift +displacement）
    - senkou_b = (HH(52) + LL(52)) / 2，投影未来 displacement 根
    - chikou  = close 回退 displacement 根（shift -displacement）

    位移方向是关键：索引时间升序时，``shift(+n)`` 把值推到未来（senkou 云），
    ``shift(-n)`` 把未来 close 拉回当前（chikou）。写反会错位 52 根。
    """
    hh_tenkan = high.rolling(tenkan).max()
    ll_tenkan = low.rolling(tenkan).min()
    tenkan_line = (hh_tenkan + ll_tenkan) / 2

    hh_kijun = high.rolling(kijun).max()
    ll_kijun = low.rolling(kijun).min()
    kijun_line = (hh_kijun + ll_kijun) / 2

    senkou_a = ((tenkan_line + kijun_line) / 2).shift(displacement)
    senkou_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(displacement)
    chikou = close.shift(-displacement)

    return {
        "tenkan": tenkan_line,
        "kijun": kijun_line,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": chikou,
    }


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> dict:
    """
    Average Directional Index (ADX) with +DI and -DI.
    Returns dict with keys: adx, di_plus, di_minus.
    ADX > 25 = trending market, < 20 = ranging/sideways.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Directional movement
    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    # Smoothed TR and DM (Wilder's smoothing = EMA with span=length)
    atr_smooth = tr.ewm(span=length, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(span=length, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(span=length, adjust=False).mean()

    di_plus = (plus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100
    di_minus = (minus_dm_smooth / atr_smooth.replace(0, np.nan)) * 100

    dx = (abs(di_plus - di_minus) / (di_plus + di_minus).replace(0, np.nan)) * 100
    adx_line = dx.ewm(span=length, adjust=False).mean()

    return {"adx": adx_line, "di_plus": di_plus, "di_minus": di_minus}


def bollinger_bands(series: pd.Series, length: int = 20, std_dev: float = 2.0) -> dict:
    """Bollinger Bands: middle (SMA), upper, lower, bandwidth, %B."""
    middle = series.rolling(length).mean()
    std = series.rolling(length).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    bandwidth = (upper - lower) / middle  # normalized bandwidth
    pct_b = (series - lower) / (upper - lower)  # %B (0=lower, 1=upper)
    return {"middle": middle, "upper": upper, "lower": lower, "bandwidth": bandwidth, "pct_b": pct_b}


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> dict:
    """Stochastic oscillator: %K and %D."""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(d_period).mean()
    return {"k": k, "d": d}
