"""HealthMonitor 暂停恢复语义测试（R1：风控暂停不可被桥恢复无条件解除）。

背景：health_monitor _on_success 原本把全部 PAUSED 引擎恢复 RUNNING，不区分
暂停原因 —— 熔断/equity 回撤暂停可能被桥健康抖动意外解除，击穿风控。
修复：仅恢复 pause_reason="bridge"（桥故障暂停）的引擎；
熔断暂停（pause_reason="circuit"）由 _run_risk_gate 冷却后恢复。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.engine import BotState
from app.bot.health_monitor import HealthMonitor


class _Engine:
    """真实引擎的最小替身：state + pause_reason（HealthMonitor 只碰这两个）。"""

    def __init__(self, state=BotState.RUNNING, pause_reason=None):
        self.state = state
        self.pause_reason = pause_reason

    async def _log_event(self, *_a, **_kw):
        pass


def _make_monitor(engines) -> HealthMonitor:
    manager = MagicMock()
    manager.engines = {f"SYM{i}": e for i, e in enumerate(engines)}
    notifier = AsyncMock()
    mon = HealthMonitor(connector=AsyncMock(), manager=manager, notifier=notifier)
    mon._is_degraded = True  # 模拟之前故障降级过（_on_success 的恢复分支只在此态触发）
    return mon


@pytest.mark.asyncio
async def test_bridge_paused_engine_restored():
    """桥故障暂停（pause_reason="bridge"）→ 桥恢复后应恢复 RUNNING。"""
    engine = _Engine(state=BotState.PAUSED, pause_reason="bridge")
    mon = _make_monitor([engine])
    await mon._on_success()
    assert engine.state == BotState.RUNNING


@pytest.mark.asyncio
async def test_circuit_paused_engine_not_restored():
    """熔断/equity 回撤暂停（pause_reason="circuit"）→ 桥恢复不得恢复该引擎。"""
    engine = _Engine(state=BotState.PAUSED, pause_reason="circuit")
    mon = _make_monitor([engine])
    await mon._on_success()
    assert engine.state == BotState.PAUSED


@pytest.mark.asyncio
async def test_mixed_pause_reasons():
    """混合暂停：只有 bridge 恢复，circuit 保持暂停。"""
    eng_bridge = _Engine(state=BotState.PAUSED, pause_reason="bridge")
    eng_circuit = _Engine(state=BotState.PAUSED, pause_reason="circuit")
    mon = _make_monitor([eng_bridge, eng_circuit])
    await mon._on_success()
    assert eng_bridge.state == BotState.RUNNING
    assert eng_circuit.state == BotState.PAUSED


@pytest.mark.asyncio
async def test_running_engine_untouched():
    """RUNNING 引擎不受恢复逻辑影响。"""
    engine = _Engine(state=BotState.RUNNING, pause_reason=None)
    mon = _make_monitor([engine])
    await mon._on_success()
    assert engine.state == BotState.RUNNING