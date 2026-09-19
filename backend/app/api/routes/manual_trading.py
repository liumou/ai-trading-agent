"""Manual trading API — every order passes the ManualOrderGate firewall.

Endpoints:
- POST   /api/trading/orders              submit (market/pending/modify-pending) → 200 REJECTED / 202 PENDING_REVIEW
- POST   /api/trading/orders/{id}/confirm second-confirm a CAUTION verdict
- GET    /api/trading/reviews             review history (manual channel)
- GET    /api/trading/reviews/{id}        poll a single review (202 flow)
- GET    /api/trading/orders              pending order list (canonical symbols)
- DELETE /api/trading/orders/{ticket}     cancel pending (risk-reducing: no LLM)
- PUT    /api/trading/positions/{ticket}  modify position SL/TP (entry-anchored drift budget)
- POST   /api/trading/positions/{ticket}/close gated close (shared with dashboard route)
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.config import get_canonical_symbol
from app.services.position_close import close_position_gated

router = APIRouter(prefix="/api/trading", tags=["trading"], dependencies=[Depends(require_auth)])


def _gate(request: Request):
    gate = getattr(request.app.state, "manual_order_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="Trading gate not initialized")
    return gate


def _account_login(request: Request) -> str:
    manager = getattr(request.app.state, "manager", None)
    login = getattr(manager, "current_account_login", "0") if manager else "0"
    return str(login or "0")


class SubmitOrderRequest(BaseModel):
    symbol: str
    order_kind: str = Field(default="market", pattern="^(market|pending)$")
    order_type: str = Field(pattern="^(BUY|SELL|BUY_LIMIT|SELL_LIMIT|BUY_STOP|SELL_STOP)$")
    lot: float = Field(gt=0)
    sl: float = Field(default=0.0, ge=0)
    tp: float = Field(default=0.0, ge=0)
    price: float | None = Field(default=None, gt=0)  # required for pending
    comment: str = Field(default="", max_length=60)
    modify_ticket: int | None = None  # pending-order ticket when modifying


class ModifyPendingRequest(BaseModel):
    price: float | None = Field(default=None, gt=0)
    sl: float | None = None
    tp: float | None = None


class ModifySlTpRequest(BaseModel):
    sl: float | None = None
    tp: float | None = None


@router.post("/orders")
async def submit_order(req: SubmitOrderRequest, request: Request):
    if req.order_kind == "pending" and req.price is None and req.modify_ticket is None:
        raise HTTPException(status_code=422, detail="price is required for pending orders")
    gate = _gate(request)
    result = await gate.submit_order(
        symbol=req.symbol,
        order_kind=req.order_kind,
        order_type=req.order_type,
        lot=req.lot,
        sl=req.sl,
        tp=req.tp,
        price=req.price,
        comment=req.comment,
        account_login=_account_login(request),
        modify_ticket=req.modify_ticket,
    )
    # 202 = 已受理待审查(PENDING_REVIEW,前端轮询/WS 跟进);
    # 200 = 已有终态(REJECTED 拒单是完整答复,不是错误)
    if result.get("status") == "PENDING_REVIEW":
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/orders/{review_id}/confirm")
async def confirm_order(review_id: int, request: Request):
    gate = _gate(request)
    result = await gate.confirm_and_execute(review_id, account_login=_account_login(request))
    return result


@router.get("/reviews")
async def list_reviews(request: Request, limit: int = 50):
    gate = _gate(request)
    return {"reviews": await gate.list_reviews(account_login=_account_login(request), limit=min(limit, 200))}


@router.get("/reviews/{review_id}")
async def get_review(review_id: int, request: Request):
    gate = _gate(request)
    review = await gate.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.get("/orders")
async def list_pending_orders(request: Request):
    gate = _gate(request)
    orders = await gate.list_pending_orders()
    # 桥返回券商名(GOLD_),归一化为规范名与持仓/行情口径一致(评审 L-3)
    for o in orders:
        o["symbol"] = get_canonical_symbol(o.get("symbol") or "")
    return {"orders": orders}


@router.delete("/orders/{ticket}")
async def cancel_pending_order(ticket: int, request: Request):
    gate = _gate(request)
    result = await gate.cancel_pending_order(ticket)
    if not result.get("cancelled"):
        raise HTTPException(status_code=400, detail=result.get("error") or result.get("reason", "Cancel failed"))
    return result


@router.put("/orders/{ticket}")
async def modify_pending_order(ticket: int, req: ModifyPendingRequest, request: Request):
    """改挂单 = 全流水线重审(价格/SL/TP 任一变更都重新过闸门+AI 审查)。

    未指定的字段取当前挂单值(审查对象 = 变更后的完整订单)。
    """
    gate = _gate(request)
    orders = await gate.list_pending_orders()
    current = next((o for o in orders if o.get("ticket") == ticket), None)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Pending order {ticket} not found on current account")
    if req.price is None and req.sl is None and req.tp is None:
        raise HTTPException(status_code=422, detail="Nothing to modify: provide price, sl or tp")

    order_type = str(current.get("type", "")).upper()
    if not order_type.startswith(("BUY", "SELL")):
        raise HTTPException(status_code=400, detail=f"Unsupported order type: {order_type}")
    direction = "BUY" if order_type.startswith("BUY") else "SELL"

    result = await gate.submit_order(
        symbol=get_canonical_symbol(str(current.get("symbol", ""))),
        order_kind="pending",
        order_type=order_type,
        lot=float(current.get("volume_initial") or current.get("lot") or 0),
        sl=float(req.sl if req.sl is not None else (current.get("sl") or 0)),
        tp=float(req.tp if req.tp is not None else (current.get("tp") or 0)),
        price=float(req.price if req.price is not None else (current.get("price_open") or 0)),
        comment=f"modify #{ticket}",
        account_login=_account_login(request),
        modify_ticket=ticket,
    )
    return result


@router.put("/positions/{ticket}")
async def modify_position_sltp(ticket: int, req: ModifySlTpRequest, request: Request):
    if req.sl is None and req.tp is None:
        raise HTTPException(status_code=422, detail="Provide sl and/or tp")
    gate = _gate(request)
    result = await gate.modify_position_sltp(
        ticket, req.sl, req.tp, account_login=_account_login(request)
    )
    if not result.get("modified"):
        if result.get("rejected"):
            return result  # 防火墙拒绝是业务结果,非 4xx 错误
        raise HTTPException(status_code=400, detail=result.get("error", "Modification failed"))
    return result


@router.post("/positions/{ticket}/close")
async def close_position(ticket: int, request: Request):
    """与 dashboard 平仓同一闸门路径(position_close.close_position_gated)。"""
    result = await close_position_gated(request.app.state.connector, request.app.state.redis, ticket)
    if not result.get("closed"):
        if result.get("rejected"):
            return result
        raise HTTPException(status_code=400, detail=result.get("error", "Close failed"))
    return result
