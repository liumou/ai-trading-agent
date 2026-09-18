"""Runtime contract tests, always mocked: never call providers or broker."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

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


# ─── 指数退避重试（2026-09-18）───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_retried_then_succeeds(harness, monkeypatch):
    """APITimeoutError 首次抛出 → 指数退避重试 → 成功。

    验证：重试间隔按次数递增（base=0.05 → 0.1 → 0.2），且 model 被调用 2 次。
    """
    from openai import APITimeoutError

    client, server, events, run = harness
    # 首抛超时，第二次成功
    client.chat.completions.create.side_effect = [APITimeoutError("slow"), reply()]
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=lambda s: sleeps.append(s)))
    with patch("app.config.settings.llm_retry_base_s", 0.05), \
         patch("app.config.settings.llm_retry_max_s", 0.2):
        result = await run(budget={"max_retries": 2})
    assert result["status"] == "completed"
    assert result["response"] == "report"
    assert client.chat.completions.create.await_count == 2
    # 第 1 次失败后等待 0.05s（attempt=0），间隔递增生效
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.05, abs=0.01)
    # 有 provider_retry 审计事件
    assert any(k == "provider_retry" for k, _ in events)


@pytest.mark.asyncio
async def test_request_timeout_retried_then_succeeds(harness, monkeypatch):
    """单请求 request_timeout 超时（RuntimeStop llm_request_timeout）也可重试。"""
    client, _, events, run = harness

    async def slow_then_ok(**kw):
        if client.chat.completions.create.call_count == 1:
            await asyncio.sleep(10)  # 首次真实 sleep，被 request_timeout_s 掐断
        return reply()

    client.chat.completions.create.side_effect = slow_then_ok
    # 不 mock asyncio.sleep：重试分支的 delay 很小（0.005s），可真实等待
    with patch("app.config.settings.llm_retry_base_s", 0.005), \
         patch("app.config.settings.llm_retry_max_s", 0.01):
        result = await run(budget={"max_retries": 1, "request_timeout_s": 0.02})
    assert result["status"] == "completed"
    assert client.chat.completions.create.await_count == 2
    assert any(k == "provider_retry" for k, _ in events)


@pytest.mark.asyncio
async def test_retry_exhausted_still_fails(harness, monkeypatch):
    """重试次数耗尽后仍失败 → 结构化失败，不无限重试。"""
    from openai import APITimeoutError

    client, _, events, run = harness
    client.chat.completions.create.side_effect = APITimeoutError("slow always")
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("app.config.settings.llm_retry_base_s", 0.01), \
         patch("app.config.settings.llm_retry_max_s", 0.02):
        result = await run(budget={"max_retries": 2})
    assert result["status"] != "completed"
    # 3 次尝试（1 初 + 2 重试）
    assert client.chat.completions.create.await_count == 3
    # 重试事件有 2 个
    assert sum(1 for k, _ in events if k == "provider_retry") == 2


@pytest.mark.asyncio
async def test_retry_zero_does_not_retry(harness):
    """max_retries=0 → 超时直接失败，不重试。"""
    from openai import APITimeoutError

    client, _, events, run = harness
    client.chat.completions.create.side_effect = APITimeoutError("slow")
    result = await run(budget={"max_retries": 0})
    assert result["status"] == "timed_out"
    assert client.chat.completions.create.await_count == 1
    assert not any(k == "provider_retry" for k, _ in events)


@pytest.mark.asyncio
async def test_retry_skipped_when_budget_exhausted(harness, monkeypatch):
    """剩余预算不足下一次退避间隔 → 放弃重试直接失败（不把分析无限拉长）。"""
    from openai import APITimeoutError

    client, _, events, run = harness
    client.chat.completions.create.side_effect = APITimeoutError("slow")
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    # 小总预算 + 大退避间隔：第一次失败后剩余已不够退避 → 放弃重试
    with patch("app.config.settings.llm_retry_base_s", 5), \
         patch("app.config.settings.llm_retry_max_s", 10):
        result = await run(budget={"max_retries": 2, "total_timeout_s": 0.03})
    assert result["status"] == "timed_out"
    # 保留触发原因标签（retry_budget_exhausted:timeout），不掩盖为 total_timeout
    assert result["reason_code"] == "retry_budget_exhausted:timeout"
    assert client.chat.completions.create.await_count == 1
    assert not any(k == "provider_retry" for k, _ in events)
