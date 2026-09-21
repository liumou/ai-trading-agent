"""MCP tools for broker operations — GUARDRAIL-GATED.

Every order execution MUST pass through TradingGuardrails.validate_order()
before reaching the MT5 Bridge. The agent cannot bypass this.
"""

import redis.asyncio as redis_lib
from loguru import logger

from app.mt5.connector import MT5BridgeConnector
from app.notifications.telegram import TelegramNotifier
from mcp_server.guardrails import TradingGuardrails

_connector: MT5BridgeConnector | None = None
_guardrails: TradingGuardrails | None = None
_notifier: TelegramNotifier | None = None
_redis: redis_lib.Redis | None = None


def init_broker(redis: redis_lib.Redis) -> None:
    """Initialize broker with Redis for guardrails. Called once at agent startup."""
    global _connector, _guardrails, _notifier, _redis
    _connector = MT5BridgeConnector()
    _guardrails = TradingGuardrails(redis)
    _notifier = TelegramNotifier()
    _redis = redis


def _require_init():
    if not _connector or not _guardrails:
        raise RuntimeError("Broker not initialized — call init_broker(redis) first")


async def _switching_in_progress() -> bool:
    """账号切换门禁（H2）：切换期间（Redis `switching:in_progress`）为 True。

    Redis 不可用时放行（best-effort，不阻塞正常交易操作）。
    """
    if _redis is None:
        return False
    try:
        return bool(await _redis.get("switching:in_progress"))
    except Exception:  # noqa: BLE001
        return False


async def place_order(
    symbol: str,
    order_type: str,
    lot: float,
    sl: float,
    tp: float,
    comment: str = "",
) -> dict:
    """Place a trade order — GUARDRAIL-GATED.

    Every order passes through guardrails validation before execution.
    The agent cannot bypass this check.

    Args:
        symbol: Trading symbol (e.g., "GOLD")
        order_type: "BUY" or "SELL"
        lot: Position size
        sl: Stop-loss price
        tp: Take-profit price
        comment: Optional order comment

    Returns:
        Dict with order result or rejection reason.
    """
    _require_init()

    # ─── SHARED PREFLIGHT（硬闸门唯一真相源）──────────────────────────────
    # 序列（resolve_symbol → 并发行情 → rolling spread → validate_order →
    # volume 归一 → rollout → llm_allow_live）抽到 app/services/
    # order_preflight.py，与手动交易通道共用 —— 此前每条注释都是真实事故
    # 教训，复制即漂移。AI 通道 strict_symbol=False 保持历史 fail-open 语义。
    from app.services.order_preflight import _sanitize_comment, preflight_order

    pf = await preflight_order(
        _connector, _redis, _guardrails,
        symbol=symbol, order_type=order_type, lot=lot, sl=sl, tp=tp,
        strict_symbol=False,
    )
    if not pf.ok:
        return {"executed": False, "rejected": True, "reason": pf.reason}
    ctx = pf.ctx
    symbol = ctx.symbol
    lot = ctx.lot
    rollout_mode = ctx.rollout_mode

    # ─── 账号切换门禁（H2）────────────────────────────────────────────────
    # 切换期间拒绝下单，防止 MCP/AI 通道把订单落在错误账号上。
    # AI 通道保持 best-effort（Redis 不可用放行）；手动通道 fail-closed。
    if await _switching_in_progress():
        logger.warning(f"place_order [{symbol}] rejected: account switch in progress")
        return {
            "executed": False,
            "rejected": True,
            "reason": "Account switch in progress — retry after switch completes",
        }

    # ─── ROLLOUT 模式语义（shadow 记录不执行 / paper 模拟）────────────────
    if rollout_mode == "shadow":
        return {
            "executed": False,
            "mode": "shadow",
            "would_execute": {
                "symbol": ctx.broker_symbol,
                "order_type": order_type,
                "lot": lot,
                "sl": sl,
                "tp": tp,
            },
            "message": "Shadow mode: order logged for review, not sent to broker",
        }

    if rollout_mode == "paper":
        import random

        return {
            "executed": True,
            "mode": "paper",
            "order": {
                "ticket": random.randint(900000, 999999),
                "symbol": ctx.broker_symbol,
                "type": order_type,
                "lot": lot,
                "price": ctx.tick.get("ask" if order_type == "BUY" else "bid", 0),
                "sl": sl,
                "tp": tp,
                "simulated": True,
            },
            "message": "Paper mode: simulated execution (not real)",
        }

    # ─── EXECUTE ORDER (live or micro) ───────────────────────────────────
    # comment 清洗规则见 _sanitize_comment（MT5 27 字符上限 + 特殊字符拒单）。
    full_comment = _sanitize_comment(comment, prefix="AI")

    logger.info(f"place_order [{ctx.broker_symbol}] {order_type} lot={lot} sl={sl} tp={tp} mode={rollout_mode}")
    order_result = await _connector.place_order(
        symbol=ctx.broker_symbol,
        order_type=order_type,
        lot=lot,
        sl=sl,
        tp=tp,
        comment=full_comment,
    )
    logger.info(f"place_order result [{symbol}]: {order_result}")

    if order_result.get("success"):
        # 开仓只记频率/间隔，不记胜负 —— 胜负由平仓路径按实际盈亏
        # record_trade_closed(is_win) 记录，否则连亏熔断永不触发。
        await _guardrails.record_order_opened()
        data = order_result["data"]
        # Send Telegram notification
        if _notifier:
            try:
                await _notifier.send_trade_alert(
                    trade_type=order_type,
                    symbol=symbol,
                    price=data.get("price", 0),
                    sl=sl,
                    tp=tp,
                    lot=lot,
                )
            except Exception as e:
                logger.error(f"Telegram notify failed: {e}")
        # Log AI-initiated trade to event DB
        try:
            from app.bot.manager import get_global_manager
            from app.db.models import BotEventType

            mgr = get_global_manager()
            engine = mgr.engines.get(symbol) if mgr else None
            if engine:
                await engine._log_event(
                    BotEventType.TRADE_OPENED,
                    f"[AI Agent] {order_type} {lot} {symbol} @ {data.get('price', 0):.2f} SL={sl} TP={tp} [{rollout_mode}]",
                )
        except Exception as e:
            logger.warning(f"Event log failed: {e}")
        return {
            "executed": True,
            "mode": rollout_mode,
            "order": data,
        }
    else:
        return {
            "executed": False,
            "error": order_result.get("error", "Order execution failed"),
        }


