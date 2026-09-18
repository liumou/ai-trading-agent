"""回归测试 — get_open_positions 必须把持仓 symbol 归一化为规范名。

背景：MT5 Bridge 返回的持仓 symbol 是券商名（GOLD_），而引擎/前端按规范名
（GOLD）过滤。此前 get_open_positions 直接用 to_broker_alias 过滤但保留券商名，
导致 position_update 推送的持仓 symbol 与 d.symbol（规范名）不一致，前端
`p.symbol !== sym` 过滤失效 → 每 30s 推送一次就叠加一份。

修复：返回前用 get_canonical_symbol() 把每个持仓的 symbol 归一为规范名
（规范名恒等返回）。本测试锁定该契约，防止有人改回裸返回。
"""

import pytest

from app.config import SYMBOL_PROFILES
from app.mt5.order_executor import OrderExecutor


@pytest.fixture(autouse=True)
def restore_profiles():
    snapshot = dict(SYMBOL_PROFILES)
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


@pytest.fixture
def gold_alias():
    """GOLD_（券商名）与 GOLD（规范名）互为一映射（DB 加载后的形态）。"""
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "GOLD_"}
    SYMBOL_PROFILES["GOLD_"] = {"broker_alias": "GOLD_", "canonical": "GOLD"}


class _FakeConnector:
    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self) -> dict:
        return {"success": True, "data": self._positions}


@pytest.mark.asyncio
async def test_normalizes_broker_symbol_to_canonical(gold_alias):
    """券商名持仓（GOLD_）必须被归一化为规范名（GOLD）。"""
    connector = _FakeConnector(
        [{"ticket": 1, "symbol": "GOLD_", "lot": 0.1, "type": "BUY", "profit": 1.0}]
    )
    executor = OrderExecutor(connector)
    positions = await executor.get_open_positions("GOLD")
    assert positions[0]["symbol"] == "GOLD"


@pytest.mark.asyncio
async def test_canonical_symbol_stays_unchanged(gold_alias):
    """已经是规范名的持仓（GOLD）保持恒等返回。"""
    connector = _FakeConnector(
        [{"ticket": 2, "symbol": "GOLD", "lot": 0.1, "type": "BUY", "profit": 2.0}]
    )
    executor = OrderExecutor(connector)
    positions = await executor.get_open_positions("GOLD")
    assert positions[0]["symbol"] == "GOLD"


@pytest.mark.asyncio
async def test_filters_other_symbols_when_symbol_given(gold_alias):
    """指定 symbol 时，其它品种的持仓不应被返回。"""
    connector = _FakeConnector(
        [
            {"ticket": 3, "symbol": "GOLD_", "lot": 0.1, "type": "BUY", "profit": 1.0},
            {"ticket": 4, "symbol": "EURUSD", "lot": 0.1, "type": "BUY", "profit": -1.0},
        ]
    )
    executor = OrderExecutor(connector)
    positions = await executor.get_open_positions("GOLD")
    assert [p["ticket"] for p in positions] == [3]
    assert positions[0]["symbol"] == "GOLD"


@pytest.mark.asyncio
async def test_no_symbol_returns_all_normalized(gold_alias):
    """不带 symbol 时返回全部持仓，且每个 symbol 都归一化。"""
    connector = _FakeConnector(
        [
            {"ticket": 5, "symbol": "GOLD_", "lot": 0.1, "type": "BUY", "profit": 1.0},
            {"ticket": 6, "symbol": "EURUSD", "lot": 0.1, "type": "SELL", "profit": 2.0},
        ]
    )
    executor = OrderExecutor(connector)
    positions = await executor.get_open_positions()
    assert positions[0]["symbol"] == "GOLD"
    assert positions[1]["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_empty_data_returns_empty():
    """MT5 返回空持仓时返回空列表（不归一化崩溃）。"""
    connector = _FakeConnector([])
    executor = OrderExecutor(connector)
    assert await executor.get_open_positions("GOLD") == []


@pytest.mark.asyncio
async def test_connector_failure_returns_empty():
    """连接器失败时返回空列表。"""
    class _FailingConnector:
        async def get_positions(self) -> dict:
            return {"success": False, "data": None}

    executor = OrderExecutor(_FailingConnector())
    assert await executor.get_open_positions("GOLD") == []
