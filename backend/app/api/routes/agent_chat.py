"""
Agent Chat API — 对话式交易计划/报告（只读分析，不可交易）。

会话与消息持久化到 DB（agent_chat_sessions / agent_chat_messages），
每轮对话走 chat_agent.run_chat_turn（只读工具白名单 + 每日调用护栏）。
"""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.db.models import AgentChatMessage, AgentChatSession
from app.db.session import async_session

router = APIRouter(prefix="/api/agent-chat", tags=["agent-chat"])

VALID_PRESETS = ("trading_plan", "report")


class SessionCreateRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=30)
    timeframe: str = Field("M15", max_length=8)
    mode: str = Field("free", pattern="^(free|trading_plan|report)$")
    title: str | None = Field(None, max_length=200)


class MessageSendRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class PresetRequest(BaseModel):
    preset: str = Field(..., pattern="^(trading_plan|report)$")


class RunCreateRequest(BaseModel):
    message: str | None = Field(None, max_length=8000)
    preset: str | None = Field(None, pattern="^(trading_plan|report)$")
    mode: str = Field("single", pattern="^(single|experts)$")
    request_id: str = Field(..., min_length=8, max_length=36)


_guardrails = None  # lazy TradingGuardrails 单例（进程内）


async def _record_agent_call() -> None:
    """对话也消耗 Agent 调用额度（guardrails 每日上限 200 次）。

    Redis 不可用时 fail-open：对话是只读分析，限流失效不构成资金风险，
    不应因此拒绝服务（broker 下单路径的护栏是 fail-closed，与此不同）。
    """
    global _guardrails
    if _guardrails is None:
        try:
            import redis.asyncio as redis_lib

            from app.config import settings
            from mcp_server.guardrails import TradingGuardrails

            client = redis_lib.from_url(settings.redis_url, decode_responses=True)
            _guardrails = TradingGuardrails(client)
        except Exception as e:
            logger.warning(f"[agent-chat] guardrails init failed, call-limit disabled: {e}")
            _guardrails = None
    if _guardrails is None:
        return
    result = await _guardrails.validate_agent_call()
    if not result.allowed:
        raise HTTPException(status_code=429, detail=result.reason)
    await _guardrails.record_agent_call()


async def _get_session_or_404(session_id: int) -> AgentChatSession:
    async with async_session() as db:
        session = await db.get(AgentChatSession, session_id)
        if not session or session.archived:
            raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")
        return session


async def _load_history(session_id: int) -> list[dict]:
    async with async_session() as db:
        rows = (await db.execute(
            AgentChatMessage.__table__.select()
            .where(AgentChatMessage.session_id == session_id)
            .order_by(AgentChatMessage.id)
        )).fetchall()
        return [{"role": r.role, "content": r.content} for r in rows]

