"""
Market data API routes — OHLCV candles + indicators for charting, day-range.

- GET /ohlcv       — candles (+ optional computed technical indicators).
- GET /tick        — current bid/ask/spread for a symbol.
- GET /day-range   — current trading-day high/low (D1 latest bar + is_current).
- GET /symbols     — configured symbols with their profiles.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, Query

from app.api.routes.bot import _get_engine, get_manager
from app.auth import require_auth
from app.config import SYMBOL_PROFILES
from app.constants import (
    INDICATOR_DECIMALS,
    INDICATOR_EMA_FAST,
    INDICATOR_EMA_SLOW,
    INDICATOR_ICHIMOKU_DISPLACEMENT,
    INDICATOR_ICHIMOKU_KIJUN,
    INDICATOR_ICHIMOKU_SENKOU_B,
    INDICATOR_ICHIMOKU_TENKAN,
    INDICATOR_MACD_FAST,
    INDICATOR_MACD_SIGNAL,
    INDICATOR_MACD_SLOW,
    INDICATOR_RSI_LENGTH,
    INDICATOR_SMA_LENGTH,
)
from app.strategy.indicators import ema, ichimoku, macd, rsi_wilder, sma

router = APIRouter(
    prefix="/api/market-data",
    tags=["market-data"],
    dependencies=[Depends(require_auth)],
)


def _round_or_none(value: float, decimals: int = INDICATOR_DECIMALS) -> float | None:
    """把 numpy 标量规范化为可 JSON 序列化的数值；NaN → None。"""
    if value is None:
        return None
    v = float(value)
    if pd.isna(v):
        return None
    return round(v, decimals)


def _asset_class(symbol: str) -> str | None:
    """取品种的资产类别（用于当日对齐判定）。"""
    return SYMBOL_PROFILES.get(symbol, {}).get("asset_class")


def compute_indicators(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """基于原始 OHLCV DataFrame 计算全套图表指标。

    返回与 df 行一一对齐的 dict 列表，每个元素含 sma55/ema20/ema50/
    rsi14/macd/macd_signal/macd_histogram/ichimoku_* 字段；未定义值 → None。

    关键：指标必须基于**未 round** 的原始 OHLC 计算，最后一步才按
    INDICATOR_DECIMALS 舍入——在 round 后的 close 上算 MACD/Ichimoku 会放大
    舍入噪声（如 BTCUSD @ ~100000 的 decimals=2 会损失有效位）。
    """
    if df.empty:
        return []
    p = params or {}
    sma_len = p.get("sma", INDICATOR_SMA_LENGTH)
    ema_fast = p.get("ema_fast", INDICATOR_EMA_FAST)
    ema_slow = p.get("ema_slow", INDICATOR_EMA_SLOW)
    rsi_len = p.get("rsi", INDICATOR_RSI_LENGTH)
    m_fast = p.get("macd_fast", INDICATOR_MACD_FAST)
    m_slow = p.get("macd_slow", INDICATOR_MACD_SLOW)
    m_signal = p.get("macd_signal", INDICATOR_MACD_SIGNAL)
    i_tenkan = p.get("ichimoku_tenkan", INDICATOR_ICHIMOKU_TENKAN)
    i_kijun = p.get("ichimoku_kijun", INDICATOR_ICHIMOKU_KIJUN)
    i_senkou_b = p.get("ichimoku_senkou_b", INDICATOR_ICHIMOKU_SENKOU_B)
    i_disp = p.get("ichimoku_displacement", INDICATOR_ICHIMOKU_DISPLACEMENT)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    sma55 = sma(close, sma_len)
    ema20 = ema(close, ema_fast)
    ema50 = ema(close, ema_slow)
    rsi14 = rsi_wilder(close, rsi_len)
    macd_res = macd(close, m_fast, m_slow, m_signal)
    ichi = ichimoku(high, low, close, i_tenkan, i_kijun, i_senkou_b, i_disp)

    rows: list[dict] = []
    for i in range(len(df)):
        rows.append(
            {
                "sma55": _round_or_none(sma55.iloc[i]),
                "ema20": _round_or_none(ema20.iloc[i]),
                "ema50": _round_or_none(ema50.iloc[i]),
                "rsi14": _round_or_none(rsi14.iloc[i]),
                "macd": _round_or_none(macd_res["macd"].iloc[i]),
                "macd_signal": _round_or_none(macd_res["signal"].iloc[i]),
                "macd_histogram": _round_or_none(macd_res["histogram"].iloc[i]),
                "ichimoku_tenkan": _round_or_none(ichi["tenkan"].iloc[i]),
                "ichimoku_kijun": _round_or_none(ichi["kijun"].iloc[i]),
                "ichimoku_senkou_a": _round_or_none(ichi["senkou_a"].iloc[i]),
                "ichimoku_senkou_b": _round_or_none(ichi["senkou_b"].iloc[i]),
                "ichimoku_chikou": _round_or_none(ichi["chikou"].iloc[i]),
            }
        )
    return rows


def is_current_day(asset_class: str | None, bar_open: pd.Timestamp | None, now: datetime | None = None) -> bool:
    """判断一根 D1 K 线是否属于"当前交易日"（自然日 UTC）。

    用 D1 K 的开盘日期与 now 的 UTC 日期是否相同判定。在两种经纪商日界
    模型（MT5 服务器 00:00 或 22:00 切换日界线）下都正确：最新 D1 K 的
    time 始终是当日开始时戳，只要它是今天聚合的 K，日期即等于 today。

    典型场景：
    - 周末/盘前：最新 D1 是上周五 → date ≠ today → False（前端标注
      "上一交易日"，不裸标"今日"）。
    - 24/7（crypto）：今日 K 恒在 → True。
    - 空/NaT：False。
    """
    n = now or datetime.utcnow()
    if bar_open is None:
        return False
    # NaN 防护：pd.NaT 不等于自身
    if bar_open != bar_open:  # type: ignore[comparison-overlap]
        return False
    # asset_class 保留在签名中（未来若支持自定义日界可扩展），当前用自然日判定
    _ = asset_class
    return bar_open.date() == n.date()


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("GOLD"),
    timeframe: str = Query("M15"),
    count: int = Query(200, le=5000),
    indicators: bool = Query(True),
    indicator_params: str | None = Query(None),
):
    """OHLCV 蜡烛（+ 可选技术指标）。

    ``indicators`` 为 False 时只返回蜡烛（payload 更小）；``indicator_params``
    可选 JSON 覆盖指标周期（默认取 constants 集中常量）。candles 字段保持
    向后兼容。
    """
    engine = _get_engine(symbol)
    # market_data.get_ohlcv internally resolves canonical → broker alias
    df = await engine.market_data.get_ohlcv(engine.symbol, timeframe, count)
    if df.empty:
        return {"candles": []}

    profile = SYMBOL_PROFILES.get(engine.symbol, {})
    decimals = profile.get("price_decimals", 2)

    candles = []
    for ts, row in df.iterrows():
        candles.append(
            {
                "time": int(ts.timestamp()),
                "open": round(float(row["open"]), decimals),
                "high": round(float(row["high"]), decimals),
                "low": round(float(row["low"]), decimals),
                "close": round(float(row["close"]), decimals),
            }
        )

    resp: dict = {"candles": candles}
    if indicators:
        params = None
        if indicator_params:
            try:
                import json

                params = json.loads(indicator_params)
            except json.JSONDecodeError:
                params = None
        resp["indicators"] = compute_indicators(df, params)
    return resp


@router.get("/day-range")
async def get_day_range(symbol: str = Query("GOLD")):
    """当前交易日的高/低/开盘价（读 D1 最新一根 K）。

    - MT5 离线/未订阅 → ``day_range: null``（不报 500，对齐 /tick 语义）。
    - 周末/盘前最新 D1 是上一交易日 → ``day_range: null`` + ``is_current: false``，
      前端标注"上一交易日"而非裸标"今日"。
    - 返回 ``date``（D1 K 的日期，UTC）供前端展示。
    """
    engine = _get_engine(symbol)
    df = await engine.market_data.get_ohlcv(engine.symbol, "D1", 2)
    if df.empty:
        return {"symbol": engine.symbol, "date": None, "day_range": None, "is_current": False}

    last = df.iloc[-1]
    bar_date = df.index[-1]
    asset = _asset_class(engine.symbol)
    current = is_current_day(asset, bar_date)
    decimals = SYMBOL_PROFILES.get(engine.symbol, {}).get("price_decimals", 2)

    return {
        "symbol": engine.symbol,
        "date": bar_date.strftime("%Y-%m-%d"),
        "day_range": {
            "open": round(float(last["open"]), decimals),
            "high": round(float(last["high"]), decimals),
            "low": round(float(last["low"]), decimals),
        },
        "is_current": current,
    }


@router.get("/tick")
async def get_tick(symbol: str = Query("GOLD")):
    """当前品种最新 tick（bid/ask/点差）—— 手动交易页报价的兜底取值。

    主链路是 WS ``price_update``（scheduler 每秒推全部引擎，无 RUNNING 过滤）；
    本端点用于刚进页面、切换品种或 WS 断线时拿到一次报价。

    ``validate=False`` 是刻意的：这里是展示用途，tick 略陈旧或点差暂时偏大
    也应显示出来（风控闸门另有自己的严格校验），validate=True 会在这些情况下
    返回 None，让页面价格无谓地闪空。
    """
    engine = _get_engine(symbol)
    tick = await engine.market_data.get_current_tick(engine.symbol, validate=False)
    if not tick:
        return {"tick": None}

    return {
        "tick": {
            "symbol": engine.symbol,
            "bid": tick.get("bid"),
            "ask": tick.get("ask"),
            "spread": tick.get("spread"),
            "time": tick.get("time"),
        }
    }


@router.get("/symbols")
async def get_symbols():
    """Return all configured symbols with their profiles."""
    mgr = get_manager()
    symbols = []
    for sym in mgr.get_symbols():
        profile = SYMBOL_PROFILES.get(sym, {})
        engine = mgr.get_engine(sym)
        symbols.append(
            {
                "symbol": sym,
                "display_name": profile.get("display_name", sym),
                "timeframe": engine.timeframe if engine else profile.get("default_timeframe", "M15"),
                "state": engine.state.value if engine else "STOPPED",
                "price_decimals": profile.get("price_decimals", 2),
                "max_lot": profile.get("max_lot", 1.0),
                "default_lot": profile.get("default_lot", 0.1),
                "ml_tp_pips": profile.get("ml_tp_pips", 5.0),
                "ml_sl_pips": profile.get("ml_sl_pips", 5.0),
                "ml_forward_bars": profile.get("ml_forward_bars", 10),
                "ml_timeframe": profile.get("ml_timeframe", profile.get("default_timeframe", "M15")),
            }
        )
    return {"symbols": symbols}