async def modify_position(ticket: int, sl: float | None = None, tp: float | None = None) -> dict:
    """Modify stop-loss and/or take-profit of an existing position.

    Guardrails:
    - Non-live rollout modes intercept and log instead of mutating broker state.
    - Stop-loss cannot be widened to more than MAX_SL_WIDEN_MULT × the distance
      from the current entry price; the AI agent can't neutralize risk by
      dragging SL to zero.
    """
    _require_init()
    if await _switching_in_progress():
        logger.warning(f"modify_position [{ticket}] rejected: account switch in progress")
        return {"modified": False, "rejected": True, "reason": "Account switch in progress"}
    rollout_mode = await _guardrails.get_persisted_rollout_mode()
    if rollout_mode in ("shadow", "paper"):
        logger.info(f"[{rollout_mode}] modify_position intercepted: ticket={ticket} sl={sl} tp={tp}")
        return {"modified": False, "rollout": rollout_mode, "ticket": ticket}

    if sl is not None:
        positions_res = await _connector.get_positions()
        if positions_res.get("success"):
            for p in positions_res.get("data", []):
                if p.get("ticket") != ticket:
                    continue
                current_sl = p.get("sl") or 0
                entry = p.get("open_price") or p.get("price") or 0
                if current_sl and entry:
                    current_dist = abs(entry - current_sl)
                    new_dist = abs(entry - sl)
                    MAX_SL_WIDEN_MULT = 2.0
                    if new_dist > current_dist * MAX_SL_WIDEN_MULT:
                        return {
                            "modified": False,
                            "rejected": True,
                            "reason": (
                                f"SL widen {new_dist:.5f} exceeds {MAX_SL_WIDEN_MULT}x "
                                f"current distance {current_dist:.5f}"
                            ),
                        }
                break

    result = await _connector.modify_position(ticket, sl=sl, tp=tp)
    if result.get("success"):
        return {"modified": True, "ticket": ticket}
    return {"modified": False, "error": result.get("error", "Modification failed")}


async def close_position(ticket: int) -> dict:
    """Close a specific position by ticket number.

    In shadow/paper rollout modes the close is intercepted (logged only) so the
    AI agent cannot liquidate a real account while we're still dry-running.
    """
    _require_init()
    if await _switching_in_progress():
        logger.warning(f"close_position [{ticket}] rejected: account switch in progress")
        return {"closed": False, "rejected": True, "reason": "Account switch in progress"}
    rollout_mode = await _guardrails.get_persisted_rollout_mode()

    # Get position info before closing for notification
    positions_res = await _connector.get_positions()
    pos_info = None
    if positions_res.get("success"):
        for p in positions_res.get("data", []):
            if p.get("ticket") == ticket:
                pos_info = p
                break

    if rollout_mode in ("shadow", "paper"):
        logger.info(f"[{rollout_mode}] close_position intercepted: ticket={ticket}")
        return {"closed": False, "rollout": rollout_mode, "ticket": ticket}

    result = await _connector.close_position(ticket)
    if result.get("success"):
        # 平仓按实际盈亏记录胜负，驱动连亏熔断（CONSECUTIVE_LOSS_HALT）
        if pos_info is not None:
            close_profit = pos_info.get("profit", 0) or 0
            await _guardrails.record_trade_closed(is_win=close_profit > 0, ticket=ticket)
        # Send Telegram notification
        if _notifier and pos_info:
            try:
                await _notifier.send_trade_alert(
                    trade_type=f"CLOSE_{pos_info.get('type', '')}",
                    symbol=pos_info.get("symbol", ""),
                    price=pos_info.get("price_current", 0),
                    sl=0,
                    tp=0,
                    lot=pos_info.get("volume", 0),
                    extra=f"${pos_info.get('profit', 0):+.2f}",
                )
            except Exception as e:
                logger.error(f"Telegram notify failed: {e}")
        # Log AI-initiated close to event DB
        if pos_info:
            try:
                from app.bot.manager import get_global_manager
                from app.db.models import BotEventType

                sym = pos_info.get("symbol", "")
                engine = (get_global_manager().engines if get_global_manager() else {}).get(sym)
                if engine:
                    await engine._log_event(
                        BotEventType.TRADE_CLOSED,
                        f"[AI Agent] CLOSE {sym} ticket={ticket} P&L=${pos_info.get('profit', 0):+.2f}",
                    )
            except Exception as e:
                logger.warning(f"Event log failed: {e}")
        return {"closed": True, "ticket": ticket}
    return {"closed": False, "error": result.get("error", "Close failed")}


async def get_positions(symbol: str | None = None) -> dict:
    """Get all open positions, optionally filtered by symbol.

    Args:
        symbol: Optional symbol filter

    Returns:
        Dict with list of open positions.
    """
    _require_init()
    result = await _connector.get_positions()
    if not result.get("success"):
        return {"error": result.get("error", "Failed to get positions")}

    positions = result.get("data", [])
    if symbol:
        positions = [p for p in positions if p.get("symbol") == symbol]

    return {"count": len(positions), "positions": positions}
