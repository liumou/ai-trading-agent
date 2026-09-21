"""
Trading guardrails — hard limits enforced at MCP broker tool level.

The agent CANNOT bypass these. Every broker.place_order() must pass through
validate_order() before execution.

State is tracked in Redis with TTL-based keys for automatic expiry.
"""

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis_lib
from loguru import logger

# ─── Hard Limits (non-negotiable) ────────────────────────────────────────────
#单笔手数	≤ 1.0 lot	超过就拒（MAX_LOT_PER_TRADE）
#单品种并发持仓	≤ 3 个	已有 3 个时，第 4 个拒（MAX_CONCURRENT_PER_SYMBOL）
#总持仓	≤ 5 个	已有 5 个时，第 6 个拒（MAX_CONCURRENT_TOTAL）
##日亏损	≤ 3% 余额	已实现亏损 ÷ 余额 ≥ 3% 就拒（MAX_DAILY_LOSS_PCT）
#连亏	< 5 笔	连续亏损 ≥ 5 笔熔断拒单（CONSECUTIVE_LOSS_HALT）
#每小时交易	< 5 笔	本小时已达 5 笔就拒（MAX_TRADES_PER_HOUR）
#交易最小间隔	≥ 120 秒	距上一笔成交不足 120 秒就拒
#点差	≤ 3× 均值	当前点差 > 该品种近 20 次滚动均值的 3 倍就拒（MAX_SPREAD_MULTIPLIER）
#SL/TP 方向	—	BUY 单 SL ≥ 买入价（会立即亏损）拒；TP ≤ 买入价拒；SELL 反之；SL=0（无止损）直接拒
# Position limits
MAX_LOT_PER_TRADE = 1.0
MAX_CONCURRENT_PER_SYMBOL = 3
MAX_CONCURRENT_TOTAL = 5

# Loss limits
MAX_DAILY_LOSS_PCT = 0.03
MAX_WEEKLY_LOSS_PCT = 0.07
CONSECUTIVE_LOSS_HALT = 5

# Execution limits
MAX_TRADES_PER_HOUR = 5
MIN_TIME_BETWEEN_TRADES = 120  # seconds
MAX_SPREAD_MULTIPLIER = 3.0

# Agent limits
MAX_AGENT_TURNS = 50
AGENT_TIMEOUT = 300  # seconds
MAX_DAILY_AGENT_CALLS = 200

# Token failure policy
ON_TOKEN_FAILURE = "pause"

# ─── Rollout Mode (Phase F) ─────────────────────────────────────────────────
# Controls execution behavior at the broker level.
# Set via ROLLOUT_MODE env var or config.settings.rollout_mode.

ROLLOUT_MODES = ("shadow", "paper", "micro", "live")
MICRO_MAX_LOT = 0.01  # Micro-live caps all orders at 0.01 lot

# ─── Redis Keys ──────────────────────────────────────────────────────────────

_KEY_PREFIX = "guardrails"


def _daily_key(name: str) -> str:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{_KEY_PREFIX}:{name}:{date}"


def _hourly_key(name: str) -> str:
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    return f"{_KEY_PREFIX}:{name}:{hour}"


# ─── Validation Result ───────────────────────────────────────────────────────


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str = ""


# ─── Guardrails Class ────────────────────────────────────────────────────────


