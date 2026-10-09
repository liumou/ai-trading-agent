"""M2 纪律门禁专项测试（评审 4 验收：边界/并发/故障模式）。

覆盖：
1. discipline_gate：休息日拦截、日次数上限、反手冷静期、冲动冷却、周/月熔断
2. 故障模式：Redis down → fail-closed（discipline_redis_down）
3. CircuitBreaker 周/月 PnL 记账 + get_period_pnl 聚合
4. guardrails 跨日 streak（盈利归零/亏损累加）
"""

from datetime import datetime, timedelta

import pytest
import fakeredis.aioredis

from app.config import settings


@pytest.fixture
def dredis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def enable_discipline(monkeypatch, dredis):
    monkeypatch.setattr(settings, "discipline_gate_enabled", True)
    monkeypatch.setattr(settings, "engine_discipline_enabled", True)
    monkeypatch.setattr(settings, "discipline_max_trades_per_day_manual", 3)
    monkeypatch.setattr(settings, "discipline_max_lots_per_day", 100.0)  # 防拆单上限放宽，避免干扰
    # 固定纪律时区本地时间为工作日（周三），避免 WEEKEND_CLOSE/REST_DAY 干扰
    from datetime import timezone
    from app.services import discipline as _d

    _wed = datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(_d, "discipline_local_now", lambda: _wed)


async def _enable_gate_in_redis(dredis):
    """门禁从 Redis 读 gate_enabled（Redis 优先于 settings）；写 cfg key 确保启用。"""
    await dredis.set("discipline:cfg:gate_enabled", "true")
    await dredis.set("discipline:cfg:engine_enabled", "true")


class TestDisciplineGate:
    @pytest.mark.asyncio
    async def test_rest_day_blocks(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)

        # 固定为上海周五
        from zoneinfo import ZoneInfo

        friday = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        monkeypatch.setattr(d, "discipline_local_now", lambda: friday)
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "REST_DAY"

    @pytest.mark.asyncio
    async def test_daily_trade_limit(self, dredis, enable_discipline, monkeypatch):
        import time
        from datetime import timezone

        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)

        # 固定纪律时钟（避免真实日期漂移）；本地时区固定周三避免 WEEKEND_CLOSE
        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        monkeypatch.setattr(d, "discipline_day_key", lambda now=None: "2026-10-09")
        monkeypatch.setattr(d, "discipline_week_key", lambda now=None: "2026-W41")
        monkeypatch.setattr(d, "is_rest_day", lambda now_local=None: False)
        monkeypatch.setattr(
            d, "discipline_local_now", lambda: datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        )
        # 预置 3 笔（flip 时间戳设为 31 分钟前，避免触发反手冷静期干扰次数断言）
        for _ in range(3):
            await dg.record_order_opened_discipline(
                dredis, account_login="1", symbol="GOLD", channel="manual", direction="BUY"
            )
        await dredis.set(f"discipline:flip:1:GOLD", f"BUY|{time.time() - 31 * 60}|1")
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "MAX_TRADES_DAY"
        assert "3/3" in res.reason

    @pytest.mark.asyncio
    async def test_flip_cooldown(self, dredis, enable_discipline, monkeypatch):
        import time
        from datetime import timezone

        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)

        monkeypatch.setattr(d, "is_rest_day", lambda now_local=None: False)
        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        monkeypatch.setattr(d, "discipline_day_key", lambda now=None: "2026-10-09")
        monkeypatch.setattr(d, "discipline_week_key", lambda now=None: "2026-W41")
        monkeypatch.setattr(
            d, "discipline_local_now", lambda: datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        )
        # 写入 BUY flip 时间戳 = now（30 分钟内反手 SELL 被拒）
        await dredis.set(f"discipline:flip:1:GOLD", f"BUY|{time.time()}|1")
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "FLIP_COOLDOWN"
        # 31 分钟前的 flip → 放行
        await dredis.set(f"discipline:flip:1:GOLD", f"BUY|{time.time() - 31 * 60}|1")
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert res.ok

    @pytest.mark.asyncio
    async def test_redis_down_fail_closed(self, enable_discipline, monkeypatch):
        import app.services.discipline as d
        import app.services.discipline_gate as dg

        monkeypatch.setattr(d, "is_rest_day", lambda now_local=None: False)
        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        monkeypatch.setattr(d, "discipline_day_key", lambda now=None: "2026-10-09")
        monkeypatch.setattr(d, "discipline_week_key", lambda now=None: "2026-W41")

        class _BrokenRedis:
            async def get(self, *a, **k):
                raise ConnectionError("redis down")

            async def keys(self, *a, **k):
                raise ConnectionError("redis down")

            async def mget(self, *a, **k):
                raise ConnectionError("redis down")

        res = await dg.check_discipline_gate(
            _BrokenRedis(), account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "discipline_redis_down"


class TestCircuitBreakerPeriod:
    @pytest.mark.asyncio
    async def test_week_month_pnl_recording(self, dredis):
        from app.risk.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(dredis, symbol="GOLD", account_login="1")
        await cb.record_trade_result(-50.0, ticket=1001)
        await cb.record_trade_result(30.0, ticket=1002)
        week_pnl = await CircuitBreaker.get_period_pnl(dredis, "week", account_login="1")
        month_pnl = await CircuitBreaker.get_period_pnl(dredis, "month", account_login="1")
        assert week_pnl == -20.0
        assert month_pnl == -20.0

    @pytest.mark.asyncio
    async def test_period_halt_until(self, dredis, enable_discipline, monkeypatch):
        from app.risk.circuit_breaker import CircuitBreaker
        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)
        from datetime import timezone

        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        monkeypatch.setattr(d, "discipline_day_key", lambda now=None: "2026-10-09")
        monkeypatch.setattr(d, "discipline_week_key", lambda now=None: "2026-W41")
        monkeypatch.setattr(d, "discipline_month_key", lambda now=None: "2026-10")
        monkeypatch.setattr(d, "discipline_until_week", lambda now=None: datetime(2026, 10, 12, 22, 0))
        monkeypatch.setattr(d, "discipline_until_month", lambda now=None: datetime(2026, 11, 1, 22, 0))
        monkeypatch.setattr(d, "is_rest_day", lambda now_local=None: False)
        monkeypatch.setattr(
            d, "discipline_local_now", lambda: datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        )

        await CircuitBreaker.set_period_halt(dredis, "1", "week")
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "WEEK_HALT"


