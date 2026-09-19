"""
MT5 Bridge — FastAPI app that runs on Windows VPS only.
Provides HTTP API to interact with MetaTrader 5.
"""

import hmac
import math
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

import MetaTrader5 as mt5
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

load_dotenv()

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")

# 最近活跃账号（I3 修复）：初始为 env 账号，/account/switch 成功后更新。
# ensure_connected() 断线重连用此账号，避免切换后断线自动翻回 env 账号
# 导致引擎跑在新账号、实际持仓却在 env 账号的静默错乱。
_active_login: int = MT5_LOGIN
_active_password: str = MT5_PASSWORD
_active_server: str = MT5_SERVER

BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "")
if not BRIDGE_API_KEY:
    logger.warning("BRIDGE_API_KEY not set — bridge will reject all requests until configured")

app = FastAPI(title="MT5 Bridge", version="1.0.0")

# --- Timeframe mapping ---
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
}


# --- Auth dependency ---
async def verify_api_key(x_bridge_key: str = Header(...)):
    if not BRIDGE_API_KEY:
        raise HTTPException(status_code=503, detail="Bridge API key not configured")
    if not hmac.compare_digest(x_bridge_key, BRIDGE_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


# --- MT5 connection helpers ---
def ensure_connected() -> bool:
    """确保终端已初始化且已登录账号。

    C2 修复：仅 `terminal_info()` 存活不代表已登录账号（切换失败/被登出时
    终端可能存活但 `account_info()` 为 None）。此时视为未连接，尝试重连。
    """
    if mt5.terminal_info() is not None and mt5.account_info() is not None:
        return True
    logger.warning("MT5 disconnected or not logged in, attempting reconnect...")
    if not mt5.initialize(MT5_PATH):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    # I3：用最近活跃账号重连（切换后的账号），而非 env 初始账号
    if _active_login and not mt5.login(_active_login, password=_active_password, server=_active_server or None):
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        return False
    logger.info("MT5 reconnected successfully")
    return True


def switch_account(login: int, password: str, server: str | None = None) -> tuple[bool, dict | None, str]:
    """在同一终端内切换到另一个账号（Phase 0 spike 已验证可行）。

    返回 (success, account_snapshot, error)。切换失败时返回 False，
    由调用方决定是否回滚到原账号。
    """
    if not mt5.terminal_info() is not None:
        if not mt5.initialize(MT5_PATH):
            return False, None, f"MT5 initialize failed: {mt5.last_error()}"
    ok = mt5.login(login, password=password, server=server or None)
    if not ok:
        return False, None, f"MT5 login failed: {mt5.last_error()}"
    info = mt5.account_info()
    if info is None:
        return False, None, f"Login returned True but account_info() is None: {mt5.last_error()}"
    # I3：记录最近活跃账号，断线重连回本账号而非 env 初始账号
    global _active_login, _active_password, _active_server
    _active_login = info.login
    _active_password = password
    _active_server = info.server or server or ""
    snapshot = {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "currency": info.currency,
    }
    return True, snapshot, ""


def mt5_response(success: bool, data=None, error: str | None = None):
    return {"success": success, "data": data, "error": error}


def _retcode_reject(message: str, result) -> dict:
    """拒绝响应带结构化 retcode —— 下游（order_executor._NO_RETRY_ERRORS、
    前端）无法从拼进 error 字符串的 code 判定可重试性（10015/10016 可改价
    重试 vs 10018/10019 不可重试）。"""
    return mt5_response(
        False,
        data={"retcode": int(result.retcode), "comment": result.comment},
        error=f"{message}: {result.comment} (code: {result.retcode})",
    )


def _resolve_filling(symbol_info) -> int:
    """按 symbol filling_mode 位掩码推导成交模式。

    挂单硬编码 IOC 会被大量券商拒（10030 INVALID_FILL）——Exchange/Netting
    模式下挂单通常要求 RETURN。位掩码：SYMBOL_FILLING_FOK=1、
    SYMBOL_FILLING_IOC=2；无匹配回落 RETURN。
    """
    modes = int(getattr(symbol_info, "filling_mode", 0) or 0)
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    if modes & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _order_type_name(order_type: int) -> str:
    for name in (
        "ORDER_TYPE_BUY",
        "ORDER_TYPE_SELL",
        "ORDER_TYPE_BUY_LIMIT",
        "ORDER_TYPE_SELL_LIMIT",
        "ORDER_TYPE_BUY_STOP",
        "ORDER_TYPE_SELL_STOP",
    ):
        if getattr(mt5, name, None) == order_type:
            return name.removeprefix("ORDER_TYPE_")
    return str(order_type)


def _serialize_order(o) -> dict:
    """挂单序列化 —— orders_get 字段与 positions 完全不同（price_open/
    volume_initial/state/type_time），不能复用 /positions 的映射。"""
    return {
        "ticket": o.ticket,
        "symbol": o.symbol,
        "type": _order_type_name(o.type),
        "lot": float(o.volume_current),
        "volume_initial": float(o.volume_initial),
        "price_open": float(o.price_open),
        "price_current": float(o.price_current),
        "sl": float(o.sl),
        "tp": float(o.tp),
        "state": int(o.state),
        "type_time": int(o.type_time),
        "time_setup": datetime.fromtimestamp(o.time_setup).isoformat() if o.time_setup else None,
        "time_expiration": datetime.fromtimestamp(o.time_expiration).isoformat() if o.time_expiration else None,
        "comment": o.comment,
        "magic": o.magic,
    }


def _clamp_volume(symbol_info, lot: float) -> float:
    """按券商 volume grid 收敛手数（与市价单 /order 同逻辑）。"""
    vol = lot
    vol_step = symbol_info.volume_step
    if vol_step > 0:
        vol = math.floor(vol / vol_step) * vol_step
        vol = round(vol, 10)
    vol = max(vol, symbol_info.volume_min)
    return min(vol, symbol_info.volume_max)


# --- Models ---
class OrderRequest(BaseModel):
    symbol: str
    type: str  # "BUY" or "SELL"
    lot: float
    sl: float
    tp: float
    comment: str = ""
    magic: int = 234000


class ModifyPositionRequest(BaseModel):
    sl: float | None = None
    tp: float | None = None


class PendingOrderRequest(BaseModel):
    """挂单请求（手动交易 Phase 1）。

    price 必填且 >0、lot >0 由 pydantic 校验（422），杜绝旧 /order 的
    "非 BUY 一律 SELL" 与 "负 lot 被 clamp 成最小手数" 两类静默放行。
    """

    symbol: str
    type: Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]
    lot: float = Field(gt=0)
    price: float = Field(gt=0)
    sl: float = Field(default=0.0, ge=0)
    tp: float = Field(default=0.0, ge=0)
    comment: str = ""
    magic: int = 234000
    expiration: str | None = None  # ISO datetime；None = GTC


