"""
Integration tests for AI Activity API account isolation.

AI 活动日志（/api/ai/activity）必须按「当前活跃账号 + '0' 遗留桶」过滤
（与 /api/history、/api/analytics 同口径）：切换账号后，其它账号的
AI 交易与 BotEvent 不得混入活动时间线与 summary 计数。
"""

from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.models import BotEvent, BotEventType, MT5Account, Trade
from app.db.session import get_db


def _make_test_app(db_session):
    from fastapi import FastAPI

    from app.api.routes.activity import router

    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


@pytest_asyncio.fixture
async def client(db_session):
    app = _make_test_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_cross_account(db_session):
    """登记账号 A(11111111，非活跃) 与 B(22222222，活跃)，
    各插一笔带 AI sentiment 的 Trade + 一条 BotEvent，外加 '0' 遗留桶各一笔。"""
    db_session.add(MT5Account(login=11111111, password_encrypted=b"x", password_nonce=b"y"))
    db_session.add(MT5Account(login=22222222, password_encrypted=b"x", password_nonce=b"y", is_active=True))
    now = datetime.utcnow()
    # Trade × 3 账号（均带 ai_sentiment_label 才会进入活动时间线）
    for i, (ticket, acct, profit) in enumerate(
        [(9301, "11111111", -50.0), (9302, "22222222", 100.0), (9303, "0", 150.0)]
    ):
        db_session.add(Trade(
            ticket=ticket, symbol="GOLD", type="BUY", lot=0.1,
            open_price=2000.0, close_price=2010.0, sl=1990.0, tp=2020.0,
            open_time=now, close_time=now, profit=profit,
            strategy_name="ema_crossover", account_login=acct,
            ai_sentiment_label="bullish", ai_sentiment_score=0.8,
        ))
    # BotEvent × 3 账号 + 1 条无账号事件（account_login 可空）
    for acct, msg in [
        (None, "system boot"), ("11111111", "A order"), ("22222222", "B order"), ("0", "legacy order"),
    ]:
        db_session.add(BotEvent(event_type=BotEventType.TRADE_OPENED, message=msg, account_login=acct))
    await db_session.commit()


class TestActivityAccountIsolation:
    async def test_activity_isolates_trade(self, client, db_session):
        """主日志 trade 类目只显示当前活跃账号(B) + '0' 桶；非活跃 A 被过滤。"""
        await _seed_cross_account(db_session)

        resp = await client.get("/api/ai/activity?category=trade")
        assert resp.status_code == 200
        items = resp.json()["items"]
        # B(9302) + '0'(9303) 两笔 AI trade；A(9301) 不得出现。
        # 只数 source=ai_trade 的项（BotEvent 的 TRADE_OPENED 也是 trade 类别，需区分）
        trade_items = [t for t in items if t.get("source") == "ai_trade"]
        assert len(trade_items) == 2, f"期望 2 笔 AI trade，实际 {len(trade_items)}"
        assert not any("9301" in t["id"] for t in trade_items), "非活跃账号 A 的 trade 必须被过滤"

    async def test_activity_isolates_bot_events(self, client, db_session):
        """BotEvent 过滤：B + '0' + 无账号事件可见；A 账号事件被过滤。"""
        await _seed_cross_account(db_session)

        resp = await client.get("/api/ai/activity?category=trade")
        assert resp.status_code == 200
        items = resp.json()["items"]
        # 无账号 "system boot" 恒可见（早期事件无归属，不误杀）
        msgs = [t["message"] for t in items if t.get("source") == "bot_engine"]
        assert any("system boot" in m for m in msgs), "无账号事件必须保留"
        assert all("A order" not in m for m in msgs), "非活跃账号 A 的 BotEvent 必须被过滤"
        assert any("B order" in m for m in msgs), "活跃账号 B 的 BotEvent 必须可见"
        assert any("legacy order" in m for m in msgs), "'0' 遗留桶事件必须可见"

    async def test_activity_summary_isolates_account(self, client, db_session):
        """summary 的 ai_trades 计数只统计当前活跃账号 + '0' 桶。"""
        await _seed_cross_account(db_session)

        resp = await client.get("/api/ai/activity/summary")
        assert resp.status_code == 200
        data = resp.json()
        # B + '0' 两笔 AI trade；A 的不计入。
        assert data["ai_trades"] == 2, f"期望 2 笔 AI trade，实际 {data['ai_trades']}"