class TestImpulseCooldown:
    @pytest.mark.asyncio
    async def test_ladder_24h_then_72h(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)
        from datetime import timedelta, timezone

        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        # 第 1 次拦截：无冷却（count=1 不触发）
        h1 = await dg.trigger_impulse_cooldown(dredis, account_login="1")
        assert h1 == 0
        # 第 2 次：24h 冷却
        h2 = await dg.trigger_impulse_cooldown(dredis, account_login="1")
        assert h2 == 24
        # 第 3 次：72h
        h3 = await dg.trigger_impulse_cooldown(dredis, account_login="1")
        assert h3 == 72
        # 第 4 次：本周禁（复用 week halt）+ 72h 冷却
        h4 = await dg.trigger_impulse_cooldown(dredis, account_login="1")
        assert h4 == 72

    @pytest.mark.asyncio
    async def test_cooldown_cross_day_orthogonal(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline as d
        import app.services.discipline_gate as dg
        await _enable_gate_in_redis(dredis)

        # 冷却绝对时间戳与日界正交：23:50 触发 24h（需 count>=2）→ 次日 00:10 仍冷却
        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 23, 50, 0))
        await dg.trigger_impulse_cooldown(dredis, account_login="1")  # count=1 不触发
        await dg.trigger_impulse_cooldown(dredis, account_login="1")  # count=2 → 24h
        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 11, 0, 10, 0))
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual"
        )
        assert not res.ok and res.code == "IMPULSE_COOLDOWN"


class TestAntiSplit:
    @pytest.mark.asyncio
    async def test_anti_split_lots_limit(self, dredis, enable_discipline, monkeypatch):
        """防拆单（M5/P3）：日累计手数超限 → 拒。"""
        import time
        from datetime import timezone

        import app.services.discipline as d
        import app.services.discipline_gate as dg

        monkeypatch.setattr(d, "discipline_now", lambda: datetime(2026, 10, 10, 10, 0, 0))
        monkeypatch.setattr(d, "discipline_day_key", lambda now=None: "2026-10-09")
        monkeypatch.setattr(d, "discipline_week_key", lambda now=None: "2026-W41")
        monkeypatch.setattr(d, "is_rest_day", lambda now_local=None: False)
        monkeypatch.setattr(
            d, "discipline_local_now", lambda: datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        )
        await _enable_gate_in_redis(dredis)
        monkeypatch.setattr(settings, "discipline_max_lots_per_day", 1.0)
        # 先开 0.6 手 → 累计 0.6 < 1.0 放行；flip key 设为 31 分钟前避免反手冷静期干扰
        await dg.record_order_opened_discipline(
            dredis, account_login="1", symbol="GOLD", channel="manual", direction="BUY", lot=0.6
        )
        await dredis.set(f"discipline:flip:1:GOLD", f"BUY|{time.time() - 31 * 60}|1")
        # 再尝试 0.5 手 → 0.6+0.5 > 1.0 拒
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"equity": 10000}, lot=0.5,
        )
        assert not res.ok and res.code == "MAX_LOTS_DAY"


class TestGuardrailsStreak:
    @pytest.mark.asyncio
    async def test_streak_cross_day_sequence(self, dredis):
        from mcp_server.guardrails import TradingGuardrails

        g = TradingGuardrails(dredis)
        # 跨日序列：亏、亏、盈、亏 → streak=1（盈利中断）
        await g.record_trade_closed(False, ticket=1)
        await g.record_trade_closed(False, ticket=2)
        await g.record_trade_closed(True, ticket=3)
        await g.record_trade_closed(False, ticket=4)
        streak = await g._get_consecutive_losses()
        assert streak == 1

    @pytest.mark.asyncio
    async def test_streak_accumulates_cross_day(self, dredis):
        from mcp_server.guardrails import TradingGuardrails

        g = TradingGuardrails(dredis)
        for i in range(5):
            await g.record_trade_closed(False, ticket=100 + i)
        streak = await g._get_consecutive_losses()
        assert streak == 5
