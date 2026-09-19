"""MT5Account CRUD + 密码加密测试（Phase 4）。

- 密码用 Vault 加密存储，API 永不返回明文（H5 相关）。
- Trade ticket 复合唯一（account_login, ticket）（H4）。
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import MT5Account, Trade
from app.db.session import get_db
from app.vault import VaultService

# 与 secrets 测试一致：测试专用 vault（已知 key）
_test_vault = VaultService("test-vault-master-key-for-testing")


def _make_test_app(db_session):
    from fastapi import FastAPI

    from app.api.routes import accounts

    app = FastAPI()
    app.include_router(accounts.router)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


@pytest_asyncio.fixture
async def client(db_session):
    with patch("app.api.routes.accounts.vault", _test_vault):
        app = _make_test_app(db_session)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture(autouse=True)
def _patch_vault_for_model():
    """模型层直接建行也用测试 vault。"""
    with patch("app.api.routes.accounts.vault", _test_vault):
        yield


async def _create_account(db_session, login: int = 1001, password: str = "secret123", **kw):
    """直接建 MT5Account 行（不经 API，供模型层/复合唯一测试用）。"""
    ciphertext, nonce = _test_vault.encrypt(password)
    acct = MT5Account(
        login=login,
        password_encrypted=ciphertext,
        password_nonce=nonce,
        server=kw.get("server", "Broker-Server"),
        broker_name=kw.get("broker_name"),
        is_active=kw.get("is_active", False),
    )
    db_session.add(acct)
    await db_session.commit()
    return acct


# ─── 模型层：密码加密存储 ──────────────────────────────────────────────────


async def test_account_password_encrypted_not_plaintext(db_session):
    """密码必须加密存储，DB 中无明文。"""
    await _create_account(db_session, login=1001, password="SuperSecretP@ss")

    result = await db_session.execute(select(MT5Account).where(MT5Account.login == 1001))
    stored = result.scalars().first()
    assert stored is not None
    assert stored.password_encrypted != b"SuperSecretP@ss"
    # 可解密回原值
    assert _test_vault.decrypt(stored.password_encrypted, stored.password_nonce) == "SuperSecretP@ss"


# ─── 模型层：Trade ticket 复合唯一（H4） ──────────────────────────────────


async def test_trade_ticket_unique_per_account(db_session):
    """H4：同一 ticket 在不同 account_login 下可共存（复合唯一）。"""
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError

    def make(sym_acct: str) -> Trade:
        return Trade(
            ticket=777, account_login=sym_acct, symbol="GOLD", type="BUY", lot=0.1,
            open_price=1.0, sl=0.9, tp=1.1, open_time=datetime.utcnow(), strategy_name="ema",
        )

    db_session.add_all([make("1001"), make("1002")])
    await db_session.commit()  # 不同账号同 ticket 应成功

    db_session.add(make("1001"))  # 同账号同 ticket 应冲突
    with pytest.raises(IntegrityError):
        await db_session.commit()


# ─── API 层：CRUD（patch vault） ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_accounts(client):
    """POST 创建 + GET 列表，密码不泄露。"""
    resp = await client.post("/api/accounts", json={
        "login": 2020, "password": "pw2020", "server": "Srv", "broker_name": "Demo"
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["login"] == 2020
    assert "password" not in body  # 不返回密码

    resp2 = await client.get("/api/accounts")
    assert resp2.status_code == 200
    items = resp2.json()
    assert any(a["login"] == 2020 for a in items)


@pytest.mark.asyncio
async def test_create_duplicate_login_conflict(client):
    """重复 login 应 409。"""
    resp = await client.post("/api/accounts", json={
        "login": 3030, "password": "pw3030", "server": "Srv"
    })
    assert resp.status_code == 201, resp.text
    resp2 = await client.post("/api/accounts", json={
        "login": 3030, "password": "pw3030", "server": "Srv"
    })
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_update_account_password(client, db_session):
    """PUT 更新密码后，DB 密文更新且可解密为新值。"""
    acct = await _create_account(db_session, login=4040, password="old-pw")
    resp = await client.put(f"/api/accounts/{acct.id}", json={"password": "new-pw"})
    assert resp.status_code == 200, resp.text

    result = await db_session.execute(select(MT5Account).where(MT5Account.id == acct.id))
    updated = result.scalars().first()
    assert _test_vault.decrypt(updated.password_encrypted, updated.password_nonce) == "new-pw"


@pytest.mark.asyncio
async def test_delete_account_soft_delete(client, db_session):
    """DELETE 软删：列表不含，但行仍存在（is_deleted=True）。"""
    acct = await _create_account(db_session, login=5050)
    resp = await client.delete(f"/api/accounts/{acct.id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp2 = await client.get("/api/accounts")
    assert not any(a["id"] == acct.id for a in resp2.json())

    result = await db_session.execute(select(MT5Account).where(MT5Account.id == acct.id))
    stored = result.scalars().first()
    assert stored.is_deleted is True


@pytest.mark.asyncio
async def test_get_current_account_empty(client):
    """无活跃账号时 /current 返回 null。"""
    resp = await client.get("/api/accounts/current")
    assert resp.status_code == 200
    assert resp.json() is None