class ModifyOrderRequest(BaseModel):
    """改挂单请求：price/sl/tp 任一变更都需后端重跑全流水线后转发至此。"""

    price: float | None = Field(default=None, gt=0)
    sl: float | None = None
    tp: float | None = None
    expiration: str | None = None


# --- Startup / Shutdown ---
MT5_PATH = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")


@app.on_event("startup")
async def startup():
    logger.info("Initializing MT5...")
    if not mt5.initialize(MT5_PATH):
        logger.error(f"MT5 init failed: {mt5.last_error()}")
        return
    logger.info("MT5 terminal connected, logging in...")
    if MT5_LOGIN and not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        return
    info = mt5.account_info()
    if info:
        logger.info(f"MT5 connected: {info.login} @ {info.server}")
    else:
        logger.warning(f"MT5 initialized but no account info: {mt5.last_error()}")


@app.on_event("shutdown")
async def shutdown():
    mt5.shutdown()
    logger.info("MT5 shutdown")


# --- Endpoints ---
@app.get("/health")
async def health():
    """健康检查。C2 修复：status 必须同时反映"终端存活 + 账号已登录"。

    切换失败后终端可能存活但未登录账号——此时返回 degraded（而非 ok），
    让后端 HealthMonitor 不会把无账号状态误判为健康并自动恢复交易。
    """
    terminal_ok = mt5.terminal_info() is not None
    info = mt5.account_info() if terminal_ok else None
    logged_in = info is not None
    account = {"login": info.login, "server": info.server} if logged_in else None
    if not terminal_ok:
        status = "disconnected"
    elif not logged_in:
        status = "degraded"
    else:
        status = "ok"
    return {"status": status, "mt5": account, "logged_in": logged_in}


