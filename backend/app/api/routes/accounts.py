"""MT5 账号管理 API（Phase 4）— 账号 CRUD + 当前活跃账号。

凭据独立于 secrets 表（H5：secrets 表会被 runner 注入到沙箱进程），
密码用 VaultService AES-256-GCM 加密存 `mt5_accounts.password_encrypted`。
API 永不返回明文密码。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router_factory import make_authed_router
from app.auth import require_auth
from app.db.models import MT5Account
from app.db.session import get_db
from app.vault import VaultUnavailableError, vault

router = make_authed_router(prefix="/api/accounts", tags=["accounts"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class AccountCreateRequest(BaseModel):
    login: int = Field(..., gt=0)
    password: str = Field(..., min_length=1)
    server: str = ""
    broker_name: str | None = None


class AccountUpdateRequest(BaseModel):
    password: str | None = None
    server: str | None = None
    broker_name: str | None = None
    is_enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    login: int
    server: str
    broker_name: str | None
    is_active: bool
    is_enabled: bool
    last_switched_at: str | None
    created_at: str


def _to_response(a: MT5Account) -> AccountResponse:
    return AccountResponse(
        id=a.id,
        login=a.login,
        server=a.server,
        broker_name=a.broker_name,
        is_active=a.is_active,
        is_enabled=a.is_enabled,
        last_switched_at=a.last_switched_at.isoformat() if a.last_switched_at else None,
        created_at=a.created_at.isoformat() if a.created_at else "",
    )


def _require_vault() -> None:
    """密码加解密依赖 Vault master key。未配置时拒绝写操作。"""
    if not vault.is_available:
        raise HTTPException(
            status_code=503,
            detail="Vault master key not configured — cannot store/decrypt MT5 account passwords",
        )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    """列出所有未删除的账号（脱敏，不含密码）。"""
    result = await db.execute(
        select(MT5Account).where(MT5Account.is_deleted.is_(False)).order_by(MT5Account.login)
    )
    return [_to_response(a) for a in result.scalars().all()]


@router.get("/current")
async def get_current_account(db: AsyncSession = Depends(get_db)):
    """当前活跃账号。"""
    result = await db.execute(select(MT5Account).where(MT5Account.is_active.is_(True)))
    account = result.scalars().first()
    if account is None:
        return None
    return _to_response(account)


@router.post("", status_code=201)
async def create_account(req: AccountCreateRequest, db: AsyncSession = Depends(get_db)):
    """新增账号。密码加密入库（Vault），不复用 secrets 表（H5）。"""
    _require_vault()

    # login 唯一性检查
    existing = await db.execute(select(MT5Account).where(MT5Account.login == req.login))
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"Account {req.login} already exists")

    try:
        ciphertext, nonce = vault.encrypt(req.password)
    except VaultUnavailableError:
        raise HTTPException(status_code=503, detail="Vault unavailable")

    account = MT5Account(
        login=req.login,
        password_encrypted=ciphertext,
        password_nonce=nonce,
        server=req.server,
        broker_name=req.broker_name,
        is_active=False,
        is_enabled=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _to_response(account)


@router.put("/{account_id}")
async def update_account(account_id: int, req: AccountUpdateRequest, db: AsyncSession = Depends(get_db)):
    """更新账号（密码可选更新，其余字段可选）。"""
    result = await db.execute(select(MT5Account).where(MT5Account.id == account_id, MT5Account.is_deleted.is_(False)))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if req.password is not None:
        _require_vault()
        try:
            ciphertext, nonce = vault.encrypt(req.password)
        except VaultUnavailableError:
            raise HTTPException(status_code=503, detail="Vault unavailable")
        account.password_encrypted = ciphertext
        account.password_nonce = nonce
    if req.server is not None:
        account.server = req.server
    if req.broker_name is not None:
        account.broker_name = req.broker_name
    if req.is_enabled is not None:
        account.is_enabled = req.is_enabled

    account.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(account)
    return _to_response(account)


@router.delete("/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """软删账号（置 is_deleted=True，保留审计）。"""
    result = await db.execute(select(MT5Account).where(MT5Account.id == account_id, MT5Account.is_deleted.is_(False)))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.is_deleted = True
    account.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"deleted": True, "id": account_id}


class SwitchRequest(BaseModel):
    actor: str = "owner"


@router.post("/{account_id}/switch")
async def switch_account(account_id: int, req: SwitchRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """切换到目标账号（Phase 5 核心）。

    编排：暂停引擎 → Bridge 切换 → 刷新规格 → 显式恢复。切换期间 Redis
    `switching:in_progress` 门禁拒绝 AI/MCP 下单（H2）。
    """
    switch_service = getattr(request.app.state, "account_switch_service", None)
    if switch_service is None:
        raise HTTPException(status_code=503, detail="Account switch service not initialized")

    try:
        result = await switch_service.switch(target_account_id=account_id, actor=req.actor)
        return result
    except Exception as e:  # noqa: BLE001
        from loguru import logger

        logger.error(f"Account switch to {account_id} failed: {e!r}")
        raise HTTPException(status_code=502, detail=str(e)) from e
