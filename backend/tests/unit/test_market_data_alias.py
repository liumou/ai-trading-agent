"""回归测试 — MT5 请求路径必须使用券商别名（GOLD → GOLD_）。

背景：AI 分析对 GOLD 报 "No tick/OHLCV data"，而 ohlcv_data 表里明明有数据。
原因是两条通道不一致：
  - 采集通道（MarketDataService）调桥前做了 to_broker_alias("GOLD") → "GOLD_"，
    成功拉到数据并以规范名 GOLD 落库，所以"表里看起来有数据"；
  - AI 分析通道走 MCP 工具 → MT5BridgeConnector，把规范名 GOLD 原样打到桥上，
    桥端查无 GOLD（券商符号是 GOLD_）→ 返回 No data。

修复把别名转换收敛到 MT5BridgeConnector（调用桥前的最后一英里）。本测试锁住
这个契约：无论谁调用 connector，请求路径都必须是券商别名。全部离线 mock，
不联网、不需要真实桥（CI 是 ubuntu 无桥环境）。
"""

from unittest.mock import AsyncMock

import pytest

from app.config import SYMBOL_PROFILES
from app.mt5.connector import MT5BridgeConnector


@pytest.fixture(autouse=True)
def restore_profiles():
    """每个用例后还原 SYMBOL_PROFILES，避免污染其它测试。"""
    snapshot = dict(SYMBOL_PROFILES)
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


@pytest.fixture
def gold_profile():
    """GOLD 的规范名 → 券商别名映射（broker_alias 来源是 DB symbol_configs）。"""
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "GOLD_"}
    # 别名行自身也注册（load_profiles_from_db 的行为），保证幂等
    SYMBOL_PROFILES["GOLD_"] = {"broker_alias": "GOLD_", "canonical": "GOLD"}


@pytest.fixture
def connector():
    """Mock 掉底层 HTTP 请求，捕获实际打到桥上的 path。"""
    conn = MT5BridgeConnector()
    captured: list[str] = []

    async def _capture(method: str, path: str, **kwargs):
        captured.append(path)
        return {"success": True, "data": []}

    conn._request = AsyncMock(side_effect=_capture)
    conn._request_fast = AsyncMock(side_effect=_capture)
    conn._captured = captured  # type: ignore[attr-defined]
    return conn


async def test_get_ohlcv_uses_broker_alias(gold_profile, connector):
    """get_ohlcv("GOLD") 必须请求 /ohlcv/GOLD_，而不是 /ohlcv/GOLD。"""
    await connector.get_ohlcv("GOLD", "M15", 100)
    assert "/ohlcv/GOLD_" in connector._captured
    assert "/ohlcv/GOLD" not in connector._captured


async def test_get_tick_uses_broker_alias(gold_profile, connector):
    """get_tick("GOLD") 必须请求 /tick/GOLD_。"""
    await connector.get_tick("GOLD")
    assert "/tick/GOLD_" in connector._captured


async def test_get_symbol_spec_uses_broker_alias(gold_profile, connector):
    """get_symbol_spec("GOLD") 必须请求 /symbol-spec/GOLD_。"""
    await connector.get_symbol_spec("GOLD")
    assert "/symbol-spec/GOLD_" in connector._captured


async def test_ohlcv_range_uses_broker_alias(gold_profile, connector, monkeypatch):
    """历史区间采集（get_ohlcv_range）走独立 client，也必须转别名。"""
    client = AsyncMock()
    client.get.return_value = AsyncMock(
        raise_for_status=AsyncMock(return_value=None),
        json=AsyncMock(return_value={"success": True, "data": []}),
    )
    monkeypatch.setattr(connector, "_get_client", AsyncMock(return_value=client))

    await connector.get_ohlcv_range("GOLD", "M15", "2026-01-01", "2026-02-01")

    called_path = client.get.call_args.args[0]
    assert called_path == "/ohlcv/GOLD_/history", f"unexpected path: {called_path}"


async def test_history_filter_uses_broker_alias(gold_profile, connector):
    """get_history 的 symbol 过滤参数必须转别名。

    桥端用 deal.symbol（券商名）严格比对，传规范名会把成交历史过滤成空。
    """
    await connector.get_history(days=7, symbol="GOLD")
    params = connector._request.call_args.kwargs["params"]
    assert params["symbol"] == "GOLD_"


async def test_conversion_is_idempotent_for_alias_input(gold_profile, connector):
    """直接传券商名 GOLD_ 时不会被二次改写成别的名字。"""
    await connector.get_ohlcv("GOLD_", "M15", 100)
    assert "/ohlcv/GOLD_" in connector._captured


async def test_unknown_symbol_passes_through_unchanged(connector):
    """无 profile 的未知符号原样透传，不影响既有行为。"""
    SYMBOL_PROFILES.pop("UNKNOWN_XYZ", None)
    await connector.get_ohlcv("UNKNOWN_XYZ", "M15", 100)
    assert "/ohlcv/UNKNOWN_XYZ" in connector._captured


async def test_symbol_without_alias_uses_canonical_name(connector):
    """没有 broker_alias 的品种（如 BTCUSD）仍用规范名请求。"""
    SYMBOL_PROFILES["BTCUSD"] = {"broker_alias": ""}
    await connector.get_ohlcv("BTCUSD", "M15", 100)
    assert "/ohlcv/BTCUSD" in connector._captured


async def test_mcp_tool_layer_also_resolves_alias(gold_profile, connector, monkeypatch):
    """端到端：MCP 行情工具（AI 分析实际调用的入口）拿到的是别名路径。

    tools.market_data 持有自己的模块级 connector 单例，这里替换它，
    验证"工具层 → connector"这条真实链路也命中别名。
    """
    from mcp_server.tools import market_data as md_tools

    # raising=True（默认）：若模块级单例属性名变动，测试应直接报错，
    # 而不是静默通过变成一个"看起来绿"的空测试。
    monkeypatch.setattr(md_tools, "_connector", connector)

    result = await md_tools.get_ohlcv("GOLD", "M15", 100)

    assert "error" not in result, result
    assert "/ohlcv/GOLD_" in connector._captured
    # 返回给 LLM 的 symbol 仍是规范名，避免下游/日志符号漂移
    assert result["symbol"] == "GOLD"