@app.get("/tick/{symbol}", dependencies=[Depends(verify_api_key)])
async def get_tick(symbol: str):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return mt5_response(False, error=f"No tick data for {symbol}")
    return mt5_response(True, data={
        "bid": tick.bid,
        "ask": tick.ask,
        "spread": round(tick.ask - tick.bid, 5),
        "time": datetime.fromtimestamp(tick.time).isoformat(),
    })


@app.get("/symbol-spec/{symbol}", dependencies=[Depends(verify_api_key)])
async def get_symbol_spec(symbol: str):
    """Return broker-side spec for a symbol (used by Symbol Config UI to validate + auto-fill)."""
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5_response(False, error=f"Symbol {symbol} not found")
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5_response(False, error=f"Symbol {symbol} not available after select")
    return mt5_response(True, data={
        "symbol": info.name,
        "path": info.path or "",
        "digits": int(info.digits),
        "point": float(info.point),
        "volume_min": float(info.volume_min),
        "volume_max": float(info.volume_max),
        "volume_step": float(info.volume_step),
        "trade_contract_size": float(info.trade_contract_size),
        "trade_tick_size": float(info.trade_tick_size),
        "trade_tick_value": float(info.trade_tick_value),
        # MT5 SYMBOL_TRADE_MODE：0=DISABLED 1=LONGONLY 2=SHORTONLY
        # 3=CLOSEONLY 4=FULL。旧版 bridge 部署可能省略此字段；后端把缺失
        # 视为"未知"而不是拒绝。
        "trade_mode": int(info.trade_mode),
        "visible": bool(info.visible),
    })


@app.get("/symbols", dependencies=[Depends(verify_api_key)])
async def list_symbols():
    """Return all broker-visible symbols with specs (used by Add Symbol UI catalog)."""
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    symbols = mt5.symbols_get()
    if symbols is None:
        return mt5_response(False, error="symbols_get returned None")
    items = [
        {
            "symbol": s.name,
            "path": s.path,
            "description": s.description,
            "digits": int(s.digits),
            "point": float(s.point),
            "volume_min": float(s.volume_min),
            "volume_max": float(s.volume_max),
            "volume_step": float(s.volume_step),
            "trade_contract_size": float(s.trade_contract_size),
            "trade_tick_size": float(s.trade_tick_size),
            "trade_tick_value": float(s.trade_tick_value),
            "currency_base": s.currency_base,
            "currency_profit": s.currency_profit,
        }
        for s in symbols
    ]
    return mt5_response(True, data={"count": len(items), "items": items})


@app.get("/ohlcv/{symbol}", dependencies=[Depends(verify_api_key)])
async def get_ohlcv(symbol: str, timeframe: str = "M15", count: int = 100):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    tf = TIMEFRAMES.get(timeframe.upper())
    if tf is None:
        return mt5_response(False, error=f"Invalid timeframe: {timeframe}")
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return mt5_response(False, error=f"No OHLCV data for {symbol}")
    data = []
    for r in rates:
        data.append({
            "time": datetime.fromtimestamp(r["time"]).isoformat(),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
        })
    return mt5_response(True, data=data)


@app.get("/account", dependencies=[Depends(verify_api_key)])
async def get_account():
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    info = mt5.account_info()
    if info is None:
        return mt5_response(False, error="Cannot get account info")
    return mt5_response(True, data={
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "free_margin": info.margin_free,
        "profit": info.profit,
        "currency": info.currency,
    })


class SwitchAccountRequest(BaseModel):
    """切换账号请求（Phase 3）。login/password 必填，server 可选（默认当前）。"""
    login: int
    password: str
    server: str | None = None


