"""GET /api/market-data/tick 路由测试（手动交易页实时报价的兜底通道）。

背景：手动交易页需要显示"当前品种实时价格"。主链路是 WS ``price_update``
（scheduler 每秒推全部引擎，无 RUNNING 过滤）；本端点是刚进页面、切换品种或
WS 断线时的兜底。测试锁住三个契约：

  1) 返回规范名 + bid/ask/spread/time（前端按 ``symbol`` 归位到 store.ticks）；
  2) 展示用途 → 必须传 ``validate=False``。``validate=True`` 在 tick 陈旧
     （>MAX_TICK_AGE_SECONDS）或点差异常（>3× 均值）时返回 None，会把页面
     价格无谓地抹成空；
  3) 无 tick 时返回 ``{"tick": null}`` 且 200（前端据此显示"等待报价"），
     未知品种 404（与 /ohlcv、/symbols 的 _get_engine 语义一致）。

全部离线 mock：不联网、不需要真实 MT5 桥（CI 是 ubuntu 无桥环境）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TICK = {
    "bid": 2350.12,
    "ask": 2350.42,
    "spread": 0.3,
    "time": "2026-09-20T10:00:00",
}


@pytest.fixture
def fake_engine():
    engine = MagicMock()
    engine.symbol = "GOLD"
    engine.market_data.get_current_tick = AsyncMock(return_value=dict(TICK))
    return engine


@pytest.fixture
def app(fake_engine):
    """真实 ``_get_engine`` + 真实路由，只把引擎/管理器换成 mock。"""
    from fastapi import FastAPI

    from app.api.routes import market_data
    from app.bot.manager import set_global_manager

    manager = MagicMock()
    # 规范名 GOLD 只有规范名引擎；别名（GOLDmicro → GOLD）走 resolve_symbol
    manager.engines = {"GOLD": fake_engine}
    manager.get_engine = MagicMock(side_effect=lambda sym: manager.engines.get(sym))
    manager.resolve_symbol = MagicMock(return_value=None)

    set_global_manager(manager)
    app = FastAPI()
    app.include_router(market_data.router)
    try:
        yield app
    finally:
        set_global_manager(None)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_tick_returns_normalized_quote(client, fake_engine):
    resp = await client.get("/api/market-data/tick", params={"symbol": "GOLD"})

    assert resp.status_code == 200
    assert resp.json() == {"tick": {"symbol": "GOLD", **TICK}}

    # 契约：只暴露前端需要的字段（不泄漏引擎内部 tick 结构）
    assert set(resp.json()["tick"]) == {"symbol", "bid", "ask", "spread", "time"}
    # 引擎按规范名取数，展示用不启用风控级校验
    assert fake_engine.market_data.get_current_tick.await_args.args[0] == "GOLD"
    assert fake_engine.market_data.get_current_tick.await_args.kwargs["validate"] is False


async def test_tick_without_data_returns_null_not_error(client, fake_engine):
    """桥未连上/无该品种 tick 时返回 null（200），前端显示"等待报价"而不是报错。"""
    fake_engine.market_data.get_current_tick.return_value = None

    resp = await client.get("/api/market-data/tick", params={"symbol": "GOLD"})

    assert resp.status_code == 200
    assert resp.json() == {"tick": None}


async def test_tick_resolves_broker_alias_to_canonical(client, app):
    """券商别名也要能取价，且返回的是规范名（前端按规范名归位）。"""
    from app.bot.manager import get_global_manager

    manager = get_global_manager()
    manager.resolve_symbol = MagicMock(return_value="GOLD")

    resp = await client.get("/api/market-data/tick", params={"symbol": "GOLDmicro"})

    assert resp.status_code == 200
    assert resp.json()["tick"]["symbol"] == "GOLD"


async def test_tick_unknown_symbol_404(client):
    resp = await client.get("/api/market-data/tick", params={"symbol": "NOPE"})

    assert resp.status_code == 404
    assert "NOPE" in resp.json()["detail"]


async def test_default_symbol_is_gold(client):
    """不带 symbol 时默认 GOLD（与 /ohlcv 一致，向后兼容）。"""
    resp = await client.get("/api/market-data/tick")

    assert resp.status_code == 200
    assert resp.json()["tick"]["symbol"] == "GOLD"
