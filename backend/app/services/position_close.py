"""Gated close for user-initiated (dashboard) position closes.

`DELETE /api/positions/{ticket}` used to call the executor directly — no
switching gate, no rollout interception, no circuit-breaker/guardrail
accounting. This module is the single choke point for user-initiated closes.

Double-accounting guard: the engine's sync_positions() tracks ALL positions
(including manually opened ones) for symbols with an active engine and already
records circuit-breaker/guardrail results when they disappear. When an engine
owns the symbol we skip accounting here and let the engine record; for
engine-less symbols we record directly so daily-loss / consecutive-loss
counters cannot be bypassed through the manual path.
"""

from loguru import logger

from app.config import get_canonical_symbol
from app.mt5.connector import MT5BridgeConnector
from app.risk.circuit_breaker import CircuitBreaker

SWITCHING_KEY = "switching:in_progress"


def _lookup_engine(symbol: str):
    """Resolve a broker-side position symbol to its owning engine (or None)."""
    try:
        from app.bot.manager import get_global_manager

        mgr = get_global_manager()
        if not mgr:
            return None
        key = mgr.resolve_symbol(symbol)
        return mgr.engines.get(key) if key else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Engine lookup failed for '{symbol}': {e!r}")
        return None


def _fail(reason: str, **extra) -> dict:
    return {"closed": False, "rejected": True, "reason": reason, **extra}


async def close_position_gated(
    connector: MT5BridgeConnector,
    redis,
    ticket: int,
) -> dict:
    """Close a position with the full gate sequence (fail-closed).

    Manual closes move real money, so unlike the MCP broker's best-effort
    switching check, a Redis failure here blocks the close instead of opening
    the gate.
    """
    # 1. Switching gate — fail-closed for manual channel (H4).
    try:
        if await redis.get(SWITCHING_KEY):
            return _fail("Account switch in progress")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Manual close [{ticket}] switching gate unavailable: {e!r}")
        return _fail("Risk gate temporarily unavailable, please retry")

    # 2. Ticket ownership: resolve against the CURRENT account's positions
    #    (tickets are only unique per account — H4 composite key).
    positions_res = await connector.get_positions()
    if not positions_res.get("success"):
        return {"closed": False, "error": positions_res.get("error", "Failed to fetch positions")}
    pos_info = next(
        (p for p in positions_res.get("data", []) if p.get("ticket") == ticket),
        None,
    )
    if pos_info is None:
        return {"closed": False, "error": "Position not found on current account"}

    # 3. Rollout gate — same semantics as the AI channel: shadow/paper must not
    #    touch a real account.
    from mcp_server.guardrails import TradingGuardrails

    guardrails = TradingGuardrails(redis)
    rollout_mode = await guardrails.get_persisted_rollout_mode()
    if rollout_mode in ("shadow", "paper"):
        return _fail(f"Rollout mode '{rollout_mode}' blocks manual close", rollout=rollout_mode)

    # 4. Execute
    result = await connector.close_position(ticket)
    if not result.get("success"):
        return {"closed": False, "error": result.get("error", "Close failed")}

    # 5. Accounting + events. Engine-tracked symbols are recorded by the
    #    engine's sync loop — recording here too would double-count daily PnL.
    #    但引擎必须 RUNNING 才会 sync：引擎 PAUSED/ERROR/STOPPED 时该品种无人
    #    记账（评审 I-1）—— 手动平仓落账，防止日亏/连亏计数在引擎暂停期间失效。
    profit = pos_info.get("profit", 0) or 0
    pos_symbol = pos_info.get("symbol", "")
    engine = _lookup_engine(pos_symbol)
    engine_records = engine is not None and getattr(engine, "state", None) is not None
    from app.bot.engine import BotState

    if engine_records:
        engine_records = engine.state == BotState.RUNNING

    if not engine_records:
        canonical = get_canonical_symbol(pos_symbol) or pos_symbol
        # 账号维度（H3）：不传则写入旧 circuit: 前缀 key，与引擎切换后读的
        # circuit:acc:{login}: 前缀 key 错位 —— 日亏闸门读不到手动平仓的盈亏。
        account_login = getattr(engine, "account_login", None) if engine else None
        if not account_login:
            try:
                from app.bot.manager import get_global_manager

                mgr = get_global_manager()
                if mgr is not None:
                    account_login = getattr(mgr, "current_account_login", None)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Manual close [{ticket}] account_login lookup failed: {e!r}")
        try:
            await CircuitBreaker(redis, symbol=canonical, account_login=account_login).record_trade_result(profit)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Manual close [{ticket}] circuit-breaker record failed: {e!r}")
        try:
            await guardrails.record_trade_closed(is_win=profit > 0)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Manual close [{ticket}] guardrail record failed: {e!r}")

    # 6. Event + WS push ([Manual] prefix distinguishes user-initiated closes).
    from app.db.models import BotEvent, BotEventType

    message = f"[Manual] CLOSE {pos_symbol} ticket={ticket} P&L=${profit:+.2f}"
    try:
        from app.db.session import async_session

        async with async_session() as session:
            session.add(BotEvent(event_type=BotEventType.TRADE_CLOSED, message=message))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Manual close [{ticket}] event log failed: {e!r}")
    try:
        import json as _json

        await redis.publish(
            "bot_event",
            _json.dumps({"type": "trade_closed", "ticket": ticket, "profit": profit, "manual": True}),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Manual close [{ticket}] WS push failed: {e!r}")

    return {"closed": True, "ticket": ticket, "profit": profit}
