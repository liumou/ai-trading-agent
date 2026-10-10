"""Trade Reviews service — 历史订单 AI 深度复盘 worker 与持久化（Phase 3C）。

复刻 chat_runs.py 的 worker 范式：
- DB 原子 claim：`UPDATE trade_reviews SET status='running', worker_token=:t,
  lease_until=:until, attempt_count=attempt_count+1 WHERE id=:id AND
  status='pending' RETURNING id`（并发下第二次 claim 命中 0 行 → None）。
- 心跳续租 + 启动 recover（lease_until 过期 running → failed，只中断不重跑）。
- 终态落库：completed（review/classification/confidence/flagged/provider_name）
  / failed（error + flagged），force 重审保留旧版进 review_history。

幂等/去重（评审 B-2）：trigger() 先 SELECT 非 failed 状态幂等返回；failed 可
重试；force 重审强制覆盖但保留旧版。Redis 每日每用户配额前置校验（超限 429）。
归属：所有读写按 account_login 过滤（IDOR 防护），触发/重审/batch 记审计。
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.trade_reviewer import TradeReviewer
from app.audit import log_audit
from app.config import settings
from app.db.models import TradeReview
from app.db.session import async_session

ACTIVE = ("pending", "running")
TERMINAL = {"completed", "failed"}


def now() -> datetime:
    """naive UTC 时间戳（对齐 trades 时区约定）。"""
    return datetime.utcnow()


class TradeReviewStore:
    """trade_reviews 表的读写 + 状态机转移（复刻 ChatRunStore）。"""

    def __init__(self, factory=None):
        self.factory = factory or async_session

    # ─── 查询 ────────────────────────────────────────────────────────────

    async def get_for_user(self, review_id: int, account_login: str) -> TradeReview | None:
        """按 id 查询复盘，强制按 account_login 归属过滤（IDOR 防护）。"""
        async with self.factory() as db:
            row = (await db.execute(select(TradeReview).where(
                TradeReview.id == review_id,
                TradeReview.account_login == account_login,
            ))).scalar_one_or_none()
            return row

    async def get_latest_by_ticket(self, ticket: int, account_login: str) -> TradeReview | None:
        """按 (ticket, account_login) 查最新复盘（归属过滤）。"""
        async with self.factory() as db:
            return (await db.execute(select(TradeReview).where(
                TradeReview.ticket == ticket,
                TradeReview.account_login == account_login,
            ).order_by(TradeReview.id.desc()).limit(1))).scalar_one_or_none()

    async def find_by_natural_key(self, ticket: int, account_login: str) -> TradeReview | None:
        """非 failed 幂等命中：返回任意未失败记录（含 running/pending/completed）。"""
        async with self.factory() as db:
            return (await db.execute(select(TradeReview).where(
                TradeReview.ticket == ticket,
                TradeReview.account_login == account_login,
                TradeReview.status.in_(ACTIVE + ("completed",)),
            ).order_by(TradeReview.id.desc()).limit(1))).scalar_one_or_none()

    # ─── 触发 ────────────────────────────────────────────────────────────

    async def create(self, *, ticket: int, account_login: str, symbol: str,
                     trade_id: int | None, open_time: datetime) -> TradeReview:
        """创建复盘任务（status=pending）。重复触发由调用方先 find_by_natural_key。"""
        async with self.factory() as db, db.begin():
            row = TradeReview(
                ticket=ticket,
                account_login=account_login or "0",
                symbol=symbol,
                trade_id=trade_id,
                open_time=open_time,
                status="pending",
            )
            db.add(row)
            await db.flush()
            return row

    async def create_from_input(self, *, ticket: int, account_login: str, symbol: str,
                                trade_id: int | None, open_time: datetime) -> TradeReview:
        """兼容别名：与 create 同语义（供路由层区分来源）。"""
        return await self.create(ticket=ticket, account_login=account_login,
                                 symbol=symbol, trade_id=trade_id, open_time=open_time)

    async def reuse_or_create(self, *, ticket: int, account_login: str, symbol: str,
                              trade_id: int | None, open_time: datetime) -> TradeReview:
        """幂等触发 + force 重审/失败重试统一入口（评审 CRITICAL-1）。

        手动单（trade_id 为 NULL）在 PG 有 partial unique index
        `uq_trade_reviews_ticket_account_excl_null WHERE trade_id IS NULL`，
        直接 INSERT 第二行同 (ticket, account_login) 会 IntegrityError → 500。
        策略：按自然键找任意状态旧行（含 completed/failed），找到则 **UPDATE 重置
        为 pending**（旧 review 保留在行上，供 worker finish 时传入 previous 进
        review_history），找不到才 INSERT。全程不产生第二行，唯一约束恒成立。
        返回 (row, previous)：previous 为旧 review 快照（供 _run_one finish 用）。
        """
        async with self.factory() as db, db.begin():
            old = (await db.execute(select(TradeReview).where(
                TradeReview.ticket == ticket,
                TradeReview.account_login == account_login,
            ).order_by(TradeReview.id.desc()).limit(1))).scalar_one_or_none()
            if old is not None:
                previous = None
                if old.status in ("completed", "failed") and old.review:
                    previous = {"review": old.review}
                old.status = "pending"
                old.symbol = symbol or old.symbol
                old.trade_id = trade_id if trade_id is not None else old.trade_id
                old.open_time = open_time
                old.error = None
                old.worker_token = None
                old.lease_until = None
                old.attempt_count = 0
                old.updated_at = now()
                await db.flush()
                return old, previous
            row = TradeReview(
                ticket=ticket,
                account_login=account_login or "0",
                symbol=symbol,
                trade_id=trade_id,
                open_time=open_time,
                status="pending",
            )
            db.add(row)
            await db.flush()
            return row, None

    async def mark_running(self, review_id: int, token: str) -> bool:
        """原子 claim：pending → running（返回是否成功）。"""
        async with self.factory() as db, db.begin():
            result = await db.execute(update(TradeReview).where(
                TradeReview.id == review_id,
                TradeReview.status == "pending",
            ).values(status="running", worker_token=token,
                     lease_until=now() + timedelta(seconds=settings.trade_review_lease_s),
                     attempt_count=TradeReview.attempt_count + 1))
            return bool(result.rowcount)

    async def finish(self, review_id: int, result: dict, *, token: str | None = None,
                     previous: dict | None = None) -> bool:
        """终态落库：completed（review 结构化）/ failed（error）。force 重审时
        旧版 review 保留进 review_history。"""
        status = "completed" if not result.get("error") else "failed"
        async with self.factory() as db, db.begin():
            where = [TradeReview.id == review_id, TradeReview.status == "running"]
            if token:
                where.extend([TradeReview.worker_token == token, TradeReview.lease_until > now()])
            row = (await db.execute(select(TradeReview).where(*where))).scalar_one_or_none()
            if row is None:
                return False  # lease lost / 已终态
            row.status = status
            row.updated_at = now()
            row.worker_token = None
            row.lease_until = None
            if status == "completed":
                row.classification = result.get("classification")
                row.review = {
                    "reasoning_correct": result.get("reasoning_correct"),
                    "loss_causes": result.get("loss_causes", []),
                    "win_causes": result.get("win_causes", []),
                    "lessons": result.get("lessons", []),
                    "improvement_actions": result.get("improvement_actions", []),
                    "summary": result.get("summary", ""),
                }
                row.confidence = result.get("confidence")
                row.flagged = bool(result.get("flagged"))
                row.provider_name = result.get("provider_name")
                row.error = None
                if previous is not None and previous.get("review"):
                    history = list(row.review_history or []) + [previous]
                    row.review_history = history[-20:]  # 最多保留 20 版
            else:
                row.error = (result.get("error") or "unknown")[:2000]
                row.flagged = True
        return True

    # ─── worker claim / heartbeat / recover ──────────────────────────────

    async def claim(self) -> tuple[int, str] | None:
        """原子 claim 最老 pending 任务，返回 (id, token)。并发第二次返回 None。"""
        token = str(uuid4())
        async with self.factory() as db, db.begin():
            review_id = await db.scalar(select(TradeReview.id).where(
                TradeReview.status == "pending").order_by(TradeReview.created_at).limit(1))
            if review_id is None:
                return None
            ok = await db.execute(update(TradeReview).where(
                TradeReview.id == review_id, TradeReview.status == "pending",
            ).values(status="running", worker_token=token,
                     lease_until=now() + timedelta(seconds=settings.trade_review_lease_s),
                     attempt_count=TradeReview.attempt_count + 1))
            if not ok.rowcount:
                return None
            return int(review_id), token

    async def heartbeat(self, review_id: int, token: str) -> bool:
        async with self.factory() as db, db.begin():
            result = await db.execute(update(TradeReview).where(
                TradeReview.id == review_id, TradeReview.status == "running",
                TradeReview.worker_token == token,
            ).values(lease_until=now() + timedelta(seconds=settings.trade_review_lease_s)))
            return bool(result.rowcount)

    async def recover(self) -> int:
        """启动恢复：lease_until 过期的 running → failed（只中断不重跑）。"""
        async with self.factory() as db, db.begin():
            stale = (await db.scalars(select(TradeReview).where(
                TradeReview.status == "running", TradeReview.lease_until < now()))).all()
            for row in stale:
                row.status = "failed"
                row.error = "worker_restart: lease expired"
                row.flagged = True
                row.worker_token = None
                row.lease_until = None
                row.updated_at = now()
            return len(stale)

    # ─── 汇总 ────────────────────────────────────────────────────────────

    async def summary(self, account_login: str, days: int = 30) -> dict:
        """跨单模式统计：近 N 天 completed 复盘的分类分布 + 根因 top + real_mistake 计数。"""
        cutoff = now() - timedelta(days=days)
        async with self.factory() as db:
            rows = (await db.scalars(select(TradeReview).where(
                TradeReview.account_login == account_login,
                TradeReview.status == "completed",
                TradeReview.created_at >= cutoff,
            ))).all()
        total = len(rows)
        breakdown: dict[str, int] = {}
        cause_counter: dict[str, int] = {}
        mistakes = 0
        for r in rows:
            cls = r.classification or "unknown"
            breakdown[cls] = breakdown.get(cls, 0) + 1
            if cls == "real_mistake":
                mistakes += 1
            review = r.review or {}
            causes = review.get("loss_causes") or review.get("win_causes") or []
            for c in causes:
                cause_counter[c] = cause_counter.get(c, 0) + 1
        top_causes = sorted(cause_counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
        return {
            "total": total,
            "window_days": days,
            "breakdown": breakdown,
            "real_mistakes": mistakes,
            "top_causes": [{"cause": k, "count": v} for k, v in top_causes],
        }


# ─── 依赖解析（worker 自包含，可测） ────────────────────────────────────


def _resolve_reviewer() -> TradeReviewer:
    """构建 TradeReviewer：优先复用 lifespan 构建好的单例（app.state），
    避免 worker 重复创建 connector；缺省降级为独立实例（测试/无状态环境）。"""
    from app.ai.client import AIClient
    from app.ai.trade_accountability import TradeAccountabilityTracker

    try:
        from starlette.requests import Request  # noqa: F401

        # lifespan 中注册的单例：app.state.hist_collector / ai_client。
        from app.main import app

        ai_client = getattr(app.state, "ai_client", None) or AIClient()
        collector = getattr(app.state, "hist_collector", None)
    except Exception:  # noqa: BLE001
        ai_client, collector = AIClient(), None

    return TradeReviewer(
        ai_client=ai_client,
        market_data=collector.market_data if collector is not None else None,
        collector=collector,
        accountability=TradeAccountabilityTracker(),
    )


async def _run_one(review_id: int, token: str, reviewer: TradeReviewer | None = None) -> None:
    """执行单条复盘：组装输入 → LLM → 终态落库。reviewer 缺省时自解析降级。"""
    store = TradeReviewStore()
    reviewer = reviewer or _resolve_reviewer()
    lost = asyncio.Event()

    async def _heartbeat():
        while not lost.is_set():
            await asyncio.sleep(max(1.0, settings.trade_review_lease_s / 3))
            try:
                if not await store.heartbeat(review_id, token):
                    lost.set()
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"trade review heartbeat failed: {type(e).__name__}")
                lost.set()
                return

    heart = asyncio.create_task(_heartbeat())
    try:
        # 读取复盘任务（独立短连接 session，LLM 期间不持有连接）。
        async with async_session() as db:
            row = (await db.execute(select(TradeReview).where(
                TradeReview.id == review_id,
                TradeReview.worker_token == token,
                TradeReview.status == "running",
            ))).scalar_one_or_none()
            if row is None:
                return
            account_login, ticket, symbol, trade_id, _open_time = (
                row.account_login, row.ticket, row.symbol, row.trade_id, row.open_time,
            )

        # 组装输入：bot 单（有 trade_id 且有 DB 记录）/ 手动单（无 trade_id）。
        # 手动单的 deal 数据（symbol/direction/lot/open_price/profit/time）由
        # bridge 回查补齐——route 层不取数，只排任务，避免拖慢 202 响应。
        inp = None
        if trade_id is not None:
            async with async_session() as db:
                from app.db.models import Trade

                trade_row = (await db.execute(select(Trade).where(
                    Trade.id == trade_id, Trade.ticket == ticket,
                    Trade.account_login == account_login,
                ))).scalar_one_or_none()
            if trade_row is not None:
                inp = await reviewer.build_input(trade=trade_row, account_login=account_login)
            else:
                # trade_id 有但记录不存在（已删除/归档）→ 降级为手动单组装。
                inp = await reviewer.build_input(
                    deal={"symbol": symbol, "ticket": ticket, "account_login": account_login},
                    account_login=account_login,
                )
        else:
            deal = await _fetch_manual_deal(ticket, account_login)
            inp = await reviewer.build_input(deal=deal, account_login=account_login)

        if inp is None:
            await store.finish(review_id, {"error": "invalid_trade_data"}, token=token)
            return

        # reuse 旧行时旧 review 已保留在行上，finish 前读出传给 previous 进
        # review_history（force 重审 / failed 重试语义）。
        async with async_session() as db:
            _row = (await db.execute(select(TradeReview).where(
                TradeReview.id == review_id
            ))).scalar_one_or_none()
            previous = ({"review": _row.review} if _row and _row.review else None)

        result = await reviewer.run(inp)
        if lost.is_set():
            raise RuntimeError("trade_review_lease_lost")
        await store.finish(review_id, result, token=token, previous=previous)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trade review run failed: {type(e).__name__}")
        with suppress(Exception):
            await store.finish(review_id, {"error": f"workflow_error: {type(e).__name__}"}, token=token)
    finally:
        heart.cancel()


async def trade_review_worker(stop_event: asyncio.Event, reviewer: TradeReviewer | None = None):
    """复盘后台 worker：启动 recover → 循环 claim → 执行 → stop_event。

    reviewer 由 main.py lifespan 注入（复用已构建的 ai_client/hist_collector）；
    缺省时 worker 自解析降级（测试/独立运行）。
    """
    store = TradeReviewStore()
    try:
        recovered = await store.recover()
        if recovered:
            logger.info(f"Trade review worker: {recovered} expired lease(s) marked failed")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Trade review worker recovery skipped: {type(e).__name__}")
    while not stop_event.is_set():
        try:
            claimed = await store.claim()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Trade review worker claim failed: {type(e).__name__}")
            claimed = None
        if claimed:
            try:
                await _run_one(*claimed, reviewer=reviewer)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Trade review worker run error: {type(e).__name__}")
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.trade_review_poll_s)
        except TimeoutError:
            pass
        # 运行期周期 recover：任务占满租约（LLM 120s ≈ lease 120s）时，同 worker
        # 其它 pending 会积压；recover 只在启动跑一次的话过期 lease 永不回收
        # （评审 MEDIUM-6）。空转时顺手清理，成本一次 UPDATE。
        try:
            recovered = await store.recover()
            if recovered:
                logger.info(f"Trade review worker: {recovered} expired lease(s) recovered")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Trade review worker recover failed: {type(e).__name__}")


async def _fetch_manual_deal(ticket: int, account_login: str) -> dict | None:
    """从 MT5 bridge history 回查指定 ticket 的手动单 deal 完整数据。

    手动单不进 trades 表，route 触发时只排任务；worker 执行时按 (ticket,
    account_login) 从当前活跃引擎的 bridge history 回查该单的
    symbol/direction/lot/open/close/sl/tp/time/net_profit。找不到（已过期 /
    bridge 离线）返回 None，由 build_input 缺 symbol 走 fail-closed。
    """
    try:
        from app.api.routes.bot import _get_engine

        engine = _get_engine(None)
        if engine is None:
            return None
        result = await engine.connector.get_history(days=90, symbol=None)
        if not result.get("success"):
            return None
        for deal in result.get("data", []):
            if int(deal.get("ticket") or 0) == int(ticket):
                deal["account_login"] = account_login
                return deal
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trade review manual deal fetch failed: {type(e).__name__}")
        return None
    return None


# ─── 路由层服务函数（幂等触发 + 配额 + 审计） ──────────────────────────


async def check_quota(username: str, redis_client) -> None:
    """Redis 每日每用户配额：trade_review:daily:{username}:{date} INCR+EXPIRE，
    超限抛 429。batch 计入同一配额。"""
    if redis_client is None:
        return  # 无 Redis 时降级不限额（worker 仍会执行）
    date_key = now().date().isoformat()
    key = f"trade_review:daily:{username}:{date_key}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 24 * 3600)
    if count > settings.trade_review_daily_quota:
        raise HTTPException(status_code=429, detail="daily trade review quota exceeded")


async def trigger_review(
    *,
    store: TradeReviewStore,
    username: str,
    redis_client,
    account_login: str,
    ticket: int,
    symbol: str,
    db: AsyncSession | None = None,
    trade_id: int | None = None,
    open_time: datetime | None = None,
    force: bool = False,
    ip: str | None = None,
) -> dict:
    """幂等触发复盘：非 failed 幂等返回已有记录；failed 可重试；force 重审强制
    重新排队（旧版保留在 review_history）。记审计（复用路由层 db 会话）。"""
    await check_quota(username, redis_client)

    if not force:
        existing = await store.find_by_natural_key(ticket, account_login)
        if existing is not None:
            return _public_review(existing)

    # 组装 open_time（手动单无 trade_id 时必须有 open_time 才能排行情窗口）。
    if open_time is None:
        open_time = now()

    # force 重审 / failed 重试：UPDATE 复用旧行（PG partial unique index 下
    # 手动单同 (ticket, account_login) 不可 INSERT 第二行），旧 review 保留
    # 在行上，worker finish 时并入 review_history。
    row, _previous = await store.reuse_or_create(
        ticket=ticket, account_login=account_login, symbol=symbol,
        trade_id=trade_id, open_time=open_time,
    )
    if db is not None:
        await log_audit(
            db, action="trade_review.trigger", actor=username,
            resource=f"trade:{ticket}", detail={"account_login": account_login, "force": force},
            ip=ip,
        )
    return _public_review(row)


def _public_review(row: TradeReview) -> dict:
    """复盘记录的对外视图（不含 worker 内部字段）。"""
    return {
        "id": row.id,
        "trade_id": row.trade_id,
        "ticket": row.ticket,
        "account_login": row.account_login,
        "symbol": row.symbol,
        "status": row.status,
        "classification": row.classification,
        "confidence": row.confidence,
        "flagged": row.flagged,
        "review": row.review,
        "review_history": row.review_history,
        "error": row.error,
        "provider_name": row.provider_name,
        "open_time": row.open_time.isoformat() if row.open_time else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
