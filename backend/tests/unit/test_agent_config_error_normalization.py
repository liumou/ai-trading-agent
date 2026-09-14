"""Agent 失败归一化测试 —— "Agent error: ..." 不得冒充真实决策（2026-09-14 事件回归守护）。"""

from unittest.mock import AsyncMock, patch

import pytest


class TestRunAgentErrorNormalization:
    @pytest.mark.asyncio
    async def test_agent_error_normalized_to_hold(self):
        """openai_loop/sdk 返回 "Agent error: Connection error." 时：
        decision 归一化为 HOLD (AI unavailable)，错误单独透传到 ai_error。"""
        from mcp_server.agent_config import run_agent

        loop_result = {
            "response": "Agent error: Connection error.",
            "tool_calls": [],
            "turns": 0,
            "duration_s": 0.1,
            "error": "Connection error.",
        }
        with (
            patch("mcp_server.agent_config.run_agent_loop", AsyncMock(return_value=loop_result)),
            patch("mcp_server.agents.prompt_registry.get_active_prompt", AsyncMock(return_value="sys")),
        ):
            result = await run_agent("candle_analysis", {"symbol": "GOLD"})

        assert result["decision"] == "HOLD (AI unavailable)"
        assert result["ai_error"] == "Connection error."

    @pytest.mark.asyncio
    async def test_agent_error_prefix_parsed_without_error_field(self):
        """仅有 response 前缀、无 error 字段时也能提取原因。"""
        from mcp_server.agent_config import run_agent

        loop_result = {
            "response": "Agent error: LLM timeout",
            "tool_calls": [],
            "turns": 0,
            "duration_s": 0.1,
        }
        with (
            patch("mcp_server.agent_config.run_agent_loop", AsyncMock(return_value=loop_result)),
            patch("mcp_server.agents.prompt_registry.get_active_prompt", AsyncMock(return_value="sys")),
        ):
            result = await run_agent("candle_analysis", {"symbol": "GOLD"})

        assert result["decision"] == "HOLD (AI unavailable)"
        assert result["ai_error"] == "LLM timeout"

    @pytest.mark.asyncio
    async def test_normal_decision_passthrough(self):
        """正常决策不受影响：原样透传，ai_error 为 None。"""
        from mcp_server.agent_config import run_agent

        ok_result = {
            "response": "BUY with confidence 0.7",
            "tool_calls": [],
            "turns": 1,
            "duration_s": 2.0,
        }
        with (
            patch("mcp_server.agent_config.run_agent_loop", AsyncMock(return_value=ok_result)),
            patch("mcp_server.agents.prompt_registry.get_active_prompt", AsyncMock(return_value="sys")),
        ):
            result = await run_agent("candle_analysis", {"symbol": "GOLD"})

        assert result["decision"] == "BUY with confidence 0.7"
        assert result.get("ai_error") is None


class TestRunMultiAgentErrorNormalization:
    @pytest.mark.asyncio
    async def test_orchestrator_agent_error_normalized(self):
        """multi-agent：orchestrator 的 LLM 失败同样归一化。"""
        from mcp_server.agent_config import run_multi_agent

        with patch(
            "mcp_server.agents.orchestrator.run_multi_agent",
            AsyncMock(
                return_value={
                    "decision": "Agent error: Connection error.",
                    "specialists": {},
                    "errors": None,
                }
            ),
        ):
            result = await run_multi_agent("candle_analysis", {"symbol": "GOLD"})

        assert result["decision"] == "HOLD (AI unavailable)"
        assert result["ai_error"] == "Connection error."