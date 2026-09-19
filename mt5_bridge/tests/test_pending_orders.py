"""MT5 Bridge 挂单端点测试(手动交易 Phase 1)。

覆盖:GET /orders、POST /order/pending(含 retcode PLACED/DONE 双成功码、
价格关系校验、filling 推导、pydantic 校验)、PUT /order/{ticket}(查单回填
type_time/expiration)、DELETE /order/{ticket}。不验证真实 MT5 SDK 行为。

运行(从 mt5_bridge/ 目录): python -m pytest tests/ -v
"""

import types

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-Bridge-Key": "test-key"}


def _symbol_info(filling_mode: int = 3):
    return types.SimpleNamespace(
        name="GOLD", visible=True, digits=2, point=0.01,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        filling_mode=filling_mode,
    )


def _tick():
    return types.SimpleNamespace(bid=2000.0, ask=2000.5, time=0)


def _order_result(retcode=10008, ticket=555, comment=""):
    return types.SimpleNamespace(retcode=retcode, order=ticket, comment=comment)


def _pending_order_ns(**overrides):
    base = dict(
        ticket=555, symbol="GOLD", type=2, volume_current=0.1, volume_initial=0.1,
        price_open=1980.0, price_current=2000.0, sl=1970.0, tp=2020.0,
        state=5, type_time=0, time_setup=1700000000, time_expiration=0,
        comment="", magic=234000,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch, mt5_mock):
    """终端存活 + 已登录,品种/行情默认可用。"""
    mt5_mock.terminal_info.return_value = object()
    mt5_mock.account_info.return_value = types.SimpleNamespace(login=12345, server="S")
    mt5_mock.last_error.return_value = (0, "no error")
    mt5_mock.symbol_info.return_value = _symbol_info()
    mt5_mock.symbol_info_tick.return_value = _tick()

    from main import app

    with TestClient(app) as c:
        yield c


# ─── GET /orders ─────────────────────────────────────────────────────────────


def test_get_orders_serializes_fields(client, mt5_mock):
    mt5_mock.orders_get.return_value = [_pending_order_ns()]

    resp = client.get("/orders", headers=AUTH)
    body = resp.json()
    assert resp.status_code == 200 and body["success"] is True
    o = body["data"][0]
    assert o["ticket"] == 555
    assert o["type"] == "BUY_LIMIT"  # ORDER_TYPE_BUY_LIMIT=2 反解
    assert o["price_open"] == 1980.0
    assert o["lot"] == 0.1


def test_get_orders_empty_when_none(client, mt5_mock):
    mt5_mock.orders_get.return_value = None
    body = client.get("/orders", headers=AUTH).json()
    assert body["success"] is True and body["data"] == []


# ─── POST /order/pending ─────────────────────────────────────────────────────


def test_place_pending_success_retcode_placed(client, mt5_mock):
    """关键回归:挂单成功 retcode=10008 PLACED,只查 DONE 会误判失败。"""
    mt5_mock.order_send.return_value = _order_result(retcode=10008)

    resp = client.post(
        "/order/pending", headers=AUTH,
        json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0,
              "sl": 1970.0, "tp": 2020.0},
    )
    body = resp.json()
    assert body["success"] is True, body
    assert body["data"]["ticket"] == 555
    req = mt5_mock.order_send.call_args.args[0]
    assert req["action"] == 5  # TRADE_ACTION_PENDING
    assert req["price"] == 1980.0


def test_place_pending_accepts_retcode_done(client, mt5_mock):
    mt5_mock.order_send.return_value = _order_result(retcode=10009, ticket=556)
    body = client.post(
        "/order/pending", headers=AUTH,
        json={"symbol": "GOLD", "type": "SELL_LIMIT", "lot": 0.1, "price": 2020.0},
    ).json()
    assert body["success"] is True


def test_place_pending_rejection_has_structured_retcode(client, mt5_mock):
    mt5_mock.order_send.return_value = _order_result(retcode=10015, comment="invalid price")
    body = client.post(
        "/order/pending", headers=AUTH,
        json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0},
    ).json()
    assert body["success"] is False
    assert body["data"]["retcode"] == 10015
    assert "invalid price" in body["error"]


