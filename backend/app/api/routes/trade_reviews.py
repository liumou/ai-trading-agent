"""Trade Reviews API — 历史订单 AI 深度复盘（Phase 3D）。

- 全部走 make_authed_router（统一鉴权，防漏鉴权）。
- 触发（POST /）→ 202：`{trade_id}` 或 `{ticket, account_login}`，force 重审。
- 查询（GET /{id}、GET /by-ticket/{ticket}）→ 归属按 account_login 校验（IDOR）。
- 批量（POST /batch）→ ≤trade_review_max_batch，逐单独立失败，计入配额。
- 汇总（GET /summary）→ 跨单模式统计（real_mistake 次数 / 根因 top）。
- 配额：Redis 每日每用户 INCR+EXPIRE，超限 429；batch 计入同一配额。
"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
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


class TriggerRequest(BaseModel):
    trade_id: int | None = Field(None, gt=0)
    ticket: int | None = Field(None, gt=0)
    account_login: str | None = Field(None, max_length=32)
    force: bool = Field(False, description="强制重审（保留旧版进 review_history）")


class TriggerItem(BaseModel):
    """单笔触发：trade_id（bot 单）或 (ticket, account_login)（手动/统一）。"""

    trade_id: int | None = Field(None, gt=0)
    ticket: int | None = Field(None, gt=0)
    account_login: str | None = Field(None, max_length=32)


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
        select(Trade).where(Trade.id == trade_id)
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


# ─── 已登记账号 TTL 缓存 ──────────────────────────────────────────────────────
#
# 归属解析 `_resolve_owned_account` 的注册检查按需查库。前端加载 /history 时会
# 对每行已平仓交易并发发起 by-ticket 查询（一次 load 可达 ~200 个），每个都独立
# 开一次 async_session()；若每请求都查库，同一登录号会被反复命中同一条 SELECT，
# 通过 8+12 的池并发空转。缓存把检查压到"每个登录号每 TTL 一次"。
#
# 正确性：TTL 内账号登记状态变化会短暂不生效（最坏 60s 内新登记账号被降级到
# 当前账号）。登记状态极少变化（只在 /api/accounts 增删时变），且降级路径同样
# 安全（见 _resolve_owned_account），故接受这一窗口。

_REGISTERED_TTL = 60  # 秒
_registered_cache: dict[str, float] = {}


def _clear_registered_cache() -> None:
    """清空已登记账号缓存（测试隔离用；生产路径不调用）。"""
    _registered_cache.clear()


async def _is_registered(login: str) -> bool:
    """登录号是否已登记在 mt5_accounts（带 60s TTL 缓存）。"""
    if login in _registered_cache and time.monotonic() - _registered_cache[login] < _REGISTERED_TTL:
        return True
    from app.db.models import MT5Account
    from app.db.session import async_session

    async with async_session() as db:
        exists = (await db.execute(
            select(MT5Account.login).where(MT5Account.login == int(login))
        )).scalar_one_or_none()
    if exists is not None:
        _registered_cache[login] = time.monotonic()
        return True
    _registered_cache.pop(login, None)
    return False


async def _resolve_owned_account(
    request: Request, client_account_login: str | None
) -> str:
    """解析本请求可访问的账号。

    归属策略：客户端账号**优先且生效**（只要已登记在 mt5_accounts 或属于
    '0' 遗留桶），未登记/脏值时**降级**到 manager 当前活跃账号。

    旧实现强制等于"当前活跃账号"，会丢掉多账号的历史复盘：复盘记录挂在
    **触发瞬间**的活跃账号名下，而 /history 行自带的 account_login 是该笔
    订单的真实账号。用户在 A 账号做的复盘，切回 B 账号后就查不到（生产
    实例：110351809 的 3 条 completed 复盘在切回 336773771 后徽章全部消失）。

    放行条件：'0' 遗留桶（单账号期未标记账号的存量 trades）；或全数字登录
    号且存在于 mt5_accounts（含已切换走的账号）。其余情况降级，不 fail-open。
    DB 不可用时同样降级，DB 抖动不构成越权依据。

    返回值统一归一化掉前导零：mt5_accounts.login 是 BigInteger，"00110351809"
    与 "110351809" 是同一个登录号（成员检查走 int() 已等价），但本函数的返回
    值会原样写进 trade_reviews.account_login。若不归一化，"00110351809" 会成为
    账号串的真实存储值，而 /history 的 owned 过滤器用规范的 str(login) 永远匹配
    不上 → 该复盘永久不可见，按规范登录号也查不回来。此处是唯一的写入点。
    """
    client = (client_account_login or "").strip()
    manager = getattr(request.app.state, "manager", None)
    current = str(getattr(manager, "current_account_login", None) or "0")
    if not client or not client.isdigit():
        return current  # 空值回落；非数字一律降级，防注入/脏值
    try:
        # int() 放在 try 内：超长数字串转换会抛 ValueError，需降级而非 500。
        client = str(int(client))
        if client == "0":
            return "0"  # '0' 遗留桶：单账号期未标记账号的存量 trades
        if await _is_registered(client):
            return client
    except Exception as e:  # noqa: BLE001 — DB 抖动不作为越权依据
        logger.warning(f"trade review account resolve failed: {type(e).__name__}")
    return current


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
        # 手动单：按请求账号校验归属（见 _resolve_owned_account），既保留
        # 多账号隔离，又不因切换账号丢失历史复盘。
        account_login = await _resolve_owned_account(request, account_login or "0")
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
    """按订单查最新复盘（归属按请求账号校验，见 _resolve_owned_account）。

    请求账号已登记 → 按它查（跨账号历史复盘可回看）；未登记/DB 降级 →
    按当前活跃账号查（保留 f1157e6 的 IDOR 收紧语义）。两条路径都查不到
    才 404。
    """
    store = TradeReviewStore()
    account = await _resolve_owned_account(request, account_login)
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
    account = await _resolve_owned_account(request, account_login)
    return await store.summary(account, days=days)


@router.get("/{review_id}")
async def get_trade_review(
    review_id: int,
    request: Request,
    account_login: str = Query("0", max_length=32),
    username: str | None = Depends(require_auth),
):
    """按 id 查复盘结果（归属按请求账号校验，见 _resolve_owned_account）。"""
    store = TradeReviewStore()
    account = await _resolve_owned_account(request, account_login)
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

    # 批量按行归属：一次解析所有请求账号 → {原值: 生效值}，避免逐单打库。
    # bot 单按 trade_id 定位，归属由 DB 解析，不受账号校验影响。
    client_accounts = [
        (it.account_login or "").strip() for it in req.items if not it.trade_id
    ]
    account_map: dict[str, str] = {}
    for acct in sorted(set(a for a in client_accounts if a)):
        resolved = await _resolve_owned_account(request, acct)
        account_map[acct] = resolved

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
                # 与 account_map 的键（已 strip）保持同一归一化口径。口径不一致
                # 会让带首尾空白的账号查不到映射 → 归属校验被静默跳过，复盘以
                # " 110351809 " 这类垃圾字符串入账，之后再也查不回来。
                ticket = item.ticket
                account_login = item.account_login.strip()
                # 纯空白账号会因上面的真值判断通过、又在 strip 后变空串，若放行
                # 会错误落进 '0' 遗留桶（故障诊断困难的静默错配）。显式拒绝。
                if not account_login:
                    raise ValueError("account_login must not be blank")
                symbol, open_time = "", datetime.utcnow()
                if account_login in account_map:
                    account_login = account_map[account_login]

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