@app.post("/account/switch", dependencies=[Depends(verify_api_key)])
async def switch_account_endpoint(req: SwitchAccountRequest):
    """在同一终端内切换账号（Phase 0 spike 已验证可行）。

    成功后返回新账号快照；失败返回错误与**当前账号状态**。

    回滚策略说明：Bridge 端不持有账号凭据库（只有 env 初始账号密码），
    因此失败时**不做自动回滚**——`mt5.login()` 失败通常是原子操作，终端
    保持在原账号；即使落入半切换态，也由后端的 AccountSwitchService
    （持有所有账号凭据）决定如何恢复。C2 语义（/health 返回 logged_in）
    保证后端能检测到"终端存活但未登录"并介入。
    """
    previous = None
    info = mt5.account_info()
    if info is not None:
        previous = {"login": info.login, "server": info.server}

    ok, snapshot, error = switch_account(req.login, req.password, req.server)
    if not ok:
        # 报告失败 + 当前终端账号状态，供后端决定回滚
        current = None
        info_after = mt5.account_info()
        if info_after is not None:
            current = {"login": info_after.login, "server": info_after.server}
        return mt5_response(False, error=f"Account switch failed: {error}", data={
            "previous": previous,
            "current": current,
        })

    logger.info(f"Account switched: {previous['login'] if previous else '?'} -> {req.login} @ {req.server or 'default'}")
    return mt5_response(True, data={
        "switched": True,
        "account": snapshot,
        "previous": previous,
    })


@app.get("/positions", dependencies=[Depends(verify_api_key)])
async def get_positions():
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    positions = mt5.positions_get()
    if positions is None:
        return mt5_response(True, data=[])
    data = []
    for p in positions:
        data.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "lot": p.volume,
            "open_price": p.price_open,
            "current_price": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "open_time": datetime.fromtimestamp(p.time).isoformat(),
            "comment": p.comment,
            "magic": p.magic,
        })
    return mt5_response(True, data=data)


@app.post("/order", dependencies=[Depends(verify_api_key)])
async def place_order(req: OrderRequest):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    symbol_info = mt5.symbol_info(req.symbol)
    if symbol_info is None:
        return mt5_response(False, error=f"Symbol {req.symbol} not found")
    if not symbol_info.visible:
        mt5.symbol_select(req.symbol, True)

    # Clamp and round volume to symbol spec
    vol = req.lot
    vol_min = symbol_info.volume_min
    vol_max = symbol_info.volume_max
    vol_step = symbol_info.volume_step
    if vol_step > 0:
        vol = math.floor(vol / vol_step) * vol_step
        vol = round(vol, 10)  # avoid float precision artifacts
    vol = max(vol, vol_min)
    vol = min(vol, vol_max)
    if vol != req.lot:
        logger.info(f"Volume adjusted: {req.lot} → {vol} (min={vol_min}, step={vol_step}, max={vol_max})")

    tick = mt5.symbol_info_tick(req.symbol)
    if tick is None:
        return mt5_response(False, error="Cannot get tick")

    order_type = mt5.ORDER_TYPE_BUY if req.type.upper() == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": vol,
        "type": order_type,
        "price": price,
        "sl": req.sl,
        "tp": req.tp,
        "deviation": 20,
        "magic": req.magic,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Order send failed: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return _retcode_reject("Order rejected", result)

    logger.info(f"Order placed: {req.type} {vol} {req.symbol} @ {price} ticket={result.order}")
    return mt5_response(True, data={
        "ticket": result.order,
        "price": price,
        "lot": req.lot,
        "type": req.type.upper(),
    })


@app.put("/position/{ticket}", dependencies=[Depends(verify_api_key)])
async def modify_position(ticket: int, req: ModifyPositionRequest):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    position = mt5.positions_get(ticket=ticket)
    if not position:
        return mt5_response(False, error=f"Position {ticket} not found")

    pos = position[0]
    new_sl = req.sl if req.sl is not None else pos.sl
    new_tp = req.tp if req.tp is not None else pos.tp

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": new_tp,
    }

    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Modify failed: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return _retcode_reject("Modify rejected", result)

    logger.info(f"Position {ticket} modified: SL={new_sl} TP={new_tp}")
    return mt5_response(True, data={"ticket": ticket, "sl": new_sl, "tp": new_tp})


@app.delete("/position/{ticket}", dependencies=[Depends(verify_api_key)])
async def close_position(ticket: int):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    position = mt5.positions_get(ticket=ticket)
    if not position:
        return mt5_response(False, error=f"Position {ticket} not found")

    pos = position[0]
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": pos.magic,
        "comment": "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Close failed: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return _retcode_reject("Close rejected", result)

    logger.info(f"Position {ticket} closed @ {price}")
    return mt5_response(True, data={"ticket": ticket, "close_price": price})


