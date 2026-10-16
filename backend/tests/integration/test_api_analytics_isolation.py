"""
Integration tests for Analytics API account isolation.

交易/盈亏聚合必须严格按「当前活跃账号 + '0' 遗留桶」过滤（与 /api/history 同口径）：
切换账号后，其它账号的 bot 单不得混入 performance / slippage 统计。
"""

from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import MT5Account, Trade
from app.db.session import get_db


def _make_test_app(db_session):
    from fastapi import FastAPI

    from app.api.routes.analytics import router

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
    各插一笔已平仓单，外加一笔 '0' 遗留桶单。"""
    db_session.add(MT5Account(login=11111111, password_encrypted=b"x", password_nonce=b"y"))
    db_session.add(MT5Account(login=22222222, password_encrypted=b"x", password_nonce=b"y", is_active=True))
    now = datetime.utcnow()
    db_session.add(Trade(
        ticket=9101, symbol="GOLD", type="BUY", lot=0.1,
        open_price=2000.0, close_price=1990.0, sl=1990.0, tp=2020.0,
        open_time=now, close_time=now, profit=-50.0,
        strategy_name="ema_crossover", account_login="11111111",
    ))
    db_session.add(Trade(
        ticket=9102, symbol="GOLD", type="BUY", lot=0.1,
        open_price=2000.0, close_price=2010.0, sl=1990.0, tp=2020.0,
        open_time=now, close_time=now, profit=100.0,
        strategy_name="ema_crossover", account_login="22222222",
    ))
    db_session.add(Trade(
        ticket=9103, symbol="GOLD", type="BUY", lot=0.1,
        open_price=2000.0, close_price=2010.0, sl=1990.0, tp=2020.0,
        open_time=now, close_time=now, profit=150.0,
        strategy_name="ema_crossover", account_login="0",
    ))
    await db_session.commit()


class TestAnalyticsAccountIsolation:
    async def test_performance_isolates_account(self, client, db_session):
        """performance 只聚合当前活跃账号 + '0' 桶；非活跃 A 账号不混入。"""
        await _seed_cross_account(db_session)
        resp = await client.get("/api/analytics/performance?days=30")
        assert resp.status_code == 200
        data = resp.json()
        # 100 (B) + 150 ('0') = 250；A 的 -50 不得计入
        assert data["total_profit"] == 250.0
        assert data["total_trades"] == 2

    async def test_performance_legacy_only_when_no_active(self, client, db_session):
        """无活跃账号时退化为仅 '0' 桶（fail-closed）。"""
        db_session.add(MT5Account(login=11111111, password_encrypted=b"x", password_nonce=b"y"))
        now = datetime.utcnow()
        db_session.add(Trade(
            ticket=9201, symbol="GOLD", type="BUY", lot=0.1,
            open_price=2000.0, close_price=2010.0, sl=1990.0, tp=2020.0,
            open_time=now, close_time=now, profit=50.0,
            strategy_name="ema_crossover", account_login="11111111",
        ))
        await db_session.commit()
        resp = await client.get("/api/analytics/performance?days=30")
        assert resp.status_code == 200
        assert resp.json()["total_trades"] == 0

    async def test_slippage_isolates_account(self, client, db_session):
        """slippage 只统计当前活跃账号 + '0' 桶。"""
        await _seed_cross_account(db_session)
        # slippage 查询要求 expected_price 非空，补上
        for ticket in (9101, 9102, 9103):
            row = (await db_session.execute(
                select(Trade).where(Trade.ticket == ticket)
            )).scalar_one()
            row.expected_price = 1999.5
        await db_session.commit()

        resp = await client.get("/api/analytics/slippage?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 2, "仅 B + '0' 两笔计入滑点"