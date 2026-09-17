"""MCP broker place_order 手数防线与券商品种名解析测试。

覆盖 code-review 发现的 PR2-8 缺口：AI/MCP 下单路径此前绕过 BotEngine 的
volume 网格防线，且未做 canonical → 券商名转换（to_broker_alias）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import mcp_server.tools.broker as broker_mod
from app.config import SYMBOL_PROFILES


@pytest.fixture(autouse=True)
def _profiles():
    snapshot = {k: dict(v) for k, v in SYMBOL_PROFILES.items()}
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


def _install(monkeypatch, profile: dict, rollout_mode: str = "live"):
    """把 mock 的 connector/guardrails 注入 broker 模块的全局变量。"""
    from app.config import settings

    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES["GOLD"] = profile

    connector = AsyncMock()
    connector.get_account.return_value = {
        "success": True,
        "data": {"balance": 10000.0, "equity": 10000.0, "profit": 0.0},
    }
    connector.get_positions.return_value = {"success": True, "data": []}
    connector.get_tick.return_value = {"success": True, "data": {"bid": 2000.0, "ask": 2000.5}}
    connector.place_order.return_value = {
        "success": True,
        "data": {"ticket": 123, "price": 2000.5, "lot": 0.1},
    }

    guardrails = MagicMock()
    guardrails.validate_order = AsyncMock(return_value=SimpleNamespace(allowed=True, reason="ok"))
    guardrails.check_rollout_mode_async = AsyncMock(return_value=SimpleNamespace(allowed=True, reason=""))
    guardrails.get_persisted_rollout_mode = AsyncMock(return_value=rollout_mode)
    guardrails.record_order_opened = AsyncMock()
    guardrails.record_trade_closed = AsyncMock()

    monkeypatch.setattr(broker_mod, "_connector", connector)
    monkeypatch.setattr(broker_mod, "_guardrails", guardrails)
    monkeypatch.setattr(broker_mod, "_notifier", None)
    monkeypatch.setattr(broker_mod, "_redis", None)  # daily pnl 回退到 account.profit

    # 测试默认 live 模式需要 LLM_ALLOW_LIVE=true 才能通过 broker 层授权检查
    prev_allow = settings.llm_allow_live
    settings.llm_allow_live = True
    monkeypatch.setattr(settings, "llm_allow_live", True)

    return connector, guardrails


class TestMcpVolumeGuard:
    @pytest.mark.asyncio
    async def test_lot_below_broker_min_rejected(self, monkeypatch):
        """Bridge 会静默把手数上调到 volume_min，AI 下单必须先在此拒单。"""
        connector, _ = _install(
            monkeypatch, {"pip_value": 1.0, "volume_min": 1.0, "volume_step": 0.1}
        )
        result = await broker_mod.place_order(
            symbol="GOLD", order_type="BUY", lot=0.5, sl=1900.0, tp=2100.0
        )
        assert result["rejected"] is True
        assert "below broker minimum" in result["reason"]
        connector.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lot_floored_to_step_grid(self, monkeypatch):
        """偏离 step 网格的手数向下取整后再下单，执行风险不超过预算。"""
        connector, _ = _install(
            monkeypatch, {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.1}
        )
        result = await broker_mod.place_order(
            symbol="GOLD", order_type="BUY", lot=0.55, sl=1900.0, tp=2100.0
        )
        assert result["executed"] is True
        assert connector.place_order.await_args.kwargs["lot"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_symbol_translated_to_broker_alias(self, monkeypatch):
        """connector 使用券商名（GOLDmicro），引擎用规范名（GOLD）。"""
        connector, _ = _install(
            monkeypatch,
            {
                "pip_value": 1.0,
                "broker_alias": "GOLDmicro",
                "volume_min": 0.01,
                "volume_step": 0.01,
            },
        )
        await broker_mod.place_order(
            symbol="GOLD", order_type="BUY", lot=0.1, sl=1900.0, tp=2100.0
        )
        assert connector.place_order.await_args.kwargs["symbol"] == "GOLDmicro"

    @pytest.mark.asyncio
    async def test_no_volume_data_skips_guard(self, monkeypatch):
        """存量未回填 volume 的品种保持原行为（不阻断下单）。"""
        connector, _ = _install(monkeypatch, {"pip_value": 1.0})
        result = await broker_mod.place_order(
            symbol="GOLD", order_type="BUY", lot=0.03, sl=1900.0, tp=2100.0
        )
        assert result["executed"] is True
        assert connector.place_order.await_args.kwargs["lot"] == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_guard_rejection_reported_in_shadow_mode(self, monkeypatch):
        """shadow 模式下也应报告规范化后的手数（防线前移到模式判断之前）。"""
        connector, _ = _install(
            monkeypatch,
            {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.1},
            rollout_mode="shadow",
        )
        result = await broker_mod.place_order(
            symbol="GOLD", order_type="BUY", lot=0.55, sl=1900.0, tp=2100.0
        )
        assert result["mode"] == "shadow"
        assert result["would_execute"]["lot"] == pytest.approx(0.5)
        connector.place_order.assert_not_awaited()