@app.delete("/positions", dependencies=[Depends(verify_api_key)])
async def close_all_positions(symbol: str | None = None):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if not positions:
        return mt5_response(True, data={"closed": 0})

    results = []
    for pos in positions:
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "emergency_close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        success = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        results.append({"ticket": pos.ticket, "success": success})
        if success:
            logger.info(f"Emergency close: {pos.ticket} @ {price}")

    return mt5_response(True, data={"closed": sum(1 for r in results if r["success"]), "results": results})


# --- Pending orders（手动交易 Phase 1）---


@app.get("/orders", dependencies=[Depends(verify_api_key)])
async def get_orders():
    """列出当前账号的全部挂单。"""
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    orders = mt5.orders_get()
    if orders is None:
        return mt5_response(True, data=[])
    return mt5_response(True, data=[_serialize_order(o) for o in orders])


def _validate_pending_price_relation(req: PendingOrderRequest, tick) -> str | None:
    """挂单价/SL/TP 与市价的关系校验，返回错误信息或 None。"""
    is_buy = req.type.startswith("BUY")
    is_limit = req.type.endswith("LIMIT")
    ref = tick.ask if is_buy else tick.bid
    if is_limit:
        if is_buy and req.price >= ref:
            return f"BUY_LIMIT price {req.price} must be below ask {ref}"
        if not is_buy and req.price <= ref:
            return f"SELL_LIMIT price {req.price} must be above bid {ref}"
    else:
        if is_buy and req.price <= ref:
            return f"BUY_STOP price {req.price} must be above ask {ref}"
        if not is_buy and req.price >= ref:
            return f"SELL_STOP price {req.price} must be below bid {ref}"
    # SL/TP 相对挂单价的方向（买：SL<price<TP；卖相反）
    if is_buy:
        if req.sl and req.sl >= req.price:
            return f"SL {req.sl} must be below pending price {req.price} for BUY"
        if req.tp and req.tp <= req.price:
            return f"TP {req.tp} must be above pending price {req.price} for BUY"
    else:
        if req.sl and req.sl <= req.price:
            return f"SL {req.sl} must be above pending price {req.price} for SELL"
        if req.tp and req.tp >= req.price:
            return f"TP {req.tp} must be below pending price {req.price} for SELL"
    return None


@app.post("/order/pending", dependencies=[Depends(verify_api_key)])
async def place_pending_order(req: PendingOrderRequest):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    symbol_info = mt5.symbol_info(req.symbol)
    if symbol_info is None:
        return mt5_response(False, error=f"Symbol {req.symbol} not found")
    if not symbol_info.visible:
        mt5.symbol_select(req.symbol, True)

    vol = _clamp_volume(symbol_info, req.lot)
    tick = mt5.symbol_info_tick(req.symbol)
    if tick is None:
        return mt5_response(False, error="Cannot get tick")

    error = _validate_pending_price_relation(req, tick)
    if error:
        return mt5_response(False, error=error)

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": req.symbol,
        "volume": vol,
        "type": getattr(mt5, f"ORDER_TYPE_{req.type}"),
        "price": req.price,
        "sl": req.sl,
        "tp": req.tp,
        "magic": req.magic,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _resolve_filling(symbol_info),
    }
    if req.expiration:
        try:
            exp_dt = datetime.fromisoformat(req.expiration)
        except ValueError:
            return mt5_response(False, error="Invalid expiration format. Use ISO: YYYY-MM-DDTHH:MM:SS")
        request["type_time"] = mt5.ORDER_TIME_SPECIFIED
        request["expiration"] = exp_dt

    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Pending order failed: {mt5.last_error()}")
    # 关键：挂单成功的 retcode 是 PLACED(10008)，市价才是 DONE(10009)。
    # 只查 DONE 会把成功挂单判为失败 → 用户重试 → 重复挂单。
    if result.retcode not in (mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE):
        return _retcode_reject("Pending order rejected", result)

    logger.info(f"Pending order placed: {req.type} {vol} {req.symbol} @ {req.price} ticket={result.order}")
    return mt5_response(True, data={"ticket": result.order, "price": req.price, "lot": vol, "type": req.type})


