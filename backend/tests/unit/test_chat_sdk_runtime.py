"""Offline contract tests: no provider subprocess, broker, database or network IO."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mcp_server.agents.chat_runtime import _Run
from mcp_server.agents import chat_sdk_runtime as sdk


def result(**kwargs):
    values = dict(subtype="success", duration_ms=1, duration_api_ms=1,
                  is_error=False, num_turns=1, session_id="mock", usage={"input_tokens": 3})
    return ResultMessage(**{**values, **kwargs})


@pytest.fixture
def run():
    state = _Run("chat_agent", "mock-model", "claude", {}, AsyncMock())
    state.server = SimpleNamespace(list_tools=AsyncMock(return_value=[]), call_tool=AsyncMock())
    return state


async def test_minimal_sdk_contract_no_network(monkeypatch, run):
    async def fake_query(**kwargs):
        assert kwargs["options"].tools == []
        assert kwargs["options"].permission_mode == "default"
        assert kwargs["options"].setting_sources == []
        yield AssistantMessage(content=[TextBlock("Public answer")], model="mock-model")
        yield result()
    monkeypatch.setattr(sdk, "query", fake_query)
    assert await sdk.run_sdk(run, "system", "question", "mock-model") == ("completed", None)
    assert run.response == "Public answer"
    assert run.usage == {"input_tokens": 3}
    run.server.call_tool.assert_not_called()
