"""BotScheduler._candle_job 对 PAUSED 引擎驱动风控回路（R1）。

背景：_run_risk_gate 内含 PAUSED→can_resume→RUNNING 恢复逻辑，但 scheduler
只在 state=="RUNNING" 时调用它 → 熔断暂停的引擎永远无法自动恢复（死代码）。
修复：scheduler 对 RUNNING 与 PAUSED 引擎都驱动 _run_risk_gate（strategy 模式
走 process_candle，其开头就是 _run_risk_gate），让冷却后的引擎自愈。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.engine import BotState
from app.bot.scheduler import BotScheduler


def _make_engine(state=BotState.RUNNING):
    eng = MagicMock()
    eng.state = state
    eng.symbol = "GOLD"
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # scheduler 读 trading_mode：无缓存 → 走 settings
    eng.redis = redis
    eng._run_risk_gate = AsyncMock(return_value=False)
    eng._detect_regime = AsyncMock()
    eng.process_candle = AsyncMock()
    return eng


def _make_scheduler(engine) -> BotScheduler:
    # 用 legacy 模式（直接传 engine）：BotScheduler 会包装为单引擎调度器。
    # 传 MagicMock manager 会因 isinstance(BotManager) 检查失败而误走 legacy。
    return BotScheduler(engine)


@pytest.mark.asyncio
@patch("app.bot.scheduler.is_market_open", return_value=True)
@patch("app.bot.scheduler.settings")
async def test_ai_autonomous_paused_engine_runs_risk_gate(mock_settings, mock_market):
    """ai_autonomous 下 PAUSED 引擎也必须被驱动 _run_risk_gate（否则恢复逻辑是死代码）。"""
    mock_settings.trading_mode = "ai_autonomous"
    engine = _make_engine(state=BotState.PAUSED)
    scheduler = _make_scheduler(engine)
    with patch.object(scheduler, "_run_ai_agent", new=AsyncMock()):
        await scheduler._candle_job(["GOLD"])
    engine._run_risk_gate.assert_awaited_once()
    engine._detect_regime.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.bot.scheduler.is_market_open", return_value=True)
@patch("app.bot.scheduler.settings")
async def test_ai_autonomous_running_engine_runs_risk_gate(mock_settings, mock_market):
    """RUNNING 引擎原有行为不变（ai_autonomous 下仍驱动风控回路）。"""
    mock_settings.trading_mode = "ai_autonomous"
    engine = _make_engine(state=BotState.RUNNING)
    scheduler = _make_scheduler(engine)
    with patch.object(scheduler, "_run_ai_agent", new=AsyncMock()):
        await scheduler._candle_job(["GOLD"])
    engine._run_risk_gate.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.bot.scheduler.is_market_open", return_value=True)
@patch("app.bot.scheduler.settings")
async def test_strategy_paused_engine_runs_process_candle(mock_settings, mock_market):
    """strategy 模式下 PAUSED 引擎仍被驱动 process_candle（开头即 _run_risk_gate，
    冷却期满可自愈，未恢复则 process_candle 内部 return）。"""
    mock_settings.trading_mode = "strategy"
    engine = _make_engine(state=BotState.PAUSED)
    scheduler = _make_scheduler(engine)
    with patch.object(scheduler, "_run_ai_agent", new=AsyncMock()):
        await scheduler._candle_job(["GOLD"])
    engine.process_candle.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.bot.scheduler.is_market_open", return_value=True)
@patch("app.bot.scheduler.settings")
async def test_stopped_engine_not_driven(mock_settings, mock_market):
    """STOPPED 引擎不参与风控状态机（用户已停用，不应被驱动）。"""
    mock_settings.trading_mode = "ai_autonomous"
    engine = _make_engine(state=BotState.STOPPED)
    scheduler = _make_scheduler(engine)
    with patch.object(scheduler, "_run_ai_agent", new=AsyncMock()):
        await scheduler._candle_job(["GOLD"])
    engine._run_risk_gate.assert_not_awaited()
    engine.process_candle.assert_not_awaited()