@pytest.mark.parametrize("payload,frag", [
    ({"type": "BUY_LIMIT", "lot": 0.1, "price": 2000.5}, "below ask"),      # limit ≥ ask
    ({"type": "SELL_LIMIT", "lot": 0.1, "price": 1999.0}, "above bid"),     # limit ≤ bid
    ({"type": "BUY_STOP", "lot": 0.1, "price": 2000.0}, "above ask"),       # stop ≤ ask
    ({"type": "SELL_STOP", "lot": 0.1, "price": 2001.0}, "below bid"),      # stop ≥ bid
    ({"type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0, "sl": 1990.0}, "SL"),    # 买 SL ≥ 挂单价
    ({"type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0, "tp": 1970.0}, "TP"),    # 买 TP ≤ 挂单价
    ({"type": "SELL_LIMIT", "lot": 0.1, "price": 2020.0, "sl": 2010.0}, "SL"),   # 卖 SL ≤ 挂单价
    ({"type": "SELL_LIMIT", "lot": 0.1, "price": 2020.0, "tp": 2030.0}, "TP"),   # 卖 TP ≥ 挂单价
])
def test_place_pending_price_relation_rejected(client, mt5_mock, payload, frag):
    body = client.post(
        "/order/pending", headers=AUTH,
        json={"symbol": "GOLD", **payload},
    ).json()
    assert body["success"] is False
    assert frag in body["error"]
    mt5_mock.order_send.assert_not_called()


def test_place_pending_filling_from_symbol_bitmask(client, mt5_mock):
    """filling_mode 位掩码推导:2=IOC 可用 → ORDER_FILLING_IOC。"""
    mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)
    mt5_mock.order_send.return_value = _order_result()
    client.post("/order/pending", headers=AUTH,
                json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0})
    assert mt5_mock.order_send.call_args.args[0]["type_filling"] == 1  # IOC


def test_place_pending_filling_fallback_return(client, mt5_mock):
    """filling_mode=0(均不支持)→ 回落 ORDER_FILLING_RETURN(挂单硬编码 IOC
    会被 10030 拒绝)。"""
    mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=0)
    mt5_mock.order_send.return_value = _order_result()
    client.post("/order/pending", headers=AUTH,
                json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0})
    assert mt5_mock.order_send.call_args.args[0]["type_filling"] == 2  # RETURN


def test_place_pending_expiration_gtc_by_default(client, mt5_mock):
    mt5_mock.order_send.return_value = _order_result()
    client.post("/order/pending", headers=AUTH,
                json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0})
    req = mt5_mock.order_send.call_args.args[0]
    assert req["type_time"] == 0  # ORDER_TIME_GTC
    assert "expiration" not in req


@pytest.mark.parametrize("bad_body,field", [
    ({"symbol": "GOLD", "type": "TRAILING", "lot": 0.1, "price": 1980.0}, "type"),
    ({"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0, "price": 1980.0}, "lot"),
    ({"symbol": "GOLD", "type": "BUY_LIMIT", "lot": -1, "price": 1980.0}, "lot"),
    ({"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 0}, "price"),
    ({"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1}, "price"),
])
def test_place_pending_pydantic_validation_422(client, bad_body, field):
    resp = client.post("/order/pending", headers=AUTH, json=bad_body)
    assert resp.status_code == 422
    assert field in resp.text


def test_place_pending_requires_auth(client):
    resp = client.post(
        "/order/pending",
        json={"symbol": "GOLD", "type": "BUY_LIMIT", "lot": 0.1, "price": 1980.0},
    )
    assert resp.status_code in (401, 422)


# ─── PUT /order/{ticket} ─────────────────────────────────────────────────────


def test_modify_pending_overlays_fields(client, mt5_mock):
    mt5_mock.orders_get.return_value = [_pending_order_ns()]
    mt5_mock.order_send.return_value = _order_result(retcode=10009)

    resp = client.put("/order/555", headers=AUTH, json={"price": 1975.0, "sl": 1965.0})
    body = resp.json()
    assert body["success"] is True, body
    req = mt5_mock.order_send.call_args.args[0]
    assert req["action"] == 7  # TRADE_ACTION_MODIFY
    assert req["price"] == 1975.0
    assert req["sl"] == 1965.0
    assert req["tp"] == 2020.0  # 未指定字段回填原单
    assert req["type_time"] == 0  # 原单 GTC 回传


def test_modify_pending_specified_expiration_backfilled(client, mt5_mock):
    """原单 ORDER_TIME_SPECIFIED=2 时 expiration 必须整包回传,否则 10016/10022。"""
    mt5_mock.orders_get.return_value = [
        _pending_order_ns(type_time=2, time_expiration=1790000000)
    ]
    mt5_mock.order_send.return_value = _order_result(retcode=10009)

    resp = client.put("/order/555", headers=AUTH, json={"price": 1975.0})
    assert resp.json()["success"] is True
    req = mt5_mock.order_send.call_args.args[0]
    assert req["type_time"] == 2
    assert req["expiration"] is not None


def test_modify_pending_not_found(client, mt5_mock):
    mt5_mock.orders_get.return_value = []
    body = client.put("/order/999", headers=AUTH, json={"price": 1.0}).json()
    assert body["success"] is False and "not found" in body["error"]
    mt5_mock.order_send.assert_not_called()


# ─── DELETE /order/{ticket} ──────────────────────────────────────────────────


def test_cancel_order_success(client, mt5_mock):
    mt5_mock.orders_get.return_value = [_pending_order_ns()]
    mt5_mock.order_send.return_value = _order_result(retcode=10009)

    body = client.delete("/order/555", headers=AUTH).json()
    assert body["success"] is True and body["data"]["cancelled"] is True
    req = mt5_mock.order_send.call_args.args[0]
    assert req == {"action": 8, "order": 555}  # TRADE_ACTION_REMOVE


def test_cancel_order_not_found(client, mt5_mock):
    mt5_mock.orders_get.return_value = []
    body = client.delete("/order/999", headers=AUTH).json()
    assert body["success"] is False
    mt5_mock.order_send.assert_not_called()


def test_cancel_requires_auth(client):
    assert client.delete("/order/555").status_code in (401, 422)
