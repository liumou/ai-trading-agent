"""deviation_guard 单元测试 —— 追涨杀跌确定性检测。

锁定行为（与 systemone._rule_spike_chase 语义一致）：
- 数据不可用（空 df / NaN / ATR≤0 / 历史不足）→ None（不误杀）
- 历史 ≥100 根 + 分位 >block 阈值 + 连续 ≥4 根同向 → block
- 分位 >warn 阈值 → warn
- 历史 <100 根 → 只允许 warn，不允许 block
"""

import numpy as np
import pandas as pd

from app.services.deviation_guard import detect_spike_chase


def _df(rows=120, base=2000.0, per_bar=0.0, vol=1.0, seed=7, last_tail=None):
    """构造 OHLCV。last_tail 若给定，覆盖最后 N 根为同向实体（制造连续 K 线）。"""
    rng = np.random.default_rng(seed)
    close = base + np.arange(rows) * per_bar + rng.normal(0, vol, rows)
    high = close + np.abs(rng.normal(0, vol * 0.5, rows)) + vol * 0.5
    low = close - np.abs(rng.normal(0, vol * 0.5, rows)) - vol * 0.5
    open_ = close + rng.normal(0, vol * 0.3, rows)
    if last_tail is not None:
        n, dirn = last_tail
        # 让最后 n 根为同向上涨实体（close > open）
        for i in range(1, n + 1):
            idx = len(close) - i
            step = vol * 0.4 * (1 if dirn > 0 else -1)
            open_[idx] = close[idx] - step if dirn > 0 else close[idx] + step
            high[idx] = max(high[idx], open_[idx], close[idx])
            low[idx] = min(low[idx], open_[idx], close[idx])
    idx = pd.date_range("2026-01-05 09:00", periods=rows, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _spike_df(entry_spread_z=3.0, rows=120):
    """构造入场偏离 EMA20 达 z 分位的 df：最近一根 close 远离均线。"""
    base = 2000.0
    rng = np.random.default_rng(42)
    close = base + rng.normal(0, 0.8, rows)
    close[-1] = base + entry_spread_z * 2.0  # 末根远离 EMA20（ATR≈1 时 z≈4~5）
    high = close + np.abs(rng.normal(0, 0.4, rows)) + 0.4
    low = close - np.abs(rng.normal(0, 0.4, rows)) - 0.4
    open_ = close + rng.normal(0, 0.3, rows)
    idx = pd.date_range("2026-01-05 09:00", periods=rows, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


# ─── 数据不可用 ────────────────────────────────────────────────────────────


def test_empty_df_returns_none():
    assert detect_spike_chase(pd.DataFrame(), 2000.0, 1) is None


def test_none_df_returns_none():
    assert detect_spike_chase(None, 2000.0, 1) is None


def test_atr_zero_returns_none():
    df = _df(rows=120, vol=0.0)  # 无波动 ATR≈0
    assert detect_spike_chase(df, 2000.0, 1) is None


def test_too_few_bars_never_block():
    """历史 <100 根：即使 z 极端也不得 block（只允许 warn）。"""
    df = _spike_df(entry_spread_z=5.0, rows=90)  # 不足 SPIKE_HIST_MIN
    res = detect_spike_chase(df, df["close"].iloc[-1], 1)
    assert res is None or res.severity == "warn"


# ─── 正常/偏离判定 ───────────────────────────────────────────────────────


def test_close_to_ema_no_flag():
    """入场价贴近 EMA20 → 无 spike 标记。"""
    df = _df()
    res = detect_spike_chase(df, 2000.0, 1)
    assert res is None


def test_extreme_deviation_warns():
    """z 极端 → 至少 warn（数据充足时）。"""
    df = _spike_df(entry_spread_z=4.0, rows=120)
    res = detect_spike_chase(df, df["close"].iloc[-1], 1)
    assert res is not None
    assert res.severity in ("warn", "block")


def test_block_requires_consecutive_directional_bars():
    """极端偏离但无连续同向 K 线 → 不得 block（防单根脉冲误杀）。"""
    # 用平盘 + 单独一根跳空（无连续实体上涨）
    df = _df(rows=120, vol=1.0, seed=9)
    res = detect_spike_chase(df, df["close"].iloc[-1] + 5.0, 1)
    if res is not None:
        assert res.severity == "warn"  # 不可能是 block


def test_custom_thresholds_applied():
    """调用方注入不同档位阈值生效。"""
    df = _spike_df(entry_spread_z=3.0, rows=120)
    res = detect_spike_chase(df, df["close"].iloc[-1], 1, spike_pct_warn=0.5, spike_pct_block=0.6)
    assert res is not None


# ─── 方向语义（jev-451：abs() 抹掉方向导致低吸/反抽被误判为追高/杀跌）─────


def _trend_df(*, up=True, rows=120, last_bull_bars=4):
    """构造趋势 df：末 N 根为同向实体 K 线，制造明确的顺向动能。"""
    rng = np.random.default_rng(11)
    base = 4000.0
    slope = np.linspace(0, 90, rows) if up else np.linspace(0, -90, rows)
    close = base + slope + rng.normal(0, 5, rows)
    high = close + np.abs(rng.normal(0, 4, rows)) + 3
    low = close - np.abs(rng.normal(0, 4, rows)) - 3
    open_ = close + rng.normal(0, 3, rows)
    for i in range(1, last_bull_bars + 1):
        idx = -i
        # up=True 时末几根为阳线（close>open），反之阴线
        open_[idx] = close[idx] - 4 if up else close[idx] + 4
        high[idx] = max(high[idx], open_[idx], close[idx])
        low[idx] = min(low[idx], open_[idx], close[idx])
    idx_ = pd.date_range("2026-01-05 09:00", periods=rows, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx_)


def _ema20(df):
    return float(df["close"].ewm(span=20, adjust=False).mean().iloc[-1])


def test_buy_below_ema_is_not_chase():
    """[jev-451] BUY 挂单价在 EMA20 下方 = 回调低吸，绝非追高 → 不得标记。

    用户场景：BUY_LIMIT 4160，现价 4190（EMA≈4184），价格需下跌才成交。
    旧实现用 abs(entry−EMA) 抹掉方向，把 -23.7 的偏离当成 +23.7 的追高 → 误 block。
    """
    df = _trend_df(up=True)
    ema = _ema20(df)
    entry = ema - 25.0  # 挂在均线下方 25 点（回调区）
    res = detect_spike_chase(df, entry, 1)  # direction=1 (BUY)
    assert res is None, f"BUY 在 EMA 下方（低吸）不应触发 spike_chase，但得到 {res}"


def test_sell_above_ema_is_not_chase():
    """[jev-451] SELL 挂单价在 EMA20 上方 = 反弹反抽，绝非杀跌 → 不得标记。"""
    df = _trend_df(up=False)
    ema = _ema20(df)
    entry = ema + 25.0  # 挂在均线上方（反抽区）
    res = detect_spike_chase(df, entry, -1)  # direction=-1 (SELL)
    assert res is None, f"SELL 在 EMA 上方（反抽）不应触发 spike_chase，但得到 {res}"


def test_buy_above_ema_still_flags():
    """方向校验不得放宽顺向判定：BUY 在 EMA 上方（真追涨）仍应标记。"""
    df = _trend_df(up=True)
    ema = _ema20(df)
    entry = ema + 40.0  # 明显买在均线上方
    res = detect_spike_chase(df, entry, 1)
    assert res is not None, "BUY 在 EMA 上方（追涨）仍应触发"
    assert res.severity in ("warn", "block")


def test_sell_below_ema_still_flags():
    """方向校验不得放宽顺向判定：SELL 在 EMA 下方（真杀跌）仍应标记。"""
    df = _trend_df(up=False)
    ema = _ema20(df)
    entry = ema - 40.0  # 明显卖在均线下方
    res = detect_spike_chase(df, entry, -1)
    assert res is not None, "SELL 在 EMA 下方（杀跌）仍应触发"
    assert res.severity in ("warn", "block")
