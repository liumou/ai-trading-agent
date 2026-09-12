"""
单元测试 — mcp_server/agents/openai_loop.py（OpenAI 兼容工具循环）。

全部 mock，不连网、不起真实 MCP 进程。覆盖：文本响应、单/多工具调用、
白名单双向过滤、tool_call_id 配对、每循环下单上限、rollout 限制、
max_turns/超时终止、异常隔离。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.agents.openai_loop import openai_agent_loop

# ─── 假对象工厂 ──────────────────────────────────────────────────────────────


def _fake_tool(name: str, description: str = "", schema: dict | None = None):
    """模拟 FastMCP Tool 对象（list_tools 的返回元素）。"""
    return SimpleNamespace(
        name=name,
        description=description or f"tool {name}",
        inputSchema=schema or {"type": "object", "properties": {}},
    )


def _fake_server(tools, results):
    """构造假 FastMCP server：list_tools 返回 tools，call_tool 按 name 取 results。"""
    server = MagicMock()
    server.list_tools = AsyncMock(return_value=tools)
    server.call_tool = AsyncMock(side_effect=lambda name, args: results.get(name, [SimpleNamespace(text="ok")]))
    return server


def _fake_chat_response(content=None, tool_calls=None):
    """构造 OpenAI ChatCompletion 形状的假响应。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _fake_tool_call(call_id: str, name: str, arguments: str = "{}"):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments))


