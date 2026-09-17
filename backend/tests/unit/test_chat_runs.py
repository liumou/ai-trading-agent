"""Isolated chat queue tests: SQLite and fake workflows only."""
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import AgentChatEvent, AgentChatMessage, AgentChatRun, AgentChatSession
from app.services.chat_runs import ChatRunStore, now


@pytest.mark.asyncio
async def test_create_persists_user_and_idempotence(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        session = AgentChatSession(symbol="GOLD")
        db.add(session)
        await db.commit()
        session_id = session.id
    store = ChatRunStore(factory)
    request_id = str(uuid4())
    run = await store.create(session_id, "hello", None, "single", request_id)
    assert run["status"] == "queued"
    assert (await store.create(session_id, "hello", None, "single", request_id))["id"] == run["id"]
    with pytest.raises(HTTPException) as exc:
        await store.create(session_id, "other", None, "single", str(uuid4()))
    assert exc.value.status_code == 409
    async with factory() as db:
        messages = (await db.scalars(select(AgentChatMessage))).all()
        assert [(m.role, m.content) for m in messages] == [("user", "hello")]


@pytest_asyncio.fixture
async def store(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        session = AgentChatSession(symbol="GOLD")
        db.add(session)
        await db.commit()
        session_id = session.id
    from app.services.chat_runs import ChatRunStore

    return ChatRunStore(factory), session_id, factory


@pytest.mark.asyncio
async def test_claim_runs_once_with_lease_and_event(store):
    st, session_id, factory = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    claimed = await st.claim()
    assert claimed is not None
    data, token = claimed
    assert data["id"] == run["id"]
    assert data["symbol"] == "GOLD" and data["message"] == "hi"
    assert (await st.get(run["id"]))["status"] == "running"
    assert await st.claim() is None  # 已被 claim，不会重复执行
    async with factory() as db:
        events = (await db.scalars(select(AgentChatEvent).where(
            AgentChatEvent.run_id == run["id"]).order_by(AgentChatEvent.sequence))).all()
    assert [e.event_type for e in events] == ["run_queued", "run_started"]
    assert await st.heartbeat(run["id"], token) is True
    assert await st.heartbeat(run["id"], "wrong-token") is False


@pytest.mark.asyncio
async def test_append_requires_live_lease(store):
    st, session_id, _ = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    _, token = await st.claim()
    await st.append(run["id"], token, "assistant_text", {"text": "partial", "agent_id": "chat_agent"})
    assert "partial" in (await st.get(run["id"]))["partial_response"]
    with pytest.raises(RuntimeError, match="lease"):
        await st.append(run["id"], "stale-token", "assistant_text", {"text": "nope"})


@pytest.mark.asyncio
async def test_finish_completed_persists_report_and_releases_guard(store):
    st, session_id, factory = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    _, token = await st.claim()
    await st.finish(run["id"], {"status": "completed", "reason_code": None,
                                "response": "final report", "partial_response": "final report", "turns": 3}, token)
    done = await st.get(run["id"])
    assert done["status"] == "completed" and done["response"] == "final report"
    assert done["turns"] == 3 and done["finished_at"]
    async with factory() as db:
        session = await db.get(AgentChatSession, session_id)
        assert session.active_run_id is None
        replies = (await db.scalars(select(AgentChatMessage).where(
            AgentChatMessage.role == "assistant"))).all()
    assert [m.content for m in replies] == ["final report"]


@pytest.mark.asyncio
async def test_failure_never_becomes_completed_report(store):
    st, session_id, _ = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    _, token = await st.claim()
    await st.finish(run["id"], {"status": "completed", "reason_code": "total_timeout",
                                "response": "should drop", "error": "chat total time budget exceeded"}, token)
    done = await st.get(run["id"])
    assert done["status"] == "failed"
    assert "should drop" not in done["response"]


@pytest.mark.asyncio
async def test_cancel_is_terminal_and_releases_guard(store):
    st, session_id, factory = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    await st.claim()
    cancelled = await st.cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    assert await st.claim() is None  # 终态不被 worker 重新领取
    async with factory() as db:
        assert (await db.get(AgentChatSession, session_id)).active_run_id is None


@pytest.mark.asyncio
async def test_recover_interrupts_expired_and_never_autoruns(store):
    st, session_id, factory = store
    run = await st.create(session_id, "hi", None, "single", str(uuid4()))
    await st.claim()
    async with factory() as db:  # 模拟进程崩溃后租约过期
        await db.execute(update(AgentChatRun).where(AgentChatRun.id == run["id"]).values(
            lease_until=now() - timedelta(seconds=1)))
        await db.commit()
    assert await st.recover() == 1
    assert (await st.get(run["id"]))["status"] == "interrupted"
    assert await st.claim() is None  # 不自动重跑
    async with factory() as db:
        assert (await db.get(AgentChatSession, session_id)).active_run_id is None
    requeued = await st.requeue(run["id"])
    assert requeued["status"] == "queued"
    assert await st.claim() is not None


@pytest.mark.asyncio
async def test_archive_blocks_while_run_active(store):
    st, session_id, _ = store
    await st.create(session_id, "hi", None, "single", str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        await st.archive(session_id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_events_cursor_and_derived_agents(store):
    st, session_id, _ = store
    run = await st.create(session_id, "hi", None, "experts", str(uuid4()))
    _, token = await st.claim()
    await st.append(run["id"], token, "agent_started",
                    {"agent_id": "risk_analyst", "execution_id": "x1", "status": "running", "model": "m"})
    await st.append(run["id"], token, "agent_completed",
                    {"agent_id": "risk_analyst", "execution_id": "x1", "status": "completed", "report": "APPROVED"})
    first = await st.events(run["id"], 0, 2)
    assert first["next_cursor"] == 2 and [e["event_type"] for e in first["events"]] == ["run_queued", "run_started"]
    # 报告由生命周期事件派生，不限于当前游标页：这一页没有 agent_completed 也能看到
    assert first["agents"][0]["report"] == "APPROVED"
    second = await st.events(run["id"], first["next_cursor"], 100)
    assert [e["event_type"] for e in second["events"]] == ["agent_started", "agent_completed"]
    assert second["agents"][0]["report"] == "APPROVED"
    assert (await st.events(run["id"], second["next_cursor"], 100))["events"] == []


def test_sanitize_redacts_credentials_and_bounds_payload():
    from app.services.chat_runs import sanitize

    out = sanitize({"api_key": "sk-live-abcdefghijklmn", "headers": {"Authorization": "Bearer abc.def"},
                    "note": "token=supersecretvalue", "keep": 1})
    assert out["api_key"] == "[REDACTED]"
    assert out["headers"]["Authorization"] == "[REDACTED]"
    assert "supersecretvalue" not in out["note"]
    assert out["keep"] == 1
    big = sanitize({"blob": "x" * 40000})
    assert big["truncated"] is True and big["original_chars"] > 24000 and len(big["sha256"]) == 64
