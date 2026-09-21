"""Shared order preflight — the single source of the hard-gate sequence.

Used by BOTH the AI/MCP channel (`mcp_server/tools/broker.py`) and the manual
trading gate (`app/services/manual_order_gate.py`). Every step here encodes a
real incident (see inline comments); this sequence must not be duplicated.

Firewall invariant: hard gates run FIRST and their verdict can only be
tightened, never loosened, by any later (LLM) review layer.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.mt5.symbol_resolver import to_broker_alias

# Rolling spread window（与 broker.py 原实现一致），key 按 symbol 分
# （评审 M-2：全局 key 让黄金与欧元的点差互相稀释，熔断失真）。
SPREAD_WINDOW = 20


@dataclass
class PreflightContext:
    symbol: str  # canonical engine key
    broker_symbol: str
    order_type: str
    base_lot: float  # as requested
    lot: float  # after volume-grid normalization (+ micro cap)
    sl: float
    tp: float
    account: dict
    positions: list
    tick: dict
    spread: float
    avg_spread: float
    daily_pnl: float
    entry_ref: float  # tick mid price, for SL/TP direction checks
    rollout_mode: str
    guardrails: Any
    profile: dict = field(default_factory=dict)


@dataclass
class PreflightOutcome:
    ok: bool
    reason: str = ""
    kind: str = ""  # data_fetch | guardrail | lot | live_auth | symbol
    ctx: PreflightContext | None = None


def _reject(reason: str, kind: str) -> PreflightOutcome:
    return PreflightOutcome(ok=False, reason=reason, kind=kind)


def _resolve_symbol(symbol: str, *, strict: bool) -> tuple[str | None, str]:
    """Resolve to the canonical engine key via the global BotManager.

    strict=True（手动通道）：解析失败/无引擎 → None（fail-closed）——否则
    SYMBOL_PROFILES miss 会绕过 volume 归一，bridge 再静默放大手数。
    strict=False（AI 通道）：保持历史行为（warn 后原样继续）。
    """
    try:
        from app.bot.manager import get_global_manager

        mgr = get_global_manager()
        if mgr is None:
            if strict:
                return None, "No active trading engine — cannot validate symbol"
            logger.warning(f"Symbol resolution skipped — no active BotManager for '{symbol}'")
            return symbol, ""
        key = mgr.resolve_symbol(symbol)
        if key:
            if key != symbol:
                logger.info(f"Symbol resolved: {symbol} → {key}")
            return key, ""
        if strict:
            return None, f"Symbol '{symbol}' not found in active engines"
        logger.warning(f"Symbol '{symbol}' not found in engines: {list(mgr.engines.keys())}")
        return symbol, ""
    except Exception as e:  # noqa: BLE001
        if strict:
            return None, f"Symbol resolution failed: {e}"
        logger.warning(f"Symbol resolution failed for '{symbol}': {e}")
        return symbol, ""


def _sanitize_comment(comment: str, prefix: str) -> str:
    """MT5 ORDER_COMMENT 上限 27 字符且拒收特殊字符（真实拒单事故）。
    清洗规则与 broker.py 原实现一致：ASCII 字母数字/下划线/短横线 + 空格。"""
    import re

    safe = re.sub(r"[^A-Za-z0-9 _-]", "", comment or "").strip()
    safe = safe[: 27 - len(prefix)]
    return f"{prefix} {safe}".strip() if safe else prefix


async def _rolling_avg_spread(redis, symbol: str, spread: float) -> float:
    if redis is None:
        return spread
    try:
        key = f"guardrails:spread_history:{symbol}"
        await redis.rpush(key, str(spread))
        await redis.ltrim(key, -SPREAD_WINDOW, -1)
        await redis.expire(key, 86400)
        recent = await redis.lrange(key, 0, -1)
        if recent:
            vals = [float(v) for v in recent if v]
            if vals:
                return sum(vals) / len(vals)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Rolling spread read failed, using current: {e}")
    return spread


async def preflight_order(
    connector,
    redis,
    guardrails,
    symbol: str,
    order_type: str,
    lot: float,
    sl: float,
    tp: float,
    *,
    strict_symbol: bool = False,
    direction: str | None = None,
    entry_price: float | None = None,
    account_login: str | None = None,
    check_live_auth: bool = True,
) -> PreflightOutcome:
    """Run the full hard-gate sequence; return context for execution or rejection.

    Does NOT execute or check the switching gate — failure semantics of that
    gate differ per channel (manual: fail-closed, AI: best-effort).

    direction/entry_price: pending orders (BUY_LIMIT/...) carry their own
    reference price — guardrails' SL/TP direction checks must run against the
    pending price, not the tick mid (the bridge validates the price relation
    separately).

    account_login: 账号维度（H3）。传入后日亏/回撤熔断 key 带账号前缀
    （``circuit:acc:{login}:``），与引擎切换账号后重建的 CircuitBreaker
    key 对齐 —— 不传则读旧 ``circuit:`` 前缀的空 key，日亏闸门静默失效。
    """
    from app.config import SYMBOL_PROFILES, get_canonical_symbol, settings
    from app.risk.circuit_breaker import CircuitBreaker
    from app.services.symbol_validation import normalize_lot_to_volume_grid
    from mcp_server.guardrails import MICRO_MAX_LOT, TradingGuardrails

    guardrails = guardrails or TradingGuardrails(redis)

    # 账号维度回退（H3）：调用方未显式传 account_login 时，尝试从全局
    # BotManager 读取当前账号 —— 保证日亏熔断 key 与引擎切换后重建的
    # 熔断器对齐，避免读到旧前缀的空 key 导致日亏闸门静默失效。
    if account_login is None:
        try:
            from app.bot.manager import get_global_manager

            _mgr = get_global_manager()
            if _mgr is not None:
                account_login = getattr(_mgr, "current_account_login", None)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"preflight_order account_login lookup failed: {e!r}")

    # 1. Symbol resolution（strict 手动通道 fail-closed）
    resolved, err = _resolve_symbol(symbol, strict=strict_symbol)
    if resolved is None:
        return _reject(err, "symbol")
    symbol = resolved

    # 2. Market state（三路并发；任一失败拒单 —— 没有数据就没有防线）
    account_res, positions_res, tick_res = await asyncio.gather(
        connector.get_account(),
        connector.get_positions(),
        connector.get_tick(symbol),
    )
    if not all(r.get("success") for r in (account_res, positions_res, tick_res)):
        detail = ", ".join(
            f"{name}: {r.get('error', 'unknown')}"
            for name, r in (("account", account_res), ("positions", positions_res), ("tick", tick_res))
            if not r.get("success")
        )
        logger.error(f"preflight_order pre-check failed: {detail}")
        return _reject(f"Failed to fetch data for guardrail validation: {detail}", "data_fetch")

    account = account_res["data"]
    positions = positions_res.get("data", [])
    tick = tick_res["data"]

    # 3. Spread + rolling average（key 按 symbol 分）
    spread = tick.get("ask", 0) - tick.get("bid", 0)
    avg_spread = await _rolling_avg_spread(redis, symbol, spread)

    # 4. daily_pnl 必须是已实现盈亏（CircuitBreaker），浮动盈亏会让盈利仓
    #    掩盖已实现亏损、绕过日亏限额。Redis 缺失时回退 account.profit（仅测试）。
    #    账号维度（H3）：传 account_login 使 key 与引擎切换后重建的熔断器
    #    对齐（circuit:acc:{login}:daily_pnl:{symbol}），否则读旧空 key，日亏闸门失效。
    if redis is not None:
        realized_daily_pnl = await CircuitBreaker(redis, symbol=symbol, account_login=account_login).get_daily_pnl()
    else:
        realized_daily_pnl = account.get("profit", 0)

    # 4b. 账户级日亏：聚合全部在线品种的已实现日亏。单品种检查会让
    #     GOLD 亏 4% + BTCUSD 亏 1% 各自不触发、组合已超 3% —— 账户级
    #     闸门堵住这条"分散亏损旁路"。
    account_daily_pnl = None
    if redis is not None:
        try:
            from app.bot.manager import get_global_manager as _ggm

            _mgr = _ggm()
            active_symbols = await CircuitBreaker.get_active_symbols(_mgr)
            account_daily_pnl = await CircuitBreaker.get_global_daily_pnl(
                redis, active_symbols, account_login
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Account-level daily PnL aggregation failed: {e!r}")

    # 5. 参考价：挂单用挂单价，市价单取 tick 中间价（ask/bid 直接取会误判
    #    BUY 的 SL>=entry）
    if entry_price is not None:
        entry_ref = entry_price
    else:
        entry_ref = (tick.get("ask", 0) + tick.get("bid", 0)) / 2 if tick.get("ask") and tick.get("bid") else 0

    # 6. GUARDRAIL CHECK（不可绕过）
    #    持仓 symbol 必须先归一化为 canonical 引擎键 —— bridge 返回的是
    #    券商别名（如 "GOLD_"），而 guardrails 按 canonical 过滤并发计数
    #    （MAX_CONCURRENT_PER_SYMBOL）；否则比较永不匹配，该闸门静默失效。
    normalized_positions = [
        {**p, "symbol": get_canonical_symbol(str(p.get("symbol") or "")) or p.get("symbol")}
        for p in positions
    ]
    result = await guardrails.validate_order(
        symbol=symbol,
        lot=lot,
        order_type=direction or order_type,
        current_positions=normalized_positions,
        account_balance=account.get("balance", 0),
        daily_pnl=realized_daily_pnl,
        spread=spread,
        avg_spread=avg_spread,
        entry_price=entry_ref,
        sl=sl,
        tp=tp,
        account_daily_pnl=account_daily_pnl,
    )
    if not result.allowed:
        return _reject(result.reason, "guardrail")

    # 6b. equity 日内回撤闸门（余额 + 浮动盈亏）。只看已实现会让持仓
    #     浮亏 8% 完全隐形；参考值=当日峰值 equity，跨日自动失效。
    if redis is not None:
        try:
            equity = account.get("balance", 0) + account.get("profit", 0)
            eq_halted, eq_ref = await CircuitBreaker.is_equity_drawdown_halted(
                redis, equity, settings.max_equity_drawdown, account_login, symbol
            )
            if eq_halted:
                return _reject(
                    f"Equity intraday drawdown: equity={equity:.2f}, ref={eq_ref:.2f} — trading halted",
                    "guardrail",
                )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Equity drawdown check failed: {e!r}")

    # 7. 券商手数防线：向下取整到 volume grid，低于 volume_min 拒单 ——
    #    否则 bridge 静默放大，击穿风险预算。
    broker_symbol = to_broker_alias(symbol)
    profile = SYMBOL_PROFILES.get(symbol) or {}
    if strict_symbol and not profile.get("volume_min"):
        # 手动通道：无 volume 配置 = 防线不存在 = 拒绝（fail-closed；
        # normalize_lot_to_volume_grid 对缺失配置原样放行，检查必须前置）
        return _reject(
            f"No volume config for '{symbol}' — cannot validate lot against broker grid",
            "lot",
        )
    guarded_lot = normalize_lot_to_volume_grid(
        lot,
        volume_min=profile.get("volume_min"),
        volume_max=profile.get("volume_max"),
        volume_step=profile.get("volume_step"),
    )
    if guarded_lot is None:
        logger.warning(
            f"preflight_order rejected [{symbol}]: lot {lot} below broker minimum "
            f"{profile.get('volume_min')} — bridge would upsize beyond risk budget"
        )
        return _reject(
            f"lot {lot} below broker minimum {profile.get('volume_min')} — "
            f"raise lot or fix symbol volume config",
            "lot",
        )
    if guarded_lot != lot:
        logger.info(f"preflight_order [{symbol}]: lot normalized {lot} → {guarded_lot}")
    normalized_lot = guarded_lot

    # 8. ROLLOUT MODE（Redis 持久化为准 —— env 口径会对 UI 降级 fail-open）
    rollout_mode = await guardrails.get_persisted_rollout_mode()

    lot = normalized_lot
    if rollout_mode == "micro":
        # cap 由调用方执行（guardrails 只报 message）——在此统一落实
        lot = min(lot, MICRO_MAX_LOT)
        logger.info(f"preflight_order [{symbol}]: micro cap applied — lot → {lot}")

    # 9. PROVIDER-AGNOSTIC LIVE AUTHORIZATION（micro/live 真实资金执行必须
    #    显式 LLM_ALLOW_LIVE=true；UI 降级与撤权即时生效）
    #    check_live_auth=False 用于引擎自营通道：该通道由用户显式启动，不因
    #    LLM 授权开关而静默停摆（llm_allow_live 语义面向 AI/agent 通道）。
    if check_live_auth and rollout_mode in ("micro", "live") and not settings.llm_allow_live:
        logger.warning(
            f"preflight_order [{symbol}] rejected: rollout={rollout_mode} requires LLM_ALLOW_LIVE=true"
        )
        return _reject(
            f"rollout={rollout_mode}: 需要显式设置 LLM_ALLOW_LIVE=true 才放开真实下单",
            "live_auth",
        )

    ctx = PreflightContext(
        symbol=symbol,
        broker_symbol=broker_symbol,
        order_type=order_type,
        base_lot=normalized_lot,
        lot=lot,
        sl=sl,
        tp=tp,
        account=account,
        positions=normalized_positions,
        tick=tick,
        spread=spread,
        avg_spread=avg_spread,
        daily_pnl=realized_daily_pnl,
        entry_ref=entry_ref,
        rollout_mode=rollout_mode,
        guardrails=guardrails,
        profile=profile,
    )
    return PreflightOutcome(ok=True, ctx=ctx)