def _patch_env(server, responses):
    """patch get_server + AsyncOpenAI。responses 是 create 的 side_effect 序列。

    注意 usage patch 必须打在**源模块** app.ai.usage_logger.log_ai_usage：
    openai_agent_loop 在函数体内 `from app.ai.usage_logger import log_ai_usage`，
    每次调用都从源模块重新取绑定 —— patch 模块属性 openai_loop.log_ai_usage 是空操作。
    返回 mock 供断言 usage 记录参数（model/tokens/success，AC-6）。
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=responses)
    usage_mock = AsyncMock()
    patches = [
        patch("mcp_server.server.get_server", return_value=server),
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("app.ai.usage_logger.log_ai_usage", usage_mock),
    ]
    return patches, mock_client, usage_mock


# ─── 测试 ────────────────────────────────────────────────────────────────────


class TestOpenAIAgentLoop:
    @pytest.mark.asyncio
    async def test_plain_text_response(self):
        """无 tool_calls → 直接返回文本，单轮结束。"""
        server = _fake_server([_fake_tool("get_tick")], {})
        patches, _, usage_mock = _patch_env(server, [_fake_chat_response(content="HOLD recommended")])
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="analyze", tool_names=["get_tick"], agent_id="test"
            )
        assert result["response"] == "HOLD recommended"
        assert result["turns"] == 1
        assert result["tool_calls"] == []
        # usage 必须记录到源模块（AC-6：patch 打在 app.ai.usage_logger 上才有效）
        usage_mock.assert_awaited_once()
        kwargs = usage_mock.call_args.kwargs
        assert kwargs["agent_id"] == "test"
        assert kwargs["usage"]["prompt_tokens"] == 10
        assert kwargs["usage"]["completion_tokens"] == 5
        assert kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_single_tool_call_then_final(self):
        """首轮工具调用 → 执行 → 次轮文本结束；tool 消息与 tool_call_id 配对。"""
        server = _fake_server(
            [_fake_tool("get_tick")],
            {"get_tick": [SimpleNamespace(text='{"bid": 2000.0}')]},
        )
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("call_1", "get_tick", "{}")]),
            _fake_chat_response(content="done"),
        ]
        patches, mock_client, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="analyze", tool_names=["get_tick"], agent_id="test"
            )
        assert result["response"] == "done"
        assert result["turns"] == 2
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool"] == "get_tick"
        assert result["tool_calls"][0]["executed"] is True  # 真实执行的调用必须标记 executed
        # 校验传给 OpenAI 的第二轮 messages 含配对 tool 消息
        second_call_messages = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_each_paired(self):
        """并行多个 tool_calls → 每个都产生对应 tool 消息（顺序一致）。"""
        server = _fake_server(
            [_fake_tool("get_tick"), _fake_tool("get_ohlcv")],
            {
                "get_tick": [SimpleNamespace(text="tick")],
                "get_ohlcv": [SimpleNamespace(text="ohlcv")],
            },
        )
        responses = [
            _fake_chat_response(
                tool_calls=[
                    _fake_tool_call("c1", "get_tick"),
                    _fake_tool_call("c2", "get_ohlcv"),
                ]
            ),
            _fake_chat_response(content="done"),
        ]
        patches, mock_client, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick", "get_ohlcv"], agent_id="test"
            )
        # 第二次请求的 messages 中，两条 tool 消息 id 顺序为 c1, c2
        msgs = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_whitelist_blocks_out_of_scope_tool(self):
        """模型调用白名单外工具 → 拒绝执行，call_tool 不被调用。"""
        server = _fake_server([_fake_tool("get_tick"), _fake_tool("place_order")], {})
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("c1", "place_order", "{}")]),
            _fake_chat_response(content="blocked"),
        ]
        patches, _, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick"], agent_id="technical_analyst"
            )
        # place_order 不在白名单，server.call_tool 不应被调用
        server.call_tool.assert_not_called()
        # 审计记录：拦截而非执行（H3）
        assert result["tool_calls"][0]["blocked"] == "whitelist"
        assert result["tool_calls"][0].get("executed") is not True

    @pytest.mark.asyncio
    async def test_max_orders_per_loop_blocks_repeat(self):
        """交易工具执行数达上限后，后续 place_order 被拒绝（MAX_ORDERS_PER_LOOP=1）。"""
        server = _fake_server(
            [_fake_tool("place_order")],
            {"place_order": [SimpleNamespace(text='{"executed": true}')]},
        )
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("c1", "place_order", '{"symbol":"GOLD"}')]),
            _fake_chat_response(tool_calls=[_fake_tool_call("c2", "place_order", '{"symbol":"GOLD"}')]),
            _fake_chat_response(content="stopped"),
        ]
        patches, _, _ = _patch_env(server, responses)
        with (
            patches[0],
            patches[1],
            patches[2],
            patch("app.config.settings.llm_max_orders_per_loop", 1),
            patch("app.config.settings.llm_allow_live", True),
            patch("mcp_server.agents.openai_loop._rollout_allows_trade", AsyncMock(return_value=(True, "paper"))),
        ):
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["place_order"], agent_id="orchestrator"
            )
        # 只执行一次 place_order（第二次被上限拦截）
        assert server.call_tool.call_count == 1
        assert len(result["tool_calls"]) == 2  # 两次尝试都记录
        # 审计日志必须区分：真实执行 1 次 + 被拦截 1 次（H3）
        executed = [tc for tc in result["tool_calls"] if tc.get("executed") is True]
        blocked = [tc for tc in result["tool_calls"] if tc.get("blocked") == "limit"]
        assert len(executed) == 1
        assert len(blocked) == 1

    @pytest.mark.asyncio
    async def test_rollout_blocks_trade_in_live_without_optin(self):
        """rollout=live 且 llm_allow_live=false → 交易工具被 AC-11 拦截。"""
        server = _fake_server([_fake_tool("place_order")], {})
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("c1", "place_order", "{}")]),
            _fake_chat_response(content="blocked by rollout"),
        ]
        patches, _, _ = _patch_env(server, responses)
        with (
            patches[0],
            patches[1],
            patches[2],
            patch("app.config.settings.llm_allow_live", False),
            patch(
                "mcp_server.agents.openai_loop._rollout_allows_trade", AsyncMock(return_value=(False, "rollout=live"))
            ),
        ):
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["place_order"], agent_id="orchestrator"
            )
        server.call_tool.assert_not_called()
        # 审计记录：rollout 拦截而非执行（H3）
        assert result["tool_calls"][0]["blocked"] == "rollout"

    @pytest.mark.asyncio
    async def test_max_turns_terminates(self):
        """模型持续返回 tool_calls → 达 max_turns 后终止。"""
        server = _fake_server([_fake_tool("get_tick")], {"get_tick": [SimpleNamespace(text="t")]})
        responses = [_fake_chat_response(tool_calls=[_fake_tool_call(f"c{i}", "get_tick")]) for i in range(10)]
        patches, _, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick"], max_turns=3, agent_id="test"
            )
        assert result["turns"] == 3
        assert "terminated" in result["response"].lower() or result.get("error")

    @pytest.mark.asyncio
    async def test_tool_error_fed_back_not_raised(self):
        """工具执行抛异常 → 错误文本回喂模型，循环不崩溃。"""
        server = MagicMock()
        server.list_tools = AsyncMock(return_value=[_fake_tool("get_tick")])
        server.call_tool = AsyncMock(side_effect=Exception("boom"))
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("c1", "get_tick")]),
            _fake_chat_response(content="recovered"),
        ]
        patches, mock_client, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick"], agent_id="test"
            )
        assert result["response"] == "recovered"
        # 第二轮 messages 的 tool 消息应含错误文本
        msgs = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert "error" in tool_msgs[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_invalid_tool_arguments_fed_back(self):
        """模型给出非法 JSON 参数 → 错误回喂而非崩溃。"""
        server = _fake_server([_fake_tool("get_tick")], {})
        responses = [
            _fake_chat_response(tool_calls=[_fake_tool_call("c1", "get_tick", "{bad json")]),
            _fake_chat_response(content="ok"),
        ]
        patches, _, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick"], agent_id="test"
            )
        assert result["response"] == "ok"
        server.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_terminates(self):
        """累计超时 → 终止并标记 error。"""
        server = _fake_server([_fake_tool("get_tick")], {"get_tick": [SimpleNamespace(text="t")]})
        responses = [_fake_chat_response(tool_calls=[_fake_tool_call(f"c{i}", "get_tick")]) for i in range(10)]
        patches, _, _ = _patch_env(server, responses)
        # timeout=0 使首轮即超时
        with patches[0], patches[1], patches[2]:
            result = await openai_agent_loop(
                system_prompt="sys", user_message="a", tool_names=["get_tick"], timeout=0, agent_id="test"
            )
        assert result["turns"] == 0
        assert result.get("error")

    @pytest.mark.asyncio
    async def test_specialist_whitelist_excludes_execution_tools(self):
        """通过 list_tools 只暴露白名单内的工具 schema（双向过滤的 schema 层）。"""
        server = _fake_server(
            [_fake_tool("get_tick"), _fake_tool("run_full_analysis"), _fake_tool("place_order")],
            {},
        )
        responses = [_fake_chat_response(content="done")]
        patches, mock_client, _ = _patch_env(server, responses)
        with patches[0], patches[1], patches[2]:
            await openai_agent_loop(
                system_prompt="sys",
                user_message="a",
                tool_names=["get_tick", "run_full_analysis"],
                agent_id="technical_analyst",
            )
        sent_tools = mock_client.chat.completions.create.call_args_list[0].kwargs["tools"]
        sent_names = {t["function"]["name"] for t in sent_tools}
        assert "place_order" not in sent_names
        assert sent_names == {"get_tick", "run_full_analysis"}
