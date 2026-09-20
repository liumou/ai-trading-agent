"""
Market data API routes — OHLCV candles for charting (multi-symbol).
"""

from fastapi import APIRouter, Depends, Query

from app.api.routes.bot import _get_engine, get_manager
from app.auth import require_auth
from app.config import SYMBOL_PROFILES

router = APIRouter(
    prefix="/api/market-data",
    tags=["market-data"],
    dependencies=[Depends(require_auth)],
)


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("GOLD"),
    timeframe: str = Query("M15"),
    count: int = Query(200, le=5000),
):
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
                "open": round(row["open"], decimals),
                "high": round(row["high"], decimals),
                "low": round(row["low"], decimals),
                "close": round(row["close"], decimals),
            }
        )
    return {"candles": candles}


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