@router.post("/sessions", dependencies=[Depends(require_auth)])
async def create_session(req: SessionCreateRequest):
    """创建对话会话。"""
    async with async_session() as db:
        session = AgentChatSession(
            title=req.title or f"{req.symbol.upper()} 对话",
            symbol=req.symbol.upper(),
            timeframe=req.timeframe,
            mode=req.mode,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return {"session": {
            "id": session.id, "title": session.title, "symbol": session.symbol,
            "timeframe": session.timeframe, "mode": session.mode,
            "created_at": session.created_at.isoformat(),
        }}


@router.get("/sessions", dependencies=[Depends(require_auth)])
async def list_sessions():
    """会话列表（归档会话不显示，按最近更新倒序）。"""
    async with async_session() as db:
        rows = (await db.execute(
            AgentChatSession.__table__.select()
            .where(AgentChatSession.archived.is_(False))
            .order_by(
                AgentChatSession.updated_at.desc().nullslast(), AgentChatSession.id.desc()
            )
        )).fetchall()
        return {"sessions": [
            {"id": r.id, "title": r.title, "symbol": r.symbol, "timeframe": r.timeframe,
             "mode": r.mode, "created_at": r.created_at.isoformat(),
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows
        ]}


@router.get("/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def get_session(session_id: int):
    """会话详情 + 全部消息。"""
    session = await _get_session_or_404(session_id)
    async with async_session() as db:
        rows = (await db.execute(
            AgentChatMessage.__table__.select()
            .where(AgentChatMessage.session_id == session_id)
            .order_by(AgentChatMessage.id)
        )).fetchall()
        return {
            "session": {"id": session.id, "title": session.title, "symbol": session.symbol,
                        "timeframe": session.timeframe, "mode": session.mode,
                        "created_at": session.created_at.isoformat()},
            "messages": [
                {"id": r.id, "role": r.role, "content": r.content,
                 "tool_calls": r.tool_calls, "duration_s": r.duration_s,
                 "created_at": r.created_at.isoformat()}
                for r in rows
            ],
        }


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def delete_session(session_id: int):
    """归档会话（保留运行记录与审计事件，不物理删除）。"""
    from app.services.chat_runs import ChatRunStore

    return await ChatRunStore().archive(session_id)


@router.get("/config", dependencies=[Depends(require_auth)])
async def get_chat_config():
    """Active chat budgets (no secrets). Terminal status is per-run, not HTTP-level."""
    from mcp_server.agents.chat_workflow import chat_budget

    return {"budget": chat_budget()}


@router.post("/sessions/{session_id}/runs", status_code=202, dependencies=[Depends(require_auth)])
async def create_run(session_id: int, req: RunCreateRequest):
    """Enqueue a chat run; returns 202 with the durable run record."""
    if not req.message and not req.preset:
        raise HTTPException(status_code=422, detail="message or preset is required")
    await _record_agent_call()
    from app.services.chat_runs import ChatRunStore

    run = await ChatRunStore().create(session_id, req.message or "", req.preset, req.mode, req.request_id)
    return {"run": run}


@router.get("/sessions/{session_id}/runs", dependencies=[Depends(require_auth)])
async def list_session_runs(session_id: int):
    from app.services.chat_runs import ChatRunStore

    return {"runs": await ChatRunStore().list_for_session(session_id)}


@router.get("/runs/{run_id}", dependencies=[Depends(require_auth)])
async def get_run(run_id: str, after: int = 0, limit: int = 100):
    """Run + ordered events + derived agent reports; poll with next_cursor."""
    from app.services.chat_runs import ChatRunStore

    return await ChatRunStore().events(run_id, max(0, after), min(max(1, limit), 500))


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_run(run_id: str):
    """Cancel a run. Stops local waiting; remote provider work may still finish."""
    from app.services.chat_runs import ChatRunStore

    return {"run": await ChatRunStore().cancel(run_id)}


@router.post("/runs/{run_id}/requeue", dependencies=[Depends(require_auth)])
async def requeue_run(run_id: str):
    """Explicit retry for an interrupted run. Never triggered automatically."""
    from app.services.chat_runs import ChatRunStore

    return {"run": await ChatRunStore().requeue(run_id)}


async def _run_and_store_turn(session: AgentChatSession, user_message: str, preset: str | None):
    """公共执行链：护栏计数 → 加载历史 → 调 chat agent → 双向落库。"""
    await _record_agent_call()
    history = await _load_history(session.id)

    from mcp_server.agents.chat_agent import run_chat_turn

    result = await run_chat_turn(
        symbol=session.symbol,
        timeframe=session.timeframe,
        user_message=user_message,
        history=history,
        preset=preset,
    )

    reply = result.get("response", "")
    # Structured failures must not masquerade as successful assistant reports.
    if result.get("error"):
        raise HTTPException(status_code=502, detail=str(result["error"]))
    if isinstance(reply, str) and reply.startswith("Agent error:"):
        raise HTTPException(status_code=502, detail=reply[len("Agent error:"):].strip())

    async with async_session() as db:
        db.add(AgentChatMessage(session_id=session.id, role="user",
                                content=user_message or f"[{preset}]"))
        db.add(AgentChatMessage(
            session_id=session.id, role="assistant", content=reply,
            tool_calls=result.get("tool_calls") or None,
            duration_s=result.get("duration_s"),
        ))
        await db.commit()

    return {"reply": reply, "tool_calls": result.get("tool_calls", []),
            "turns": result.get("turns", 0), "duration_s": result.get("duration_s", 0)}


@router.post("/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
async def send_message(session_id: int, req: MessageSendRequest):
    """发送消息并返回 Agent 回复（同步，LLM 超时约 120s）。"""
    session = await _get_session_or_404(session_id)
    return await _run_and_store_turn(session, req.message, None)


@router.post("/sessions/{session_id}/preset", dependencies=[Depends(require_auth)])
async def send_preset(session_id: int, req: PresetRequest):
    """快捷意图：一键生成交易计划（trading_plan）或市场报告（report）。"""
    if req.preset not in VALID_PRESETS:
        raise HTTPException(status_code=422, detail=f"Invalid preset: {req.preset}")
    session = await _get_session_or_404(session_id)
    return await _run_and_store_turn(session, "", req.preset)

