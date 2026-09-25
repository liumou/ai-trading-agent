"""PriceAlertService 巡检引擎单元测试。

覆盖核心判定逻辑：严格比较、持续时长状态机、达上限停用、数据不可用重置、原子计数。
使用 fakeredis + SQLite 内存库 + mock FeishuNotifier（不真实发送）。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceAlert
from app.services.price_alert_service import (
    PRICE_CACHE_PREFIX,
    PriceAlertService,
    _trigger_ttl,
)


class FakeFeishu:
    """Mock FeishuNotifier：记录发送调用，不真实发请求。

    `fail_next` 为 True 时下一次发送返回 False，用于验证发送失败的回滚路径
    （原实现恒返回 True，导致该路径零覆盖——发送失败曾静默耗尽通知名额）。
    """

    def __init__(self, enabled: bool = True, fail_next: bool = False):
        self.enabled = enabled
        self.fail_next = fail_next
        self.sent: list[tuple[int, float]] = []  # (alert_id, price)
        self.attempts = 0

    async def send_price_alert_card(self, alert, current_price: float) -> bool:
        self.attempts += 1
        if self.fail_next:
            self.fail_next = False
            return False
        self.sent.append((alert.id, current_price))
        return True


@pytest_asyncio.fixture
async def service(redis_client, db_engine, db_session):
    """构造 PriceAlertService，注入 fake redis + mock notifier + SQLite session_factory。

    SQLite 内存库按连接隔离，因此 fixture 同时把 factory 挂到 service 上，
    所有测试内 DB 读写必须走 service 的 factory（同一连接空间）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    notifier = FakeFeishu()
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    svc = PriceAlertService(notifier, redis_client, session_factory=factory)
    svc._test_factory = factory
    return svc


async def _create_alert(service, **overrides):
    """用 service 的 session factory 插入一条 PriceAlert（与 service 同连接空间）。"""
    defaults = dict(
        symbol="GOLD",
        condition="above",
        trigger_price=3350.0,
        duration_seconds=60,
        max_notifications=1,
        sent_count=0,
        is_active=True,
    )
    defaults.update(overrides)
    alert = PriceAlert(**defaults)
    async with service._test_factory() as session:
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return alert


async def _seed_price(redis_client, symbol: str, bid: float):
    """向 Redis price cache 写入 tick（模拟 scheduler._fetch_tick）。"""
    import json

    await redis_client.setex(
        f"{PRICE_CACHE_PREFIX}{symbol}", 10, json.dumps({"bid": bid, "ask": bid + 1, "symbol": symbol})
    )


# ─── 严格比较 ────────────────────────────────────────────────────────────────


class TestIsSatisfied:
    async def test_above_strict_greater(self):
        assert PriceAlertService._is_satisfied("above", 3351.0, 3350.0) is True
        assert PriceAlertService._is_satisfied("above", 3349.0, 3350.0) is False

    async def test_below_strict_less(self):
        assert PriceAlertService._is_satisfied("below", 3349.0, 3350.0) is True
        assert PriceAlertService._is_satisfied("below", 3351.0, 3350.0) is False

    async def test_equal_is_not_satisfied(self):
        """价格恰好等于阈值视为不满足（严格比较，贴合用户"大于/小于"语义）。"""
        assert PriceAlertService._is_satisfied("above", 3350.0, 3350.0) is False
        assert PriceAlertService._is_satisfied("below", 3350.0, 3350.0) is False

    async def test_invalid_condition(self):
        assert PriceAlertService._is_satisfied("invalid", 100, 50) is False


# ─── 持续时长状态机 ──────────────────────────────────────────────────────────


class TestDurationStateMachine:
    async def test_trigger_after_duration(self, service, redis_client):
        """价格持续满足超过 duration_seconds 才触发。"""
        alert = await _create_alert(service, duration_seconds=2, max_notifications=1)
        await _seed_price(redis_client, "GOLD", 3351.0)

        # 第一轮：开始计时（未到 2 秒）
        await service.check_all()
        assert len(service.feishu_notifier.sent) == 0

        # 第二轮：模拟时间前进，达到 2 秒 → 触发
        # 为便于测试，直接设置 first_trigger 为 2 秒前
        import time

        await redis_client.setex(
            f"price_alert:first_trigger:{alert.id}", 300, str(int(time.time()) - 3)
        )
        await service.check_all()
        assert len(service.feishu_notifier.sent) == 1

    async def test_price_drop_resets_timer(self, service, redis_client):
        """价格中途回落 → 重置计时，需重新连续满足。"""
        alert = await _create_alert(service, duration_seconds=2, max_notifications=1)
        await _seed_price(redis_client, "GOLD", 3351.0)
        await service.check_all()  # 第一轮开始计时

        # 价格回落 → 第二轮重置
        await _seed_price(redis_client, "GOLD", 3349.0)
        await service.check_all()
        # first_trigger 应被清除
        first = await redis_client.get(f"price_alert:first_trigger:{alert.id}")
        assert first is None

        # 价格再回升，重新计时（未到 2 秒不触发）
        await _seed_price(redis_client, "GOLD", 3351.0)
        await service.check_all()
        assert len(service.feishu_notifier.sent) == 0

    async def test_no_tick_resets_timer(self, service, redis_client):
        """数据不可用（无 tick）→ 重置计时，不清已发送计数。"""
        alert = await _create_alert(service, duration_seconds=2, max_notifications=5, sent_count=2)
        # 无 price cache → tick None
        await service.check_all()
        first = await redis_client.get(f"price_alert:first_trigger:{alert.id}")
        assert first is None
        # sent_count 未被清除（仍为 2）
        async with service._test_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.sent_count == 2


