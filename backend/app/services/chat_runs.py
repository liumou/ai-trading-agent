"""Durable, single-owner chat queue. No DB transaction spans model/tool awaits.

Two new tables only: agent executions/reports are derived from persisted
agent_started/agent_completed events, not a separate agent_runs table.
Payloads are redacted and bounded; truncation is explicit, not raw archival.
"""
import asyncio
import hashlib
import json
import re
from contextlib import suppress
from datetime import datetime, timedelta, UTC
from uuid import uuid4

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select, update

from app.config import settings
from app.db.models import AgentChatEvent, AgentChatMessage, AgentChatRun, AgentChatSession
from app.db.session import async_session

ACTIVE = ("queued", "running")
TERMINAL = {"completed", "failed", "timed_out", "incomplete", "cancelled", "interrupted"}

_SECRET_PATTERN = re.compile(r"(?i)(api.?key|authorization|password|secret|token|cookie)")


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def sanitize(value, limit=24000):
    """Bound JSON after recursive credential redaction, including free text."""
    def clean(v, depth=0):
        if depth > 12:
            return "[truncated:depth]"
        if isinstance(v, dict):
            return {str(k)[:100]: "[REDACTED]" if _SECRET_PATTERN.search(str(k)) else clean(x, depth+1)
                    for k, x in list(v.items())[:200]}
        if isinstance(v, (list, tuple)):
            return [clean(x, depth+1) for x in v[:200]]
        if isinstance(v, str):
            v = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", v)
            v = re.sub(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[^\s,;\"}]+", r"\1[REDACTED]", v)
            return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", v)
        return v if v is None or isinstance(v, (int, float, bool)) else str(v)
    cleaned = clean(value)
    raw = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(raw) > limit:
        return {"truncated": True, "preview": raw[:limit], "sha256": hashlib.sha256(raw.encode()).hexdigest(), "original_chars": len(raw)}
    return cleaned


def public_run(run):
    fields = ("id", "session_id", "mode", "status", "reason_code", "budget", "response",
              "partial_response", "turns", "duration_s", "error", "request_id")
    out = {k: getattr(run, k) for k in fields}
    out.update({k: getattr(run, k).isoformat()+"Z" if getattr(run, k) else None
                for k in ("created_at", "started_at", "finished_at")})
    out.update(message=run.input.get("message", ""), preset=run.input.get("preset"))
    return out


class ChatRunStore:
    def __init__(self, factory=None):
        self.factory = factory or async_session

    async def create(self, session_id, message, preset, mode, request_id, lang="zh"):
        from mcp_server.agents.chat_workflow import chat_budget
        async with self.factory() as db, db.begin():
            # UPDATE obtains a row lock on PG and a write lock on SQLite. This
            # serializes replay checks, active guard, and archive atomically.
            locked = await db.execute(update(AgentChatSession).where(
                AgentChatSession.id == session_id, AgentChatSession.archived.is_(False)
            ).values(updated_at=now()))
            if not locked.rowcount:
                raise HTTPException(404, "Chat session not found")
            existing = await db.scalar(select(AgentChatRun).where(
                AgentChatRun.session_id == session_id, AgentChatRun.request_id == request_id))
            if existing:
                return public_run(existing)
            session = await db.get(AgentChatSession, session_id)
            if session.active_run_id:
                raise HTTPException(409, "Session already has an active run")
            history = list((await db.scalars(select(AgentChatMessage).where(
                AgentChatMessage.session_id == session_id).order_by(AgentChatMessage.id.desc()).limit(20))).all())
            run_id = str(uuid4())
            data = {"symbol": session.symbol, "timeframe": session.timeframe, "message": message,
                    "preset": preset, "lang": lang, "history": [
                        {"role": m.role, "content": m.content[:2000]} for m in reversed(history)]}
            run = AgentChatRun(id=run_id, session_id=session_id, request_id=request_id,
                               mode=mode, input=sanitize(data, 100000), budget=chat_budget(),
                               status="queued", created_at=now(), response="", partial_response="",
                               turns=0, duration_s=0, sequence=1)
            db.add(run)
            session.active_run_id = run_id
            db.add(AgentChatMessage(session_id=session_id, role="user", content=sanitize(message or f"[{preset}]")))
            db.add(AgentChatEvent(run_id=run_id, sequence=1, event_type="run_queued", payload={"mode": mode}))
            await db.flush()
            return public_run(run)

    async def get(self, run_id):
        async with self.factory() as db:
            run = await db.get(AgentChatRun, run_id)
            if not run:
                raise HTTPException(404, "Chat run not found")
            return public_run(run)


    async def events(self, run_id, after=0, limit=100):
        async with self.factory() as db:
            run = await db.get(AgentChatRun, run_id)
            if not run:
                raise HTTPException(404, "Chat run not found")
            query = select(AgentChatEvent).where(AgentChatEvent.run_id == run_id)
            rows = (await db.scalars(query.where(AgentChatEvent.sequence > after)
                                    .order_by(AgentChatEvent.sequence).limit(limit))).all()
            # Derive reports from lifecycle events, not only this cursor page.
            lifecycle = (await db.scalars(query.where(AgentChatEvent.event_type.in_(
                ("agent_started", "agent_completed"))).order_by(AgentChatEvent.sequence))).all()
            agents = {}
            for e in lifecycle:
                key = e.execution_id or e.agent_id
                if not key:
                    continue
                a = agents.setdefault(key, {"agent_id": e.agent_id, "execution_id": e.execution_id,
                                            "status": "running", "report": ""})
                a.update(e.payload)
                if e.event_type == "agent_started":
                    a["status"] = "running"
            cursor = rows[-1].sequence if rows else after
            return {"run": public_run(run), "events": [
                {"sequence": e.sequence, "event_type": e.event_type, "agent_id": e.agent_id,
                 "execution_id": e.execution_id, "payload": e.payload,
                 "created_at": e.created_at.isoformat()+"Z"} for e in rows],
                "agents": list(agents.values()), "next_cursor": cursor}

    async def list_for_session(self, session_id):
        async with self.factory() as db:
            session = await db.get(AgentChatSession, session_id)
            if not session:
                raise HTTPException(404, "Chat session not found")
            rows = (await db.scalars(select(AgentChatRun).where(
                AgentChatRun.session_id == session_id).order_by(AgentChatRun.created_at.desc()).limit(100))).all()
            return [public_run(r) for r in rows]

    async def append(self, run_id, token, kind, payload):
        async with self.factory() as db, db.begin():
            # Atomic allocation serializes parallel expert emits too.
            seq = await db.scalar(update(AgentChatRun).where(
                AgentChatRun.id == run_id, AgentChatRun.status == "running",
                AgentChatRun.worker_token == token, AgentChatRun.lease_until > now()
            ).values(sequence=AgentChatRun.sequence+1).returning(AgentChatRun.sequence))
            if seq is None:
                raise RuntimeError("chat_run_lease_lost")
            safe = sanitize(payload)
            if not isinstance(safe, dict):
                safe = {"value": safe}
            agent_id, execution_id = payload.get("agent_id"), payload.get("execution_id")
            db.add(AgentChatEvent(run_id=run_id, sequence=seq, event_type=kind[:64], payload=safe,
                                  agent_id=str(agent_id)[:100] if agent_id else None,
                                  execution_id=str(execution_id)[:100] if execution_id else None))
            text = payload.get("text") if kind in ("assistant_text", "agent_text", "agent_summary") else None
            if text:
                run = await db.get(AgentChatRun, run_id)
                run.partial_response = bounded_text(run.partial_response + "\n" + str(text))


    async def finish(self, run_id, result, token=None):
        async with self.factory() as db, db.begin():
            where = [AgentChatRun.id == run_id, AgentChatRun.status.in_(ACTIVE)]
            if token:
                where.extend([AgentChatRun.worker_token == token, AgentChatRun.lease_until > now()])
            seq = await db.scalar(update(AgentChatRun).where(*where).values(
                sequence=AgentChatRun.sequence+1).returning(AgentChatRun.sequence))
            if seq is None:
                return
            run = await db.get(AgentChatRun, run_id)
            status = result.get("status", "failed")
            if status not in TERMINAL or (result.get("error") and status == "completed"):
                status = "failed"
            run.status, run.reason_code = status, result.get("reason_code")
            run.response = bounded_text(result.get("response", "")) if status == "completed" else ""
            run.partial_response = bounded_text(result.get("partial_response") or
                (result.get("response") if status != "completed" else "") or run.partial_response)
            run.error = bounded_text(result.get("error", "")) or None
            run.turns = int(result.get("turns") or run.turns)
            run.finished_at = now()
            run.duration_s = (run.finished_at-run.started_at).total_seconds() if run.started_at else 0
            run.lease_until = None
            await db.execute(update(AgentChatSession).where(
                AgentChatSession.id == run.session_id, AgentChatSession.active_run_id == run_id
            ).values(active_run_id=None, updated_at=now()))
            db.add(AgentChatEvent(run_id=run_id, sequence=seq, event_type="run_finished",
                                  payload={"status": status, "reason_code": run.reason_code}))
            if run.response:
                db.add(AgentChatMessage(session_id=run.session_id, role="assistant", content=run.response,
                                       duration_s=run.duration_s))

    async def cancel(self, run_id):
        await self.get(run_id)
        # Cross-process signal; heartbeat cancels await, not remote computation.
        await self.finish(run_id, {"status": "cancelled", "reason_code": "cancelled_by_user"})
        return await self.get(run_id)

    async def archive(self, session_id):
        async with self.factory() as db, db.begin():
            locked = await db.execute(update(AgentChatSession).where(
                AgentChatSession.id == session_id).values(updated_at=now()))
            if not locked.rowcount:
                raise HTTPException(404, "Chat session not found")
            session = await db.get(AgentChatSession, session_id)
            if session.active_run_id:
                raise HTTPException(409, "Cancel the active run before archiving")
            session.archived = True
        return {"success": True, "status": "archived", "session_id": session_id}


    async def claim(self):
        """Atomically claim the oldest queued run; returns (run_input, worker_token)."""
        token = str(uuid4())
        async with self.factory() as db, db.begin():
            run_id = await db.scalar(select(AgentChatRun.id).where(
                AgentChatRun.status == "queued").order_by(AgentChatRun.created_at).limit(1))
            if run_id is None:
                return None
            seq = await db.scalar(update(AgentChatRun).where(
                AgentChatRun.id == run_id, AgentChatRun.status == "queued"
            ).values(status="running", worker_token=token, started_at=now(), reason_code=None,
                     lease_until=now() + timedelta(seconds=settings.chat_lease_s),
                     sequence=AgentChatRun.sequence + 1).returning(AgentChatRun.sequence))
            if seq is None:
                return None
            run = await db.get(AgentChatRun, run_id)
            D = dict(run.input or {})
            D.update(id=run_id, mode=run.mode, budget=run.budget or {})
            db.add(AgentChatEvent(run_id=run_id, sequence=seq, event_type="run_started",
                                  payload={"lease_s": settings.chat_lease_s}))
            return D, token

    async def heartbeat(self, run_id, token):
        async with self.factory() as db, db.begin():
            extended = await db.execute(update(AgentChatRun).where(
                AgentChatRun.id == run_id, AgentChatRun.status == "running",
                AgentChatRun.worker_token == token
            ).values(lease_until=now() + timedelta(seconds=settings.chat_lease_s)))
            return bool(extended.rowcount)

    async def recover(self):
        """Expired leases become interrupted; interrupted runs are never auto-rerun."""
        async with self.factory() as db, db.begin():
            stale = (await db.scalars(select(AgentChatRun).where(
                AgentChatRun.status == "running", AgentChatRun.lease_until < now()))).all()
            for run in stale:
                # Allocate the event sequence atomically: writing an event without
                # bumping run.sequence desyncs the counter and breaks later appends.
                seq = await db.scalar(update(AgentChatRun).where(
                    AgentChatRun.id == run.id, AgentChatRun.status == "running"
                ).values(status="interrupted", reason_code="worker_restart", finished_at=now(),
                         lease_until=None, worker_token=None,
                         sequence=AgentChatRun.sequence + 1).returning(AgentChatRun.sequence))
                if seq is None:
                    continue
                db.add(AgentChatEvent(run_id=run.id, sequence=seq, event_type="run_interrupted",
                                      payload={"reason_code": "worker_restart"}))
                await db.execute(update(AgentChatSession).where(
                    AgentChatSession.id == run.session_id,
                    AgentChatSession.active_run_id == run.id).values(active_run_id=None))
            return len(stale)

    async def requeue(self, run_id):
        """Explicit user retry of an interrupted run; never called automatically."""
        async with self.factory() as db, db.begin():
            run = await db.get(AgentChatRun, run_id)
            if not run or run.status != "interrupted":
                raise HTTPException(409, "Only interrupted runs can be requeued")
            session = await db.get(AgentChatSession, run.session_id)
            if session.active_run_id:
                raise HTTPException(409, "Session already has an active run")
            seq = await db.scalar(update(AgentChatRun).where(
                AgentChatRun.id == run_id, AgentChatRun.status == "interrupted"
            ).values(status="queued", reason_code=None, finished_at=None, error=None,
                     sequence=AgentChatRun.sequence + 1).returning(AgentChatRun.sequence))
            if seq is None:
                raise HTTPException(409, "Run is no longer interrupted")
            session.active_run_id = run_id
            db.add(AgentChatEvent(run_id=run_id, sequence=seq, event_type="run_requeued",
                                  payload={"explicit": True}))
            return public_run(run)


def bounded_text(value, limit=24000):
    safe = sanitize(str(value or ""), limit)
    if isinstance(safe, dict):
        return safe["preview"] + "\n[truncated sha256=" + safe["sha256"] + "]"
    return safe


async def _heartbeat_loop(store, run_id, token, lost):
    """Keep the lease fresh; a lost lease aborts the run instead of orphaning it."""
    while True:
        await asyncio.sleep(max(1.0, settings.chat_lease_s / 3))
        try:
            if not await store.heartbeat(run_id, token):
                lost.set()
                return
        except Exception as e:
            logger.warning(f"chat heartbeat failed: {type(e).__name__}")
            lost.set()
            return


async def run_claimed(store, data, token):
    """Execute one claimed run: audited events, lease heartbeat, terminal result."""
    run_id = data["id"]
    lost = asyncio.Event()

    async def emit(kind, payload):
        if lost.is_set():
            raise RuntimeError("chat_run_lease_lost")
        await store.append(run_id, token, kind, payload)

    from mcp_server.agents.chat_workflow import run_workflow

    heart = asyncio.create_task(_heartbeat_loop(store, run_id, token, lost))
    try:
        result = await run_workflow(data, emit)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"chat workflow failed: {type(e).__name__}")
        result = {"status": "failed", "reason_code": "workflow_error", "response": "", "partial_response": ""}
    finally:
        heart.cancel()
    with suppress(Exception):
        await store.finish(run_id, result, token)
    return result


async def chat_worker(stop_event):
    """Durable queue worker. DB errors leave runs queued; never silently re-runs."""
    store = ChatRunStore()
    try:
        recovered = await store.recover()
        if recovered:
            logger.info(f"Chat worker: {recovered} interrupted run(s) marked for explicit retry")
    except Exception as e:
        logger.warning(f"Chat worker recovery skipped: {type(e).__name__}")
    while not stop_event.is_set():
        try:
            claimed = await store.claim()
        except Exception as e:
            logger.warning(f"Chat worker claim failed: {type(e).__name__}")
            claimed = None
        if claimed:
            try:
                await run_claimed(store, *claimed)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Chat worker run error: {type(e).__name__}")
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.chat_worker_poll_s)
        except TimeoutError:
            continue
