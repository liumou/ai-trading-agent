"""Trade Reviews API — 历史订单 AI 深度复盘（Phase 3D）。

- 全部走 make_authed_router（统一鉴权，防漏鉴权）。
- 触发（POST /）→ 202：`{trade_id}` 或 `{ticket, account_login}`，force 重审。
- 查询（GET /{id}、GET /by-ticket/{ticket}）→ 归属按 account_login 过滤（IDOR）。
- 批量（POST /batch）→ ≤trade_review_max_batch，逐单独立失败，计入配额。
- 汇总（GET /summary）→ 跨单模式统计（real_mistake 次数 / 根因 top）。
- 配额：Redis 每日每用户 INCR+EXPIRE，超限 429；batch 计入同一配额。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router_factory import make_authed_router
from app.auth import require_auth
from app.config import settings
from app.constants import REVIEW_WINDOW_DAYS
from app.db.models import Trade
from app.db.session import get_db
from app.services.trade_reviews import TradeReviewStore, trigger_review

router = make_authed_router(prefix="/api/trade-reviews", tags=["trade-reviews"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class TriggerItem(BaseModel):
    """单笔触发：trade_id（bot 单）或 (ticket, account_login)（手动/统一）。"""

    trade_id: int | None = Field(None, gt=0)
    ticket: int | None = Field(None, gt=0)
    account_login: str | None = Field(None, max_length=32)


class TriggerRequest(BaseModel):
    trade_id: int | None = Field(None, gt=0)
    ticket: int | None = Field(None, gt=0)
    account_login: str | None = Field(None, max_length=32)
    force: bool = Field(False, description="强制重审（保留旧版进 review_history）")


class BatchRequest(BaseModel):
    items: list[TriggerItem] = Field(..., min_length=1)
    force: bool = Field(False)


# ─── 工具 ────────────────────────────────────────────────────────────────────


def _require_identifier(req: TriggerRequest) -> tuple[int, int, str]:
    """校验 trade_id 或 (ticket, account_login)，返回 (trade_id, ticket, account_login)。"""
    if req.trade_id:
        return req.trade_id, 0, "0"  # trade_id 定位，ticket/account 由 DB 解析
    if req.ticket and req.account_login:
        return 0, req.ticket, req.account_login
    raise HTTPException(status_code=422, detail="Provide trade_id or (ticket + account_login)")


async def _resolve_trade(db: AsyncSession, trade_id: int) -> dict:
    """按 trade_id 查 trades 表，返回 (ticket, account_login, symbol, open_time)。"""
    row = (await db.execute(
        __import__("sqlalchemy").select(Trade).where(Trade.id == trade_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {
        "ticket": row.ticket,
        "account_login": row.account_login or "0",
        "symbol": row.symbol,
        "open_time": row.open_time,
    }


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _query_account_login(request: Request, fallback: str) -> str:
    """查询端点归属账号：优先服务端推导（manager 当前活跃账号），客户端
    Query 仅作 fallback。防 IDOR 横向越权 —— 用户只能查自己活跃账号的复盘，
    不能靠改 Query 读其它账号（评审 MEDIUM-4）。"""
    manager = getattr(request.app.state, "manager", None)
    current = getattr(manager, "current_account_login", None)
    return str(current or fallback or "0")


# ─── 路由 ────────────────────────────────────────────────────────────────────


@router.post("", status_code=202)
async def create_trade_review(
    req: TriggerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    username: str | None = Depends(require_auth),
):
    """触发单笔复盘。trade_id 定位 bot 单；ticket+account_login 定位手动单。"""
    store = TradeReviewStore()
    redis_client = getattr(request.app.state, "redis", None)
    actor = username or "owner"

    trade_id, ticket, account_login = _require_identifier(req)

    if req.trade_id:
        trade = await _resolve_trade(db, req.trade_id)
        ticket, account_login = trade["ticket"], trade["account_login"]
        symbol, open_time = trade["symbol"], trade["open_time"]
    else:
        # 手动单：归属由服务端 manager.current_account_login 推导（与 GET 端
        # _query_account_login 一致，防 IDOR 为他账号触发复盘 / 消耗 LLM 配额）。
        account_login = _query_account_login(request, account_login or "0")
        symbol = ""
        open_time = datetime.utcnow()

    result = await trigger_review(
        store=store,
        username=actor,
        redis_client=redis_client,
        account_login=account_login,
        ticket=ticket,
        symbol=symbol,
        db=db,
        trade_id=req.trade_id,
        open_time=open_time,
        force=req.force,
        ip=_client_ip(request),
    )
    return {"status": "accepted", "review": result}


@router.get("/by-ticket/{ticket}")
async def get_review_by_ticket(
    ticket: int,
    request: Request,
    account_login: str = Query("0", max_length=32),
    username: str | None = Depends(require_auth),
):
    """按订单查最新复盘（归属服务端推导，客户端仅 fallback）。"""
    store = TradeReviewStore()
    account = _query_account_login(request, account_login)
    row = await store.get_latest_by_ticket(ticket, account)
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return _public_review(row)


@router.get("/summary")
async def get_trade_review_summary(
    request: Request,
    account_login: str = Query("0", max_length=32),
    days: int = Query(REVIEW_WINDOW_DAYS, ge=1, le=365),
    username: str | None = Depends(require_auth),
):
    """跨单模式统计：近 N 天分类分布 + real_mistake 次数 + 根因 top。"""
    store = TradeReviewStore()
    account = _query_account_login(request, account_login)
    return await store.summary(account, days=days)


@router.get("/{review_id}")
async def get_trade_review(
    review_id: int,
    request: Request,
    account_login: str = Query("0", max_length=32),
    username: str | None = Depends(require_auth),
):
    """按 id 查复盘结果（归属服务端推导，客户端仅 fallback）。"""
    store = TradeReviewStore()
    account = _query_account_login(request, account_login)
    row = await store.get_for_user(review_id, account)
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return _public_review(row)


@router.post("/batch", status_code=202)
async def batch_trade_reviews(
    req: BatchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    username: str | None = Depends(require_auth),
):
    """批量补齐复盘：≤trade_review_max_batch，逐单独立失败返回 {created, failed}。"""
    if len(req.items) > settings.trade_review_max_batch:
        raise HTTPException(
            status_code=422,
            detail=f"Max {settings.trade_review_max_batch} items per batch",
        )
    store = TradeReviewStore()
    redis_client = getattr(request.app.state, "redis", None)
    actor = username or "owner"
    ip = _client_ip(request)

    created, failed = [], []
    for item in req.items:
        try:
            trade_id = item.trade_id
            if trade_id:
                trade = await _resolve_trade(db, trade_id)
                ticket, account_login = trade["ticket"], trade["account_login"]
                symbol, open_time = trade["symbol"], trade["open_time"]
            else:
                if not (item.ticket and item.account_login):
                    raise ValueError("Provide trade_id or (ticket + account_login)")
                ticket, account_login = item.ticket, item.account_login
                symbol, open_time = "", datetime.utcnow()

            result = await trigger_review(
                store=store, username=actor, redis_client=redis_client,
                account_login=account_login, ticket=ticket, symbol=symbol,
                db=db, trade_id=trade_id, open_time=open_time, force=req.force, ip=ip,
            )
            created.append(result)
        except HTTPException as e:
            failed.append({"ticket": item.ticket, "trade_id": item.trade_id, "error": e.detail})
        except ValueError as e:
            failed.append({"ticket": item.ticket, "trade_id": item.trade_id, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            failed.append({"ticket": item.ticket, "trade_id": item.trade_id, "error": str(e)})

    return {"created": len(created), "failed": failed, "created_items": created}


def _public_review(row) -> dict:
    from app.services.trade_reviews import _public_review as _svc

    return _svc(row)
