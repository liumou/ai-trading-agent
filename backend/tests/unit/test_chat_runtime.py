"""Runtime contract tests, always mocked: never call providers or broker."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from mcp_server.agents import chat_runtime as rt


def reply(text="report", calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls or []), finish_reason="stop")], usage=None)


def call(name="get_tick", ident="t1"):
    return NS(id=ident, function=NS(name=name, arguments='{"symbol":"GOLD"}'))


@pytest.fixture
def harness(monkeypatch):
    client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=reply()))), close=AsyncMock())
    server = NS(list_tools=AsyncMock(return_value=[NS(name="get_tick", description="tick", inputSchema={"type":"object"})]),
                call_tool=AsyncMock(return_value={"price": 3000}))
    monkeypatch.setattr(rt, "_openai_client", lambda: client)
    monkeypatch.setattr(rt, "_server", lambda: server)
    events = []
    async def emit(kind, payload):
        events.append((kind, payload.copy()))
    async def run(**overrides):
        budget = {"total_timeout_s": 2, "request_timeout_s": 1, "tool_timeout_s": .5, "max_turns": 3, "max_retries": 0}
        budget.update(overrides.pop("budget", {}))
        return await rt.run_chat_runtime(system_prompt="readonly", user_message="plan", tool_names=["get_tick"],
            agent_id="chat_agent", model="offline", provider="openai_compat", budget=budget,
            emit=overrides.pop("emit", emit), **overrides)
    return client, server, events, run


@pytest.mark.asyncio
async def test_public_entry_completes(harness):
    client, server, events, run = harness
    result = await run()
    assert result["status"] == "completed" and result["response"] == "report"
    assert result["turns"] == 1
    assert "assistant_text" in [e[0] for e in events]
    server.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_partial_text_and_tool_result_preserved(harness):
    client, server, events, run = harness
    client.chat.completions.create.side_effect = [reply("checking data", [call()]), reply("final")]
    result = await run()
    assert result["status"] == "completed"
    assert "checking data" in result["partial_response"]
    assert result["response"] == "final"
    tool = next(p for k,p in events if k == "tool_finished")
    assert tool["tool_call_id"] == "t1" and "3000" in tool["output"]
    assert tool["duration_s"] >= 0


@pytest.mark.asyncio
async def test_total_timeout_interrupts_model(harness):
    client, server, events, run = harness
    cancelled = asyncio.Event()
    async def blocked(**kw):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    client.chat.completions.create.side_effect = blocked
    result = await run(budget={"total_timeout_s": .03, "request_timeout_s": 1})
    assert result["status"] == "timed_out"
    assert result["reason_code"] == "total_timeout" and cancelled.is_set()
    assert not result["response"]


@pytest.mark.asyncio
async def test_request_timeout_distinct(harness):
    client, _, _, run = harness
    async def blocked(**kw):
        await asyncio.sleep(10)
    client.chat.completions.create.side_effect = blocked
    result = await run(budget={"request_timeout_s": .02})
    assert result["reason_code"] == "llm_request_timeout"


@pytest.mark.asyncio
async def test_max_turns_no_execution_on_summary_turn(harness):
    client, server, events, run = harness
    client.chat.completions.create.return_value = reply("still collecting", [call()])
    result = await run(budget={"max_turns": 2})
    assert result["status"] == "incomplete" and result["reason_code"] == "max_turns"
    assert server.call_tool.await_count == 1
    assert client.chat.completions.create.call_args.kwargs["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_tool_timeout_preserves_partial(harness):
    client, server, events, run = harness
    async def blocked(*args):
        await asyncio.sleep(10)
    server.call_tool.side_effect = blocked
    client.chat.completions.create.side_effect = [reply("partial", [call()]), reply("missing data")]
    result = await run(budget={"tool_timeout_s": .02})
    assert result["status"] != "completed" and result["reason_code"] == "tool_timeout"
    assert "partial" in result["partial_response"]
    assert any(k == "tool_finished" and p["reason_code"] == "tool_timeout" for k,p in events)


@pytest.mark.asyncio
async def test_model_cannot_call_write_tool(harness):
    client, server, events, run = harness
    client.chat.completions.create.side_effect = [reply("", [call("place_order")]), reply("denied")]
    result = await run()
    assert result["reason_code"] == "tool_not_allowed"
    server.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_audit_error_prevents_model(harness):
    client, server, events, run = harness
    result = await run(emit=AsyncMock(side_effect=RuntimeError("offline audit")))
    assert result["reason_code"] == "audit_error"
    client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_empty_choices_failed(harness):
    client, _, _, run = harness
    client.chat.completions.create.return_value = NS(choices=[], usage=None)
    assert (await run())["reason_code"] == "empty_response"
