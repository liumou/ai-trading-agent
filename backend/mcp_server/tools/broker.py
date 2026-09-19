"""MCP tools for broker operations — GUARDRAIL-GATED.

Every order execution MUST pass through TradingGuardrails.validate_order()
before reaching the MT5 Bridge. The agent cannot bypass this.
"""

import re

import redis.asyncio as redis_lib
from loguru import logger

from app.mt5.connector import MT5BridgeConnector
from app.notifications.telegram import TelegramNotifier
from app.risk.circuit_breaker import CircuitBreaker
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

    # 把品种解析为活跃引擎键（例如券商别名 GOLDmicro → 规范名 GOLD，
    # 或按引擎键的实际情况反向解析）。别名映射来自 DB 加载的别名 profile ——
    # 引擎以 symbol_configs 的 `symbol` 列为主键。
    try:
        from app.bot.manager import get_global_manager

        mgr = get_global_manager()
        if mgr is None:
            logger.warning(f"Symbol resolution skipped — no active BotManager for '{symbol}'")
        else:
            key = mgr.resolve_symbol(symbol)
            if key and key != symbol:
                logger.info(f"Symbol resolved: {symbol} → {key}")
                symbol = key
            elif key is None:
                logger.warning(f"Symbol '{symbol}' not found in engines: {list(mgr.engines.keys())}")
    except Exception as e:
        logger.warning(f"Symbol resolution failed for '{symbol}': {e}")

    # Get current state for guardrail checks (concurrent)
    import asyncio as _aio

    account_res, positions_res, tick_res = await _aio.gather(
        _connector.get_account(),
        _connector.get_positions(),
        _connector.get_tick(symbol),
    )

    if not all(r.get("success") for r in [account_res, positions_res, tick_res]):
        failed = []
        if not account_res.get("success"):
            failed.append(f"account: {account_res.get('error', 'unknown')}")
        if not positions_res.get("success"):
            failed.append(f"positions: {positions_res.get('error', 'unknown')}")
        if not tick_res.get("success"):
            failed.append(f"tick: {tick_res.get('error', 'unknown')}")
        logger.error(f"place_order pre-check failed: {', '.join(failed)}")
        return {"error": f"Failed to fetch data for guardrail validation: {', '.join(failed)}"}

    account = account_res["data"]
    positions = positions_res.get("data", [])
    tick = tick_res["data"]

    spread = tick.get("ask", 0) - tick.get("bid", 0)
    # rolling avg spread：从 Redis 读最近 N 次点差求均值，替代 avg_spread = spread
    # 的恒等式 —— 否则 spread 熔断（spread > avg*3）永远不可能触发，高波动/
    # 流动性枯竭时段 AI 会照常追单。
    avg_spread = spread
    if _redis is not None:
        try:
            SPREAD_KEY = "guardrails:spread_history"
            SPREAD_WINDOW = 20
            await _redis.rpush(SPREAD_KEY, str(spread))
            await _redis.ltrim(SPREAD_KEY, -SPREAD_WINDOW, -1)
            await _redis.expire(SPREAD_KEY, 86400)
            recent = await _redis.lrange(SPREAD_KEY, 0, -1)
            if recent:
                vals = [float(v) for v in recent if v]
                avg_spread = sum(vals) / len(vals) if vals else spread
        except Exception as e:
            logger.debug(f"Rolling spread read failed, using current: {e}")
            avg_spread = spread

    # ─── GUARDRAIL CHECK (non-bypassable) ────────────────────────────────
    # daily_pnl must come from CircuitBreaker (closed-trade realized P&L).
    # account.profit is *floating* unrealized P&L, which would let open winners
    # mask realized losses and bypass the daily-loss limit. Fall back to the
    # account float only when Redis isn't wired up (e.g., unit tests).
    if _redis is not None:
        cb = CircuitBreaker(_redis, symbol=symbol)
        realized_daily_pnl = await cb.get_daily_pnl()
    else:
        realized_daily_pnl = account.get("profit", 0)

    # 参考价取 tick 中间价（当前 ask/bid 的平均），用于 SL/TP 方向校验。
    # 避免直接取 ask 或 bid 导致 BUY 单被误判 SL>=entry（ask 高于 bid）。
    entry_ref = (tick.get("ask", 0) + tick.get("bid", 0)) / 2 if tick.get("ask") and tick.get("bid") else 0

    result = await _guardrails.validate_order(
        symbol=symbol,
        lot=lot,
        order_type=order_type,
        current_positions=positions,
        account_balance=account.get("balance", 0),
        daily_pnl=realized_daily_pnl,
        spread=spread,
        avg_spread=avg_spread,
        entry_price=entry_ref,
        sl=sl,
        tp=tp,
    )

    if not result.allowed:
        return {
            "executed": False,
            "rejected": True,
            "reason": result.reason,
        }

    # ─── 券商手数防线 + 品种名解析（与策略引擎同一套防线）────────────────
    # connector 使用的是券商品种名；引擎以 symbol_configs 的规范名为主键。
    # 不做 to_broker_alias() 转换，AI 订单会以错误的名字到达 MT5。手数防线把
    # 手数向下取整到券商 step，并拒绝低于 volume_min 的手数 —— 否则 bridge
    # 会静默放大，击穿风险预算。
    from app.config import SYMBOL_PROFILES
    from app.mt5.symbol_resolver import to_broker_alias
    from app.services.symbol_validation import normalize_lot_to_volume_grid

    broker_symbol = to_broker_alias(symbol)
    profile = SYMBOL_PROFILES.get(symbol) or {}
    guarded_lot = normalize_lot_to_volume_grid(
        lot,
        volume_min=profile.get("volume_min"),
        volume_max=profile.get("volume_max"),
        volume_step=profile.get("volume_step"),
    )
    if guarded_lot is None:
        logger.warning(
            f"place_order rejected [{symbol}]: lot {lot} below broker minimum "
            f"{profile.get('volume_min')} — bridge would upsize beyond risk budget"
        )
        return {
            "executed": False,
            "rejected": True,
            "reason": (
                f"lot {lot} below broker minimum {profile.get('volume_min')} — "
                f"raise lot or fix symbol volume config"
            ),
        }
    if guarded_lot != lot:
        logger.info(f"place_order [{symbol}]: lot normalized {lot} → {guarded_lot}")
        lot = guarded_lot

    # ─── ROLLOUT MODE CHECK (Phase F) ────────────────────────────────────
    # 统一走 Redis 持久化模式（get_persisted_rollout_mode），与 openai_loop
    # 的 _rollout_allows_trade 口径一致 —— 否则前端把模式降级为 paper 后，
    # 本工具层仍按进程 env 的 micro/live 执行真实下单（fail-open）。
    from mcp_server.guardrails import MICRO_MAX_LOT

    rollout_mode = await _guardrails.get_persisted_rollout_mode()

    if rollout_mode == "shadow":
        # Shadow: log everything but don't execute
        return {
            "executed": False,
            "mode": "shadow",
            "would_execute": {
                "symbol": broker_symbol,
                "order_type": order_type,
                "lot": lot,
                "sl": sl,
                "tp": tp,
            },
            "message": "Shadow mode: order logged for review, not sent to broker",
        }

    if rollout_mode == "paper":
        # Paper: simulate execution with fake ticket
        import random

        return {
            "executed": True,
            "mode": "paper",
            "order": {
                "ticket": random.randint(900000, 999999),
                "symbol": broker_symbol,
                "type": order_type,
                "lot": lot,
                "price": tick.get("ask" if order_type == "BUY" else "bid", 0),
                "sl": sl,
                "tp": tp,
                "simulated": True,
            },
            "message": "Paper mode: simulated execution (not real)",
        }

    if rollout_mode == "micro":
        # Micro: cap lot at MICRO_MAX_LOT（Redis 校验已允许，此处统一封顶）
        lot = min(lot, MICRO_MAX_LOT)
        logger.info(f"place_order [{symbol}]: micro cap applied — lot → {lot}")

    # ─── PROVIDER-AGNOSTIC LIVE AUTHORIZATION ────────────────────────────
    # 护栏下沉到 broker 层（原仅在 openai_loop 通道存在）：micro/live 真实
    # 资金执行必须显式 LLM_ALLOW_LIVE=true，否则任何 provider（含 Claude
    # SDK 通道）都不得越过 shadow/paper。保证 UI 降级与撤权即时生效。
    from app.config import settings

    if rollout_mode in ("micro", "live") and not settings.llm_allow_live:
        logger.warning(
            f"place_order [{symbol}] rejected: rollout={rollout_mode} requires LLM_ALLOW_LIVE=true (provider-agnostic)"
        )
        return {
            "executed": False,
            "rejected": True,
            "reason": (
                f"rollout={rollout_mode}: 需要显式设置 LLM_ALLOW_LIVE=true 才放开真实下单"
            ),
        }

    # ─── 账号切换门禁（H2） ──────────────────────────────────────────────
    # 切换期间（AccountSwitchService 置 Redis `switching:in_progress`）拒绝
    # 下单，防止 MCP/AI 通道在账号切换瞬间把订单落在错误账号上。
    if _redis is not None:
        try:
            switching = await _redis.get("switching:in_progress")
            if switching:
                logger.warning(f"place_order [{symbol}] rejected: account switch in progress")
                return {
                    "executed": False,
                    "rejected": True,
                    "reason": "Account switch in progress — retry after switch completes",
                }
        except Exception:  # noqa: BLE001
            pass  # Redis 不可用时放行（门禁 best-effort，不阻塞正常下单）

    # ─── EXECUTE ORDER (live or micro) ───────────────────────────────────
    # comment 清洗：MT5 ORDER_COMMENT 硬上限 27 字符，且不接受 `[`/`]` 等
    # 特殊字符（日志曾见 'Invalid "comment" argument' 真实拒单）。
    # 清洗规则：ASCII 字母数字/下划线/短横线 + 空格，其余剔除；再按
    # 剩余预算截断（前缀本身占位，留给 AI 文本的只有 27 - len(prefix)）。
    prefix = "AI"
    safe_comment = re.sub(r"[^A-Za-z0-9 _-]", "", comment or "")
    safe_comment = safe_comment.strip()
    max_comment_len = 27 - len(prefix)
    safe_comment = safe_comment[:max_comment_len]
    full_comment = f"{prefix} {safe_comment}".strip() if safe_comment else prefix

    logger.info(f"place_order [{broker_symbol}] {order_type} lot={lot} sl={sl} tp={tp} mode={rollout_mode}")
    order_result = await _connector.place_order(
        symbol=broker_symbol,
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
            await _guardrails.record_trade_closed(is_win=close_profit > 0)
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
