"""
Unit tests for mcp_server/agents/ — multi-agent architecture.
Tests with mocked Claude Agent SDK.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_server.agents.base import MODEL_ORCHESTRATOR, MODEL_SPECIALIST


class TestToolSubsets:
    def test_all_specialist_tools_are_strings(self):
        """Verify specialist TOOL_NAMES are valid string lists (SDK filters by name)."""
        from mcp_server.agents.fundamental_analyst import TOOL_NAMES as fund
        from mcp_server.agents.orchestrator import ORCHESTRATOR_TOOL_NAMES as orch
        from mcp_server.agents.risk_analyst import TOOL_NAMES as risk_t
        from mcp_server.agents.technical_analyst import TOOL_NAMES as tech

        for names in [tech, fund, risk_t, orch]:
            assert len(names) > 0
            for n in names:
                assert isinstance(n, str), f"Tool name must be str, got {type(n)}"

    def test_technical_no_execution(self):
        from mcp_server.agents.technical_analyst import TOOL_NAMES

        assert not {"place_order", "modify_position", "close_position"} & set(TOOL_NAMES)

    def test_fundamental_no_execution(self):
        from mcp_server.agents.fundamental_analyst import TOOL_NAMES

        assert not {"place_order", "modify_position", "close_position"} & set(TOOL_NAMES)

    def test_risk_no_execution(self):
        from mcp_server.agents.risk_analyst import TOOL_NAMES

        assert not {"place_order", "modify_position", "close_position"} & set(TOOL_NAMES)

    def test_orchestrator_has_execution(self):
        from mcp_server.agents.orchestrator import ORCHESTRATOR_TOOL_NAMES

        assert "place_order" in ORCHESTRATOR_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_agent_tool_names_never_drift_from_registered_tools(self):
        """AC-12 不变式：所有 agent 的 TOOL_NAMES 必须 ⊆ 真实 list_tools() 名称集。

        防止「agent 引用未注册工具」漂移再次发生（reflector 的 memory 三工具
        历史上就漏注册过）。新增 agent/工具时此测试自动守护。
        """
        from mcp_server.agents.fundamental_analyst import TOOL_NAMES as fund
        from mcp_server.agents.orchestrator import ORCHESTRATOR_TOOL_NAMES as orch
        from mcp_server.agents.reflector import TOOL_NAMES as reflector
        from mcp_server.agents.risk_analyst import TOOL_NAMES as risk_t
        from mcp_server.agents.technical_analyst import TOOL_NAMES as tech
        from mcp_server.server import get_server

        registered = {t.name for t in await get_server().list_tools()}
        all_agent_tools = set(tech) | set(fund) | set(risk_t) | set(reflector) | set(orch)
        missing = all_agent_tools - registered
        assert not missing, f"agent TOOL_NAMES 引用了未注册工具: {missing}"
        # 交易执行工具必须在册（orchestrator 白名单的前提）
        assert {"place_order", "modify_position", "close_position"} <= registered


class TestModelSelection:
    def test_specialist_matches_settings_default(self):
        """MODEL_SPECIALIST 常量应等于 settings.model_specialist 的默认值（而非裸模型名断言）。"""
        from app.config import settings

        assert MODEL_SPECIALIST == (settings.model_specialist or MODEL_SPECIALIST)

    def test_orchestrator_matches_settings_default(self):
        from app.config import settings

        assert MODEL_ORCHESTRATOR == (settings.model_orchestrator or MODEL_ORCHESTRATOR)

    @pytest.mark.asyncio
    async def test_run_agent_loop_resolves_model_from_settings(self):
        """model 参数缺省时按 agent_id 解析到 settings 的 per-agent 模型。"""
        from unittest.mock import AsyncMock, patch

        from mcp_server.agents.base import run_agent_loop

        with patch("mcp_server.agents.base.sdk_agent_loop", AsyncMock(return_value={})) as mock_loop:
            # model=None（默认）→ 解析到 settings.model_specialist（非 orchestrator agent）
            await run_agent_loop(system_prompt="s", user_message="u", agent_id="technical_analyst")
        # sdk_agent_loop 收到的 model 应等于默认 specialist 模型
        assert mock_loop.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_single_agent_uses_orchestrator_grade_model(self):
        """零回归（H1）：single_agent 模式默认模型必须与旧行为一致 = orchestrator 档
        （settings.model_orchestrator），而非被静默降级为 specialist 档。"""
        from unittest.mock import AsyncMock, patch

        from mcp_server.agents.base import run_agent_loop

        with patch("mcp_server.agents.base.sdk_agent_loop", AsyncMock(return_value={})) as mock_loop:
            await run_agent_loop(system_prompt="s", user_message="u", agent_id="single_agent")
        assert mock_loop.call_args.kwargs["model"] == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_orchestrator_uses_orchestrator_grade_model(self):
        """multi-agent 的 orchestrator 同样解析到 orchestrator 档模型。"""
        from unittest.mock import AsyncMock, patch

        from mcp_server.agents.base import run_agent_loop

        with patch("mcp_server.agents.base.sdk_agent_loop", AsyncMock(return_value={})) as mock_loop:
            await run_agent_loop(system_prompt="s", user_message="u", agent_id="orchestrator")
        assert mock_loop.call_args.kwargs["model"] == "claude-sonnet-4-20250514"


class TestBaseAgentLoop:
    @pytest.mark.asyncio
    async def test_handles_text_response(self):
        from mcp_server.agents.base import run_agent_loop

        mock_result = {
            "response": "HOLD recommended",
            "tool_calls": [],
            "turns": 1,
            "duration_s": 2.0,
        }

        with patch("mcp_server.agents.base.sdk_agent_loop", AsyncMock(return_value=mock_result)):
            result = await run_agent_loop(system_prompt="test", user_message="Analyze")

        assert "HOLD recommended" in result["response"]
        assert result["turns"] == 1

    @pytest.mark.asyncio
    async def test_handles_sdk_error(self):
        from mcp_server.agents.base import run_agent_loop

        mock_result = {
            "response": "Agent error: rate limited",
            "tool_calls": [],
            "turns": 0,
            "duration_s": 0.1,
            "error": "rate limited",
        }

        with patch("mcp_server.agents.base.sdk_agent_loop", AsyncMock(return_value=mock_result)):
            result = await run_agent_loop(system_prompt="test", user_message="test")

        assert "error" in result


class TestOrchestratorSynthesis:
    def test_build_synthesis_message(self):
        from mcp_server.agents.orchestrator import _build_synthesis_message

        msg = _build_synthesis_message(
            job_type="candle_analysis",
            job_input={"symbol": "GOLD"},
            symbol="GOLD",
            timeframe="M15",
            technical_report="bullish",
            fundamental_report="neutral",
            risk_report="approved",
        )
        assert "Technical Analysis" in msg
        assert "bullish" in msg

    def test_synthesis_with_reflection(self):
        from mcp_server.agents.orchestrator import _build_synthesis_message

        msg = _build_synthesis_message(
            job_type="candle_analysis",
            job_input={"symbol": "GOLD"},
            symbol="GOLD",
            timeframe="M15",
            technical_report="b",
            fundamental_report="n",
            risk_report="a",
            reflection_report="Win rate 70%",
        )
        assert "Reflection" in msg
        assert "Win rate 70%" in msg


class TestMultiAgentIntegration:
    @pytest.mark.asyncio
    async def test_orchestrator_full_pipeline(self):
        from mcp_server.agents.orchestrator import run_multi_agent

        mock_result = {"response": "analysis done", "tool_calls": [], "turns": 2}

        with (
            patch(
                "mcp_server.agents.orchestrator.reflector.reflect",
                AsyncMock(return_value={"response": "65% win rate", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.technical_analyst.analyze",
                AsyncMock(return_value={"response": "bullish", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.fundamental_analyst.analyze",
                AsyncMock(return_value={"response": "neutral", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.risk_analyst.analyze",
                AsyncMock(return_value={"response": "approved", "tool_calls": [], "turns": 1}),
            ),
            patch("mcp_server.agents.orchestrator.run_agent_loop", AsyncMock(return_value=mock_result)),
        ):
            result = await run_multi_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})

        assert "decision" in result
        assert "specialists" in result

    @pytest.mark.asyncio
    async def test_specialist_error_graceful(self):
        from mcp_server.agents.orchestrator import run_multi_agent

        with (
            patch(
                "mcp_server.agents.orchestrator.reflector.reflect",
                AsyncMock(return_value={"response": "", "tool_calls": [], "turns": 0}),
            ),
            patch(
                "mcp_server.agents.orchestrator.technical_analyst.analyze", AsyncMock(side_effect=Exception("timeout"))
            ),
            patch(
                "mcp_server.agents.orchestrator.fundamental_analyst.analyze",
                AsyncMock(return_value={"response": "n", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.risk_analyst.analyze",
                AsyncMock(return_value={"response": "a", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.run_agent_loop",
                AsyncMock(return_value={"response": "HOLD", "tool_calls": [], "turns": 1, "duration_s": 1}),
            ),
        ):
            result = await run_multi_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})

        assert "decision" in result
        assert result.get("errors") is not None


class TestSpecialistFailureSemantics:
    """An analysis timeout/error must never be presented as a neutral market signal."""

    def test_specialist_failed_detects_fallback_text(self):
        from mcp_server.agents.orchestrator import _specialist_failed

        # The exact fallback the OpenAI loop returns on budget exhaustion.
        failed = {
            "response": "Agent loop terminated (timeout/max_turns)",
            "error": "agent loop failed (timeout or exception)",
            "tool_calls": [],
            "turns": 4,
        }
        assert _specialist_failed(failed) is not None
        # Empty response is a failure too.
        assert _specialist_failed({"response": "", "tool_calls": []}) is not None
        # A real report is not a failure.
        assert _specialist_failed({"response": "bullish 0.7 confidence", "tool_calls": []}) is None

    @pytest.mark.asyncio
    async def test_timeout_specialist_not_reported_as_neutral(self):
        from app.config import settings
        from mcp_server.agents.orchestrator import run_multi_agent

        with (
            patch(
                "mcp_server.agents.orchestrator.reflector.reflect",
                AsyncMock(return_value={"response": "65% win rate", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.technical_analyst.analyze",
                AsyncMock(return_value={
                    "response": "Agent loop terminated (timeout/max_turns)",
                    "error": "agent loop failed (timeout or exception)",
                    "tool_calls": [], "turns": 4,
                }),
            ),
            patch(
                "mcp_server.agents.orchestrator.fundamental_analyst.analyze",
                AsyncMock(return_value={"response": "neutral", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.risk_analyst.analyze",
                AsyncMock(return_value={"response": "approved", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.run_agent_loop",
                AsyncMock(return_value={
                    "response": "HOLD", "tool_calls": [], "turns": 1, "duration_s": 1,
                }),
            ),
        ):
            result = await run_multi_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})

        tech_report = result["specialists"]["technical"]["report"]
        assert "分析未完成" in tech_report          # surfaced as failure
        assert "无任何技术信号" not in tech_report   # NOT treated as a neutral signal
        assert "technical" in (result.get("errors") or {})
        assert result["specialists"]["fundamental"]["report"] == "neutral"

    @pytest.mark.asyncio
    async def test_orchestrator_uses_configured_budget(self):
        from app.config import settings
        from mcp_server.agents.orchestrator import run_multi_agent

        mock_loop = AsyncMock(return_value={"response": "HOLD", "tool_calls": [], "turns": 1, "duration_s": 1})
        with (
            patch(
                "mcp_server.agents.orchestrator.reflector.reflect",
                AsyncMock(return_value={"response": "ok", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.technical_analyst.analyze",
                AsyncMock(return_value={"response": "b", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.fundamental_analyst.analyze",
                AsyncMock(return_value={"response": "n", "tool_calls": [], "turns": 1}),
            ),
            patch(
                "mcp_server.agents.orchestrator.risk_analyst.analyze",
                AsyncMock(return_value={"response": "a", "tool_calls": [], "turns": 1}),
            ),
            patch("mcp_server.agents.orchestrator.run_agent_loop", mock_loop),
        ):
            await run_multi_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})

        kwargs = mock_loop.await_args.kwargs
        assert kwargs["max_turns"] == settings.multi_agent_orchestrator_max_turns
        assert kwargs["timeout"] == settings.multi_agent_orchestrator_timeout_s


class TestSynthesisFailureNote:
    def test_failure_note_present(self):
        from mcp_server.agents.orchestrator import _build_synthesis_message

        msg = _build_synthesis_message(
            job_type="candle_analysis", job_input={"symbol": "GOLD"}, symbol="GOLD",
            timeframe="M15", technical_report="[technical 分析未完成...]",
            fundamental_report="neutral", risk_report="approved",
            failed_specialists={"technical"},
        )
        assert "IMPORTANT: analyst failures" in msg
        assert "technical" in msg
        assert "Do NOT interpret a failed analysis as a neutral" in msg

    def test_no_failure_note_when_all_succeeded(self):
        from mcp_server.agents.orchestrator import _build_synthesis_message

        msg = _build_synthesis_message(
            job_type="candle_analysis", job_input={"symbol": "GOLD"}, symbol="GOLD",
            timeframe="M15", technical_report="b", fundamental_report="n", risk_report="a",
        )
        assert "IMPORTANT: analyst failures" not in msg
