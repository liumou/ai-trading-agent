"""Review 修复专项测试（2026-10-10 code review 后补充）。

覆盖本轮修复的关键点（评审 CRITICAL/HIGH）：
1. 周/月 PnL 账号隔离：不同账号 key 前缀不串扰（circuit:acc:{login}:）
2. 日熔断 rest-of-period 主动触发：account_daily_pnl ≤ -limit×balance → DAILY_HALT
3. 连亏周停：loss_streak ≥ 阈值 → WEEK_HALT
4. 反手硬拒：当日第 2 次反手 FLIP_REJECT；跨外汇日 flip_today 归零
5. stats 端点不再 next(get_db())（路由层 200，而非必然 500）
6. backfill 白名单：只转换 bridge 来源行，引擎 UTC 行不动
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import fakeredis.aioredis

from app.config import settings


@pytest.fixture
def dredis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def enable_discipline(monkeypatch):
    """固定纪律时区为周三工作日 + 启用门禁 + 放宽防拆单。"""
    monkeypatch.setattr(settings, "discipline_gate_enabled", True)
    monkeypatch.setattr(settings, "engine_discipline_enabled", True)
    monkeypatch.setattr(settings, "discipline_max_trades_per_day_manual", 100)
    monkeypatch.setattr(settings, "discipline_max_lots_per_day", 1000.0)
    from datetime import timezone
    from app.services import discipline as _d

    _wed = datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(_d, "discipline_local_now", lambda: _wed)


def _pin_discipline_clock(monkeypatch):
    """固定纪律时钟（日=2026-10-07，周=2026-W41，上海周三）。

    既要 patch app.services.discipline（record/until 用，函数内延迟 import），
    也要 patch discipline_gate / circuit_breaker 模块顶部的绑定（gate 内直接
    引用 discipline_now() 等 —— 模块级 import 绑定，不改则读真实时间）。
    """
    import app.services.discipline as d
    import app.services.discipline_gate as dg

    _now = lambda: datetime(2026, 10, 8, 10, 0, 0)
    _day = lambda now=None: "2026-10-07"
    _week = lambda now=None: "2026-W41"
    _month = lambda now=None: "2026-10"
    _until_day = lambda now=None: datetime(2026, 10, 8, 22, 0)
    _until_week = lambda now=None: datetime(2026, 10, 12, 22, 0)
    _until_month = lambda now=None: datetime(2026, 11, 1, 22, 0)
    _rest = lambda now_local=None: False
    # app.services.discipline 模块属性
    for obj, val in [
        ("discipline_now", _now), ("discipline_day_key", _day),
        ("discipline_week_key", _week), ("discipline_month_key", _month),
        ("discipline_until_day", _until_day), ("discipline_until_week", _until_week),
        ("discipline_until_month", _until_month), ("is_rest_day", _rest),
    ]:
        monkeypatch.setattr(d, obj, val)
    # discipline_gate 模块顶部绑定（模块级 import，需显式覆盖）
    for obj, val in [
        ("discipline_now", _now), ("discipline_day_key", _day),
        ("discipline_week_key", _week), ("discipline_month_key", _month),
        ("is_rest_day", _rest),
    ]:
        monkeypatch.setattr(dg, obj, val)


class TestPeriodPnlAccountIsolation:
    """周/月 PnL key 账号隔离（评审 CRITICAL：key 无账号维度 → 跨账号误熔断）。"""

    @pytest.mark.asyncio
    async def test_period_pnl_isolated_per_account(self, dredis):
        from app.risk.circuit_breaker import CircuitBreaker

        # 账号 A 亏损 -50，账号 B 盈利 +30
        cb_a = CircuitBreaker(dredis, symbol="GOLD", account_login="1001")
        cb_b = CircuitBreaker(dredis, symbol="GOLD", account_login="1002")
        await cb_a.record_trade_result(-50.0, ticket=1)
        await cb_b.record_trade_result(30.0, ticket=2)

        # 各自读取只看到自己的 PnL（不跨账号求和）
        assert await CircuitBreaker.get_period_pnl(dredis, "week", account_login="1001") == -50.0
        assert await CircuitBreaker.get_period_pnl(dredis, "week", account_login="1002") == 30.0
        # 不带账号（旧行为）读到空 → 0（新 key 有 acc: 前缀，旧 pattern 读不到）
        assert await CircuitBreaker.get_period_pnl(dredis, "week") == 0.0


class TestDailyHaltActiveTrigger:
    """日熔断 rest-of-period 主动触发（评审 CRITICAL：此前读无人写入的 day key）。"""

    @pytest.mark.asyncio
    async def test_daily_halt_triggered_by_account_daily_pnl(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline_gate as dg

        _pin_discipline_clock(monkeypatch)
        monkeypatch.setattr(settings, "max_daily_loss", 0.03)

        # 余额 10000，账户级日亏 -400（-4% > 3%）→ 触发 DAILY_HALT
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"balance": 10000},
            account_daily_pnl=-400.0,
        )
        assert not res.ok and res.code == "DAILY_HALT"
        # 标记已写入：下次无 account_daily_pnl 参数也被拦（rest-of-period）
        res2 = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"balance": 10000},
        )
        assert not res2.ok and res2.code == "DAILY_HALT"

    @pytest.mark.asyncio
    async def test_daily_halt_not_triggered_under_limit(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline_gate as dg

        _pin_discipline_clock(monkeypatch)
        monkeypatch.setattr(settings, "max_daily_loss", 0.03)
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"balance": 10000},
            account_daily_pnl=-200.0,  # -2% < 3%
        )
        assert res.ok


class TestConsecutiveLossWeekHalt:
    """连亏周停（评审 HIGH：配置存在但 gate 从不读 loss_streak）。"""

    @pytest.mark.asyncio
    async def test_streak_above_threshold_triggers_week_halt(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline_gate as dg
        from mcp_server.guardrails import TradingGuardrails

        _pin_discipline_clock(monkeypatch)
        monkeypatch.setattr(settings, "discipline_consecutive_loss_week_halt", 5)

        # 连亏 5 笔（跨日序列）
        gr = TradingGuardrails(dredis)
        for i in range(5):
            await gr.record_trade_closed(is_win=False, ticket=100 + i, account_login="1")

        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"balance": 10000},
        )
        assert not res.ok and res.code == "WEEK_HALT"

    @pytest.mark.asyncio
    async def test_streak_below_threshold_allowed(self, dredis, enable_discipline, monkeypatch):
        import app.services.discipline_gate as dg
        from mcp_server.guardrails import TradingGuardrails

        _pin_discipline_clock(monkeypatch)
        monkeypatch.setattr(settings, "discipline_consecutive_loss_week_halt", 5)

        gr = TradingGuardrails(dredis)
        for i in range(3):
            await gr.record_trade_closed(is_win=False, ticket=200 + i, account_login="1")

        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual",
            account={"balance": 10000},
        )
        assert res.ok


class TestFlipRejectSecondSwitch:
    """当日第 2 次反手硬拒（评审 HIGH：只做时间冷却、不消费 flip_count）。"""

    @pytest.mark.asyncio
    async def test_second_flip_today_rejected(self, dredis, enable_discipline, monkeypatch):
        import time

        import app.services.discipline_gate as dg

        _pin_discipline_clock(monkeypatch)
        # 模拟：已 BUY 后反手 SELL（flip key 记录 last_dir=SELL，flip_today=1，
        # 时间 31 分钟前避开时间冷却）。本次再 BUY = 第 2 次反手 → 硬拒。
        await dredis.set(
            f"discipline:flip:1:GOLD", f"SELL|{time.time() - 31 * 60}|1|1|2026-10-07"
        )
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual", direction="BUY"
        )
        assert not res.ok and res.code == "FLIP_REJECT"

    @pytest.mark.asyncio
    async def test_flip_today_resets_across_day(self, dredis, enable_discipline, monkeypatch):
        import time

        import app.services.discipline_gate as dg

        _pin_discipline_clock(monkeypatch)
        # flip key 记的是昨日（2026-10-06），flip_today=1 → 跨日视为 0，放行
        await dredis.set(f"discipline:flip:1:GOLD", f"BUY|{time.time() - 31 * 60}|1|1|2026-10-06")
        res = await dg.check_discipline_gate(
            dredis, account_login="1", symbol="GOLD", channel="manual", direction="SELL"
        )
        assert res.ok


class TestStatsEndpoint:
    """stats 端点修复：不再 next(get_db())（评审 CRITICAL：必然 500）。"""

    @pytest.mark.asyncio
    async def test_discipline_stats_route_calls_service(self, db_session):
        """直接调用 get_discipline_stats 不抛 TypeError（修复核心：Depends(get_db)）。"""
        from app.services.discipline_stats import get_discipline_stats

        out = await get_discipline_stats(db_session, account_login="1", days=30)
        assert out["account_login"] == "1"
        assert "opens" in out and "discipline_score" in out


class TestBackfillWhitelist:
    """backfill 只转换 bridge 来源行（评审 CRITICAL：盲目换算破坏正确 UTC 行）。"""

    def test_bridge_source_only(self):
        import types

        from scripts.backfill_trade_timezone import _is_bridge_source

        row = types.SimpleNamespace(strategy_name="adopted_from_mt5")
        assert _is_bridge_source(row) is True

        row2 = types.SimpleNamespace(strategy_name="orphan")
        assert _is_bridge_source(row2) is True

        # 引擎策略单（UTC 正确）不动
        row3 = types.SimpleNamespace(strategy_name="EMA")
        assert _is_bridge_source(row3) is False
        row4 = types.SimpleNamespace(strategy_name="partial_tp_from_12345")
        assert _is_bridge_source(row4) is False
        # 无 strategy_name 不动
        row5 = types.SimpleNamespace(strategy_name=None)
        assert _is_bridge_source(row5) is False