@app.put("/order/{ticket}", dependencies=[Depends(verify_api_key)])
async def modify_pending_order(ticket: int, req: ModifyOrderRequest):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return mt5_response(False, error=f"Pending order {ticket} not found")
    order = orders[0]

    # TRADE_ACTION_MODIFY 必须回传 type_time；原单 ORDER_TIME_SPECIFIED 时
    # expiration 也必填，否则多数券商以 10016/10022 拒绝 → 先查单整包回填。
    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "symbol": order.symbol,
        "price": req.price if req.price is not None else float(order.price_open),
        "sl": req.sl if req.sl is not None else float(order.sl),
        "tp": req.tp if req.tp is not None else float(order.tp),
        "type_time": int(order.type_time),
    }
    if int(order.type_time) == mt5.ORDER_TIME_SPECIFIED:
        if req.expiration:
            try:
                request["expiration"] = datetime.fromisoformat(req.expiration)
            except ValueError:
                return mt5_response(False, error="Invalid expiration format. Use ISO: YYYY-MM-DDTHH:MM:SS")
        elif order.time_expiration:
            request["expiration"] = datetime.fromtimestamp(order.time_expiration)

    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Modify order failed: {mt5.last_error()}")
    if result.retcode not in (mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE):
        return _retcode_reject("Modify order rejected", result)

    logger.info(f"Pending order {ticket} modified: price={request['price']} SL={request['sl']} TP={request['tp']}")
    return mt5_response(True, data={
        "ticket": ticket, "price": request["price"], "sl": request["sl"], "tp": request["tp"],
    })


@app.delete("/order/{ticket}", dependencies=[Depends(verify_api_key)])
async def cancel_order(ticket: int):
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return mt5_response(False, error=f"Pending order {ticket} not found")

    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    result = mt5.order_send(request)
    if result is None:
        return mt5_response(False, error=f"Cancel failed: {mt5.last_error()}")
    if result.retcode not in (mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE):
        return _retcode_reject("Cancel rejected", result)

    logger.info(f"Pending order {ticket} cancelled")
    return mt5_response(True, data={"ticket": ticket, "cancelled": True})


@app.get("/ohlcv/{symbol}/history", dependencies=[Depends(verify_api_key)])
async def get_ohlcv_history(symbol: str, timeframe: str = "M15", from_date: str = "", to_date: str = ""):
    """Get historical OHLCV data by date range. Returns up to 50000 bars."""
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")
    tf = TIMEFRAMES.get(timeframe.upper())
    if tf is None:
        return mt5_response(False, error=f"Invalid timeframe: {timeframe}")
    try:
        dt_from = datetime.fromisoformat(from_date)
        dt_to = datetime.fromisoformat(to_date)
    except (ValueError, TypeError):
        return mt5_response(False, error="Invalid date format. Use ISO format: YYYY-MM-DD")

    rates = mt5.copy_rates_range(symbol, tf, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        return mt5_response(False, error=f"No OHLCV data for {symbol} in range")
    data = []
    for r in rates:
        data.append({
            "time": datetime.fromtimestamp(r["time"]).isoformat(),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
        })
    logger.info(f"Historical OHLCV: {symbol} {timeframe} {from_date} to {to_date} = {len(data)} bars")
    return mt5_response(True, data=data)


@app.get("/history", dependencies=[Depends(verify_api_key)])
async def get_history(days: int = 1, symbol: str | None = None):
    """Get closed deals (trades) from the last N days, optionally filtered by symbol."""
    if not ensure_connected():
        return mt5_response(False, error="MT5 not connected")

    from_date = datetime.now() - timedelta(days=days)
    to_date = datetime.now() + timedelta(days=1)

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        return mt5_response(True, data=[])

    result = []
    for deal in deals:
        if deal.entry == 1 and deal.type in (0, 1):  # entry=1 means exit, type 0=buy 1=sell
            if symbol and deal.symbol != symbol:
                continue
            result.append({
                "ticket": deal.position_id,
                "deal_ticket": deal.ticket,
                "symbol": deal.symbol,
                "type": "BUY" if deal.type == 1 else "SELL",  # exit type is opposite
                "lot": deal.volume,
                "price": deal.price,
                "profit": deal.profit,
                "commission": deal.commission,
                "swap": deal.swap,
                "comment": deal.comment,
                "time": datetime.fromtimestamp(deal.time).isoformat(),
            })

    return mt5_response(True, data=result)
