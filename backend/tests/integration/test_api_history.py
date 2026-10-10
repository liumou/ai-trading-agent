"""
Integration tests for History API routes.
"""

from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.models import Trade
from app.db.session import get_db


def _make_test_app(db_session):
    from fastapi import FastAPI

    from app.api.routes.history import router

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


@pytest_asyncio.fixture
async def seeded_db(db_session):
    """Insert sample trades for testing."""
    trades = [
        Trade(
            ticket=1001,
            symbol="GOLD",
            type="BUY",
            lot=0.1,
            open_price=2000.0,
            close_price=2010.0,
            sl=1990.0,
            tp=2020.0,
            open_time=datetime(2025, 1, 1, 10, 0),
            close_time=datetime(2025, 1, 1, 12, 0),
            profit=100.0,
            strategy_name="ema_crossover",
        ),
        Trade(
            ticket=1002,
            symbol="GOLD",
            type="SELL",
            lot=0.1,
            open_price=2010.0,
            close_price=2020.0,
            sl=2020.0,
            tp=2000.0,
            open_time=datetime(2025, 1, 2, 10, 0),
            close_time=datetime(2025, 1, 2, 12, 0),
            profit=-100.0,
            strategy_name="ema_crossover",
        ),
    ]
    for t in trades:
        db_session.add(t)
    await db_session.commit()
    return trades


class TestHistoryRoutes:
    async def test_get_trades(self, client, seeded_db):
        resp = await client.get("/api/history/trades?days=365")
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data

    async def test_get_trades_account_login(self, client, db_session):
        """bot 单必须透传 account_login（前端复盘触发按 (ticket, account_login) 归属）。"""
        from datetime import datetime as _dt

        db_session.add(Trade(
            ticket=7771, symbol="GOLD", type="BUY", lot=0.1,
            open_price=2000.0, close_price=2010.0, sl=1990.0, tp=2020.0,
            open_time=_dt.now(), close_time=_dt.now(), profit=100.0,
            strategy_name="ema_crossover", account_login="12345678",
        ))
        await db_session.commit()

        resp = await client.get("/api/history/trades?days=30")
        assert resp.status_code == 200
        bot_rows = [r for r in resp.json()["trades"] if r["source"] == "bot"]
        assert bot_rows, "bot 行应存在"
        assert all(r.get("account_login") for r in bot_rows)
        assert any(r.get("account_login") == "12345678" for r in bot_rows)

    async def test_get_trades_empty(self, client):
        resp = await client.get("/api/history/trades?days=1")
        assert resp.status_code == 200

    async def test_get_daily_pnl(self, client, seeded_db):
        resp = await client.get("/api/history/daily-pnl?days=365")
        assert resp.status_code == 200

    async def test_get_daily_pnl_net_profit_priority(self, client, monkeypatch):
        """daily-pnl 净额优先（net_profit 含 commission/swap），与 history
        合并行口径一致；旧 Bridge 无 net_profit 时回落毛额 profit。"""
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock

        today_close = datetime.now(UTC).replace(microsecond=0).isoformat()

        class _FakeEngine:
            connector = AsyncMock()
            connector.get_history.return_value = {
                "success": True,
                "data": [{
                    "ticket": 7777,
                    "symbol": "GOLD",
                    "type": "BUY",
                    "lot": 0.1,
                    "open_price": 2000.0,
                    "open_time": today_close,
                    "sl": 0.0,
                    "tp": 0.0,
                    "close_price": 2020.0,
                    "close_time": today_close,
                    # daily-pnl 按 close_time>=今天 00:00 UTC 过滤，close 必须今天
                    "time": today_close,
                    "price": 2020.0,
                    "profit": 20.0,
                    "net_profit": 19.85,  # 净额优先
                }],
            }

        class _FakeManager:
            engines = {"GOLD": _FakeEngine()}

        monkeypatch.setattr(
            "app.bot.manager.get_global_manager", lambda: _FakeManager()
        )
        monkeypatch.setattr(
            "app.api.routes.bot._get_engine", lambda symbol=None: _FakeEngine()
        )

        resp = await client.get("/api/history/daily-pnl?days=365")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_pnl"] == 19.85   # 净额而非毛额 20.0
        assert data["trade_count"] == 1

    async def test_get_performance(self, client, seeded_db):
        resp = await client.get("/api/history/performance?days=365")
        assert resp.status_code == 200

    async def test_get_trades_uses_bridge_new_fields(self, client, monkeypatch):
        """MT5 合并通道字段契约：开仓价≠平仓价、SL/TP 真实值、开仓/平仓时间分离。

        Bridge 新版 /history 返回 open_price/open_time/sl/tp/close_price/
        close_time/net_profit（订单-成交配对）。合并行不能再用「open_price=price」
        的旧兜底 —— 否则「开仓价=平仓价」bug 复现。
        """
        from unittest.mock import AsyncMock

        class _FakeEngine:
            connector = AsyncMock()
            connector.get_history.return_value = {
                "success": True,
                "data": [{
                    "ticket": 9999,
                    "symbol": "GOLD",
                    "type": "BUY",
                    "lot": 0.1,
                    "open_price": 2000.0,
                    "open_time": "2026-10-01T10:00:00+00:00",
                    "sl": 1990.0,
                    "tp": 2030.0,
                    "close_price": 2020.0,
                    "close_time": "2026-10-01T12:00:00+00:00",
                    "profit": 20.0,
                    "net_profit": 19.85,
                    "time": "2026-10-01T12:00:00+00:00",   # 兼容别名
                    "price": 2020.0,                       # 兼容别名
                }],
            }

        class _FakeManager:
            engines = {"GOLD": _FakeEngine()}

        # history.py 函数内 import get_global_manager，故 patch 源头模块
        monkeypatch.setattr(
            "app.bot.manager.get_global_manager", lambda: _FakeManager()
        )
        monkeypatch.setattr(
            "app.api.routes.bot._get_engine", lambda symbol=None: _FakeEngine()
        )

        resp = await client.get("/api/history/trades?days=365")
        assert resp.status_code == 200
        rows = resp.json()["trades"]
        mt5_rows = [r for r in rows if r["source"] == "mt5"]
        assert mt5_rows, "MT5 合并行应存在"
        r = mt5_rows[0]
        assert r["open_price"] == 2000.0      # 开仓价来自订单，不是平仓价
        assert r["close_price"] == 2020.0
        assert r["open_price"] != r["close_price"]  # 核心：不再相同
        assert r["sl"] == 1990.0
        assert r["tp"] == 2030.0
        assert r["open_time"] != r["close_time"]    # 开仓/平仓时间分离
        assert r["profit"] == 19.85                 # 净额优先
        assert r["account_login"] == "0"            # 手动单归属兜底
