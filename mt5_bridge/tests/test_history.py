"""MT5 Bridge /history 端点测试（历史记录字段补全）。

覆盖订单-成交配对：开仓价/时间/SL/TP 来自 history_orders_get，平仓价/时间/
盈亏来自 history_deals_get；部分平仓聚合；方向判定；品种过滤。不验证真实
MT5 SDK 行为。

运行(从 mt5_bridge/ 目录): python -m pytest tests/test_history.py -v
"""

import types

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-Bridge-Key": "test-key"}


def _order_ns(**overrides):
    """已成交订单（history_orders_get 返回项）。

    真实 MT5 MqlTradeOrder 字段：没有 position_id！SL/TP/开仓价在此对象上，
    关联持仓靠 deal.order → order.ticket。
    """
    base = dict(
        ticket=111,                       # 订单 ticket（deal.order 关联它）
        symbol="GOLD", type=0,            # ORDER_TYPE_BUY
        volume_initial=0.1, volume_current=0.1,
        price_open=2000.0, price_current=2010.0,
        sl=1990.0, tp=2030.0,
        time_setup=1700000000, time_done=1700000100,
        comment="", magic=234000,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _entry_deal_ns(**overrides):
    """开仓成交（entry=0，DEAL_ENTRY_IN）。提供开仓价/时间/方向。"""
    base = dict(
        ticket=800, order=111, position_id=5001, symbol="GOLD",
        type=0,        # DEAL_TYPE_BUY（开 BUY 仓）
        entry=0,       # DEAL_ENTRY_IN
        volume=0.1, price=2000.0, profit=0.0,
        commission=0.0, swap=0.0, time=1700000100, comment="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _exit_deal_ns(**overrides):
    """平仓成交（entry=1，DEAL_ENTRY_OUT）。提供平仓价/时间/盈亏。"""
    base = dict(
        ticket=900, order=111, position_id=5001, symbol="GOLD",
        type=1,        # DEAL_TYPE_SELL（平 BUY 仓）
        entry=1,       # DEAL_ENTRY_OUT
        volume=0.1, price=2020.0, profit=20.0,
        commission=-0.1, swap=-0.05, time=1700000200, comment="ok",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def client(monkeypatch, mt5_mock):
    mt5_mock.terminal_info.return_value = object()
    mt5_mock.account_info.return_value = types.SimpleNamespace(login=12345, server="S")
    mt5_mock.last_error.return_value = (0, "no error")

    from main import app

    with TestClient(app) as c:
        yield c


def test_history_full_fields(client, mt5_mock):
    """核心：开仓/平仓字段全部来自成交-订单配对，不再开仓价=平仓价。"""
    mt5_mock.history_orders_get.return_value = [_order_ns()]
    mt5_mock.history_deals_get.return_value = [_entry_deal_ns(), _exit_deal_ns()]

    body = client.get("/history", headers=AUTH).json()
    assert body["success"] is True
    rows = body["data"]
    assert len(rows) == 1
    r = rows[0]
    assert r["ticket"] == 5001                  # position_id（来自成交）
    assert r["type"] == "BUY"                   # 开仓成交 type=0
    assert r["open_price"] == 2000.0            # 来自订单 price_open
    assert r["close_price"] == 2020.0           # 来自平仓成交
    assert r["sl"] == 1990.0                    # 来自订单（deal.order→order.ticket）
    assert r["tp"] == 2030.0
    assert r["open_time"] != r["close_time"]    # 开仓时间与平仓时间分离
    assert r["profit"] == 20.0
    # 兼容别名：旧 Bridge 消费点读 price/time（平仓语义）
    assert r["price"] == 2020.0
    assert r["time"] == r["close_time"]
    # 净额 = profit + commission + swap
    assert r["net_profit"] == pytest.approx(19.85)


def test_history_direction_from_deal_type(client, mt5_mock):
    """方向由开仓成交类型判定：挂单触发（order type=3 SELL_LIMIT）的开仓成交
    type 恒为 1（SELL），不能比 ORDER_TYPE_SELL 错判为 BUY。"""
    mt5_mock.history_orders_get.return_value = [_order_ns(type=3)]  # SELL_LIMIT
    mt5_mock.history_deals_get.return_value = [
        _entry_deal_ns(type=1),  # DEAL_TYPE_SELL（平 SELL 仓=开仓成交 type 1）
        _exit_deal_ns(type=0),   # DEAL_TYPE_BUY（平 SELL 仓=平仓成交 type 0）
    ]

    rows = client.get("/history", headers=AUTH).json()["data"]
    assert rows[0]["type"] == "SELL"


def test_history_open_time_uses_time_done(client, mt5_mock):
    """开仓时间优先 time_done（成交时间），回落 time_setup（挂单创建时间）。"""
    mt5_mock.history_orders_get.return_value = [
        _order_ns(time_done=1700000500, time_setup=1700000000)
    ]
    mt5_mock.history_deals_get.return_value = [_entry_deal_ns(), _exit_deal_ns()]

    rows = client.get("/history", headers=AUTH).json()["data"]
    assert rows[0]["open_time"].startswith("2023-11-14T22:21:40")  # 1700000500 UTC


def test_history_open_price_fallback_without_order(client, mt5_mock):
    """订单匹配不到时（deal.order 无对应 order.ticket）不丢行：开仓价/时间
    回落开仓成交，SL/TP 记 0。"""
    mt5_mock.history_orders_get.return_value = [_order_ns(ticket=999)]  # 与 deal.order=111 不匹配
    mt5_mock.history_deals_get.return_value = [_entry_deal_ns(), _exit_deal_ns()]

    rows = client.get("/history", headers=AUTH).json()["data"]
    assert len(rows) == 1
    r = rows[0]
    assert r["open_price"] == 2000.0            # 回落开仓成交 price
    assert r["sl"] == 0.0 and r["tp"] == 0.0


def test_history_partial_close_aggregates(client, mt5_mock):
    """部分平仓：同一 position 多条 exit deal，盈亏聚合净额，价格取最后一条。"""
    mt5_mock.history_orders_get.return_value = [_order_ns()]
    mt5_mock.history_deals_get.return_value = [
        _entry_deal_ns(),
        _exit_deal_ns(ticket=901, volume=0.05, price=2010.0, profit=5.0,
                      commission=-0.05, swap=0.0, time=1700000200),
        _exit_deal_ns(ticket=902, volume=0.05, price=2020.0, profit=15.0,
                      commission=-0.05, swap=0.0, time=1700000300),
    ]

    rows = client.get("/history", headers=AUTH).json()["data"]
    assert len(rows) == 1
    r = rows[0]
    assert r["close_price"] == 2020.0          # 最后一条
    assert r["profit"] == 20.0                 # 5 + 15
    assert r["net_profit"] == pytest.approx(19.9)  # 20 + (-0.1) + 0
    assert r["lot"] == 0.1                     # 开仓成交原始手数


def test_history_unclosed_position_excluded(client, mt5_mock):
    """只有平仓成交的持仓进入历史；持仓中（无 exit deal）的排除。"""
    mt5_mock.history_orders_get.return_value = [_order_ns()]
    mt5_mock.history_deals_get.return_value = [
        _entry_deal_ns(position_id=5001),
        _entry_deal_ns(position_id=5002, ticket=801, order=112),
        _exit_deal_ns(position_id=5001),
    ]

    rows = client.get("/history", headers=AUTH).json()["data"]
    assert [r["ticket"] for r in rows] == [5001]


def test_history_symbol_filter(client, mt5_mock):
    """品种过滤用开仓成交的 symbol（与后端消费同一来源）。"""
    mt5_mock.history_orders_get.return_value = [_order_ns()]
    mt5_mock.history_deals_get.return_value = [
        _entry_deal_ns(position_id=5001, symbol="GOLD"),
        _entry_deal_ns(position_id=5002, ticket=801, order=112, symbol="OILCash"),
        _exit_deal_ns(position_id=5001, symbol="GOLD"),
        _exit_deal_ns(position_id=5002, ticket=901, symbol="OILCash"),
    ]

    rows = client.get("/history?symbol=GOLD", headers=AUTH).json()["data"]
    assert [r["symbol"] for r in rows] == ["GOLD"]


def test_history_symbol_filter_underscore_alias(client, mt5_mock):
    """券商别名带下划线（GOLD_）也能被规范名过滤命中 —— 严格逐字比较会误过滤。"""
    mt5_mock.history_orders_get.return_value = [_order_ns()]
    mt5_mock.history_deals_get.return_value = [
        _entry_deal_ns(position_id=5001, symbol="GOLD_"),
        _entry_deal_ns(position_id=5002, ticket=801, order=112, symbol="OILCash"),
        _exit_deal_ns(position_id=5001, symbol="GOLD_"),
        _exit_deal_ns(position_id=5002, ticket=901, symbol="OILCash"),
    ]

    rows = client.get("/history?symbol=GOLD", headers=AUTH).json()["data"]
    assert [r["symbol"] for r in rows] == ["GOLD_"]


def test_history_empty_when_no_data(client, mt5_mock):
    mt5_mock.history_orders_get.return_value = None
    mt5_mock.history_deals_get.return_value = None
    body = client.get("/history", headers=AUTH).json()
    assert body["success"] is True and body["data"] == []


def test_history_requires_auth(client):
    assert client.get("/history").status_code in (401, 422)
