"""行情提醒 API（Phase 5）— 价格阈值规则 CRUD + 启停 + 测试发送。

- 全部走 make_authed_router（统一鉴权，防漏鉴权）。
- symbol 校验用 resolve_canonical_symbol + get_active_symbols（支持 DB 动态品种/别名，
  不误拒合法品种）。
- toggle 重开时重置 sent_count + 清理 Redis first_trigger（修复达上限后重开静默失效）。
- 删除规则时清理 Redis 孤儿 key（防状态泄漏）。
- 测试发送走 app.state.feishu_notifier（不读写 DB）。
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router_factory import make_authed_router
from app.config import get_active_symbols, resolve_canonical_symbol
from app.db.models import PriceAlert
from app.db.session import get_db

router = make_authed_router(prefix="/api/price-alerts", tags=["price-alerts"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class PriceAlertCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    condition: str = Field("above", pattern="^(above|below)$")
    trigger_price: float = Field(..., gt=0)
    duration_seconds: int = Field(60, ge=1, le=86400)
    max_notifications: int = Field(1, ge=1, le=1000)
    note: str | None = Field(None, max_length=200)


class PriceAlertUpdate(BaseModel):
    condition: str | None = Field(None, pattern="^(above|below)$")
    trigger_price: float | None = Field(None, gt=0)
    duration_seconds: int | None = Field(None, ge=1, le=86400)
    max_notifications: int | None = Field(None, ge=1, le=1000)
    note: str | None = Field(None, max_length=200)


class PriceAlertTestRequest(BaseModel):
    symbol: str = Field("GOLD", min_length=1, max_length=32)


class PriceAlertResponse(BaseModel):
    id: int
    symbol: str
    condition: str
    trigger_price: float
    duration_seconds: int
    max_notifications: int
    sent_count: int
    note: str | None
    is_active: bool
    last_sent_at: str | None
    created_at: str
    updated_at: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, alert: PriceAlert) -> "PriceAlertResponse":
        return cls(
            id=alert.id,
            symbol=alert.symbol,
            condition=alert.condition,
            trigger_price=alert.trigger_price,
            duration_seconds=alert.duration_seconds,
            max_notifications=alert.max_notifications,
            sent_count=alert.sent_count,
            note=alert.note,
            is_active=alert.is_active,
            last_sent_at=alert.last_sent_at.isoformat() if alert.last_sent_at else None,
            created_at=alert.created_at.isoformat() if alert.created_at else None,
            updated_at=alert.updated_at.isoformat() if alert.updated_at else None,
        )


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _validate_symbol(symbol: str) -> str:
    """校验品种存在性，返回规范名。支持 DB 动态品种/别名。"""
    canonical = resolve_canonical_symbol(symbol)
    active = get_active_symbols()
    # 规范名或原始名在活跃列表中即接受
    if canonical in active or symbol in active:
        return canonical
    raise HTTPException(
        status_code=422,
        detail=f"Unknown symbol '{symbol}' — must be an active symbol",
    )


async def _get_alert_or_404(db: AsyncSession, alert_id: int) -> PriceAlert:
    alert = await db.get(PriceAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Price alert {alert_id} not found")
    return alert


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _cleanup_trigger_state(request: Request, alert_id: int) -> None:
    """删除规则的 Redis first_trigger 状态（防孤儿 key）。用 app.state 的 service。"""
    service = getattr(request.app.state, "price_alert_service", None)
    if service is not None:
        try:
            await service.cleanup_rule(alert_id)
        except Exception:
            pass


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_price_alert_status(request: Request, db: AsyncSession = Depends(get_db)):
    """返回提醒链路的运行状态。

    未配置 FEISHU_WEBHOOK_URL 时巡检引擎静默短路，用户只会看到"提醒没反应"，
    无从排查。前端据此显示横幅，把配置缺失变成一眼可见的状态。
    只报布尔状态，不暴露 webhook URL（属机密）。

    注意：必须注册在 ``/{alert_id}`` 之前，否则 ``status`` 会被路径参数抢走匹配。
    """
    notifier = getattr(request.app.state, "feishu_notifier", None)
    enabled = bool(notifier and notifier.enabled)
    active_count = 0
    if enabled:
        result = await db.execute(select(PriceAlert).where(PriceAlert.is_active.is_(True)))
        active_count = len(list(result.scalars().all()))

    return {
        "feishu_enabled": enabled,
        "config_key": "FEISHU_WEBHOOK_URL",
        "active_alerts": active_count,
        "message": None
        if enabled
        else "飞书未配置：提醒规则已保存但不会发送任何消息。请在服务端设置 FEISHU_WEBHOOK_URL 后重启服务。",
    }


@router.get("")
async def list_price_alerts(db: AsyncSession = Depends(get_db)):
    """列出全部提醒规则（含运行态 sent_count / is_active）。"""
    result = await db.execute(select(PriceAlert).order_by(PriceAlert.created_at.desc()))
    alerts = list(result.scalars().all())
    return [PriceAlertResponse.from_orm(a) for a in alerts]


@router.post("", status_code=201)
async def create_price_alert(req: PriceAlertCreate, db: AsyncSession = Depends(get_db)):
    """创建提醒规则。校验品种存在 + 参数合法性。"""
    canonical = _validate_symbol(req.symbol)
    alert = PriceAlert(
        symbol=canonical,
        condition=req.condition,
        trigger_price=req.trigger_price,
        duration_seconds=req.duration_seconds,
        max_notifications=req.max_notifications,
        note=req.note,
        is_active=True,
        sent_count=0,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return PriceAlertResponse.from_orm(alert)


@router.put("/{alert_id}")
async def update_price_alert(
    alert_id: int,
    req: PriceAlertUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """更新规则。更新后重置 sent_count + 清理 first_trigger（重新计时）。"""
    alert = await _get_alert_or_404(db, alert_id)
    updates = req.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(alert, field, value)
    # 更新即重置计数与发送状态（用户改条件/阈值期望重新开始）
    alert.sent_count = 0
    alert.last_sent_at = None
    alert.updated_at = _now()
    await db.commit()
    await db.refresh(alert)
    await _cleanup_trigger_state(request, alert_id)
    return PriceAlertResponse.from_orm(alert)


@router.delete("/{alert_id}")
async def delete_price_alert(
    alert_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """删除规则。同时清理 Redis 状态（防孤儿 key）。"""
    alert = await _get_alert_or_404(db, alert_id)
    await db.delete(alert)
    await db.commit()
    await _cleanup_trigger_state(request, alert_id)
    return {"status": "deleted", "id": alert_id}


@router.post("/{alert_id}/toggle")
async def toggle_price_alert(
    alert_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """启停规则。

    关键：从停用 → 启用时**必须重置** sent_count + 清理 first_trigger，
    否则达上限后重开会静默失效（永远无法再发送且 UI 显示为开启）。
    """
    alert = await _get_alert_or_404(db, alert_id)
    alert.is_active = not alert.is_active
    if alert.is_active:
        # 重新开启：重置计数与发送状态，清除 Redis 计时
        alert.sent_count = 0
        alert.last_sent_at = None
        await _cleanup_trigger_state(request, alert_id)
    alert.updated_at = _now()
    await db.commit()
    await db.refresh(alert)
    return PriceAlertResponse.from_orm(alert)


@router.post("/test")
async def test_price_alert(req: PriceAlertTestRequest, request: Request):
    """发送测试卡片（验证飞书 webhook 配置）。不读写 DB。"""
    feishu_notifier = getattr(request.app.state, "feishu_notifier", None)
    if feishu_notifier is None or not feishu_notifier.enabled:
        raise HTTPException(status_code=503, detail="Feishu notifier not configured")
    ok = await feishu_notifier.send_test_card(symbol=req.symbol)
    if not ok:
        raise HTTPException(status_code=502, detail="Feishu test card send failed")
    return {"status": "sent", "symbol": req.symbol}