class TradingGuardrails:
    """Enforces hard trading limits at the MCP tool level."""

    def __init__(self, redis: redis_lib.Redis):
        self.redis = redis

    def get_rollout_mode(self) -> str:
        """Synchronous fallback used only when no Redis connection is available
        (tests, init-time checks). Production callers must use the async
        :meth:`get_persisted_rollout_mode` so a UI-driven downgrade (e.g.
        live → paper after an incident) actually takes effect.
        """
        mode = os.environ.get("ROLLOUT_MODE", "shadow")
        return mode if mode in ROLLOUT_MODES else "shadow"

    async def set_rollout_mode(self, mode: str) -> None:
        """Persist rollout mode to Redis (survives restarts)."""
        if mode not in ROLLOUT_MODES:
            raise ValueError(f"Invalid rollout mode: {mode}. Must be one of {ROLLOUT_MODES}")
        await self.redis.set(f"{_KEY_PREFIX}:rollout_mode", mode)
        os.environ["ROLLOUT_MODE"] = mode

    async def get_persisted_rollout_mode(self) -> str:
        """Get rollout mode. Redis is the source of truth — the env var is
        only used as a bootstrap default before any UI write has happened.
        Otherwise an env-set ``ROLLOUT_MODE=live`` would silently override an
        operator's UI downgrade after an incident.
        """
        try:
            val = await self.redis.get(f"{_KEY_PREFIX}:rollout_mode")
        except Exception:
            val = None
        if val:
            mode = val.decode() if isinstance(val, bytes) else str(val)
            if mode in ROLLOUT_MODES:
                return mode
        return self.get_rollout_mode()

    async def check_rollout_mode_async(self, lot: float) -> GuardrailResult:
        """Async variant that reads Redis first. Prefer this in MCP tool code
        paths so live-mode downgrades from the UI take effect immediately."""
        mode = await self.get_persisted_rollout_mode()
        return self._evaluate_rollout(mode, lot)

    def check_rollout_mode(self, lot: float) -> GuardrailResult:
        """Sync fallback. Prefer :meth:`check_rollout_mode_async` from any
        coroutine context so the Redis-persisted mode wins over a stale env
        value.
        """
        return self._evaluate_rollout(self.get_rollout_mode(), lot)

    def _evaluate_rollout(self, mode: str, lot: float) -> GuardrailResult:
        if mode == "shadow":
            return GuardrailResult(False, "SHADOW MODE: order logged but not executed")

        if mode == "paper":
            return GuardrailResult(False, "PAPER MODE: order simulated, not sent to broker")

        if mode == "micro":
            if lot > MICRO_MAX_LOT:
                return GuardrailResult(
                    True,  # allowed, but lot will be capped
                    f"MICRO MODE: lot capped from {lot} to {MICRO_MAX_LOT}",
                )
            return GuardrailResult(True)

        # mode == "live"
        return GuardrailResult(True)

    async def validate_order(
        self,
        symbol: str,
        lot: float,
        order_type: str,
        current_positions: list[dict],
        account_balance: float,
        daily_pnl: float,
        spread: float,
        avg_spread: float,
        entry_price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        account_daily_pnl: float | None = None,
    ) -> GuardrailResult:
        """Validate a trade order against all guardrails.

        Args:
            symbol: Trading symbol (e.g., "GOLD")
            lot: Lot size for the order
            order_type: "BUY" or "SELL"
            current_positions: List of open positions [{symbol, lot, profit, ...}]
            account_balance: Current account balance
            daily_pnl: Today's realized P&L for this symbol
            spread: Current spread in pips
            avg_spread: Average spread for this symbol
            entry_price: Reference price for SL/TP validation (None skips)
            sl: Stop-loss price (None skips validation)
            tp: Take-profit price (None skips validation)
            account_daily_pnl: Today's realized P&L across ALL symbols
                (账户级日亏，None 跳过——单品种检查可能漏掉分散亏损)
        """
        result = await self._validate_order_core(
            symbol=symbol,
            lot=lot,
            order_type=order_type,
            current_positions=current_positions,
            account_balance=account_balance,
            daily_pnl=daily_pnl,
            spread=spread,
            avg_spread=avg_spread,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            account_daily_pnl=account_daily_pnl,
        )
        if not result.allowed:
            await self._audit_rejection(symbol, result.reason)
        return result

    async def _audit_rejection(self, symbol: str, reason: str) -> None:
        """风控拒绝审计：WARNING 日志 + Redis 当日拒绝列表（供运维/UI 追踪）。

        此前拒绝只以 reason 字符串返回给调用方（AI agent 自行总结、手动通道
        仅日志），用户侧完全不可见 —— 风控是否在工作无从核对。
        """
        logger.warning(f"Guardrail rejected [{symbol}]: {reason}")
        try:
            key = f"{_KEY_PREFIX}:rejections:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            await self.redis.rpush(key, f"{datetime.now(UTC).isoformat()} {symbol} {reason}")
            await self.redis.expire(key, 86400 * 2)
            await self.redis.ltrim(key, -500, -1)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Rejection audit failed: {e!r}")

    async def _validate_order_core(
        self,
        symbol: str,
        lot: float,
        order_type: str,
        current_positions: list[dict],
        account_balance: float,
        daily_pnl: float,
        spread: float,
        avg_spread: float,
        entry_price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        account_daily_pnl: float | None = None,
    ) -> GuardrailResult:
        """Core guardrail checks (see :meth:`validate_order` for docs)."""
        # 1. Max lot per trade
        if lot > MAX_LOT_PER_TRADE:
            return GuardrailResult(False, f"Lot {lot} exceeds max {MAX_LOT_PER_TRADE}")

        if lot <= 0:
            return GuardrailResult(False, f"Invalid lot size: {lot}")

        # 2. Max concurrent positions per symbol
        symbol_positions = [p for p in current_positions if p.get("symbol") == symbol]
        if len(symbol_positions) >= MAX_CONCURRENT_PER_SYMBOL:
            return GuardrailResult(
                False,
                f"{symbol}: {len(symbol_positions)} positions (max {MAX_CONCURRENT_PER_SYMBOL})",
            )

        # 3. Max concurrent positions total
        if len(current_positions) >= MAX_CONCURRENT_TOTAL:
            return GuardrailResult(
                False,
                f"Total positions {len(current_positions)} (max {MAX_CONCURRENT_TOTAL})",
            )

        # 3b. SL/TP sanity validation — AI can place garbage orders (no SL,
        #     wrong-direction SL/TP). These must be rejected before reaching
        #     the broker, otherwise a real account runs unprotected.
        if entry_price is not None and entry_price > 0:
            if order_type == "BUY":
                if sl is not None and sl <= 0:
                    return GuardrailResult(False, f"Invalid SL {sl} for BUY — must be > 0")
                if tp is not None and tp <= 0:
                    return GuardrailResult(False, f"Invalid TP {tp} for BUY — must be > 0")
                if sl is not None and sl >= entry_price:
                    return GuardrailResult(False, f"BUY SL {sl} >= entry {entry_price} — would lose immediately")
                if tp is not None and tp <= entry_price:
                    return GuardrailResult(False, f"BUY TP {tp} <= entry {entry_price} — invalid")
            elif order_type == "SELL":
                if sl is not None and sl <= 0:
                    return GuardrailResult(False, f"Invalid SL {sl} for SELL — must be > 0")
                if tp is not None and tp <= 0:
                    return GuardrailResult(False, f"Invalid TP {tp} for SELL — must be > 0")
                if sl is not None and sl <= entry_price:
                    return GuardrailResult(False, f"SELL SL {sl} <= entry {entry_price} — would lose immediately")
                if tp is not None and tp >= entry_price:
                    return GuardrailResult(False, f"SELL TP {tp} >= entry {entry_price} — invalid")

        # 4. Daily loss limit
        if account_balance > 0 and daily_pnl < 0:
            loss_pct = abs(daily_pnl) / account_balance
            if loss_pct >= MAX_DAILY_LOSS_PCT:
                return GuardrailResult(
                    False,
                    f"Daily loss {loss_pct:.1%} exceeds limit {MAX_DAILY_LOSS_PCT:.0%}",
                )

        # 4b. Account-level daily loss（多品种分散亏损也能触发）
        if account_daily_pnl is not None and account_balance > 0 and account_daily_pnl < 0:
            acct_loss_pct = abs(account_daily_pnl) / account_balance
            if acct_loss_pct >= MAX_DAILY_LOSS_PCT:
                return GuardrailResult(
                    False,
                    f"Account daily loss {acct_loss_pct:.1%} exceeds limit "
                    f"{MAX_DAILY_LOSS_PCT:.0%} (all symbols)",
                )

        # 5. Consecutive loss halt
        consecutive_losses = await self._get_consecutive_losses()
        if consecutive_losses >= CONSECUTIVE_LOSS_HALT:
            return GuardrailResult(
                False,
                f"{consecutive_losses} consecutive losses (halt at {CONSECUTIVE_LOSS_HALT})",
            )

        # 6. Trades per hour
        trades_this_hour = await self._get_trades_this_hour()
        if trades_this_hour >= MAX_TRADES_PER_HOUR:
            return GuardrailResult(
                False,
                f"{trades_this_hour} trades this hour (max {MAX_TRADES_PER_HOUR})",
            )

        # 7. Min time between trades
        last_trade_time = await self._get_last_trade_time()
        if last_trade_time:
            elapsed = time.time() - last_trade_time
            if elapsed < MIN_TIME_BETWEEN_TRADES:
                remaining = int(MIN_TIME_BETWEEN_TRADES - elapsed)
                return GuardrailResult(
                    False,
                    f"Too soon — wait {remaining}s (min {MIN_TIME_BETWEEN_TRADES}s between trades)",
                )

        # 8. Spread check
        if avg_spread > 0 and spread > avg_spread * MAX_SPREAD_MULTIPLIER:
            return GuardrailResult(
                False,
                f"Spread {spread:.1f} > {MAX_SPREAD_MULTIPLIER}x avg ({avg_spread:.1f})",
            )

        return GuardrailResult(True)

    async def validate_agent_call(self) -> GuardrailResult:
        """Check if the agent is within daily call limits."""
        calls_today = await self._get_daily_agent_calls()
        if calls_today >= MAX_DAILY_AGENT_CALLS:
            return GuardrailResult(
                False,
                f"Daily agent call limit reached: {calls_today}/{MAX_DAILY_AGENT_CALLS}",
            )
        return GuardrailResult(True)

    # ─── State Tracking ─────────────────────────────────────────────────────

    async def record_trade(self, is_win: bool) -> None:
        """Record a trade for frequency/interval tracking.

        NOTE: ``is_win`` is kept for backward compatibility but the win/loss
        outcome is recorded separately via :meth:`record_trade_closed`.
        Calling this with ``is_win=True`` at open time no longer pollutes the
        consecutive-loss counter with fake wins.
        """
        await self.record_order_opened()

    async def record_order_opened(self) -> None:
        """Record an order open for frequency + interval limits (not P&L outcome)."""
        # Update hourly counter
        hour_key = _hourly_key("trades")
        await self.redis.incr(hour_key)
        await self.redis.expire(hour_key, 3600)

        # Update last trade time
        await self.redis.set(f"{_KEY_PREFIX}:last_trade_time", str(time.time()))

    async def record_trade_closed(self, is_win: bool, ticket: int | None = None) -> None:
        """Record a closed trade outcome for consecutive loss tracking.

        Must be called from the close path with the REAL P&L outcome.
        Previously this was only ever called with ``is_win=True`` at open time,
        so CONSECUTIVE_LOSS_HALT could never trigger.

        ``ticket`` 传入时按 ticket 幂等：同一笔平仓（重启重检、启动回填）
        只记一次，避免连亏计数虚增。
        """
        if ticket is not None:
            seen_key = f"{_KEY_PREFIX}:closed_tickets:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            if await self.redis.sismember(seen_key, ticket):
                return
            await self.redis.sadd(seen_key, ticket)
            await self.redis.expire(seen_key, 86400 * 2)
        key = _daily_key("trade_results")
        await self.redis.rpush(key, "1" if is_win else "0")
        await self.redis.expire(key, 86400 * 2)  # 2 days TTL

    async def record_agent_call(self) -> None:
        """Increment daily agent call counter."""
        key = _daily_key("agent_calls")
        await self.redis.incr(key)
        await self.redis.expire(key, 86400 * 2)

    async def get_status(self) -> dict:
        """Get current guardrail state for monitoring."""
        try:
            from app.config import settings

            max_equity_drawdown = settings.max_equity_drawdown
        except Exception:  # noqa: BLE001
            max_equity_drawdown = 0.03
        return {
            "consecutive_losses": await self._get_consecutive_losses(),
            "trades_this_hour": await self._get_trades_this_hour(),
            "agent_calls_today": await self._get_daily_agent_calls(),
            "last_trade_time": await self._get_last_trade_time(),
            "limits": {
                "max_lot": MAX_LOT_PER_TRADE,
                "max_concurrent_symbol": MAX_CONCURRENT_PER_SYMBOL,
                "max_concurrent_total": MAX_CONCURRENT_TOTAL,
                "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
                "max_equity_drawdown_pct": max_equity_drawdown,
                "max_trades_per_hour": MAX_TRADES_PER_HOUR,
                "max_agent_calls": MAX_DAILY_AGENT_CALLS,
            },
        }

    # ─── Internal Helpers ────────────────────────────────────────────────────

    async def _get_consecutive_losses(self) -> int:
        """Count consecutive losses from the end of today's results."""
        key = _daily_key("trade_results")
        results = await self.redis.lrange(key, 0, -1)
        if not results:
            return 0

        count = 0
        for r in reversed(results):
            val = r.decode() if isinstance(r, bytes) else str(r)
            if val == "0":
                count += 1
            else:
                break
        return count

    async def _get_trades_this_hour(self) -> int:
        """Get trade count for the current hour."""
        key = _hourly_key("trades")
        val = await self.redis.get(key)
        return int(val) if val else 0

    async def _get_last_trade_time(self) -> float | None:
        """Get timestamp of last trade."""
        val = await self.redis.get(f"{_KEY_PREFIX}:last_trade_time")
        return float(val) if val else None

    async def _get_daily_agent_calls(self) -> int:
        """Get agent call count for today."""
        key = _daily_key("agent_calls")
        val = await self.redis.get(key)
        return int(val) if val else 0
