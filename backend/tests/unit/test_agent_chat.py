"""
Unit tests for Agent Chat — 对话式交易计划/报告（只读，不可交易）。

覆盖：
1. 安全红线：chat 工具白名单不含任何执行类工具
2. _build_user_message：历史拼接、裁剪、preset 模板
3. run_chat_turn：分发与参数
4. 路由：会话 CRUD、消息往返（mock chat agent）、持久化
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mcp_server.agents.chat_agent import (
    CHAT_TOOL_NAMES,
    MAX_HISTORY_TURNS,
    PRESET_PROMPTS,
    _build_user_message,
    run_chat_turn,
)

# ─── 安全红线 ────────────────────────────────────────────────────────────────


class TestChatAgentSafety:
    EXECUTION_TOOLS = {"place_order", "modify_position", "close_position"}
    WRITE_TOOLS = EXECUTION_TOOLS | {"log_decision", "log_reasoning", "apply_strategy",
                                     "save_memory", "save_learning", "save_context",
                                     "validate_memory", "recommend_strategy"}

    def test_no_execution_tools(self):
        """对话 Agent 绝不能带执行类工具（机制级红线）。"""
        assert not self.EXECUTION_TOOLS & set(CHAT_TOOL_NAMES)

    def test_no_write_tools(self):
        """对话 Agent 不带任何写操作工具（只读分析）。"""
        assert not self.WRITE_TOOLS & set(CHAT_TOOL_NAMES)

    def test_whitelist_nonempty_and_str(self):
        assert len(CHAT_TOOL_NAMES) > 0
        for n in CHAT_TOOL_NAMES:
            assert isinstance(n, str)

    def test_has_core_read_tools(self):
        for tool in ("run_full_analysis", "get_account", "get_ohlcv", "detect_regime"):
            assert tool in CHAT_TOOL_NAMES


# ─── 消息构造 ────────────────────────────────────────────────────────────────


class TestBuildUserMessage:
    def test_plain_question(self):
        msg = _build_user_message("GOLD", "M15", "现在能做多吗？", None, None)
        assert "GOLD" in msg and "现在能做多吗？" in msg

    def test_history_included(self):
        history = [
            {"role": "user", "content": "分析 GOLD"},
            {"role": "assistant", "content": "趋势向上"},
        ]
        msg = _build_user_message("GOLD", "M15", "继续", history, None)
        assert "分析 GOLD" in msg and "趋势向上" in msg and "继续" in msg

    def test_history_cropped(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(MAX_HISTORY_TURNS + 10)]
        msg = _build_user_message("GOLD", "M15", "继续", history, None)
        assert "m0" not in msg  # 最旧的被裁掉
        assert f"m{MAX_HISTORY_TURNS + 9}" in msg  # 最新的保留

    def test_preset_overrides_history(self):
        history = [{"role": "user", "content": "旧话题"}]
        msg = _build_user_message("GOLD", "M15", "", history, "trading_plan")
        assert "交易计划" in msg and "GOLD" in msg
        assert "旧话题" not in msg

    def test_preset_template_renders(self):
        assert "{symbol}" not in PRESET_PROMPTS["trading_plan"].format(symbol="GOLD", timeframe="M15")


# ─── run_chat_turn 分发 ──────────────────────────────────────────────────────


class TestRunChatTurn:
    @pytest.mark.asyncio
    async def test_dispatch_with_history(self):
        captured = {}

        async def fake_loop(**kwargs):
            captured.update(kwargs)
            return {"response": "ok", "tool_calls": [], "turns": 1}

        with patch("mcp_server.agents.chat_agent.run_agent_loop", side_effect=fake_loop):
            result = await run_chat_turn(
                symbol="GOLD", timeframe="M15", user_message="你好",
                history=[{"role": "user", "content": "早"}],
            )

        assert result["response"] == "ok"
        assert captured["agent_id"] == "chat_agent"
        assert set(captured["tool_names"]) == set(CHAT_TOOL_NAMES)
        assert "早" in captured["user_message"] and "你好" in captured["user_message"]

    @pytest.mark.asyncio
    async def test_requires_message_or_preset(self):
        with pytest.raises(ValueError):
            await run_chat_turn(symbol="GOLD", user_message="")


# ─── 路由（mock chat agent + 内存 SQLite）────────────────────────────────────


@pytest_asyncio.fixture
async def chat_db(monkeypatch, db_engine):
    """用内存 SQLite 替换路由与 store 的 async_session（绝不触真实 DB）。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    import app.api.routes.agent_chat as mod
    import app.services.chat_runs as store_mod

    monkeypatch.setattr(mod, "async_session", factory)
    monkeypatch.setattr(store_mod, "async_session", factory)

    # 屏蔽护栏（无 Redis 环境）
    async def _noop():
        return None

    monkeypatch.setattr(mod, "_record_agent_call", _noop)
    return mod


@pytest_asyncio.fixture
async def created_session(chat_db):
    from app.db.models import AgentChatSession

    async with chat_db.async_session() as db:
        s = AgentChatSession(title="GOLD 对话", symbol="GOLD", timeframe="M15", mode="free")
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


