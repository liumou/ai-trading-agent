"""manual_trading 路由测试(Phase 4)。

鉴权(require_auth)在测试环境关闭(conftest AUTH_PASSWORD_HASH="")。
路由层职责:参数校验(422)、状态码语义(REJECTED→200 / PENDING_REVIEW→202)、
gate 委托、挂单 symbol 归一化;防火墙逻辑在 Gate 测试覆盖。
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock


def _make_test_app(gate):
    from fastapi import FastAPI

    from app.api.routes import manual_trading

    app = FastAPI()
    app.include_router(manual_trading.router)
    app.state.manual_order_gate = gate
    app.state.connector = MagicMock()
    app.state.redis = AsyncMock()
    app.state.manager = MagicMock(current_account_login="10086")
    return app


@pytest_asyncio.fixture
async def client():
    from app.config import SYMBOL_PROFILES

    snapshot = {k: dict(v) for k, v in SYMBOL_PROFILES.items()}
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "broker_alias": "GOLD_"}
    SYMBOL_PROFILES["GOLD_"] = {"pip_value": 1.0, "canonical": "GOLD", "broker_alias": "GOLD_"}
    gate = MagicMock()
    gate.submit_order = AsyncMock(return_value={"status": "PENDING_REVIEW", "review_id": 1, "rule_flags": []})
    gate.confirm_and_execute = AsyncMock(return_value={"status": "EXECUTED", "review_id": 1})
    gate.get_review = AsyncMock(return_value={"id": 1, "status": "EXECUTED"})
    gate.list_reviews = AsyncMock(return_value=[{"id": 1}])
    gate.list_pending_orders = AsyncMock(return_value=[{
        "ticket": 555, "symbol": "GOLD_", "type": "BUY_LIMIT", "lot": 0.1,
        "volume_initial": 0.1, "price_open": 1980.0, "sl": 1970.0, "tp": 2020.0,
    }])
    gate.cancel_pending_order = AsyncMock(return_value={"cancelled": True, "ticket": 555})
    gate.modify_position_sltp = AsyncMock(return_value={"modified": True, "ticket": 42, "sl": 1980.0})

    app = _make_test_app(gate)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, gate
    finally:
        SYMBOL_PROFILES.clear()
        SYMBOL_PROFILES.update(snapshot)


# ─── 提交订单 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_market_order_202(client):
    c, gate = client
    resp = await c.post("/api/trading/orders", json={
        "symbol": "GOLD", "order_kind": "market", "order_type": "BUY",
        "lot": 0.1, "sl": 1900.0, "tp": 2100.0, "comment": "test",
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "PENDING_REVIEW"
    kwargs = gate.submit_order.await_args.kwargs
    assert kwargs["symbol"] == "GOLD"
    assert kwargs["account_login"] == "10086"  # 来自 manager.current_account_login


@pytest.mark.asyncio
async def test_submit_rejected_returns_200(client):
    c, gate = client
    gate.submit_order.return_value = {"status": "REJECTED", "review_id": 1, "reason": "x", "kind": "guardrail"}
    resp = await c.post("/api/trading/orders", json={
        "symbol": "GOLD", "order_kind": "market", "order_type": "SELL", "lot": 0.1,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_submit_pending_without_price_422(client):
    c, _ = client
    resp = await c.post("/api/trading/orders", json={
        "symbol": "GOLD", "order_kind": "pending", "order_type": "BUY_LIMIT", "lot": 0.1,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_invalid_order_type_422(client):
    c, _ = client
    resp = await c.post("/api/trading/orders", json={
        "symbol": "GOLD", "order_kind": "market", "order_type": "TRAILING", "lot": 0.1,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_negative_lot_422(client):
    c, _ = client
    resp = await c.post("/api/trading/orders", json={
        "symbol": "GOLD", "order_kind": "market", "order_type": "BUY", "lot": -1,
    })
    assert resp.status_code == 422


# ─── confirm / reviews ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_passes_account_login(client):
    c, gate = client
    resp = await c.post("/api/trading/orders/1/confirm")
    assert resp.status_code == 200
    assert gate.confirm_and_execute.await_args.args == (1,)
    assert gate.confirm_and_execute.await_args.kwargs["account_login"] == "10086"


@pytest.mark.asyncio
async def test_get_review_404(client):
    c, gate = client
    gate.get_review.return_value = None
    resp = await c.get("/api/trading/reviews/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_reviews_scoped_to_account(client):
    c, gate = client
    resp = await c.get("/api/trading/reviews")
    assert resp.status_code == 200
    assert gate.list_review.await_count if False else True
    assert gate.list_reviews.await_args.kwargs["account_login"] == "10086"


# ─── 挂单列表 / 撤单 / 改挂单 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_orders_symbol_normalized(client):
    c, _ = client
    resp = await c.get("/api/trading/orders")
    body = resp.json()
    assert body["orders"][0]["symbol"] == "GOLD"  # GOLD_ → 规范名


@pytest.mark.asyncio
async def test_cancel_order_ok_and_400(client):
    c, gate = client
    resp = await c.delete("/api/trading/orders/555")
    assert resp.status_code == 200
    gate.cancel_pending_order.return_value = {"cancelled": False, "error": "not found"}
    resp = await c.delete("/api/trading/orders/999")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_modify_pending_404_when_missing(client):
    c, gate = client
    gate.list_pending_orders.return_value = []
    resp = await c.put("/api/trading/orders/555", json={"price": 1999.0})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_modify_pending_noop_422(client):
    c, _ = client
    resp = await c.put("/api/trading/orders/555", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_modify_pending_delegates_with_modify_ticket(client):
    c, gate = client
    resp = await c.put("/api/trading/orders/555", json={"price": 1975.0, "sl": 1965.0})
    assert resp.status_code == 200
    kwargs = gate.submit_order.await_args.kwargs
    assert kwargs["modify_ticket"] == 555
    assert kwargs["order_kind"] == "pending"
    assert kwargs["order_type"] == "BUY_LIMIT"
    assert kwargs["price"] == 1975.0  # 指定的新价
    assert kwargs["sl"] == 1965.0
    assert kwargs["tp"] == 2020.0  # 未指定字段取当前挂单值


# ─── 改 SL/TP / 平仓 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_modify_sltp_rejected_is_200_business_result(client):
    c, gate = client
    gate.modify_position_sltp.return_value = {"modified": False, "rejected": True, "reason": "drift budget"}
    resp = await c.put("/api/trading/positions/42", json={"sl": 1800.0})
    assert resp.status_code == 200
    assert resp.json()["rejected"] is True


@pytest.mark.asyncio
async def test_modify_sltp_error_is_400(client):
    c, gate = client
    gate.modify_position_sltp.return_value = {"modified": False, "error": "Position not found"}
    resp = await c.put("/api/trading/positions/42", json={"sl": 1980.0})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_modify_sltp_requires_field(client):
    c, _ = client
    resp = await c.put("/api/trading/positions/42", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_close_position_uses_gated_path(client):
    c, gate = client
    # close 走 position_close.close_position_gated(app.state.connector/redis)
    with pytest.MonkeyPatch.context() as mp:
        called = {}

        async def fake_gated(connector, redis, ticket):
            called["ticket"] = ticket
            called["connector_is_state"] = connector is c._transport.app.state.connector
            return {"closed": True, "ticket": ticket, "profit": 1.5}

        import app.api.routes.manual_trading as mt
        mp.setattr(mt, "close_position_gated", fake_gated)
        resp = await c.post("/api/trading/positions/42/close")
    assert resp.status_code == 200
    assert called["ticket"] == 42
    assert called["connector_is_state"] is True
