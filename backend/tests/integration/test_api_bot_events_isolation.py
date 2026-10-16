"""
Integration tests for /api/bot/events account isolation.

bot 事件端点（GET /api/bot/events）是 dashboard 首页 EventFeed 的数据源，
必须与 /api/ai/activity 同口径按「当前活跃账号 + '0' 遗留桶」过滤 ——
切换账号后不得再列出旧账号的事件；缓存键必须带账号维度，否则 10s TTL
内切换账号会读到上一账号的缓存事件。
"""

from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.routes.bot import set_manager
from app.db.models import BotEvent, BotEventType, MT5Account
from app.db.session import get_db


def _make_test_app(db_session, redis_client=None):
    from fastapi import FastAPI

    from app.api.routes.bot import router

    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.state.redis = redis_client
    return app


@pytest_asyncio.fixture
async def client(db_session):
    """无 Redis 的客户端 —— 直连 DB，用于验证 DB 过滤本身。"""
    app = _make_test_app(db_session, redis_client=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_cached(db_session, redis_client):
    """带 fakeredis 的客户端 —— 用于验证缓存键的账号维度。"""
    app = _make_test_app(db_session, redis_client=redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_events(db_session):
    """账号 A(11111111，非活跃) / B(22222222，活跃) / '0' 遗留桶 / 无归属 各一条事件。"""
    db_session.add(MT5Account(login=11111111, password_encrypted=b"x", password_nonce=b"y"))
    db_session.add(MT5Account(login=22222222, password_encrypted=b"x", password_nonce=b"y", is_active=True))
    for acct, msg in [
        (None, "legacy no-account event"),
        ("11111111", "A-only event"),
        ("22222222", "B-only event"),
        ("0", "bucket-zero event"),
    ]:
        db_session.add(BotEvent(event_type=BotEventType.TRADE_OPENED, message=msg, account_login=acct))
    await db_session.commit()


class TestBotEventsAccountIsolation:
    async def test_events_isolated_by_active_account(self, client, db_session):
        """GET /api/bot/events 只返回活跃账号 B + '0' 桶 + 无归属事件。"""
        await _seed_events(db_session)

        resp = await client.get("/api/bot/events")
        assert resp.status_code == 200
        msgs = [e["message"] for e in resp.json()["events"]]

        assert "B-only event" in msgs, "活跃账号 B 的事件必须可见"
        assert "bucket-zero event" in msgs, "'0' 遗留桶事件必须可见"
        assert "legacy no-account event" in msgs, "无归属早期事件不得误杀"
        assert "A-only event" not in msgs, "非活跃账号 A 的事件必须被过滤"

    async def test_events_cached_key_carries_account(self, db_session, redis_client):
        """切换账号后不得命中上一个账号的缓存键（缓存键必须含账号维度）。

        同一 app 实例内先以账号 A 请求（写缓存），再切到账号 B 请求 ——
        若缓存键不含账号，第二次请求会直接命中 A 的缓存（10s TTL 内），
        本测试即失败。
        """
        from unittest.mock import MagicMock

        await _seed_events(db_session)
        app = _make_test_app(db_session, redis_client=redis_client)
        transport = ASGITransport(app=app)

        mgr = MagicMock()
        mgr.current_account_login = "11111111"
        set_manager(mgr)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                await c.get("/api/bot/events")
                keys_a = [k.decode() if isinstance(k, bytes) else k for k in await redis_client.keys("cache:bot_events:*")]
                assert any("11111111" in k for k in keys_a), f"首次请求缓存键缺账号维度：{keys_a}"

                # 切换到账号 B
                mgr.current_account_login = "22222222"
                await c.get("/api/bot/events")
                keys_b = [k.decode() if isinstance(k, bytes) else k for k in await redis_client.keys("cache:bot_events:*")]
        finally:
            set_manager(None)

        assert any("22222222" in k for k in keys_b), f"切换账号后缓存键未变：{keys_b}"
        assert len(keys_b) >= 2, f"两个账号必须产生两个独立缓存键：{keys_b}"

    async def test_post_event_stamps_current_account(self, db_session, redis_client):
        """POST /api/bot/events 必须给事件打上当前账号（否则无归属事件跨账号可见）。"""
        from unittest.mock import MagicMock

        mgr = MagicMock()
        mgr.current_account_login = "22222222"
        set_manager(mgr)
        try:
            app = _make_test_app(db_session, redis_client=None)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/api/bot/events", json={"event_type": "AI_ANALYSIS", "message": "agent note"})
            assert resp.status_code == 200
        finally:
            set_manager(None)

        from sqlalchemy import select

        row = (await db_session.execute(
            select(BotEvent).where(BotEvent.message.contains("agent note"))
        )).scalar_one()
        assert row.account_login == "22222222", "POST 事件必须绑定当前账号"

    async def test_post_event_without_manager_stays_null(self, db_session):
        """无 manager（引导期）时不臆造账号 —— 保持 NULL，不污染任何账号视图。"""
        set_manager(None)
        app = _make_test_app(db_session, redis_client=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/bot/events", json={"event_type": "AI_ANALYSIS", "message": "boot note"})
        assert resp.status_code == 200

        from sqlalchemy import select

        row = (await db_session.execute(
            select(BotEvent).where(BotEvent.message.contains("boot note"))
        )).scalar_one()
        assert row.account_login is None, "无 manager 时不得臆造账号归属"

    async def test_events_isolated_when_db_lookup_fails(self, client, db_session):
        """DB 账号解析异常时退化为 {'0'} —— 宁可少显示，不 fail-open 泄漏。"""
        await _seed_events(db_session)
        # 删掉 mt5_accounts 使解析回落（模拟异常/未迁移），只应看到 '0' 桶 + 无归属
        from sqlalchemy import delete

        await db_session.execute(delete(MT5Account))
        await db_session.commit()

        resp = await client.get("/api/bot/events")
        assert resp.status_code == 200
        msgs = [e["message"] for e in resp.json()["events"]]
        assert "bucket-zero event" in msgs
        assert "B-only event" not in msgs, "无活跃账号时不得显示任意账号事件"
        assert "A-only event" not in msgs


class TestEventTimestampFreshness:
    async def test_created_at_present(self, client, db_session):
        """事件时间戳正常序列化（回归保护）。"""
        db_session.add(
            BotEvent(event_type=BotEventType.STARTED, message="boot", account_login="0", created_at=datetime.utcnow())
        )
        await db_session.commit()

        resp = await client.get("/api/bot/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert events and events[0]["created_at"]