class TestChatRoutes:
    @pytest.mark.asyncio
    async def test_create_session_accepts_v1_mode(self, chat_db):
        """SessionCreateRequest.mode 接受 V1 会话层枚举（free），拒绝 run 层枚举（single）。

        回归 422：前端 createChatSession 传 "free"（session.mode 已废弃、不承载 UI
        语义，真实模式在 run.mode）。create_session 端点此前零覆盖。
        """
        from pydantic import ValidationError

        from app.api.routes.agent_chat import SessionCreateRequest, create_session

        # 1) pydantic 层：会话层合法值 "free" 通过，run 层枚举 "single" 被 pattern 拒绝
        assert SessionCreateRequest(symbol="GOLD", mode="free").mode == "free"
        with pytest.raises(ValidationError):
            SessionCreateRequest(symbol="GOLD", mode="single")

        # 2) 路由层：直接调用 create_session（借用 fixture 的异步会话）确认不 422。
        resp = await create_session(SessionCreateRequest(symbol="GOLD", timeframe="M15", mode="free"))
        assert resp["session"]["symbol"] == "GOLD"
        assert resp["session"]["mode"] == "free"
        assert isinstance(resp["session"]["id"], int)

    @pytest.mark.asyncio
    async def test_create_and_get_session(self, chat_db, created_session):
        from app.api.routes.agent_chat import get_session

        resp = await get_session(created_session)
        assert resp["session"]["symbol"] == "GOLD"
        assert resp["messages"] == []

    @pytest.mark.asyncio
    async def test_get_session_404(self, chat_db):
        from fastapi import HTTPException

        from app.api.routes.agent_chat import get_session

        with pytest.raises(HTTPException) as exc:
            await get_session(99999)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_send_message_roundtrip(self, chat_db, created_session):
        from app.api.routes.agent_chat import MessageSendRequest, send_message

        with patch(
            "mcp_server.agents.chat_agent.run_chat_turn",
            new=AsyncMock(return_value={"response": "建议观望", "tool_calls": [], "turns": 1, "duration_s": 1.5}),
        ):
            resp = await send_message(created_session, MessageSendRequest(message="能做多吗？"))

        assert resp["reply"] == "建议观望"
        detail = await chat_db.get_session(created_session)
        roles = [m["role"] for m in detail["messages"]]
        assert roles == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_preset_roundtrip(self, chat_db, created_session):
        from app.api.routes.agent_chat import PresetRequest, send_preset

        with patch(
            "mcp_server.agents.chat_agent.run_chat_turn",
            new=AsyncMock(return_value={"response": "计划如下", "tool_calls": [], "turns": 2, "duration_s": 2.0}),
        ) as mock_run:
            resp = await send_preset(created_session, PresetRequest(preset="trading_plan"))

        assert resp["reply"] == "计划如下"
        # preset 调用应带 preset 参数
        assert mock_run.call_args.kwargs.get("preset") == "trading_plan"

    @pytest.mark.asyncio
    async def test_loop_termination_is_not_a_successful_reply(self, chat_db, created_session):
        """Regression: exact user-visible failure must not be saved as a report."""
        from fastapi import HTTPException
        from app.api.routes.agent_chat import MessageSendRequest, send_message

        with patch(
            "mcp_server.agents.chat_agent.run_chat_turn",
            new=AsyncMock(return_value={
                "response": "Agent loop terminated (timeout/max_turns)",
                "error": "agent loop failed (timeout or exception)",
                "tool_calls": [], "turns": 4, "duration_s": 142.8,
            }),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_message(created_session, MessageSendRequest(message="生成计划"))
        assert exc.value.status_code == 502
        detail = await chat_db.get_session(created_session)
        assert not any(m["role"] == "assistant" for m in detail["messages"])


    @pytest.mark.asyncio
    async def test_agent_error_normalized_to_502(self, chat_db, created_session):
        from fastapi import HTTPException

        from app.api.routes.agent_chat import MessageSendRequest, send_message

        with patch(
            "mcp_server.agents.chat_agent.run_chat_turn",
            new=AsyncMock(return_value={"response": "Agent error: LLM down", "tool_calls": []}),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_message(created_session, MessageSendRequest(message="hi"))
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_delete_session_archives_and_preserves_audit(self, chat_db, created_session):
        """V2 起 DELETE = 归档：会话隐藏但消息/审计保留（不再物理删除）。"""
        from fastapi import HTTPException
        from sqlalchemy import select

        from app.api.routes.agent_chat import delete_session, get_session, list_sessions
        from app.db.models import AgentChatMessage

        async with chat_db.async_session() as db:
            db.add(AgentChatMessage(session_id=created_session, role="user", content="hi"))
            await db.commit()

        resp = await delete_session(created_session)
        assert resp["success"] is True and resp["status"] == "archived"

        # 归档后：列表不显示、详情 404，但历史消息仍在库中
        assert (await list_sessions())["sessions"] == []
        with pytest.raises(HTTPException):
            await get_session(created_session)

        async with chat_db.async_session() as db:
            kept = (await db.execute(
                select(AgentChatMessage).where(AgentChatMessage.session_id == created_session)
            )).scalars().all()
        assert [m.content for m in kept] == ["hi"]

