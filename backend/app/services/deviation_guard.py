"""deviation_guard — 入场价偏离（追涨杀跌）确定性检测，三通道共用。

把 systemone 里原先内嵌在 LocalRuleEngine 的 spike_chase（追高/杀跌）公式
抽成纯函数，供手动闸门（manual_order_gate）、统一硬闸门（order_preflight）、
SystemOne 引擎（systemone.LocalRuleEngine）共同复用 —— 同一口径、单一维护点。

语义（与 systemone._rule_spike_chase 完全一致）：
    z = |entry − EMA20| / ATR14
    以近 SPIKE_HIST_MIN 根的滚动 z 分位判严重度：
      - pct > block 阈值 且 连续 ≥ CONSEC_DIRECTIONAL_BARS 根同向 K 线 → block
      - pct > warn 阈值 → warn
      - 历史不足 SPIKE_HIST_MIN 根 → 只允许 warn，不允许 block（防少样本误杀）
    阈值由调用方通过 spike_pct_warn / spike_pct_block 注入（手动与自动可配不同档）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.strategy.indicators import atr as calc_atr
from app.strategy.indicators import ema as calc_ema

SPIKE_EMA_PERIOD = 20
ATR_PERIOD = 14
SPIKE_HIST_MIN = 100  # z 分位历史不足 100 根：只允许 warn，不允许 block
CONSEC_DIRECTIONAL_BARS = 4  # spike block 需要的连续同向 K 线


@dataclass
class SpikeResult:
    """spike_chase 判定结果。severity ∈ {"warn", "block"}。"""

    severity: str
    z: float
    pct: float
    detail: str


def _consecutive_directional_bars(m15: pd.DataFrame, direction: int) -> int:
    """从最新 K 线向前数连续同向实体 K 线根数（0/1 实体视为反向中断）。"""
    if m15 is None or m15.empty or "open" not in m15 or "close" not in m15:
        return 0
    body_dir = (m15["close"] > m15["open"]).astype(int).replace(0, -1)
    n = 0
    for v in reversed(body_dir.tolist()):
        if int(v) == direction:
            n += 1
        else:
            break
    return n


def detect_spike_chase(
    m15: pd.DataFrame | None,
    entry: float,
    direction: int,
    *,
    spike_pct_warn: float = 0.90,
    spike_pct_block: float = 0.99,
) -> SpikeResult | None:
    """检测入场价相对 EMA20 的追涨/杀跌偏离。

    m15: M15 OHLCV DataFrame（open/high/low/close 列）。
    entry: 下单参考价（市价单 = tick mid；挂单 = 挂单价）。
    direction: 1=BUY, -1=SELL。
    spike_pct_warn / spike_pct_block: 滚动分位阈值（调用方注入，可手动/自动不同档）。
    数据不可用（空/NaN/ATR≤0/历史不足）→ 返回 None（不误杀，由调用方决定后续）。
    """
    if m15 is None or m15.empty:
        return None
    close = m15["close"]
    ema20 = calc_ema(close, SPIKE_EMA_PERIOD)
    atr14 = calc_atr(m15["high"], m15["low"], close, ATR_PERIOD)
    if (
        ema20 is None
        or atr14 is None
        or pd.isna(ema20.iloc[-1])
        or pd.isna(atr14.iloc[-1])
        or float(atr14.iloc[-1]) <= 0
    ):
        return None

    ema_last = float(ema20.iloc[-1])
    # [jev-451] 方向语义：只对「顺向偏离」判定追涨/杀跌。
    #   BUY(1)  仅 entry > EMA 才是追涨；entry < EMA 是回调低吸，不标记
    #   SELL(-1) 仅 entry < EMA 才是杀跌；entry > EMA 是反弹反抽，不标记
    # 旧实现用 abs() 抹掉方向，把低吸（BUY 挂在均线下）误判成追高 block。
    deviation = float(entry) - ema_last
    if direction >= 0 and deviation <= 0:  # BUY 但没买在均线上方
        return None
    if direction < 0 and deviation >= 0:  # SELL 但没卖在均线下方
        return None

    z_now = abs(deviation) / float(atr14.iloc[-1])
    z_hist = ((close - ema20).abs() / atr14).dropna()
    pct = float((z_hist < z_now).mean()) if len(z_hist) else 0.0
    detail = f"入场偏离 z={z_now:.2f}（近 {len(z_hist)} 根分位 p{pct * 100:.0f}）"

    if (
        len(z_hist) >= SPIKE_HIST_MIN
        and pct > spike_pct_block
        and _consecutive_directional_bars(m15, direction) >= CONSEC_DIRECTIONAL_BARS
    ):
        return SpikeResult(
            "block", z_now, pct,
            f"{detail}，且连续 {CONSEC_DIRECTIONAL_BARS} 根同向K线",
        )
    if pct > spike_pct_warn:
        return SpikeResult("warn", z_now, pct, detail)
    return None