# ─── 达上限停用 + 原子计数 ───────────────────────────────────────────────────


class TestSaturation:
    async def test_reach_max_deactivates(self, service, redis_client):
        """达到 max_notifications 后自动停用。"""
        alert = await _create_alert(service, duration_seconds=0, max_notifications=2)
        await _seed_price(redis_client, "GOLD", 3351.0)

        # 直接触发两次
        await service._dispatch_send(alert, 3351.0)
        await service._dispatch_send(alert, 3351.0)

        async with service._test_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.sent_count == 2
            assert refreshed.is_active is False
        assert len(service.feishu_notifier.sent) == 2

    async def test_over_limit_not_sent(self, service, redis_client):
        """sent_count 已等于上限时不再发送（原子计数 WHERE sent_count < max）。"""
        alert = await _create_alert(service, max_notifications=1, sent_count=1)
        await service._dispatch_send(alert, 3351.0)
        assert len(service.feishu_notifier.sent) == 0

    async def test_send_failure_rolls_back_quota(self, service, redis_client):
        """发送失败必须归还名额，规则保持活跃，下一轮可重试。

        回归测试：原实现先落库 sent_count+1 再发送，失败不回滚。默认
        max_notifications=1 时一次网络抖动即永久耗尽名额，且规则仍显示活跃。
        """
        alert = await _create_alert(service, max_notifications=1)
        service.feishu_notifier.fail_next = True
        await service._dispatch_send(alert, 3351.0)

        async with service._test_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.sent_count == 0, "发送失败不得消耗通知名额"
            assert refreshed.is_active is True, "发送失败不得停用规则"
        assert len(service.feishu_notifier.sent) == 0

    async def test_send_failure_then_retry_succeeds(self, service, redis_client):
        """首次发送失败后，名额已归还，下一轮能正常发送并生效。"""
        alert = await _create_alert(service, max_notifications=1)
        service.feishu_notifier.fail_next = True
        await service._dispatch_send(alert, 3351.0)
        assert len(service.feishu_notifier.sent) == 0

        await service._dispatch_send(alert, 3352.0)
        assert len(service.feishu_notifier.sent) == 1

        async with service._test_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.sent_count == 1
            assert refreshed.is_active is False, "成功后达到上限应停用"

    async def test_over_limit_does_not_deactivate_as_side_effect(
        self, service, redis_client
    ):
        """名额已用尽但未停用的规则，_dispatch_send 不再额外发送。"""
        alert = await _create_alert(service, max_notifications=1, sent_count=1)
        await service._dispatch_send(alert, 3351.0)
        assert len(service.feishu_notifier.sent) == 0

        async with service._test_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.sent_count == 1, "名额用尽不得继续加计"


# ─── 基础设施故障隔离 ────────────────────────────────────────────────────────


class TestInfraFailureIsolation:
    async def test_claim_error_does_not_deactivate(self, service, redis_client):
        """占位写库失败（基础设施故障）不得停用规则，也不得占用名额。

        回归测试：原实现把 DB 异常与"名额用尽"合并成同一个 False 返回值，
        再据此调用 _deactivate_if_saturated —— 等于把基础设施故障伪装成
        "已达上限"这一业务结论。
        """
        alert = await _create_alert(service, max_notifications=1)

        # 让占位阶段抛错（模拟 DB 连接故障）。工厂本身是同步的，
        # 其返回值才作为异步上下文管理器，故这里直接同步抛错。
        original_factory = service._session_factory
        calls = 0

        def flaky_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("db connection lost")
            return original_factory()

        service._session_factory = flaky_factory
        try:
            await service._dispatch_send(alert, 3351.0)
        finally:
            service._session_factory = original_factory

        async with original_factory() as session:
            refreshed = await session.get(PriceAlert, alert.id)
            assert refreshed.is_active is True, "基础设施故障不得停用规则"
            assert refreshed.sent_count == 0, "占位失败不得留下残留计数"
        assert service.feishu_notifier.attempts == 0, "占位失败不应尝试发送"

    async def test_disabled_notifier_warns_without_crashing(self, redis_client, db_engine):
        """飞书未配置时 check_all 静默短路（原有行为），但不得抛异常。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        notifier = FakeFeishu(enabled=False)
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        svc = PriceAlertService(notifier, redis_client, session_factory=factory)

        await svc.check_all()  # 不抛异常
        assert svc.feishu_notifier.attempts == 0

    async def test_disabled_warning_only_once(self, redis_client, db_engine):
        """未配置的告警只打一次，避免每 2 秒巡检刷屏。

        loguru 默认不接标准 logging，caplog 捕不到，故直接挂临时 sink。
        """
        import loguru
        from sqlalchemy.ext.asyncio import async_sessionmaker

        records: list[str] = []
        sink_id = loguru.logger.add(lambda msg: records.append(str(msg)), level="WARNING")
        try:
            notifier = FakeFeishu(enabled=False)
            factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
            svc = PriceAlertService(notifier, redis_client, session_factory=factory)

            await svc.check_all()
            await svc.check_all()
        finally:
            loguru.logger.remove(sink_id)

        warnings = [r for r in records if "FEISHU_WEBHOOK_URL" in r]
        assert len(warnings) == 1


# ─── 工具函数 ────────────────────────────────────────────────────────────────


class TestTriggerTtl:
    async def test_ttl_floor(self):
        """TTL 至少 300 秒。"""
        assert _trigger_ttl(10) == 300
        assert _trigger_ttl(60) == 300

    async def test_ttl_scales(self):
        """TTL 随 duration 增长。"""
        assert _trigger_ttl(300) == 600
        assert _trigger_ttl(1000) == 2000