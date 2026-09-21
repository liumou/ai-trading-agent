"""
Circuit Breaker — tracks daily P&L via Redis, halts trading when limit is reached.
Supports per-symbol market hour reset and cooldown-based auto-recovery.

Reset hour is derived from the symbol's asset_class via app.market.sessions so
user-added symbols inherit the correct daily reset without hardcoded lookups.
"""

from datetime import UTC, datetime

import redis.asyncio as redis
from loguru import logger

from app.config import SYMBOL_PROFILES, settings
from app.constants import DEFAULT_MAX_DRAWDOWN_FROM_PEAK, DEFAULT_PORTFOLIO_MAX_LOSS, MIN_TTL_SECONDS
from app.market.sessions import seconds_until_reset

# Cooldown period before auto-recovery (minutes)
DEFAULT_COOLDOWN_MINUTES = 60


def _asset_class_for(symbol: str) -> str | None:
    profile = SYMBOL_PROFILES.get(symbol) or {}
    return profile.get("asset_class")


class CircuitBreaker:
    def __init__(
        self,
        redis_client: redis.Redis,
        symbol: str = "GOLD",
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        account_login: str | None = None,
    ):
        """每日 P&L 熔断。

        H3 修复：所有 key 带账号维度前缀。``account_login=None`` 保持旧 key
        （向后兼容，现有调用不受影响）；切换服务传入当前账号后按账号隔离，
        避免跨账号日损/回撤污染。

        ``"0"`` 是默认占位（未连接账号）—— 归一化为 None，使引导期
        （引擎构造无前缀 + preflight 读到 account_login="0"）key 对齐；
        否则 preflight 读 ``circuit:acc:0:`` 而引擎写 ``circuit:``，日亏
        闸门在首次切换前静默失效（评审 C1 引导期变体）。
        """
        if account_login == "0":
            account_login = None
        self.redis = redis_client
        self.symbol = symbol
        self.account_login = account_login
        prefix = f"circuit:acc:{account_login}:" if account_login else "circuit:"
        self.pnl_key = f"{prefix}daily_pnl:{symbol}"
        self.trade_count_key = f"{prefix}trade_count:{symbol}"
        self.triggered_key = f"{prefix}triggered_at:{symbol}"
        self.cooldown_minutes = cooldown_minutes

    async def record_trade_result(self, profit: float, ticket: int | None = None) -> None:
        """记录一笔已实现盈亏到当日 P&L。

        ``ticket`` 传入时按 ticket 幂等：同一笔平仓（重启重检、启动回填、
        手动+引擎双通道）只会记一次。ticket 集合与当日 P&L 同 TTL 域，跨日
        自动失效。
        """
        prefix = f"circuit:acc:{self.account_login}:" if self.account_login else "circuit:"
        if ticket is not None:
            seen_key = f"{prefix}closed_tickets:{self.symbol}"
            if await self.redis.sismember(seen_key, ticket):
                return
            await self.redis.sadd(seen_key, ticket)
            await self.redis.expire(seen_key, 86400 * 2)

        current = await self.redis.get(self.pnl_key)
        current_pnl = float(current) if current else 0.0
        new_pnl = current_pnl + profit
        ttl = self._seconds_until_reset(self.symbol)
        await self.redis.set(self.pnl_key, str(new_pnl), ex=ttl)

        count = await self.redis.get(self.trade_count_key)
        new_count = int(count) + 1 if count else 1
        await self.redis.set(self.trade_count_key, str(new_count), ex=ttl)

        logger.info(
            f"Circuit breaker [{self.symbol}]: recorded profit={profit:.2f}, daily_pnl={new_pnl:.2f}, trades={new_count}"
        )

    async def get_daily_pnl(self) -> float:
        val = await self.redis.get(self.pnl_key)
        return float(val) if val else 0.0

    async def get_trade_count(self) -> int:
        val = await self.redis.get(self.trade_count_key)
        return int(val) if val else 0

    async def is_triggered(self, balance: float) -> bool:
        daily_pnl = await self.get_daily_pnl()
        max_loss = balance * settings.max_daily_loss
        triggered = daily_pnl <= -max_loss

        # Early warning at 80% of daily loss limit (once per day)
        if not triggered and daily_pnl <= -(max_loss * 0.8):
            warn_key = f"{self.pnl_key.removesuffix(f'daily_pnl:{self.symbol}')}drawdown_warned:{self.symbol}"
            already_warned = await self.redis.get(warn_key)
            if not already_warned:
                ttl = self._seconds_until_reset(self.symbol)
                await self.redis.set(warn_key, "1", ex=ttl)
                logger.warning(
                    f"Drawdown warning [{self.symbol}]: daily_pnl={daily_pnl:.2f} "
                    f"({abs(daily_pnl / max_loss) * 100:.0f}% of limit)"
                )

        if triggered:
            # Record trigger time for cooldown
            await self.redis.set(self.triggered_key, datetime.now(UTC).isoformat(), ex=86400)
            logger.warning(
                f"Circuit breaker [{self.symbol}] TRIGGERED: daily_pnl={daily_pnl:.2f}, limit=-{max_loss:.2f}"
            )
        return triggered

    async def can_resume(self) -> bool:
        """Check if enough cooldown time has passed to allow auto-recovery."""
        triggered_at_str = await self.redis.get(self.triggered_key)
        if not triggered_at_str:
            return True

        try:
            triggered_at = datetime.fromisoformat(
                triggered_at_str.decode() if isinstance(triggered_at_str, bytes) else triggered_at_str
            )
            if triggered_at.tzinfo is None:
                triggered_at = triggered_at.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return True

        elapsed = (datetime.now(UTC) - triggered_at).total_seconds() / 60
        if elapsed < self.cooldown_minutes:
            logger.debug(f"Circuit breaker [{self.symbol}] cooldown: {self.cooldown_minutes - elapsed:.0f}m remaining")
            return False

        # Cooldown passed, clear trigger
        await self.redis.delete(self.triggered_key)
        logger.info(f"Circuit breaker [{self.symbol}] cooldown complete — ready to resume")
        return True

    async def reset(self) -> None:
        await self.redis.delete(self.pnl_key, self.trade_count_key, self.triggered_key)
        logger.info(f"Circuit breaker [{self.symbol}] reset")

    @staticmethod
    def _acc_key(account_login: str | None, suffix: str) -> str:
        """生成带账号维度的 key。account_login 为 None 时保持旧 key（向后兼容）。

        ``"0"`` 默认占位归一化为 None（与 __init__ 保持一致，避免引导期
        全局日亏读 ``circuit:acc:0:`` 而引擎写 ``circuit:`` 的错位）。
        """
        if account_login == "0":
            account_login = None
        return f"circuit:acc:{account_login}:{suffix}" if account_login else f"circuit:{suffix}"

    @staticmethod
    async def get_global_daily_pnl(redis_client, symbols: list[str], account_login: str | None = None) -> float:
        """Sum daily PnL across all symbols for portfolio-level risk check."""
        keys = [CircuitBreaker._acc_key(account_login, f"daily_pnl:{symbol}") for symbol in symbols]
        values = await redis_client.mget(keys)
        return sum(float(v) for v in values if v)

    @staticmethod
    async def is_global_triggered(
        redis_client,
        symbols: list[str],
        balance: float,
        max_portfolio_loss: float = DEFAULT_PORTFOLIO_MAX_LOSS,
        account_login: str | None = None,
    ) -> bool:
        """Check if total daily loss across all symbols exceeds portfolio limit."""
        total_pnl = await CircuitBreaker.get_global_daily_pnl(redis_client, symbols, account_login)
        max_loss = balance * max_portfolio_loss
        triggered = total_pnl <= -max_loss
        if triggered:
            logger.warning(f"GLOBAL circuit breaker TRIGGERED: total_pnl={total_pnl:.2f}, limit=-{max_loss:.2f}")
        return triggered

    @staticmethod
    async def update_peak_balance(redis_client, balance: float, account_login: str | None = None) -> float:
        """Track peak balance in Redis (no TTL — persists across restarts).

        H3：peak_balance 按账号分键，避免切换账号制造不可恢复的全局回撤停盘。
        """
        key = CircuitBreaker._acc_key(account_login, "peak_balance")
        current = await redis_client.get(key)
        peak = float(current) if current else 0.0
        if balance > peak:
            peak = balance
            await redis_client.set(key, str(peak))
        return peak

    @staticmethod
    async def is_drawdown_halted(
        redis_client,
        balance: float,
        max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_FROM_PEAK,
        account_login: str | None = None,
    ) -> bool:
        """Check if balance dropped > X% from peak. Returns True to halt trading."""
        peak = await CircuitBreaker.update_peak_balance(redis_client, balance, account_login)
        if peak <= 0:
            return False
        drawdown_pct = (peak - balance) / peak
        if drawdown_pct >= max_drawdown_pct:
            logger.warning(
                f"ABSOLUTE DRAWDOWN HALT: balance={balance:.2f}, peak={peak:.2f}, "
                f"drawdown={drawdown_pct:.1%} >= limit={max_drawdown_pct:.1%}"
            )
            return True
        return False

    @staticmethod
    async def is_equity_drawdown_halted(
        redis_client,
        equity: float,
        max_drawdown_pct: float,
        account_login: str | None = None,
        symbol: str | None = None,
    ) -> tuple[bool, float]:
        """日内 equity（余额 + 浮动盈亏）回撤闸门。

        参考值 = 当日所见最高 equity（key 带秒级重置 TTL，跨日自动失效；
        首个观测值即当日基准）。回撤 ≥ max_drawdown_pct 返回 True=停新开仓。
        max_drawdown_pct <= 0 视为禁用。返回 (halted, ref_equity)。
        """
        if max_drawdown_pct <= 0 or equity <= 0:
            return False, 0.0
        key = CircuitBreaker._acc_key(account_login, "equity_ref")
        ttl = CircuitBreaker._seconds_until_reset(symbol) if symbol else 86400
        ref_raw = await redis_client.get(key)
        if ref_raw is None:
            ref = equity
            await redis_client.set(key, str(ref), ex=ttl)
        else:
            ref = float(ref_raw)
            if equity > ref:
                ref = equity
                await redis_client.set(key, str(ref), ex=ttl)
        drawdown_pct = (ref - equity) / ref if ref > 0 else 0.0
        if drawdown_pct >= max_drawdown_pct:
            logger.warning(
                f"EQUITY DRAWDOWN HALT: equity={equity:.2f}, ref={ref:.2f}, "
                f"drawdown={drawdown_pct:.1%} >= limit={max_drawdown_pct:.1%}"
            )
            return True, ref
        return False, ref

    @staticmethod
    async def get_active_symbols(manager=None) -> list[str]:
        """在线交易品种集合（全局日亏/回撤的作用域）。

        优先取在线引擎集合（含 /symbols 热重载加入的 DB 管理品种），
        回退到 SYMBOL_PROFILES / settings.symbol_list。与引擎
        _check_circuit_breakers 的作用域口径一致，供 preflight 等单品种
        校验方聚合账户级日亏时复用。
        """
        if manager is not None:
            engines = getattr(manager, "engines", None)
            if engines:
                return list(engines.keys())
        from app.config import SYMBOL_PROFILES, settings

        return [s for s, p in SYMBOL_PROFILES.items() if "canonical" not in p] or settings.symbol_list

    @staticmethod
    async def backfill_today(
        connector,
        redis_client,
        symbol: str,
        account_login: str | None = None,
    ) -> int:
        """启动回填：用 MT5 当日已平仓历史补齐日亏/连亏计数（按 ticket 幂等）。

        覆盖引擎不在场/崩溃期平仓漏记 —— 日亏 3% 闸门的数据源一旦缺口，
        单品种校验会读到旧值放行。返回回填笔数。
        """
        from mcp_server.guardrails import TradingGuardrails

        # 桥返回的 deal symbol 是券商别名（如 "GOLD_"），后端 canonical 是
        # "GOLD" —— 两侧去下划线/大写后比较（to_broker_alias 对 GOLD 返回
        # "GOLD" 而非 "GOLD_"，不能直接用于匹配）。
        sym_norm = symbol.upper().replace("_", "")
        hist = await connector.get_history(days=1)
        if not hist.get("success"):
            return 0
        cb = CircuitBreaker(redis_client, symbol=symbol, account_login=account_login)
        gr = TradingGuardrails(redis_client)
        n = 0
        for d in hist.get("data", []):
            deal_sym = (d.get("symbol") or "").upper().replace("_", "")
            if deal_sym != sym_norm:
                continue
            ticket = d.get("ticket")
            profit = d.get("profit", 0) or 0
            await cb.record_trade_result(profit, ticket=ticket)
            await gr.record_trade_closed(is_win=profit > 0, ticket=ticket)
            n += 1
        if n:
            logger.info(f"Circuit breaker backfill [{symbol}]: {n} closed deals reconciled")
        return n

    @staticmethod
    def _seconds_until_reset(symbol: str) -> int:
        """Seconds until daily reset, derived from the symbol's asset class."""
        asset_class = _asset_class_for(symbol)
        # Strip timezone info — sessions.seconds_until_reset uses naive UTC internally
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        return max(seconds_until_reset(asset_class, now=now_naive), MIN_TTL_SECONDS)
