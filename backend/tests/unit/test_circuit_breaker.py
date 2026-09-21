"""
Unit tests for Circuit Breaker — daily PnL tracking, trigger, cooldown, reset.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.constants import MIN_TTL_SECONDS
from app.market.sessions import get_reset_hour
from app.risk.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    @pytest_asyncio.fixture
    async def cb(self, redis_client):
        return CircuitBreaker(redis_client, symbol="GOLD", cooldown_minutes=60)

    async def test_record_trade_result(self, cb):
        await cb.record_trade_result(100.0)
        pnl = await cb.get_daily_pnl()
        assert pnl == pytest.approx(100.0)

    async def test_record_multiple_trades(self, cb):
        await cb.record_trade_result(100.0)
        await cb.record_trade_result(-50.0)
        pnl = await cb.get_daily_pnl()
        assert pnl == pytest.approx(50.0)

    async def test_trade_count(self, cb):
        await cb.record_trade_result(10.0)
        await cb.record_trade_result(-5.0)
        count = await cb.get_trade_count()
        assert count == 2

    async def test_not_triggered_within_limit(self, cb):
        await cb.record_trade_result(-100.0)
        # Balance 10000, max_daily_loss 0.03 → limit is -300
        triggered = await cb.is_triggered(balance=10000)
        assert triggered is False

    async def test_triggered_exceeds_limit(self, cb):
        await cb.record_trade_result(-350.0)
        # -350 < -300 → triggered
        triggered = await cb.is_triggered(balance=10000)
        assert triggered is True

    async def test_can_resume_no_trigger(self, cb):
        # No trigger recorded → can resume
        result = await cb.can_resume()
        assert result is True

    async def test_can_resume_before_cooldown(self, cb, redis_client):
        # Set trigger time to now
        await redis_client.set(
            cb.triggered_key,
            datetime.now(UTC).isoformat(),
            ex=86400,
        )
        result = await cb.can_resume()
        assert result is False

    async def test_can_resume_after_cooldown(self, cb, redis_client):
        # Set trigger time to 2 hours ago (cooldown is 60 min)
        past = datetime.now(UTC) - timedelta(hours=2)
        await redis_client.set(
            cb.triggered_key,
            past.isoformat(),
            ex=86400,
        )
        result = await cb.can_resume()
        assert result is True

    async def test_reset_clears_keys(self, cb, redis_client):
        await cb.record_trade_result(-100.0)
        await cb.reset()
        pnl = await cb.get_daily_pnl()
        count = await cb.get_trade_count()
        assert pnl == 0.0
        assert count == 0

    async def test_initial_pnl_zero(self, cb):
        pnl = await cb.get_daily_pnl()
        assert pnl == 0.0

    async def test_initial_count_zero(self, cb):
        count = await cb.get_trade_count()
        assert count == 0


class TestCircuitBreakerGlobal:
    async def test_global_pnl_sum(self, redis_client):
        cb_gold = CircuitBreaker(redis_client, "GOLD")
        cb_oil = CircuitBreaker(redis_client, "OILCash")
        await cb_gold.record_trade_result(-100.0)
        await cb_oil.record_trade_result(-50.0)
        total = await CircuitBreaker.get_global_daily_pnl(redis_client, ["GOLD", "OILCash"])
        assert total == pytest.approx(-150.0)

    async def test_global_triggered(self, redis_client):
        cb = CircuitBreaker(redis_client, "GOLD")
        await cb.record_trade_result(-1100.0)
        triggered = await CircuitBreaker.is_global_triggered(
            redis_client,
            ["GOLD"],
            balance=10000,
            max_portfolio_loss=0.10,
        )
        assert triggered is True

    async def test_global_not_triggered(self, redis_client):
        cb = CircuitBreaker(redis_client, "GOLD")
        await cb.record_trade_result(-50.0)
        triggered = await CircuitBreaker.is_global_triggered(
            redis_client,
            ["GOLD"],
            balance=10000,
            max_portfolio_loss=0.10,
        )
        assert triggered is False


class TestCircuitBreakerTicketIdempotency:
    """按 ticket 幂等 —— 重启重检/启动回填/手动+引擎双通道不得重复记账。"""

    async def test_same_ticket_recorded_once(self, redis_client):
        cb = CircuitBreaker(redis_client, "GOLD")
        await cb.record_trade_result(-100.0, ticket=966668039)
        await cb.record_trade_result(-100.0, ticket=966668039)
        assert await cb.get_daily_pnl() == pytest.approx(-100.0)
        assert await cb.get_trade_count() == 1

    async def test_different_tickets_accumulate(self, redis_client):
        cb = CircuitBreaker(redis_client, "GOLD")
        await cb.record_trade_result(-100.0, ticket=1)
        await cb.record_trade_result(-50.0, ticket=2)
        assert await cb.get_daily_pnl() == pytest.approx(-150.0)
        assert await cb.get_trade_count() == 2

    async def test_no_ticket_always_accumulates(self, redis_client):
        cb = CircuitBreaker(redis_client, "GOLD")
        await cb.record_trade_result(-100.0)
        await cb.record_trade_result(-100.0)
        assert await cb.get_daily_pnl() == pytest.approx(-200.0)


class TestEquityDrawdownGate:
    """日内 equity（余额+浮动盈亏）回撤闸门。"""

    async def test_first_observation_sets_reference(self, redis_client):
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10000.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is False
        assert ref == pytest.approx(10000.0)

    async def test_halted_after_drawdown(self, redis_client):
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10000.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9600.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is True
        assert ref == pytest.approx(10000.0)

    async def test_within_limit_not_halted(self, redis_client):
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10000.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        halted, _ = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9900.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is False

    async def test_ref_rises_with_equity_gain(self, redis_client):
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10000.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10500.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        # 从新高 10500 回撤 4% = 10080 也应触发
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10080.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is True
        assert ref == pytest.approx(10500.0)

    async def test_disabled_when_zero(self, redis_client):
        halted, _ = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9600.0, max_drawdown_pct=0.0, symbol="GOLD"
        )
        assert halted is False

    async def test_account_scoped_keys(self, redis_client):
        # 不同账号的 equity 参考独立，不互相污染
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10000.0, max_drawdown_pct=0.03, account_login="A1", symbol="GOLD"
        )
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9700.0, max_drawdown_pct=0.03, account_login="A2", symbol="GOLD"
        )
        assert halted is False
        assert ref == pytest.approx(9700.0)


class TestEquityDrawdownRefFloor:
    """R3：equity 回撤基准支持 min_ref 下限（当日初始余额）。

    背景：ref 首次采样=当前 equity。若引擎当日中途重启/迟启动且已深亏
    （如 -8%），ref 被钉在低位 → 当日"再跌 3%"失效。min_ref 让基准
    至少不低于"当日初始余额"，重启不洗白已发生的亏损。
    """

    async def test_first_observation_with_min_ref_floor(self, redis_client):
        # 当日已亏：初始余额 10000（min_ref），当前 equity 9200（-8%）
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9200.0, max_drawdown_pct=0.03, symbol="GOLD", min_ref=10000.0
        )
        # 相对当日初始余额已超 3% → 应立即触发（不洗白）
        assert halted is True
        assert ref == pytest.approx(10000.0)

    async def test_min_ref_ignored_when_equity_above(self, redis_client):
        # equity 高于 min_ref → ref 用 equity（正常运行不受影响）
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10100.0, max_drawdown_pct=0.03, symbol="GOLD", min_ref=10000.0
        )
        assert halted is False
        assert ref == pytest.approx(10100.0)

    async def test_min_ref_does_not_affect_subsequent_peak_tracking(self, redis_client):
        # min_ref 只作用于首次采样；后续仍跟踪运行峰值
        await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=9200.0, max_drawdown_pct=0.03, symbol="GOLD", min_ref=10000.0
        )
        # 反弹到 10500（新高）→ ref 跟随；回撤 4% 触发
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10500.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is False
        assert ref == pytest.approx(10500.0)
        halted, ref = await CircuitBreaker.is_equity_drawdown_halted(
            redis_client, equity=10080.0, max_drawdown_pct=0.03, symbol="GOLD"
        )
        assert halted is True
        assert ref == pytest.approx(10500.0)


class TestBackfillToday:
    """启动回填：用 MT5 历史补齐漏记的当日已实现 P&L / 连亏计数。"""

    async def test_backfill_records_and_is_idempotent(self, redis_client):
        from unittest.mock import AsyncMock

        connector = AsyncMock()
        connector.get_history.return_value = {
            "success": True,
            "data": [
                {"ticket": 1001, "symbol": "GOLD_", "profit": -122.05},
                {"ticket": 1002, "symbol": "GOLD_", "profit": -90.9},
                {"ticket": 1003, "symbol": "BTCUSD", "profit": 50.0},  # 非本品种忽略
            ],
        }
        n = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n == 2
        cb = CircuitBreaker(redis_client, "GOLD")
        assert await cb.get_daily_pnl() == pytest.approx(-212.95)
        assert await cb.get_trade_count() == 2

        # 幂等：再次回填不重复计数
        n2 = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n2 == 2
        assert await cb.get_daily_pnl() == pytest.approx(-212.95)
        assert await cb.get_trade_count() == 2

    async def test_backfill_history_failure_returns_zero(self, redis_client):
        from unittest.mock import AsyncMock

        connector = AsyncMock()
        connector.get_history.return_value = {"success": False, "data": [], "error": "bridge down"}
        n = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n == 0

    async def test_backfill_counts_net_pnl_including_commission_swap(self, redis_client):
        """R2：回填记账必须含 commission/swap —— 否则日亏低估、风控偏松。

        profit=-90、commission=-3、swap=-2 → 净亏 -95（而非 -90）。
        """
        from unittest.mock import AsyncMock

        connector = AsyncMock()
        connector.get_history.return_value = {
            "success": True,
            "data": [
                {"ticket": 2001, "symbol": "GOLD_", "profit": -90.0, "commission": -3.0, "swap": -2.0},
            ],
        }
        n = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n == 1
        cb = CircuitBreaker(redis_client, "GOLD")
        assert await cb.get_daily_pnl() == pytest.approx(-95.0)

    async def test_backfill_missing_commission_swap_defaults_zero(self, redis_client):
        """旧桥响应无 commission/swap 字段 → 按 0 处理，不崩溃。"""
        from unittest.mock import AsyncMock

        connector = AsyncMock()
        connector.get_history.return_value = {
            "success": True,
            "data": [
                {"ticket": 2002, "symbol": "GOLD_", "profit": -90.0},
            ],
        }
        n = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n == 1
        cb = CircuitBreaker(redis_client, "GOLD")
        assert await cb.get_daily_pnl() == pytest.approx(-90.0)

    async def test_net_pnl_helper(self):
        """net_pnl 辅助函数：profit+commission+swap，缺字段按 0。"""
        assert CircuitBreaker.net_pnl({"profit": 100.0, "commission": -2.0, "swap": -1.5}) == pytest.approx(96.5)
        assert CircuitBreaker.net_pnl({"profit": 100.0}) == pytest.approx(100.0)
        assert CircuitBreaker.net_pnl({"profit": 0.0}) == 0.0

    async def test_backfill_aggregates_partial_close_deals(self, redis_client):
        """R4（F1 防御）：同一 position_id 分多次平仓（未来部分平仓）时，
        按 position 聚合全部退出 deal 的净 P&L —— 否则 ticket 幂等会跳过
        后续 deal，日亏低估。"""
        from unittest.mock import AsyncMock

        connector = AsyncMock()
        connector.get_history.return_value = {
            "success": True,
            "data": [
                {"ticket": 3001, "symbol": "GOLD_", "profit": -50.0, "commission": -1.0, "swap": -1.0},
                {"ticket": 3001, "symbol": "GOLD_", "profit": -30.0, "commission": -1.0, "swap": -1.0},
            ],
        }
        n = await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert n == 1  # 2 条 deal 同 position，聚合为 1 笔
        cb = CircuitBreaker(redis_client, "GOLD")
        # 净合计 = (-50-1-1) + (-30-1-1) = -84
        assert await cb.get_daily_pnl() == pytest.approx(-84.0)
        assert await cb.get_trade_count() == 1
        # 幂等：再次回填不重复
        await CircuitBreaker.backfill_today(connector, redis_client, "GOLD")
        assert await cb.get_daily_pnl() == pytest.approx(-84.0)


class TestSecondsUntilReset:
    def test_returns_positive(self):
        seconds = CircuitBreaker._seconds_until_reset("GOLD")
        assert seconds >= MIN_TTL_SECONDS

    def test_metal_reset_hour(self):
        # GOLD → metal asset class → reset at 22:00 UTC
        assert get_reset_hour("metal") == 22

    def test_crypto_reset_hour(self):
        assert get_reset_hour("crypto") == 0

    def test_unknown_symbol_defaults_to_zero(self):
        # Unknown symbol → defaults to hour 0
        seconds = CircuitBreaker._seconds_until_reset("UNKNOWN")
        assert seconds >= MIN_TTL_SECONDS
